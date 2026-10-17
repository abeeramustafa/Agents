"""Rank filtering and decision guards. These tests do not call the model."""

import pytest

from app.data_store import DataStore
from app.orchestrator import FlowError, Orchestrator, accept_ranks, guard_suppliers, keep_decisions, pin_approved
from app.session_store import Session, SessionStore


def test_unknown_rank_ids_are_dropped_and_the_list_caps_at_eight():
    valid = {f"P{index}" for index in range(1, 12)}
    ranked = [{"product_id": "NOPE", "rank": 1, "rationale": "invented", "watch_out": ""}]
    ranked += [
        {"product_id": f"P{index}", "rank": index, "rationale": "ok", "watch_out": ""}
        for index in range(1, 12)
    ]
    accepted = accept_ranks(ranked, valid)
    assert [row["product_id"] for row in accepted] == [f"P{index}" for index in range(1, 9)]
    assert all(row["product_id"] in valid for row in accepted)


def test_decisions_survive_only_for_products_that_remain():
    kept = keep_decisions({"P1": "approved", "P2": "rejected", "P3": "approved"}, {"P1", "P9"})
    assert kept == {"P1": "approved"}


def test_supplier_jump_requires_an_approval():
    session = Session(id="s", stage="BRIEF")
    with pytest.raises(FlowError, match="before comparing"):
        guard_suppliers(session)

    session.stage = "PRODUCT_REVIEW"
    with pytest.raises(FlowError, match="at least one"):
        guard_suppliers(session)


def test_final_plan_waits_for_a_supplier_on_every_product():
    from app.orchestrator import guard_final

    session = Session(id="s", stage="SUPPLIER_REVIEW")
    session.supplier_sections = [
        {"product_id": "P1", "name": "Earbuds", "offers": [{"supplier_id": "S01"}], "selected_supplier_ids": []}
    ]
    with pytest.raises(FlowError, match="at least one supplier"):
        guard_final(session)

    session.supplier_sections[0]["selected_supplier_ids"] = ["S01"]
    guard_final(session)


def test_approved_count_matches_the_cards_and_survives_a_rerank():
    orchestrator = Orchestrator(DataStore(), SessionStore())
    session = orchestrator.sessions.create()
    session.stage = "PRODUCT_REVIEW"
    session.candidates = [
        {
            "product_id": "P001",
            "name": "True Wireless Earbuds Basic",
            "opportunity_score": 40,
            "est_net_margin_pct": 20,
            "demand_score": 30,
            "risk_flags": [{"code": "thin_margin", "reason": "Thin margin"}],
        },
        {
            "product_id": "P041",
            "name": "Adjustable Aluminum Laptop Stand",
            "opportunity_score": 50,
            "est_net_margin_pct": 25,
            "demand_score": 40,
            "risk_flags": [],
        },
    ]
    session.ranked = [
        {"product_id": "P001", "rank": 1, "rationale": "Omar wrote this.", "rationale_source": "omar", "watch_out": ""},
        {"product_id": "P041", "rank": 2, "rationale": "", "rationale_source": "auto", "watch_out": ""},
    ]
    view = orchestrator.set_decision(session.id, "P001", "approved")
    assert view["approved_count"] == 1
    assert view["cards"][0]["decision"] == "approved"
    assert view["cards"][0]["rationale_source"] == "omar"
    assert view["cards"][1]["rationale_source"] == "auto"
    assert view["cards"][0]["risk_flags"]
    assert "P001" not in view["activity_log"][-1]["text"]
    assert "True Wireless Earbuds Basic" in view["activity_log"][-1]["text"]

    reranked = pin_approved(
        [{"product_id": "P041", "rank": 1, "rationale": "x", "rationale_source": "omar", "watch_out": ""}],
        session.candidates,
        session.product_decisions,
    )
    assert [row["product_id"] for row in reranked] == ["P041", "P001"]
    session.ranked = reranked
    session.product_decisions = keep_decisions(
        session.product_decisions, {row["product_id"] for row in reranked}
    )
    again = orchestrator.view(session)
    assert again["approved_count"] == 1
    approved_cards = [card for card in again["cards"] if card["decision"] == "approved"]
    assert [card["product_id"] for card in approved_cards] == ["P001"]