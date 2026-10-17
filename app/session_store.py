"""In-memory sessions. One browser tab, one id. Restarting the process clears them.

There is no database on purpose. A single server worker is required because
two workers would not share this dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from app.copy import event_text, hide_ids


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Session:
    id: str
    stage: str = "BRIEF"
    filters: dict = field(default_factory=dict)
    chat: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    ranked: list = field(default_factory=list)
    product_decisions: dict = field(default_factory=dict)
    supplier_analysis: dict = field(default_factory=dict)
    supplier_selection: dict = field(default_factory=dict)
    supplier_sections: list = field(default_factory=list)
    supplier_progress: dict = field(
        default_factory=lambda: {
            "running": False,
            "completed": 0,
            "total": 0,
            "batch": 0,
            "batches": 0,
        }
    )
    plan: dict | None = None
    plan_key: str = ""
    activity_log: list = field(default_factory=list)
    gate_status: str = "brief"
    clarifying_question: str | None = None
    chips: list = field(default_factory=list)
    closest_matches: list = field(default_factory=list)
    note: str | None = None
    banner: str = ""

    def add_chat(self, name: str, role: str, text: str) -> None:
        self.chat.append({"name": name, "role": role, "text": text})

    def add_event(self, actor: str, action: str, detail: str, names: dict[str, str] | None = None) -> None:
        action = hide_ids(action, names)
        detail = hide_ids(detail, names)
        self.activity_log.append(
            {
                "timestamp": _now(),
                "actor": actor,
                "action": action,
                "detail": detail[:180],
                "text": event_text(actor, action, detail)[:220],
            }
        )


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        session = Session(id=uuid4().hex)
        session.add_event("SourceDesk", "Opened a session", "Waiting for a brief.")
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)
