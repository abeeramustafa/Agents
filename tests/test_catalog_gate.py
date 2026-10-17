"""The gate decides from the CSVs. These tests do not call the model."""

from pathlib import Path

from app.catalog_gate import FUTURE_NOTE, apply_intake
from app.data_store import DataStore
from app.schemas import IntakeOutput, ProductFilters

ROOT = Path(__file__).resolve().parent.parent
STORE = DataStore(ROOT / "data")
LABELS = set(STORE.catalog_meta()["categories"]) | set(STORE.catalog_meta()["subcategories"])


def test_earfones_resolves_to_earbuds_already_in_the_catalog():
    result = apply_intake(STORE, _intake("earfones", keywords=["earfones"]))
    assert result.status == "ready"
    assert result.in_catalog is True
    assert result.matched_label == "Earphones"
    assert "Earphones" in result.message
    assert "P001" in result.product_ids
    assert "P005" in result.product_ids
    for product_id in result.product_ids:
        assert STORE.get_product(product_id) is not None


def test_drones_and_shoes_stay_out_of_the_catalog():
    for text in ("drones", "shoes"):
        result = apply_intake(STORE, _intake(text, keywords=[text]))
        assert result.status == "out_of_catalog"
        assert result.product_ids == []
        assert text in result.message
        assert result.note == FUTURE_NOTE
        assert len(result.closest_matches) == 3
        assert {match.label for match in result.closest_matches} <= LABELS


def test_an_invented_category_does_not_return_the_whole_catalog():
    result = apply_intake(
        STORE,
        _intake("drones", categories=["Drones"]),
    )
    assert result.status == "out_of_catalog"
    assert result.product_ids == []


def test_a_real_category_is_kept_when_a_fake_one_is_mixed_in():
    result = apply_intake(
        STORE,
        _intake("audio", categories=["Audio", "Drones"]),
    )
    assert result.status == "ready"
    assert "P001" in result.product_ids
    assert result.filters.categories == ["Audio"]
    for product_id in result.product_ids:
        assert STORE.get_product(product_id)["category"] == "Audio"


def test_a_written_price_and_margin_are_read_even_if_maya_misses_them():
    # The brief is the whole catalog plus limits. "electronics" is not a missing product.
    result = apply_intake(
        STORE,
        _intake(""),
        user_text="Electronics under 30 dollars retail, good margins, low returns",
    )
    assert result.status == "ready"
    assert result.filters.max_retail_price == 30
    assert result.filters.min_net_margin_pct == 15
    assert result.filters.max_return_rate_pct == 8
    assert result.product_ids
    # Maya also stuffed generic words into keywords. Those must not zero the search.
    echoed = apply_intake(
        STORE,
        _intake(
            "",
            filters=ProductFilters(
                max_retail_price=30,
                min_net_margin_pct=15,
                max_return_rate_pct=8,
                keywords=["Electronics", "retail"],
            ),
        ),
        user_text="Electronics under 30 dollars retail, good margins, low returns",
    )
    assert echoed.status == "ready"
    assert echoed.filters.keywords == []
    assert echoed.product_ids
    for product_id in result.product_ids:
        product = STORE.get_product(product_id)
        assert product["retail_price_usd"] <= 30


def test_a_missing_product_does_not_fall_back_to_the_price_band():
    result = apply_intake(
        STORE,
        _intake("drones"),
        user_text="drones under 30 dollars",
    )
    assert result.status == "out_of_catalog"
    assert result.product_ids == []


def test_a_price_filter_limits_real_products():
    result = apply_intake(
        STORE,
        _intake("electronics", filters=ProductFilters(max_retail_price=30)),
    )
    assert result.status == "ready"
    assert result.product_ids
    for product_id in result.product_ids:
        assert STORE.get_product(product_id)["retail_price_usd"] <= 30


def test_a_vague_request_asks_one_question_and_returns_no_products():
    result = apply_intake(
        STORE,
        _intake("", clarifying_question="Do you want audio, power, or smart home?"),
    )
    assert result.status == "clarify"
    assert result.product_ids == []
    assert result.clarifying_question == "Do you want audio, power, or smart home?"


def test_home_appliances_quotes_the_item_and_maps_closest_matches():
    from app.catalog_gate import requested_item

    phrase = requested_item(
        "I want to sell home appliances for my store",
        "I want to sell home appliances for my store",
    )
    assert phrase.lower() == "home appliances"
    result = apply_intake(
        STORE,
        _intake("I want to sell home appliances for my store"),
        user_text="I want to sell home appliances for my store",
    )
    assert result.status == "out_of_catalog"
    assert "home appliances" in result.message
    assert "I want to sell" not in result.message
    labels = {match.label for match in result.closest_matches}
    assert "Smart Home" in labels
    assert "Robot Vacuums" in labels


def test_an_empty_request_does_not_dump_every_product():
    result = apply_intake(STORE, _intake(""))
    assert result.status == "clarify"
    assert result.product_ids == []
    assert result.clarifying_question


def _intake(text: str, clarifying_question: str | None = None, filters: ProductFilters | None = None, **filter_kwargs) -> IntakeOutput:
    if filters is None:
        filters = ProductFilters(**filter_kwargs)
    return IntakeOutput(
        in_catalog=True,
        clarifying_question=clarifying_question,
        filters=filters,
        requested_item_text=text,
        message="Noted.",
    )
