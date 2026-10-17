"""Chat and log lines the founder reads. IDs stay in JSON; names go on the page."""

from __future__ import annotations

import re

_SPEAKER = re.compile(
    r"^\s*(?:hey,?\s+)?(?P<name>Maya|Omar|Lina|Karim|Sana)\s*"
    r"(?::+|[-–]|here[,.]?|says?[,.]?|here is[,.]?)\s*",
    re.I,
)
_ID = re.compile(r"\bP\d{3}\b|\bS\d{2}\b")


def strip_speaker(name: str, text: str) -> str:
    """The bubble already shows Maya. The sentence should not start with Maya:."""

    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    match = _SPEAKER.match(cleaned)
    if match and match.group("name").casefold() == name.casefold():
        cleaned = cleaned[match.end() :].lstrip()
    return cleaned.strip()


def first_person(text: str) -> str:
    """Maya talks as I, not as Maya."""

    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"\bMaya (found|matched|couldn't|could not|asked)\b", r"I \1", cleaned, flags=re.I)
    cleaned = re.sub(r"\bMaya's\b", "my", cleaned, flags=re.I)
    return cleaned


def hide_ids(text: str, names: dict[str, str] | None = None) -> str:
    """Replace P041 / S24 with catalog names. Drop an id we cannot name."""

    lookup = names or {}

    def replace(match: re.Match) -> str:
        key = match.group(0)
        return lookup.get(key) or lookup.get(key.upper()) or ""

    cleaned = _ID.sub(replace, str(text or ""))
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip(" ,;:-")


def chat_line(name: str, text: str, names: dict[str, str] | None = None) -> str:
    cleaned = strip_speaker(name, text)
    if name == "Maya":
        cleaned = first_person(cleaned)
    return hide_ids(cleaned, names)


def event_text(actor: str, action: str, detail: str) -> str:
    action = (action or "").strip()
    detail = (detail or "").strip()
    if not detail:
        return f"{actor} {action[0].lower() + action[1:]}" if action else actor
    if action:
        lead = action[0].lower() + action[1:] if action else ""
        return f"{actor} {lead} {detail}".strip()
    return f"{actor} {detail}".strip()
