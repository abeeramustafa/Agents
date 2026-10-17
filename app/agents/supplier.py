"""Lina compares offers for one product. She does not invent suppliers."""

from app.agents.base import BaseAgent
from app.agents.prompts import LINA_ROLE
from app.schemas import SupplierOutput


class SupplierAgent(BaseAgent):
    name = "Lina"
    role = "Supplier analyst"
    persona = "Careful sourcing analyst who trusts reliability over the lowest price."
    temperature = 0.2
    output_schema = SupplierOutput
    role_text = LINA_ROLE
