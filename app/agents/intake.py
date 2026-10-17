"""Maya turns a sentence into filters. She does not search the catalog herself."""

from app.agents.base import BaseAgent
from app.agents.prompts import MAYA_ROLE
from app.schemas import IntakeOutput


class IntakeAgent(BaseAgent):
    name = "Maya"
    role = "Intake"
    persona = "Friendly intake specialist who asks only what she needs."
    temperature = 0.2
    output_schema = IntakeOutput
    role_text = MAYA_ROLE


def intake_payload(message: str, current_filters: dict, catalog_meta: dict) -> dict:
    """The facts Maya is allowed to see. Price range and category lists come from code."""

    return {
        "user_message": message,
        "current_filters": current_filters,
        "valid_categories": catalog_meta["categories"],
        "valid_subcategories": catalog_meta["subcategories"],
        "retail_price_min": catalog_meta["retail_price_min"],
        "retail_price_max": catalog_meta["retail_price_max"],
    }
