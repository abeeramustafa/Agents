"""Build supplier sections from the catalog, then keep Lina inside that list.

Scores and the cheapest-supplier warning come from code. Lina only chooses
among supplier ids that already have an offer for the product.
"""

from __future__ import annotations

import asyncio

from app.data_store import DataStore
from app.llm_client import LLMError
from app.scoring import score_offers

# These three make a low price a bad default. Other flags stay on the row.
CHEAP_RISKS = {
    "low_reliability": "low reliability",
    "no_certifications": "no certifications",
    "upfront_payment_100": "100% upfront",
}

CONCURRENCY = 3
RATE_LIMIT_BACKOFF_SECONDS = 2.0
RATE_LIMIT_ATTEMPTS = 3


def prepare_product(catalog: DataStore, product_id: str) -> dict | None:
    product = catalog.get_product(product_id)
    if product is None:
        return None
    joined = []
    for offer in catalog.offers_for_product(product_id):
        supplier = catalog.get_supplier(offer["supplier_id"])
        if supplier is None:
            continue
        certifications = supplier.get("certifications") or []
        joined.append(
            {
                **offer,
                "supplier_name": supplier["name"],
                "country": supplier["country"],
                "reliability_score": supplier["reliability_score"],
                "certifications": certifications,
                "payment_terms": supplier["payment_terms"],
                "typical_reply_hours": supplier["typical_reply_hours"],
                "years_in_business": supplier["years_in_business"],
            }
        )
    scored = {row["offer_id"]: row for row in score_offers(joined)}
    offers = []
    for row in joined:
        score = scored[row["offer_id"]]
        offers.append(
            {
                "offer_id": row["offer_id"],
                "supplier_id": row["supplier_id"],
                "supplier_name": row["supplier_name"],
                "country": row["country"],
                "unit_price_usd": round(float(row["unit_price_usd"]), 2),
                "min_order_qty": int(float(row["min_order_qty"])),
                "lead_time_days": int(float(row["lead_time_days"])),
                "reliability_score": float(row["reliability_score"]),
                "certifications": ", ".join(row["certifications"]) or "None listed",
                "payment_terms": row["payment_terms"],
                "stock_status": row["stock_status"],
                "supplier_score": round(float(score["supplier_score"]), 1),
                "risk_flags": score["risk_flags"],
                "landed_unit_cost_est": round(float(score["landed_unit_cost_est"]), 2),
                "landed_cost_note": score["landed_cost_note"],
            }
        )
    # Score first so the recommended row is easy to find. Checkboxes stay empty.
    offers.sort(key=lambda row: (-row["supplier_score"], row["unit_price_usd"], row["supplier_id"]))
    single = len(offers) < 2
    return {
        "product_id": product_id,
        "name": product["name"],
        "category": product["category"],
        "limited_supply": single,
        "status": "ready" if single else "pending",
        "error": "",
        "offers": offers,
        "recommended_supplier_ids": [],
        "comparison_notes": (
            "Only one offer is in the catalog, so there is no comparison."
            if single
            else ""
        ),
        "risks": [],
        "cheapest_supplier_warning": cheapest_supplier_warning(offers),
        "audit": None,
        "selected_supplier_ids": [],
    }


def cheapest_supplier_warning(offers: list[dict]) -> str:
    """Warn when the lowest unit price carries a reliability, certification, or upfront-payment flag."""

    if not offers:
        return ""
    cheapest = min(offers, key=lambda row: (float(row["unit_price_usd"]), row["supplier_id"]))
    hits = []
    for flag in cheapest.get("risk_flags") or []:
        label = CHEAP_RISKS.get(flag.get("code"))
        if label and label not in hits:
            hits.append(label)
    if not hits:
        return ""
    return (
        f"{cheapest['supplier_name']} is the cheapest offer "
        f"at ${float(cheapest['unit_price_usd']):.2f}, but it has {', '.join(hits)}. "
        "Shipping is not included in this price."
    )


def accept_supplier_ids(recommended: list[str], valid_ids: set[str], limit: int = 3) -> list[str]:
    """Keep Lina's order. Drop blanks, duplicates, and ids that have no offer."""

    kept = []
    for supplier_id in recommended:
        supplier_id = str(supplier_id or "").strip()
        if supplier_id not in valid_ids or supplier_id in kept:
            continue
        kept.append(supplier_id)
        if len(kept) == limit:
            break
    return kept


def accept_risks(risks: list[dict], valid_ids: set[str]) -> list[dict]:
    kept = []
    seen = set()
    for row in risks:
        supplier_id = str(row.get("supplier_id") or "").strip()
        if supplier_id not in valid_ids or supplier_id in seen:
            continue
        seen.add(supplier_id)
        kept.append({"supplier_id": supplier_id, "risk": str(row.get("risk") or "")})
    return kept


def lina_payload(section: dict) -> dict:
    """Facts Lina may cite. The warning is left empty because code writes it."""

    return {
        "product": {
            "product_id": section["product_id"],
            "name": section["name"],
            "category": section["category"],
        },
        "offers": [
            {
                "supplier_id": row["supplier_id"],
                "supplier_name": row["supplier_name"],
                "unit_price_usd": row["unit_price_usd"],
                "min_order_qty": row["min_order_qty"],
                "lead_time_days": row["lead_time_days"],
                "reliability_score": row["reliability_score"],
                "certifications": row["certifications"],
                "payment_terms": row["payment_terms"],
                "supplier_score": row["supplier_score"],
                "risk_flags": [flag["code"] for flag in row["risk_flags"]],
                "landed_unit_cost_est": row["landed_unit_cost_est"],
            }
            for row in section["offers"]
        ],
    }


def chunks(items: list, size: int) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


async def run_with_rate_backoff(call, sleep=asyncio.sleep):
    """Retry a rate-limited call a couple of times. Other failures surface immediately."""

    last_error = None
    for attempt in range(RATE_LIMIT_ATTEMPTS):
        try:
            return await call()
        except LLMError as exc:
            last_error = exc
            if not getattr(exc, "rate_limited", False) or attempt == RATE_LIMIT_ATTEMPTS - 1:
                raise
            await sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
    raise last_error
