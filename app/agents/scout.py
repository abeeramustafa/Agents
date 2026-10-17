"""Omar ranks products the code already scored. He does not invent rows."""

from app.agents.base import BaseAgent
from app.agents.prompts import OMAR_ROLE
from app.schemas import ScoutOutput


class ScoutAgent(BaseAgent):
    name = "Omar"
    role = "Product research"
    persona = "Practical product researcher, direct, no hype."
    temperature = 0.2
    output_schema = ScoutOutput
    role_text = OMAR_ROLE


def scout_payload(candidates: list[dict]) -> dict:
    """The only rows Omar may cite. Figures are rounded to what the card will show."""

    rows = []
    for row in candidates:
        rows.append(
            {
                "product_id": row["product_id"],
                "name": row["name"],
                "category": row["category"],
                "subcategory": row["subcategory"],
                "retail_price_usd": round(float(row["retail_price_usd"]), 2),
                "wholesale_price_usd": round(float(row["market_wholesale_price_usd"]), 2),
                "est_net_margin_pct": round(float(row["est_net_margin_pct"]), 1),
                "demand_score": round(float(row["demand_score"]), 1),
                "competition_pressure": round(float(row["competition_pressure"]), 1),
                "opportunity_score": round(float(row["opportunity_score"]), 1),
                "trend": row["trend"],
                "return_rate_pct": row["return_rate_pct"],
                "risk_flags": [flag["code"] for flag in row["risk_flags"]],
            }
        )
    return {"candidates": rows}
