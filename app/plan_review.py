"""Turn Sana's JSON into a plan that only names suppliers the human selected.

Code fills any product she skipped, and the risk checklist always includes
the flags already computed for those selected offers.
"""

from __future__ import annotations

import re

_SUPPLIER_ID = re.compile(r"\bS\d+\b")
_DONE_CLAIM = re.compile(r"\b(emailed|email sent|i sent|ordered|purchased|paid)\b", re.I)

OUTREACH_STEP = "Draft outreach to the selected suppliers. Sending email is not built yet."


def plan_key(sections: list[dict]) -> str:
    """Fingerprint of the picks. A later edit means the saved plan is stale."""

    parts = []
    for section in sections:
        ids = ",".join(section.get("selected_supplier_ids") or [])
        parts.append(f"{section.get('product_id')}:{ids}")
    return "|".join(parts)


def sana_payload(sections: list[dict], products: dict[str, dict] | None = None) -> dict:
    """Only the selected offers. Unselected suppliers are not in her DATA block."""

    rows = []
    catalog = products or {}
    for section in sections:
        chosen = list(section.get("selected_supplier_ids") or [])
        if not chosen:
            continue
        chosen_set = set(chosen)
        product = catalog.get(section["product_id"], {})
        row = {
            "product_id": section["product_id"],
            "name": section["name"],
            "category": section["category"],
            "limited_supply": bool(section.get("limited_supply")),
            "cheapest_supplier_warning": section.get("cheapest_supplier_warning") or "",
            "selected_offers": [
                {
                    "supplier_id": offer["supplier_id"],
                    "supplier_name": offer["supplier_name"],
                    "unit_price_usd": offer["unit_price_usd"],
                    "min_order_qty": offer["min_order_qty"],
                    "lead_time_days": offer["lead_time_days"],
                    "reliability_score": offer["reliability_score"],
                    "certifications": offer["certifications"],
                    "payment_terms": offer["payment_terms"],
                    "supplier_score": offer["supplier_score"],
                    "risk_flags": [flag.get("reason") or flag.get("code") for flag in offer.get("risk_flags") or []],
                }
                for offer in section["offers"]
                if offer["supplier_id"] in chosen_set
            ],
        }
        if product.get("retail_price_usd") is not None:
            row["retail_price_usd"] = product["retail_price_usd"]
        if product.get("market_wholesale_price_usd") is not None:
            row["market_wholesale_price_usd"] = product["market_wholesale_price_usd"]
        rows.append(row)
    return {
        "products": rows,
        "note": "Drafting outreach is not built. next_steps may only tell the founder to draft outreach later.",
    }


def accept_plan(result, sections: list[dict]) -> dict:
    """Keep her order of ideas, but the product list follows the human's selections."""

    chosen_sections = [section for section in sections if section.get("selected_supplier_ids")]
    allowed = {
        supplier_id
        for section in chosen_sections
        for supplier_id in section["selected_supplier_ids"]
    }
    by_product = {}
    for item in result.plan:
        product_id = str(item.product_id or "").strip()
        if product_id and product_id not in by_product:
            by_product[product_id] = item

    plan = []
    for section in chosen_sections:
        selected = list(section["selected_supplier_ids"])
        item = by_product.get(section["product_id"])
        # The human's checkboxes are the list. Sana does not get to drop a pick.
        supplier_ids = selected
        why = ""
        if item is not None:
            why = _trim_words(item.why, 40)
            if why and not _text_ok(why, allowed):
                why = ""
        if not why:
            why = _code_why(section, supplier_ids)
        plan.append(
            {
                "product_id": section["product_id"],
                "name": section["name"],
                "category": section.get("category") or "",
                "limited_supply": bool(section.get("limited_supply")),
                "supplier_ids": supplier_ids,
                "suppliers": _supplier_rows(section, supplier_ids),
                "why": why,
            }
        )

    risks = _code_risks(chosen_sections)
    for text in result.risks_to_check:
        line = _trim_words(str(text or ""), 40)
        if line and _text_ok(line, allowed) and line not in risks:
            risks.append(line)
    if not risks:
        risks.append("No risk flags on the selected suppliers.")

    steps = []
    for text in result.next_steps:
        line = _trim_words(str(text or ""), 30)
        if not line or not _text_ok(line, allowed) or _DONE_CLAIM.search(line):
            continue
        if line not in steps:
            steps.append(line)
        if len(steps) == 4:
            break
    if not steps:
        steps.append(OUTREACH_STEP)

    headline = _trim_words(str(result.headline or ""), 30)
    if not headline or not _text_ok(headline, allowed):
        count = len(plan)
        headline = f"Sourcing plan for {count} product{'' if count == 1 else 's'}."
    message = _trim_words(str(result.message or ""), 40)
    if not message or not _text_ok(message, allowed):
        message = headline
    return {
        "headline": headline,
        "plan": plan,
        "risks_to_check": risks[:12],
        "next_steps": steps,
        "message": message,
    }


def _supplier_rows(section: dict, supplier_ids: list[str]) -> list[dict]:
    by_id = {row["supplier_id"]: row for row in section["offers"]}
    rows = []
    for supplier_id in supplier_ids:
        offer = by_id.get(supplier_id)
        if offer is None:
            continue
        rows.append(
            {
                "supplier_id": supplier_id,
                "supplier_name": offer["supplier_name"],
                "unit_price_usd": offer["unit_price_usd"],
                "supplier_score": offer["supplier_score"],
            }
        )
    return rows


def _code_why(section: dict, supplier_ids: list[str]) -> str:
    by_id = {row["supplier_id"]: row for row in section["offers"]}
    bits = []
    for supplier_id in supplier_ids:
        offer = by_id.get(supplier_id)
        if offer is None:
            continue
        parts = [f"{offer['supplier_name']} at ${float(offer['unit_price_usd']):.2f}"]
        if offer.get("supplier_score") is not None:
            parts.append(f"score {offer['supplier_score']}")
        if offer.get("reliability_score") not in (None, ""):
            parts.append(f"reliability {offer['reliability_score']}")
        if offer.get("lead_time_days") not in (None, ""):
            parts.append(f"lead {offer['lead_time_days']} days")
        bits.append(", ".join(parts))
    if not bits:
        return f"You selected a supplier for {section['name']} from this product's offers."
    return _trim_words(f"{section['name']}: " + "; ".join(bits) + ".", 60)


def despite_line(product_name: str, supplier_name: str, flags: list[dict]) -> str:
    reasons = []
    for flag in flags or []:
        reason = str(flag.get("reason") or flag.get("code") or "").replace("_", " ").strip()
        if reason and reason not in reasons:
            reasons.append(reason)
    if not reasons:
        return ""
    return f"{product_name}: You chose {supplier_name} despite: " + "; ".join(reasons)


def _code_risks(sections: list[dict]) -> list[str]:
    risks = []
    for section in sections:
        chosen = set(section.get("selected_supplier_ids") or [])
        for offer in section["offers"]:
            if offer["supplier_id"] not in chosen:
                continue
            line = despite_line(section["name"], offer["supplier_name"], offer.get("risk_flags") or [])
            if line and line not in risks:
                risks.append(line)
    return risks


def _text_ok(text: str, allowed: set[str]) -> bool:
    return set(_SUPPLIER_ID.findall(text)) <= allowed


def _trim_words(text: str, limit: int) -> str:
    words = str(text or "").split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip(".,;") + "…"
