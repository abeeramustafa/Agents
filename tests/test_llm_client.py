"""JSON parsing and the one retry. No network."""

import asyncio
from types import SimpleNamespace

import pytest

from app.llm_client import LLMError, call_json
from app.schemas import LlmPing


def test_fenced_json_is_accepted_and_a_bad_first_reply_retries_once():
    calls = {"n": 0}

    async def complete(messages, temperature):
        calls["n"] += 1
        if calls["n"] == 1:
            assert temperature == 0
            return _fake("```json\nnot json\n```")
        assert "failed validation" in messages[-1]["content"]
        return _fake('Sure\n{"ok": true, "reply": "pong"}')

    result = asyncio.run(call_json("system", "user text", LlmPing, 0, complete=complete))
    assert result == LlmPing(ok=True, reply="pong")
    assert calls["n"] == 2


def test_two_bad_replies_become_a_friendly_error():
    async def complete(messages, temperature):
        return _fake("hello")

    with pytest.raises(LLMError, match="did not match the schema"):
        asyncio.run(call_json("system", "user text", LlmPing, 0, complete=complete))


def _fake(content: str):
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=4)
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=usage)
