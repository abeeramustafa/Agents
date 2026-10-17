"""Sana writes the final plan. She does not pick new products or suppliers."""

from app.agents.base import BaseAgent
from app.agents.prompts import SANA_ROLE
from app.schemas import PlanOutput


class PlannerAgent(BaseAgent):
    name = "Sana"
    role = "Sourcing lead"
    persona = "Calm sourcing lead who writes for a busy founder."
    temperature = 0.4
    output_schema = PlanOutput
    role_text = SANA_ROLE
