"""Sana's plan stays inside the suppliers the human selected. No model calls."""

from app.data_store import DataStore
from app.orchestrator import FlowError, Orchestrator
from app.plan_review import accept_plan
from app.schemas import PlanItem, PlanOutput
from app.session_store import SessionStore


def _sections():
    return [
        {
            "product_id": "P001",
            "name": "Earbuds",
            "category": "Audio",
            "limited_supply": False,
            "offers": [
                {
                    "supplier_id": "S01",
                    "supplier_name": "Alpha",
                    "unit_price_usd": 6.0,
                    "supplier_score": 80.0,
                    "risk_flags": [],
                },
                {
                    "supplier_id": "S24",
                    "supplier_name": "Quick",
                    "unit_price_usd": 5.25,
                    "supplier_score": 40.0,
                    "risk_flags": [{"code": "low_reliability", "reason": "Reliability is 5.6."}],
                },
            ],
            "selected_supplier_ids": ["S01"],
            "cheapest_supplier_warning": "Quick (S24) is the cheapest offer at $5.25, but it has low reliability.",
        },
        {
            "product_id": "P016",
            "name": "Vacuum",
            "category": "Smart Home",
            "limited_supply": True,
            "offers": [
                {
                    "supplier_id": "S05",
                    "supplier_name": "Beta",
                    "unit_price_usd": 58.88,
                    "supplier_score": 70.0,
                    "risk_flags": [],
                }
            ],
            "selected_supplier_ids": ["S05"],
            "cheapest_supplier_warning": "",
        },
    ]


def test_unknown_supplier_is_dropped_and_a_skipped_product_is_filled():
    result = PlanOutput(
        headline="A short plan",
        plan=[PlanItem(product_id="P001", supplier_ids=["S01", "S99"], why="Alpha at $6.00 is the pick.")],
        risks_to_check=["S99 is risky", "Check the deposit"],
        next_steps=["I emailed the supplier", "Draft outreach next"],
        message="Here is the plan.",
    )
    accepted = accept_plan(result, _sections())
    assert [row["product_id"] for row in accepted["plan"]] == ["P001", "P016"]
    assert accepted["plan"][0]["supplier_ids"] == ["S01"]
    assert {row["supplier_name"] for row in accepted["plan"][0]["suppliers"]} == {"Alpha"}
    assert accepted["plan"][1]["supplier_ids"] == ["S05"]
    assert "58.88" in accepted["plan"][1]["why"]
    assert all("S99" not in risk for risk in accepted["risks_to_check"])
    assert accepted["next_steps"] == ["Draft outreach next"]
    assert "S99" not in accepted["headline"]

    risky = _sections()
    risky[0]["selected_supplier_ids"] = ["S01", "S24"]
    chosen = accept_plan(result, risky)
    assert chosen["plan"][0]["supplier_ids"] == ["S01", "S24"]
    assert [row["supplier_name"] for row in chosen["plan"][0]["suppliers"]] == ["Alpha", "Quick"]
    assert any("You chose Quick despite" in risk and "Earbuds" in risk for risk in chosen["risks_to_check"])
    assert all("S24" not in risk for risk in chosen["risks_to_check"])


def test_back_to_the_plan_waits_until_sana_has_written_one():
    orchestrator = Orchestrator(DataStore(), SessionStore())
    session = orchestrator.sessions.create()
    session.stage = "SUPPLIER_REVIEW"
    session.supplier_sections = _sections()
    try:
        orchestrator.go_back(session.id, "FINAL_REVIEW")
        raise AssertionError("plan should be missing")
    except FlowError as exc:
        assert "no plan" in exc.error

    session.plan = {"headline": "Saved", "plan": [], "risks_to_check": [], "next_steps": []}
    session.plan_key = "stale"
    try:
        orchestrator.go_back(session.id, "FINAL_REVIEW")
        raise AssertionError("stale plan should not open")
    except FlowError as exc:
        assert "picks changed" in exc.error

    from app.plan_review import plan_key

    session.plan_key = plan_key(session.supplier_sections)
    view = orchestrator.go_back(session.id, "FINAL_REVIEW")
    assert view["stage"] == "FINAL_REVIEW"
    assert view["plan"]["headline"] == "Saved"
