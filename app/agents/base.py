"""Shared agent shape. An agent is a prompt plus a schema, not a framework."""

import json

from pydantic import BaseModel

from app.agents.prompts import GROUNDING
from app.llm_client import call_json


class BaseAgent:
    name: str
    role: str
    persona: str
    temperature: float
    output_schema: type[BaseModel]
    role_text: str

    def system_prompt(self) -> str:
        # Grounding is first so a role prompt cannot quietly drop the catalog rule.
        return f"{GROUNDING}\n\nPersona: {self.persona}\n\n{self.role_text}"

    async def run(self, data: dict) -> BaseModel:
        # The DATA block is the only facts the model is allowed to use.
        user = json.dumps({"DATA": data}, indent=2)
        return await call_json(self.system_prompt(), user, self.output_schema, self.temperature)
