"""Catalog loading and filtering. The CSVs in data/ are the real fixture."""

from pathlib import Path

import pandas as pd
import pytest

from app.data_store import CatalogError, DataStore, net_margin_pct

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def test_real_catalog_loads_and_links():
    store = DataStore(DATA)
    meta = store.catalog_meta()
    assert meta["product_count"] == 63
    assert meta["supplier_count"] == 24
    assert meta["offer_count"] == 321
    assert meta["retail_price_min"] == 5.99
    assert meta["retail_price_max"] == 139.99
    assert "Audio" in meta["categories"]
    assert "True Wireless Earbuds" in meta["subcategories"]

    product = store.get_product("P001")
    assert product["name"] == "True Wireless Earbuds Basic"
    assert store.get_product("nope") is None

    supplier = store.get_supplier("S01")
    assert supplier["categories"] == ["Audio", "Wearables"]
    assert store.get_supplier("nope") is None

    offers = store.offers_for_product("P001")
    assert offers
    assert {offer["product_id"] for offer in offers} == {"P001"}
    assert store.offers_for_product("missing") == []


def test_filter_matches_price_category_returns_and_keywords():
    store = DataStore(DATA)
    cheap_audio = store.filter_products(
        {"categories": ["audio"], "max_retail_price": 20, "keywords": ["speaker"]}
    )
    ids = [row["product_id"] for row in cheap_audio]
    assert "P003" in ids
    assert "P002" not in ids

    low_returns = store.filter_products({"max_return_rate_pct": 6.5, "categories": ["Audio"]})
    assert "P003" in {row["product_id"] for row in low_returns}
    assert "P001" not in {row["product_id"] for row in low_returns}

    # A typo is not a substring. The catalog gate, not this filter, handles that.
    assert store.filter_products({"keywords": ["earfones"]}) == []


def test_margin_filter_uses_the_cost_allowance_formula():
    # P001: retail 19.99, wholesale 6.50, allowance 0.40 -> 27.48% net.
    assert net_margin_pct(19.99, 6.50, 0.40) == pytest.approx(27.4837, abs=1e-3)
    store = DataStore(DATA)
    kept = store.filter_products(
        {"categories": ["Audio"], "subcategories": ["True Wireless Earbuds"], "min_net_margin_pct": 27},
        cost_allowance=0.40,
    )
    assert "P001" in {row["product_id"] for row in kept}
    dropped = store.filter_products(
        {"categories": ["Audio"], "subcategories": ["True Wireless Earbuds"], "min_net_margin_pct": 30},
        cost_allowance=0.40,
    )
    assert "P001" not in {row["product_id"] for row in dropped}


def test_empty_filters_return_the_whole_catalog_sorted_by_demand():
    store = DataStore(DATA)
    rows = store.filter_products({})
    assert len(rows) == 63
    searches = [row["monthly_search_index"] for row in rows]
    assert searches == sorted(searches, reverse=True)


def test_missing_column_names_the_file(tmp_path: Path):
    _write_valid_catalog(tmp_path)
    products = pd.read_csv(tmp_path / "products.csv")
    products.drop(columns=["trend"]).to_csv(tmp_path / "products.csv", index=False)
    with pytest.raises(CatalogError, match="products.csv: missing columns trend"):
        DataStore(tmp_path)


def test_duplicate_product_id_names_the_file(tmp_path: Path):
    _write_valid_catalog(tmp_path)
    products = pd.read_csv(tmp_path / "products.csv")
    products.loc[1, "product_id"] = "P1"
    products.to_csv(tmp_path / "products.csv", index=False)
    with pytest.raises(CatalogError, match="products.csv: duplicate product_id P1"):
        DataStore(tmp_path)


def test_offer_must_reference_a_real_product_and_supplier(tmp_path: Path):
    _write_valid_catalog(tmp_path)
    offers = pd.read_csv(tmp_path / "offers.csv")
    offers.loc[0, "product_id"] = "P999"
    offers.to_csv(tmp_path / "offers.csv", index=False)
    with pytest.raises(CatalogError, match="offers.csv: offer references unknown product_id P999"):
        DataStore(tmp_path)

    _write_valid_catalog(tmp_path)
    offers = pd.read_csv(tmp_path / "offers.csv")
    offers.loc[0, "supplier_id"] = "S999"
    offers.to_csv(tmp_path / "offers.csv", index=False)
    with pytest.raises(CatalogError, match="offers.csv: offer references unknown supplier_id S999"):
        DataStore(tmp_path)


def test_bad_trend_is_rejected(tmp_path: Path):
    _write_valid_catalog(tmp_path)
    products = pd.read_csv(tmp_path / "products.csv")
    products.loc[0, "trend"] = "exploding"
    products.to_csv(tmp_path / "products.csv", index=False)
    with pytest.raises(CatalogError, match="products.csv: trend has unsupported values exploding"):
        DataStore(tmp_path)


def _write_valid_catalog(folder: Path) -> None:
    pd.DataFrame(
        [
            {
                "product_id": "P1",
                "name": "Sample Earbuds",
                "category": "Audio",
                "subcategory": "True Wireless Earbuds",
                "market_wholesale_price_usd": 5,
                "retail_price_usd": 20,
                "min_order_qty": 10,
                "monthly_search_index": 80,
                "avg_rating": 4.2,
                "review_count": 100,
                "competitor_count": 10,
                "trend": "rising",
                "return_rate_pct": 4,
                "notes": "",
            },
            {
                "product_id": "P2",
                "name": "Sample Speaker",
                "category": "Audio",
                "subcategory": "Bluetooth Speakers",
                "market_wholesale_price_usd": 14,
                "retail_price_usd": 20,
                "min_order_qty": 10,
                "monthly_search_index": 40,
                "avg_rating": 4.0,
                "review_count": 50,
                "competitor_count": 12,
                "trend": "stable",
                "return_rate_pct": 8,
                "notes": "battery note",
            },
        ]
    ).to_csv(folder / "products.csv", index=False)
    pd.DataFrame(
        [
            {
                "supplier_id": "S1",
                "name": "Sample Co",
                "country": "China",
                "city": "Shenzhen",
                "region": "Asia",
                "categories": "Audio;Wearables",
                "reliability_score": 8,
                "avg_lead_time_days": 15,
                "payment_terms": "30% deposit, 70% before shipment",
                "certifications": "CE;FCC",
                "languages": "English;Mandarin",
                "typical_reply_hours": 6,
                "contact_person": "Li",
                "contact_email": "li@example.com",
                "years_in_business": 5,
                "notes": "",
            }
        ]
    ).to_csv(folder / "suppliers.csv", index=False)
    pd.DataFrame(
        [
            {
                "offer_id": "O1",
                "supplier_id": "S1",
                "product_id": "P1",
                "unit_price_usd": 5.5,
                "min_order_qty": 10,
                "lead_time_days": 12,
                "stock_status": "in_stock",
            }
        ]
    ).to_csv(folder / "offers.csv", index=False)
