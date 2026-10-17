"""Karim checks another agent's words against the data it was given."""

from app.agents.base import BaseAgent
from app.agents.prompts import KARIM_ROLE
from app.schemas import AuditOutput


class AuditorAgent(BaseAgent):
    name = "Karim"
    role = "Auditor"
    persona = "Skeptical reviewer whose only job is to catch unsupported claims."
    temperature = 0.2
    output_schema = AuditOutput
    role_text = KARIM_ROLE
