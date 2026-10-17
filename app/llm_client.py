"""One async call that must come back as JSON matching a Pydantic model.

The provider is OpenAI-compatible. Groq and xAI both work if LLM_BASE_URL
and LLM_MODEL point at them. This module never logs the API key or the
user's text.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, AuthenticationError, BadRequestError, RateLimitError
from pydantic import BaseModel, ValidationError

from app.config import settings

log = logging.getLogger("sourcedesk.llm")

TIMEOUT_SECONDS = 30
SchemaT = TypeVar("SchemaT", bound=BaseModel)

_client: AsyncOpenAI | None = None


class LLMError(RuntimeError):
    """The model call failed in a way the UI can show. No stack trace."""

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


async def call_json(
    system: str,
    user: str,
    schema: type[SchemaT],
    temperature: float,
    *,
    complete=None,
) -> SchemaT:
    """Call the model, validate, and retry once if the JSON is wrong.

    `complete` is a test hook. Production leaves it empty and uses the provider.
    """

    if complete is None:
        _require_settings()
        complete = _provider_complete

    schema_block = json.dumps(schema.model_json_schema())
    full_system = (
        f"{system}\n\nOutput JSON only, with no markdown fences. "
        f"The JSON must match this schema:\n{schema_block}"
    )
    messages = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": user},
    ]
    last_error = "The reply was empty."
    for attempt in (1, 2):
        started = time.perf_counter()
        raw = await complete(messages, temperature)
        _log_usage(schema.__name__, started, raw)
        text = _message_text(raw)
        try:
            return schema.model_validate(_parse_json(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            log.warning("llm json rejected schema=%s attempt=%s", schema.__name__, attempt)
            if attempt == 2:
                break
            # The second call sees the validation error, not a hidden rewrite of the data.
            messages = [
                *messages,
                {"role": "assistant", "content": text or ""},
                {
                    "role": "user",
                    "content": (
                        "That reply failed validation: "
                        f"{last_error[:1500]}\nReply again with JSON only."
                    ),
                },
            ]
    raise LLMError("The model returned JSON that did not match the schema.")


async def _provider_complete(messages: list[dict], temperature: float):
    client = _get_client()
    try:
        return await _create(client, messages, temperature, json_mode=True)
    except BadRequestError as exc:
        # Some models reject response_format. One plain retry, then surface the error.
        if "response_format" not in str(exc).lower():
            raise LLMError("The model provider rejected the request.") from exc
        try:
            return await _create(client, messages, temperature, json_mode=False)
        except (APITimeoutError, APIConnectionError, AuthenticationError, APIStatusError, BadRequestError) as retry_exc:
            raise _friendly(retry_exc) from retry_exc
    except (APITimeoutError, APIConnectionError, AuthenticationError, APIStatusError) as exc:
        raise _friendly(exc) from exc


async def _create(client: AsyncOpenAI, messages: list[dict], temperature: float, *, json_mode: bool):
    kwargs = {
        "model": settings.llm_model,
        "temperature": temperature,
        "messages": messages,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return await client.chat.completions.create(**kwargs)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        # max_retries is 0 so a dead key fails fast instead of hiding behind SDK retries.
        _client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=TIMEOUT_SECONDS,
            max_retries=0,
        )
    return _client


def _require_settings() -> None:
    missing = [
        name
        for name, value in (
            ("LLM_BASE_URL", settings.llm_base_url),
            ("LLM_API_KEY", settings.llm_api_key),
            ("LLM_MODEL", settings.llm_model),
        )
        if not value
    ]
    if missing:
        raise LLMError(f"Missing environment values: {', '.join(missing)}")


def _friendly(exc: Exception) -> LLMError:
    if isinstance(exc, RateLimitError):
        return LLMError("The model provider rate-limited the request.", rate_limited=True)
    if isinstance(exc, AuthenticationError):
        return LLMError("The model provider rejected the API key.")
    if isinstance(exc, APITimeoutError):
        return LLMError("The model took too long to answer.")
    if isinstance(exc, APIConnectionError):
        return LLMError("Could not reach the model provider. Check LLM_BASE_URL.")
    if isinstance(exc, APIStatusError):
        return LLMError(f"The model provider returned status {exc.status_code}.")
    return LLMError("The model provider rejected the request.")


def _message_text(raw) -> str:
    try:
        content = raw.choices[0].message.content
    except (AttributeError, IndexError):
        return ""
    return content or ""


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # A short preface sometimes surrounds the object. Keep the outer braces.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _log_usage(schema_name: str, started: float, raw) -> None:
    usage = getattr(raw, "usage", None)
    log.info(
        "llm schema=%s latency_ms=%s prompt_tokens=%s completion_tokens=%s",
        schema_name,
        int((time.perf_counter() - started) * 1000),
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
    )
