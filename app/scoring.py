"""Deterministic scores for products and supplier offers.

Every number the UI shows comes from here. The model may explain these
figures later. It must not recompute them. Each result includes the
parts, because the product card has a "Why this score?" panel.
"""

from __future__ import annotations

import math
import re

from app.config import settings

# Opportunity loses 5 points per risk flag, and never more than 4 flags' worth.
RISK_PENALTY_EACH = 5
RISK_PENALTY_CAP = 20

# A reply that takes a full day scores 0. Faster replies scale up to 100.
REPLY_HOURS_AT_ZERO = 24

# Net margin of 0% scores 0, 40% or more scores 100.
MARGIN_AT_FULL_SCORE = 40

# Ratings below 3.5 score 0. A 5.0 scores 100.
RATING_LOW = 3.5
RATING_HIGH = 5.0


def net_margin_pct(retail: float, wholesale: float, cost_allowance: float) -> float:
    """Estimated net margin as a percent of retail.

    cost_allowance stands in for shipping, duties, marketplace fees, ads,
    and returns. Those costs are not in the CSVs, so they are one share of
    the retail price rather than a fake shipping quote.
    """

    retail = float(retail)
    if retail <= 0:
        raise ValueError("retail price must be greater than 0")
    return (retail - float(wholesale) - retail * float(cost_allowance)) / retail * 100


def score_product(
    product: dict,
    *,
    offer_count: int,
    review_low: float,
    review_high: float,
    competitor_low: float,
    competitor_high: float,
    cost_allowance: float | None = None,
) -> dict:
    """Score one product. Review and competitor bounds are the whole catalog.

    Pass the catalog min and max, not the filtered shortlist. Otherwise a
    product's score would change just because the user narrowed the search.
    """

    allowance = settings.cost_allowance if cost_allowance is None else float(cost_allowance)
    margin = net_margin_pct(
        product["retail_price_usd"],
        product["market_wholesale_price_usd"],
        allowance,
    )
    margin_scaled = _clip(margin / MARGIN_AT_FULL_SCORE * 100)

    search_index = float(product["monthly_search_index"])
    reviews_scaled = _log_scale(float(product["review_count"]), review_low, review_high)
    rating_scaled = _clip(
        (float(product["avg_rating"]) - RATING_LOW) / (RATING_HIGH - RATING_LOW) * 100
    )
    trend_bonus = _trend_bonus(product.get("trend"))
    # Search is already 0-100, so it is half the demand score. The other
    # half is reviews, rating, and a small trend nudge. Clip because a
    # declining product with weak demand would otherwise go below 0.
    demand_raw = (
        0.5 * search_index
        + 0.2 * reviews_scaled
        + 0.2 * rating_scaled
        + trend_bonus
    )
    demand = _clip(demand_raw)
    competition = _log_scale(
        float(product["competitor_count"]),
        competitor_low,
        competitor_high,
    )
    flags = _product_flags(product, margin, offer_count)
    penalty = min(RISK_PENALTY_CAP, RISK_PENALTY_EACH * len(flags))
    demand_points = 0.40 * demand
    margin_points = 0.35 * margin_scaled
    competition_points = 0.25 * (100 - competition)
    opportunity = _clip(demand_points + margin_points + competition_points - penalty)

    return {
        "product_id": product["product_id"],
        "cost_allowance": allowance,
        "est_net_margin_pct": margin,
        "margin_scaled": margin_scaled,
        "demand_score": demand,
        "search_index": search_index,
        "reviews_scaled": reviews_scaled,
        "rating_scaled": rating_scaled,
        "trend_bonus": trend_bonus,
        "competition_pressure": competition,
        "demand_points": demand_points,
        "margin_points": margin_points,
        "competition_points": competition_points,
        "risk_penalty": penalty,
        "opportunity_score": opportunity,
        "risk_flags": flags,
        "offer_count": int(offer_count),
    }


def score_catalog(
    products: list[dict],
    offer_counts: dict[str, int] | None = None,
    cost_allowance: float | None = None,
) -> list[dict]:
    """Score every product using one set of catalog bounds."""

    if not products:
        return []
    counts = offer_counts or {}
    review_low = min(float(row["review_count"]) for row in products)
    review_high = max(float(row["review_count"]) for row in products)
    competitor_low = min(float(row["competitor_count"]) for row in products)
    competitor_high = max(float(row["competitor_count"]) for row in products)
    return [
        score_product(
            row,
            offer_count=counts.get(row["product_id"], 0),
            review_low=review_low,
            review_high=review_high,
            competitor_low=competitor_low,
            competitor_high=competitor_high,
            cost_allowance=cost_allowance,
        )
        for row in products
    ]


def score_offers(offers: list[dict]) -> list[dict]:
    """Compare offers for a single product.

    Price and lead time are relative to the best offer in this list: the
    cheapest price and the shortest lead time score 100. A one-offer list
    is the best by definition, so both of those parts are 100.
    """

    if not offers:
        return []
    prices = [float(row["unit_price_usd"]) for row in offers]
    leads = [float(row["lead_time_days"]) for row in offers]
    best_price, worst_price = min(prices), max(prices)
    best_lead, worst_lead = min(leads), max(leads)
    scored = []
    for row in offers:
        reliability = _clip(float(row["reliability_score"]) / 10 * 100)
        price_score = _best_low_score(float(row["unit_price_usd"]), best_price, worst_price)
        lead_score = _best_low_score(float(row["lead_time_days"]), best_lead, worst_lead)
        certification = _certification_score(row.get("certifications"))
        responsiveness = _responsiveness_score(float(row["typical_reply_hours"]))
        payment = _payment_safety_score(str(row.get("payment_terms", "")))
        # Weights sum to 1, so the parts stay on a 0-100 scale.
        reliability_points = 0.35 * reliability
        price_points = 0.25 * price_score
        lead_points = 0.15 * lead_score
        certification_points = 0.10 * certification
        responsiveness_points = 0.05 * responsiveness
        payment_points = 0.10 * payment
        total = _clip(
            reliability_points
            + price_points
            + lead_points
            + certification_points
            + responsiveness_points
            + payment_points
        )
        unit_price = float(row["unit_price_usd"])
        scored.append(
            {
                "offer_id": row.get("offer_id"),
                "supplier_id": row.get("supplier_id"),
                "product_id": row.get("product_id"),
                "supplier_score": total,
                "reliability_100": reliability,
                "price_score": price_score,
                "lead_time_score": lead_score,
                "certification_score": certification,
                "responsiveness_score": responsiveness,
                "payment_safety_score": payment,
                "reliability_points": reliability_points,
                "price_points": price_points,
                "lead_time_points": lead_points,
                "certification_points": certification_points,
                "responsiveness_points": responsiveness_points,
                "payment_points": payment_points,
                # The CSV has no shipping column. Do not invent one.
                "landed_unit_cost_est": unit_price,
                "landed_cost_note": "Shipping is not modeled. This estimate is the offer unit price.",
                "risk_flags": _supplier_flags(row),
            }
        )
    return scored


def _product_flags(product: dict, margin: float, offer_count: int) -> list[dict]:
    flags = []
    returns = float(product["return_rate_pct"])
    competitors = float(product["competitor_count"])
    if returns >= 9:
        flags.append(_flag("high_returns", f"Return rate is {returns:g}%, at or above 9%."))
    if competitors >= 250:
        flags.append(
            _flag("saturated", f"{competitors:g} competitors, at or above 250.")
        )
    if str(product.get("trend", "")).strip().casefold() == "declining":
        flags.append(_flag("declining_trend", "Catalog trend is declining."))
    if margin < 15:
        flags.append(_flag("thin_margin", f"Estimated net margin is {margin:.1f}%, under 15%."))
    if offer_count < 2:
        flags.append(
            _flag("limited_supply", f"Only {offer_count} offer(s). Fewer than 2 suppliers.")
        )
    notes = str(product.get("notes") or "")
    if _mentions_compliance(notes):
        flags.append(
            _flag(
                "compliance_note",
                "Notes mention certification, battery, or safety.",
            )
        )
    return flags


def _supplier_flags(offer: dict) -> list[dict]:
    flags = []
    reliability = float(offer["reliability_score"])
    years = float(offer["years_in_business"])
    terms = str(offer.get("payment_terms", ""))
    if reliability < 6:
        flags.append(_flag("low_reliability", f"Reliability is {reliability:g}, below 6."))
    if not _real_certifications(offer.get("certifications")):
        flags.append(_flag("no_certifications", "No certifications are listed."))
    if "100% upfront" in terms.casefold():
        flags.append(_flag("upfront_payment_100", "Payment terms say 100% upfront."))
    if years < 3:
        flags.append(_flag("new_company", f"In business {years:g} years, under 3."))
    if str(offer.get("stock_status", "")).strip().casefold() == "made_to_order":
        flags.append(
            _flag("made_to_order_delay", "Made to order, so lead time includes production.")
        )
    return flags


def _mentions_compliance(notes: str) -> bool:
    text = notes.casefold()
    return any(word in text for word in ("certification", "battery", "safety"))


def _trend_bonus(trend) -> float:
    name = str(trend or "").strip().casefold()
    if name == "rising":
        return 10
    if name == "declining":
        return -10
    # "stable" and anything unexpected add nothing.
    return 0


def _log_scale(value: float, low: float, high: float) -> float:
    """Map a catalog value onto 0-100 using log(1 + x).

    Log keeps a jump from 10 to 100 competitors meaningful without letting
    a jump from 100 to 1000 dominate. If the catalog has no spread, every
    row sits at 50 so the term does not invent a winner.
    """

    if high == low:
        return 50.0
    start = math.log1p(low)
    span = math.log1p(high) - start
    return _clip((math.log1p(value) - start) / span * 100)


def _best_low_score(value: float, best: float, worst: float) -> float:
    """100 when value equals the lowest (best) offer, 0 at the highest."""

    if worst == best:
        return 100.0
    return _clip((worst - value) / (worst - best) * 100)


def _certification_score(certifications) -> float:
    # One listed certification is enough for full points. The catalog does
    # not say that UL is worth more than CE, so this is not a count.
    return 100.0 if _real_certifications(certifications) else 0.0


def _real_certifications(certifications) -> list[str]:
    if certifications is None:
        return []
    if isinstance(certifications, str):
        parts = certifications.split(";")
    else:
        parts = list(certifications)
    cleaned = []
    for part in parts:
        text = str(part).strip()
        if text and text.casefold() != "none listed":
            cleaned.append(text)
    return cleaned


def _responsiveness_score(reply_hours: float) -> float:
    return _clip((1 - float(reply_hours) / REPLY_HOURS_AT_ZERO) * 100)


def _payment_safety_score(terms: str) -> float:
    """How little cash is at risk before the goods move.

    0   "100% upfront"
    40  half or more due before the balance (50% deposit / advance)
    70  a smaller deposit (the 30% and 40% terms in this catalog)
    100 net terms, credit, or no upfront percent at all
    """

    text = terms.casefold()
    if "100% upfront" in text:
        return 0.0
    if "net 30" in text or "30-day credit" in text or "30 day credit" in text:
        return 100.0
    percents = [float(match) for match in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]
    if not percents:
        return 100.0
    if percents[0] >= 50:
        return 40.0
    return 70.0


def _clip(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, float(value)))


def _flag(code: str, reason: str) -> dict:
    return {"code": code, "reason": reason}
