"""Supplier rules that do not call the model."""

import asyncio

import pytest

from app.data_store import DataStore
from app.llm_client import LLMError
from app.supplier_review import (
    accept_supplier_ids,
    cheapest_supplier_warning,
    prepare_product,
    run_with_rate_backoff,
)
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = DataStore(ROOT / "data")


def test_unknown_supplier_ids_are_dropped():
    kept = accept_supplier_ids(["S01", "NOPE", "S01", "S02", "S03", "S04"], {"S01", "S02", "S03"})
    assert kept == ["S01", "S02", "S03"]


def test_cheapest_risky_offer_warns_when_s24_is_lowest():
    section = prepare_product(STORE, "P001")
    cheapest = min(section["offers"], key=lambda row: row["unit_price_usd"])
    assert cheapest["supplier_id"] == "S24"
    scores = [row["supplier_score"] for row in section["offers"]]
    assert scores == sorted(scores, reverse=True)
    assert section["selected_supplier_ids"] == []
    warning = cheapest_supplier_warning(section["offers"])
    assert "S24" not in warning
    assert "Shenzhen" in warning or "QuickShip" in warning
    assert "100% upfront" in warning
    assert "no certifications" in warning
    assert "low reliability" in warning
    assert warning == section["cheapest_supplier_warning"]


def test_single_offer_product_has_no_comparison():
    section = prepare_product(STORE, "P016")
    assert section["limited_supply"] is True
    assert len(section["offers"]) == 1
    assert section["status"] == "ready"
    assert section["offers"][0]["supplier_id"] == "S05"
    assert "no comparison" in section["comparison_notes"]
    assert section["recommended_supplier_ids"] == []


def test_rate_limit_retries_once_then_surfaces():
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        raise LLMError("limited", rate_limited=True)

    async def pause(_seconds):
        return None

    with pytest.raises(LLMError, match="limited"):
        asyncio.run(run_with_rate_backoff(call, sleep=pause))
    assert calls["n"] == 3
