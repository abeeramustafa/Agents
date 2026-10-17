"""Hand-checked scores. Expected numbers are written out, not copied from the function."""

import math
from pathlib import Path

import pytest

from app.data_store import DataStore
from app.scoring import net_margin_pct, score_catalog, score_offers, score_product

ROOT = Path(__file__).resolve().parent.parent


def test_net_margin_on_a_20_dollar_product():
    # (20 - 8 - 20 * 0.40) / 20 * 100 = 4 / 20 * 100 = 20
    assert net_margin_pct(20, 8, 0.40) == 20
    # (19.99 - 6.50 - 7.996) / 19.99 * 100 = 5.494 / 19.99 * 100
    assert net_margin_pct(19.99, 6.50, 0.40) == pytest.approx(27.4837, abs=1e-3)


def test_product_score_breaks_down_to_the_formula():
    # Catalog bounds: reviews 100..10000, competitors 10..1000.
    # This product is the low end of both, so both log scales are 0.
    # Rating 4.0 -> (4.0 - 3.5) / 1.5 * 100 = 33.333...
    # Demand = 0.5 * 60 + 0.2 * 0 + 0.2 * 33.333... + 0 = 36.666...
    # Margin 20% -> margin_scaled 50.
    # Flags: returns 9, one offer, note says battery. Penalty 15.
    # Opportunity = 0.40 * 36.666... + 0.35 * 50 + 0.25 * 100 - 15 = 42.1666...
    product = _product(
        review_count=100,
        competitor_count=10,
        return_rate_pct=9,
        notes="Contains a battery",
        trend="stable",
        monthly_search_index=60,
        avg_rating=4.0,
    )
    scored = score_product(
        product,
        offer_count=1,
        review_low=100,
        review_high=10000,
        competitor_low=10,
        competitor_high=1000,
        cost_allowance=0.40,
    )

    assert scored["reviews_scaled"] == 0
    assert scored["competition_pressure"] == 0
    assert scored["rating_scaled"] == pytest.approx(100 / 3)
    assert scored["trend_bonus"] == 0
    assert scored["demand_score"] == pytest.approx(36.6666667)
    assert scored["margin_scaled"] == 50
    assert scored["risk_penalty"] == 15
    assert scored["opportunity_score"] == pytest.approx(42.1666667)
    assert [flag["code"] for flag in scored["risk_flags"]] == [
        "high_returns",
        "limited_supply",
        "compliance_note",
    ]
    # The log span is an independent check, not a call into scoring.
    span = math.log1p(10000) - math.log1p(100)
    assert span > 0


def test_demand_and_margin_clip_and_the_penalty_caps_at_20():
    weak = _product(
        monthly_search_index=0,
        review_count=0,
        avg_rating=3.5,
        trend="declining",
        competitor_count=250,
        return_rate_pct=12,
        retail_price_usd=10,
        market_wholesale_price_usd=8,
        notes="Needs a safety certification",
    )
    # Reviews sit at the catalog floor, rating at 3.5, search at 0.
    # Demand raw = 0 + 0 + 0 - 10 = -10, clipped to 0.
    # Margin = (10 - 8 - 4) / 10 * 100 = -20, so margin_scaled is 0 and thin_margin fires.
    scored = score_product(
        weak,
        offer_count=0,
        review_low=0,
        review_high=1000,
        competitor_low=250,
        competitor_high=250,
        cost_allowance=0.40,
    )
    assert scored["demand_score"] == 0
    assert scored["reviews_scaled"] == 0
    assert scored["competition_pressure"] == 50
    assert scored["est_net_margin_pct"] == -20
    assert scored["margin_scaled"] == 0
    assert len(scored["risk_flags"]) == 6
    assert scored["risk_penalty"] == 20
    assert 0 <= scored["opportunity_score"] <= 100


def test_margin_above_40_percent_saturates_the_margin_bar():
    # (50 - 5 - 20) / 50 * 100 = 50% net, which is past the 40% ceiling.
    rich = _product(retail_price_usd=50, market_wholesale_price_usd=5)
    scored = score_product(
        rich,
        offer_count=3,
        review_low=100,
        review_high=100,
        competitor_low=10,
        competitor_high=10,
        cost_allowance=0.40,
    )
    assert scored["est_net_margin_pct"] == 50
    assert scored["margin_scaled"] == 100
    assert scored["risk_flags"] == []


def test_supplier_scores_match_the_weighted_sum():
    offers = score_offers(
        [
            {
                "offer_id": "O1",
                "supplier_id": "S1",
                "product_id": "P1",
                "unit_price_usd": 10,  # cheapest -> 100
                "lead_time_days": 20,  # slowest -> 0
                "stock_status": "in_stock",
                "reliability_score": 8,  # 80
                "certifications": ["CE", "FCC"],
                "typical_reply_hours": 12,  # (1 - 12/24) * 100 = 50
                "payment_terms": "30% deposit, 70% before shipment",  # 70
                "years_in_business": 5,
            },
            {
                "offer_id": "O2",
                "supplier_id": "S2",
                "product_id": "P1",
                "unit_price_usd": 20,  # 0
                "lead_time_days": 10,  # 100
                "stock_status": "made_to_order",
                "reliability_score": 5,  # 50
                "certifications": "None listed",
                "typical_reply_hours": 0,  # 100
                "payment_terms": "100% upfront",  # 0
                "years_in_business": 2,
            },
        ]
    )
    first, second = offers
    # 0.35*80 + 0.25*100 + 0.15*0 + 0.10*100 + 0.05*50 + 0.10*70
    # = 28 + 25 + 0 + 10 + 2.5 + 7 = 72.5
    assert first["supplier_score"] == 72.5
    assert first["landed_unit_cost_est"] == 10
    assert first["risk_flags"] == []
    assert "not modeled" in first["landed_cost_note"]

    # 0.35*50 + 0.25*0 + 0.15*100 + 0.10*0 + 0.05*100 + 0.10*0 = 37.5
    assert second["supplier_score"] == 37.5
    assert [flag["code"] for flag in second["risk_flags"]] == [
        "low_reliability",
        "no_certifications",
        "upfront_payment_100",
        "new_company",
        "made_to_order_delay",
    ]


def test_a_single_offer_is_the_best_price_and_lead_time():
    scored = score_offers(
        [
            {
                "offer_id": "O1",
                "supplier_id": "S1",
                "unit_price_usd": 9,
                "lead_time_days": 30,
                "stock_status": "in_stock",
                "reliability_score": 10,
                "certifications": "CE",
                "typical_reply_hours": 0,
                "payment_terms": "Net 30 for repeat customers",
                "years_in_business": 4,
            }
        ]
    )
    assert scored[0]["price_score"] == 100
    assert scored[0]["lead_time_score"] == 100
    assert scored[0]["payment_safety_score"] == 100
    assert scored[0]["supplier_score"] == 100


def test_catalog_bounds_come_from_the_whole_list():
    low = _product(product_id="A", review_count=100, competitor_count=10)
    high = _product(product_id="B", review_count=10000, competitor_count=1000)
    scored = {row["product_id"]: row for row in score_catalog([low, high], {"A": 2, "B": 2})}
    assert scored["A"]["reviews_scaled"] == 0
    assert scored["B"]["reviews_scaled"] == 100
    assert scored["A"]["competition_pressure"] == 0
    assert scored["B"]["competition_pressure"] == 100


def test_real_catalog_scores_stay_on_the_scale():
    store = DataStore(ROOT / "data")
    products = store.filter_products({})
    counts = {row["product_id"]: len(store.offers_for_product(row["product_id"])) for row in products}
    scored = score_catalog(products, counts, cost_allowance=0.40)
    assert len(scored) == 63
    for row in scored:
        assert 0 <= row["demand_score"] <= 100
        assert 0 <= row["competition_pressure"] <= 100
        assert 0 <= row["opportunity_score"] <= 100
        assert row["risk_penalty"] <= 20
    earbuds = next(row for row in scored if row["product_id"] == "P001")
    assert earbuds["est_net_margin_pct"] == pytest.approx(27.4837, abs=1e-3)


def _product(**overrides) -> dict:
    row = {
        "product_id": "P1",
        "name": "Sample",
        "category": "Audio",
        "subcategory": "True Wireless Earbuds",
        "market_wholesale_price_usd": 8,
        "retail_price_usd": 20,
        "min_order_qty": 10,
        "monthly_search_index": 60,
        "avg_rating": 4.0,
        "review_count": 100,
        "competitor_count": 10,
        "trend": "stable",
        "return_rate_pct": 4,
        "notes": "",
    }
    row.update(overrides)
    return row
