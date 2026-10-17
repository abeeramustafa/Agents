"""Decides which agent runs next. This is ordinary code, not a model.

BRIEF asks Maya, then scores with the catalog math, then asks Omar.
PRODUCT_REVIEW records approve or reject.
SUPPLIER_REVIEW asks Lina only for approved products, three at a time.
FINAL_REVIEW asks Sana to summarize the picks the human already made.
"""

from __future__ import annotations

import asyncio

from app.agents.auditor import AuditorAgent
from app.agents.intake import IntakeAgent, intake_payload
from app.agents.planner import PlannerAgent
from app.agents.scout import ScoutAgent, scout_payload
from app.agents.supplier import SupplierAgent
from app.catalog_gate import apply_intake
from app.config import settings
from app.copy import chat_line, hide_ids
from app.data_store import DataStore
from app.llm_client import LLMError
from app.plan_review import accept_plan, despite_line, plan_key, sana_payload
from app.scoring import score_catalog
from app.session_store import Session, SessionStore
from app.supplier_review import (
    CONCURRENCY,
    accept_risks,
    accept_supplier_ids,
    chunks,
    lina_payload,
    prepare_product,
    run_with_rate_backoff,
)

CANDIDATE_LIMIT = 15
RANK_LIMIT = 8
STAGE_ORDER = ["BRIEF", "PRODUCT_REVIEW", "SUPPLIER_REVIEW", "FINAL_REVIEW"]


class FlowError(Exception):
    """A transition the human is not allowed to make. The UI shows error and hint."""

    def __init__(self, error: str, hint: str = "") -> None:
        self.error = error
        self.hint = hint
        super().__init__(error)


class Orchestrator:
    def __init__(self, catalog: DataStore, sessions: SessionStore) -> None:
        self.catalog = catalog
        self.sessions = sessions
        self.maya = IntakeAgent()
        self.omar = ScoutAgent()
        self.lina = SupplierAgent()
        self.karim = AuditorAgent()
        self.sana = PlannerAgent()
        self._supplier_tasks: dict[str, asyncio.Task] = {}

    def require(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise FlowError("That session expired.", "Reload the page to start again.")
        return session

    async def handle_message(self, session_id: str, text: str) -> dict:
        session = self.require(session_id)
        brief = text.strip()
        if not brief:
            raise FlowError("Type a product or pick an example.", "The box cannot be empty.")

        names = self._names(session)
        session.add_chat("You", "Founder", brief)
        session.add_event("You", "Sent a brief", brief, names)
        try:
            intake = await self.maya.run(
                intake_payload(brief, session.filters, self.catalog.catalog_meta())
            )
        except LLMError:
            session.chat.pop()
            session.activity_log.pop()
            raise

        gate = apply_intake(self.catalog, intake, user_text=brief)
        session.filters = gate.filters.model_dump()
        session.banner = gate.message
        session.note = gate.note
        session.clarifying_question = gate.clarifying_question
        session.add_chat("Maya", "Intake", chat_line("Maya", gate.message, names))
        session.add_event("Maya", _maya_action(gate.status), gate.message, names)

        if gate.status == "clarify":
            session.stage = "BRIEF"
            session.gate_status = "clarify"
            session.chips = list(self.catalog.catalog_meta()["categories"])
            session.closest_matches = []
            return self.view(session)

        if gate.status == "out_of_catalog":
            session.stage = "BRIEF"
            session.gate_status = "out_of_catalog"
            session.chips = []
            session.closest_matches = [match.model_dump() for match in gate.closest_matches]
            return self.view(session)

        candidates = self._top_candidates(gate.product_ids)
        session.candidates = candidates
        ranked, omar_message, used_fallback = await self._rank(candidates)
        ranked = pin_approved(ranked, candidates, session.product_decisions)
        remaining = {str(row["product_id"]) for row in ranked}
        # A new search drops decisions only for products that fell off the list.
        session.product_decisions = keep_decisions(session.product_decisions, remaining)
        session.ranked = ranked
        session.stage = "PRODUCT_REVIEW"
        session.gate_status = "ready"
        session.chips = []
        session.closest_matches = []
        session.clarifying_question = None
        session.note = None
        if used_fallback:
            omar_message = (
                "I could not finish a written ranking, so these are the top scores from the catalog math."
            )
        names = self._names(session)
        session.banner = chat_line("Omar", omar_message or gate.message, names)
        session.add_chat("Omar", "Product research", session.banner)
        session.add_event("Omar", "Ranked products", f"{len(ranked)} products", names)
        return self.view(session)

    def set_decision(self, session_id: str, product_id: str, decision: str) -> dict:
        session = self.require(session_id)
        if session.stage != "PRODUCT_REVIEW":
            raise FlowError("Approve products from the product list.", "Send a brief first.")
        product_id = str(product_id).strip()
        shown = {str(row["product_id"]) for row in session.ranked}
        if product_id not in shown:
            raise FlowError("That product is not on the list.", "Reload the page.")
        name = _name_for(session, product_id)
        if decision == "undo":
            session.product_decisions.pop(product_id, None)
            session.product_decisions = keep_decisions(session.product_decisions, shown)
            action = "cleared a decision on"
        elif decision in {"approved", "rejected"}:
            session.product_decisions[product_id] = decision
            action = "approved" if decision == "approved" else "rejected"
        else:
            raise FlowError("Decision must be approved, rejected, or undo.", "")
        session.add_event("You", action, name, self._names(session))
        return self.view(session)

    def go_back(self, session_id: str, stage: str) -> dict:
        session = self.require(session_id)
        if stage not in {"BRIEF", "PRODUCT_REVIEW", "SUPPLIER_REVIEW", "FINAL_REVIEW"}:
            raise FlowError("That stage is not available.", "Use the stepper to move between the four stages.")
        if stage == "PRODUCT_REVIEW" and not session.ranked:
            raise FlowError("There are no ranked products yet.", "Send a brief first.")
        if stage == "SUPPLIER_REVIEW" and not session.supplier_sections:
            raise FlowError("There is no supplier review yet.", "Approve products and continue.")
        if stage == "FINAL_REVIEW":
            if not session.plan:
                raise FlowError(
                    "There is no plan yet.",
                    "Choose a supplier for each product, then continue.",
                )
            if session.plan_key != plan_key(session.supplier_sections):
                raise FlowError(
                    "The supplier picks changed.",
                    "Continue to the final plan again so Sana can rewrite it.",
                )
        session.stage = stage
        session.add_event("You", "went back to", _stage_label(stage), self._names(session))
        return self.view(session)

    async def request_suppliers(self, session_id: str) -> dict:
        session = self.require(session_id)
        guard_suppliers(session)
        approved = _approved_ids(session)
        if not approved:
            raise FlowError(
                "Approve a product that is still on the list.",
                "Rejected products do not get a supplier comparison.",
            )
        self._sync_supplier_sections(session, approved)
        session.stage = "SUPPLIER_REVIEW"
        session.add_event(
            "You",
            "opened supplier review",
            f"{len(approved)} approved products",
            self._names(session),
        )
        self._start_supplier_job(session)
        return self.view(session)

    async def retry_supplier(self, session_id: str, product_id: str) -> dict:
        session = self.require(session_id)
        if session.stage != "SUPPLIER_REVIEW":
            raise FlowError("Retry from the supplier review.", "Continue to suppliers first.")
        section = _section(session, product_id)
        if section is None:
            raise FlowError("That product is not in this review.", "Reload the page.")
        if section["limited_supply"]:
            raise FlowError("This product has nothing to compare.", "Select the only supplier if you want it.")
        section["status"] = "pending"
        section["error"] = ""
        await self._analyze_one(session, section)
        self._refresh_progress(session, running=False)
        return self.view(session)

    def select_suppliers(self, session_id: str, product_id: str, supplier_ids: list[str]) -> dict:
        session = self.require(session_id)
        if session.stage != "SUPPLIER_REVIEW":
            raise FlowError("Select suppliers from the supplier review.", "Continue to suppliers first.")
        section = _section(session, product_id)
        if section is None:
            raise FlowError("That product is not in this review.", "Reload the page.")
        valid = {row["supplier_id"] for row in section["offers"]}
        chosen = accept_supplier_ids(supplier_ids, valid, limit=len(valid) or 1)
        session.supplier_selection[product_id] = chosen
        section["selected_supplier_ids"] = chosen
        labels = _supplier_names(section, chosen)
        session.add_event(
            "You",
            "selected suppliers for",
            f"{section['name']}: {labels or 'none'}",
            self._names(session),
        )
        for offer in section["offers"]:
            if offer["supplier_id"] not in chosen:
                continue
            line = despite_line(section["name"], offer["supplier_name"], offer.get("risk_flags") or [])
            if line:
                session.add_event("You", "chose a flagged supplier", line, self._names(session))
        return self.view(session)

    async def request_final(self, session_id: str) -> dict:
        session = self.require(session_id)
        guard_final(session)
        products = {}
        for section in session.supplier_sections:
            product = self.catalog.get_product(section["product_id"])
            if product is None:
                continue
            products[section["product_id"]] = {
                "retail_price_usd": round(float(product["retail_price_usd"]), 2),
                "market_wholesale_price_usd": round(float(product["market_wholesale_price_usd"]), 2),
            }
        payload = sana_payload(session.supplier_sections, products)
        # Karim audits Omar and Lina only. A planner failure must not invent a plan.
        result = await run_with_rate_backoff(lambda: self.sana.run(payload))
        accepted = accept_plan(result, session.supplier_sections)
        names = self._names(session)
        message = chat_line("Sana", accepted.pop("message"), names)
        accepted["headline"] = hide_ids(accepted["headline"], names)
        accepted["risks_to_check"] = [hide_ids(row, names) for row in accepted["risks_to_check"]]
        accepted["next_steps"] = [hide_ids(row, names) for row in accepted["next_steps"]]
        for item in accepted["plan"]:
            item["why"] = hide_ids(item["why"], names)
        session.plan = accepted
        session.plan_key = plan_key(session.supplier_sections)
        session.stage = "FINAL_REVIEW"
        session.add_chat("Sana", "Sourcing lead", message)
        session.add_event("Sana", "wrote the final plan", accepted["headline"], names)
        return self.view(session)

    def view(self, session: Session) -> dict:
        meta = self.catalog.catalog_meta()
        return {
            "id": session.id,
            "stage": session.stage,
            "gate_status": session.gate_status,
            "banner": session.banner,
            "note": session.note,
            "clarifying_question": session.clarifying_question,
            "chips": session.chips,
            "closest_matches": session.closest_matches,
            "chat": session.chat,
            "activity_log": session.activity_log,
            "cards": _cards(session) if session.stage == "PRODUCT_REVIEW" else [],
            "supplier_sections": session.supplier_sections if session.stage == "SUPPLIER_REVIEW" else [],
            "supplier_progress": dict(session.supplier_progress),
            "plan": session.plan if session.stage == "FINAL_REVIEW" else None,
            "approved_count": len(_approved_ids(session)),
            "reachable_stages": _reachable(session),
            "categories": meta["categories"],
            "retail_price_min": meta["retail_price_min"],
            "retail_price_max": meta["retail_price_max"],
        }

    def _sync_supplier_sections(self, session: Session, approved: list[str]) -> None:
        existing = {row["product_id"]: row for row in session.supplier_sections}
        sections = []
        for product_id in approved:
            current = existing.get(product_id)
            if current is None:
                current = prepare_product(self.catalog, product_id)
            if current is None:
                continue
            chosen = accept_supplier_ids(
                session.supplier_selection.get(product_id, []),
                {row["supplier_id"] for row in current["offers"]},
                limit=max(len(current["offers"]), 1),
            )
            current["selected_supplier_ids"] = chosen
            session.supplier_selection[product_id] = chosen
            sections.append(current)
        session.supplier_sections = sections
        dropped = set(session.supplier_selection) - set(approved)
        for product_id in dropped:
            session.supplier_selection.pop(product_id, None)
            session.supplier_analysis.pop(product_id, None)
        pending = [row for row in sections if row["status"] == "pending"]
        show_batches = len(sections) > 6
        session.supplier_progress = {
            "running": bool(pending),
            "completed": len(sections) - len(pending),
            "total": len(sections),
            "batch": 1 if pending else 0,
            "batches": len(chunks(pending, CONCURRENCY)) if show_batches else 0,
        }

    def _start_supplier_job(self, session: Session) -> None:
        if not session.supplier_progress["running"]:
            return
        task = self._supplier_tasks.get(session.id)
        if task is not None and not task.done():
            return
        self._supplier_tasks[session.id] = asyncio.create_task(self._supplier_job(session.id))

    async def _supplier_job(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            return
        try:
            batch_index = 0
            while True:
                pending = [row for row in session.supplier_sections if row["status"] == "pending"]
                if not pending:
                    break
                group = pending[:CONCURRENCY]
                batch_index += 1
                # More than six approved products is enough to wait, so name the batch.
                if len(session.supplier_sections) > 6:
                    remaining = len(pending) - len(group)
                    session.supplier_progress["batch"] = batch_index
                    session.supplier_progress["batches"] = batch_index + (
                        (remaining + CONCURRENCY - 1) // CONCURRENCY if remaining else 0
                    )
                # return_exceptions keeps one failed product from cancelling the others.
                await asyncio.gather(
                    *(self._analyze_one(session, section) for section in group),
                    return_exceptions=True,
                )
            failed = sum(1 for row in session.supplier_sections if row["status"] == "error")
            compared = sum(
                1
                for row in session.supplier_sections
                if row["status"] == "ready" and not row["limited_supply"]
            )
            if compared or failed:
                detail = f"Compared {compared} products."
                if failed:
                    detail += f" {failed} still need a retry."
                session.add_chat("Lina", "Supplier analyst", chat_line("Lina", detail, self._names(session)))
                session.add_event("Lina", "finished supplier comparison", detail, self._names(session))
            else:
                session.add_event(
                    "Lina",
                    "skipped comparison",
                    "Every approved product has a single offer.",
                    self._names(session),
                )
        finally:
            if session is not None:
                self._refresh_progress(session, running=False)

    async def _analyze_one(self, session: Session, section: dict) -> None:
        if section["limited_supply"] or len(section["offers"]) < 2:
            section["status"] = "ready"
            self._refresh_progress(session, running=True)
            return
        payload = lina_payload(section)
        valid = {row["supplier_id"] for row in section["offers"]}
        try:
            result = await run_with_rate_backoff(lambda: self.lina.run(payload))
        except LLMError as exc:
            section["status"] = "error"
            section["error"] = str(exc)
            session.add_event("Lina", "could not compare", section["name"], self._names(session))
            self._refresh_progress(session, running=True)
            return
        except Exception:
            section["status"] = "error"
            section["error"] = "Lina could not compare this product."
            session.add_event("Lina", "could not compare", section["name"], self._names(session))
            self._refresh_progress(session, running=True)
            return
        names = self._names(session)
        recommended = accept_supplier_ids(result.recommended_supplier_ids, valid)
        risks = accept_risks([row.model_dump() for row in result.risks], valid)
        for row in risks:
            row["risk"] = hide_ids(row["risk"], names)
        section["recommended_supplier_ids"] = recommended
        section["comparison_notes"] = hide_ids(_trim_words(result.comparison_notes, 80), names)
        section["risks"] = risks
        # Code owns this sentence. Lina's copy is discarded even when she wrote one.
        section["status"] = "ready"
        section["error"] = ""
        section["audit"] = await self._audit(payload, result)
        session.supplier_analysis[section["product_id"]] = {
            "recommended_supplier_ids": recommended,
            "comparison_notes": section["comparison_notes"],
            "risks": risks,
            "audit": section["audit"],
        }
        session.add_event("Lina", "compared suppliers for", section["name"], names)
        self._refresh_progress(session, running=True)

    async def _audit(self, payload: dict, result) -> dict | None:
        if not settings.enable_auditor:
            return None
        try:
            audit = await self.karim.run(
                {
                    "agent": "Lina",
                    "agent_output": result.model_dump(),
                    "offers": payload["offers"],
                }
            )
        except LLMError:
            return {"passed": None, "notes": 0, "skipped": True, "issues": []}
        issues = [{"where": row.where, "problem": row.problem} for row in audit.issues]
        return {
            "passed": bool(audit.passed) and not issues,
            "notes": len(issues),
            "skipped": False,
            "issues": issues,
        }

    def _refresh_progress(self, session: Session, running: bool) -> None:
        total = len(session.supplier_sections)
        completed = sum(1 for row in session.supplier_sections if row["status"] != "pending")
        progress = session.supplier_progress
        progress["completed"] = completed
        progress["total"] = total
        progress["running"] = running

    def _top_candidates(self, product_ids: list[str]) -> list[dict]:
        # Bounds come from the whole catalog. A shortlist must not change a score.
        all_rows = self.catalog.filter_products({})
        counts = {
            row["product_id"]: len(self.catalog.offers_for_product(row["product_id"]))
            for row in all_rows
        }
        scored = {row["product_id"]: row for row in score_catalog(all_rows, counts)}
        wanted = [scored[product_id] for product_id in product_ids if product_id in scored]
        wanted.sort(key=lambda row: (-row["opportunity_score"], row["product_id"]))
        chosen = []
        for score in wanted[:CANDIDATE_LIMIT]:
            product = self.catalog.get_product(score["product_id"])
            if product is None:
                continue
            chosen.append(_present(product, score))
        return chosen

    async def _rank(self, candidates: list[dict]) -> tuple[list[dict], str, bool]:
        fallback = code_rank(candidates)
        if not candidates:
            return [], "", True
        try:
            scout = await self.omar.run(scout_payload(candidates))
        except LLMError:
            return fallback, "", True
        valid = {str(row["product_id"]) for row in candidates}
        by_id = {str(row["product_id"]): row for row in candidates}
        accepted = accept_ranks([row.model_dump() for row in scout.ranked], valid)
        if not accepted:
            return fallback, scout.message, True
        for row in accepted:
            source = by_id[row["product_id"]]
            written = hide_ids(_trim_words(row.get("rationale") or ""), self._candidate_names(candidates))
            if written:
                row["rationale"] = written
                row["rationale_source"] = "omar"
            else:
                row["rationale"] = _code_rationale(source)
                row["rationale_source"] = "auto"
            row["watch_out"] = hide_ids(_trim_words(row.get("watch_out") or "", 30), self._candidate_names(candidates))
        return accepted, scout.message, False

    def _names(self, session: Session) -> dict[str, str]:
        names = {}
        for product_id, product in self.catalog._products.items():
            names[str(product_id)] = product["name"]
        for supplier_id, supplier in self.catalog._suppliers.items():
            names[str(supplier_id)] = supplier["name"]
        for row in session.candidates:
            names[str(row["product_id"])] = row.get("name") or names.get(str(row["product_id"]), "")
        for section in session.supplier_sections:
            names[str(section["product_id"])] = section.get("name") or names.get(str(section["product_id"]), "")
            for offer in section.get("offers") or []:
                names[str(offer["supplier_id"])] = offer.get("supplier_name") or names.get(
                    str(offer["supplier_id"]), ""
                )
        return {key: value for key, value in names.items() if value}

    def _candidate_names(self, candidates: list[dict]) -> dict[str, str]:
        names = {}
        for row in candidates:
            names[str(row["product_id"])] = row.get("name") or ""
        return {key: value for key, value in names.items() if value}


def keep_decisions(decisions: dict, remaining: set[str]) -> dict:
    wanted = {str(item) for item in remaining}
    return {
        str(product_id): decision
        for product_id, decision in decisions.items()
        if str(product_id) in wanted and decision in {"approved", "rejected"}
    }


def pin_approved(ranked: list[dict], candidates: list[dict], decisions: dict) -> list[dict]:
    """Keep an approved product on the list when Omar's new top 8 dropped it."""

    by_id = {str(row["product_id"]): row for row in candidates}
    seen = {str(row["product_id"]) for row in ranked}
    pinned = [dict(row, product_id=str(row["product_id"])) for row in ranked]
    for product_id, decision in decisions.items():
        product_id = str(product_id)
        if decision != "approved" or product_id in seen or product_id not in by_id:
            continue
        source = by_id[product_id]
        pinned.append(
            {
                "product_id": product_id,
                "rank": len(pinned) + 1,
                "rationale": _code_rationale(source),
                "rationale_source": "auto",
                "watch_out": source["risk_flags"][0]["reason"] if source.get("risk_flags") else "",
            }
        )
        seen.add(product_id)
    return pinned


def accept_ranks(ranked: list[dict], valid_ids: set[str], limit: int = RANK_LIMIT) -> list[dict]:
    """Keep Omar's order, drop ids the catalog did not give him, cap the list."""

    kept = []
    seen: set[str] = set()
    ordered = sorted(ranked, key=lambda row: row.get("rank") if isinstance(row.get("rank"), int) else 99)
    for row in ordered:
        product_id = str(row.get("product_id") or "")
        if product_id not in {str(item) for item in valid_ids} or product_id in seen:
            continue
        seen.add(product_id)
        kept.append(
            {
                "product_id": product_id,
                "rank": len(kept) + 1,
                "rationale": str(row.get("rationale") or ""),
                "rationale_source": "omar",
                "watch_out": str(row.get("watch_out") or ""),
            }
        )
        if len(kept) == limit:
            break
    return kept


def code_rank(candidates: list[dict], limit: int = RANK_LIMIT) -> list[dict]:
    rows = []
    for index, row in enumerate(candidates[:limit], start=1):
        watch = row["risk_flags"][0]["reason"] if row["risk_flags"] else ""
        rows.append(
            {
                "product_id": str(row["product_id"]),
                "rank": index,
                "rationale": _code_rationale(row),
                "rationale_source": "auto",
                "watch_out": watch,
            }
        )
    return rows


def guard_suppliers(session: Session) -> None:
    if session.stage not in {"PRODUCT_REVIEW", "SUPPLIER_REVIEW"}:
        raise FlowError(
            "Approve products before comparing suppliers.",
            "Send a brief and review the cards first.",
        )
    if not _approved_ids(session):
        raise FlowError(
            "Approve at least one product before continuing.",
            "Use Approve on a card, then try again.",
        )


def guard_final(session: Session) -> None:
    if session.stage != "SUPPLIER_REVIEW":
        raise FlowError("Pick suppliers before the final plan.", "Continue to suppliers first.")
    missing = []
    for section in session.supplier_sections:
        if not section["offers"]:
            continue
        if not section["selected_supplier_ids"]:
            missing.append(section["name"])
    if missing:
        raise FlowError(
            "Choose at least one supplier for every product.",
            "Still open: " + ", ".join(missing[:4]),
        )


def _approved_ids(session: Session) -> list[str]:
    decisions = {str(key): value for key, value in session.product_decisions.items()}
    return [
        str(row["product_id"])
        for row in session.ranked
        if decisions.get(str(row["product_id"])) == "approved"
    ]


def _section(session: Session, product_id: str) -> dict | None:
    for row in session.supplier_sections:
        if row["product_id"] == product_id:
            return row
    return None


def _present(product: dict, score: dict) -> dict:
    row = dict(score)
    row["name"] = product["name"]
    row["category"] = product["category"]
    row["subcategory"] = product["subcategory"]
    row["retail_price_usd"] = round(float(product["retail_price_usd"]), 2)
    row["market_wholesale_price_usd"] = round(float(product["market_wholesale_price_usd"]), 2)
    row["trend"] = product["trend"]
    row["return_rate_pct"] = float(product["return_rate_pct"])
    row["est_net_margin_pct"] = round(float(score["est_net_margin_pct"]), 1)
    row["demand_score"] = round(float(score["demand_score"]), 1)
    row["competition_pressure"] = round(float(score["competition_pressure"]), 1)
    row["opportunity_score"] = round(float(score["opportunity_score"]), 1)
    return _plain(row)


def _plain(value):
    # Pandas cells can be numpy scalars, which the JSON encoder will not take.
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes, dict, list)):
        try:
            return item()
        except (ValueError, AttributeError):
            return value
    return value


def _cards(session: Session) -> list[dict]:
    by_id = {str(row["product_id"]): row for row in session.candidates}
    decisions = {str(key): value for key, value in session.product_decisions.items()}
    cards = []
    for ranked in session.ranked:
        product_id = str(ranked["product_id"])
        base = by_id.get(product_id)
        if base is None:
            continue
        card = dict(base)
        card["product_id"] = product_id
        card["rank"] = ranked["rank"]
        card["rationale"] = ranked.get("rationale") or _code_rationale(base)
        card["rationale_source"] = ranked.get("rationale_source") or (
            "omar" if ranked.get("rationale") else "auto"
        )
        if not ranked.get("rationale"):
            card["rationale_source"] = "auto"
        card["watch_out"] = ranked.get("watch_out") or ""
        card["decision"] = decisions.get(product_id)
        card["risk_flags"] = [
            {"code": str(flag.get("code") or ""), "reason": str(flag.get("reason") or "")}
            for flag in (card.get("risk_flags") or [])
        ]
        cards.append(card)
    return cards


def _code_rationale(row: dict) -> str:
    return (
        f"Opportunity {row['opportunity_score']:.0f}. "
        f"Net margin {row['est_net_margin_pct']:.0f}%. "
        f"Demand {row['demand_score']:.0f}."
    )


def _trim_words(text: str, limit: int = 40) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]).rstrip(".,;") + "…"


def _maya_action(status: str) -> str:
    if status == "clarify":
        return "Asked a question"
    if status == "out_of_catalog":
        return "Reported an item outside the catalog"
    return "Prepared filters"


def _name_for(session: Session, product_id: str) -> str:
    product_id = str(product_id)
    for row in session.candidates:
        if str(row["product_id"]) == product_id:
            return row["name"]
    for row in session.ranked:
        if str(row["product_id"]) == product_id and row.get("name"):
            return row["name"]
    return product_id


def _supplier_names(section: dict, supplier_ids: list[str]) -> str:
    by_id = {row["supplier_id"]: row["supplier_name"] for row in section["offers"]}
    return ", ".join(by_id[item] for item in supplier_ids if item in by_id)


def _stage_label(stage: str) -> str:
    return {
        "BRIEF": "Brief",
        "PRODUCT_REVIEW": "Products",
        "SUPPLIER_REVIEW": "Suppliers",
        "FINAL_REVIEW": "Plan",
    }.get(stage, stage)


def _reachable(session: Session) -> list[str]:
    stages = ["BRIEF"]
    if session.ranked:
        stages.append("PRODUCT_REVIEW")
    if session.supplier_sections:
        stages.append("SUPPLIER_REVIEW")
    if session.plan and session.plan_key == plan_key(session.supplier_sections):
        stages.append("FINAL_REVIEW")
    return stages
