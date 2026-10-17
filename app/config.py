"""Read process settings from the environment.

The API key never lives in source. A missing key must not stop the
process: later phases turn that into a friendly error instead.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load the project-root .env when the app starts on a laptop.
# In deployment the same names are real environment variables, and
# load_dotenv does not override those.
load_dotenv()


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _as_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    port: int
    enable_auditor: bool
    cost_allowance: float


def load_settings() -> Settings:
    return Settings(
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        port=_as_int("PORT", 8000),
        enable_auditor=_as_bool("ENABLE_AUDITOR", True),
        cost_allowance=_as_float("COST_ALLOWANCE", 0.40),
    )


settings = load_settings()
