"""Provider selection (OpenAI vs OpenRouter) and request shaping."""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

from edukindle import config
from edukindle.llm import LLM, LLMError, parse_json


def load_with(monkeypatch, **env):
    for var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "EDUKINDLE_PROVIDER",
                "EDUKINDLE_MODEL", "EDUKINDLE_OFFLINE"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return config.load()


def test_openrouter_key_alone_selects_openrouter(monkeypatch):
    cfg = load_with(monkeypatch, OPENROUTER_API_KEY="or-key")
    assert cfg.provider == "openrouter" and cfg.llm_enabled
    assert cfg.api_key == "or-key"
    assert cfg.base_url == config.OPENROUTER_URL
    assert cfg.model == "openai/gpt-5-mini"


def test_openai_key_wins_unless_provider_forced(monkeypatch):
    cfg = load_with(monkeypatch, OPENAI_API_KEY="oa", OPENROUTER_API_KEY="or")
    assert cfg.provider == "openai" and cfg.api_key == "oa" and cfg.base_url is None
    cfg = load_with(monkeypatch, OPENAI_API_KEY="oa", OPENROUTER_API_KEY="or",
                    EDUKINDLE_PROVIDER="OpenRouter", EDUKINDLE_MODEL="anthropic/claude-haiku-4.5")
    assert cfg.provider == "openrouter" and cfg.api_key == "or"
    assert cfg.model == "anthropic/claude-haiku-4.5"


def test_bad_provider_rejected(monkeypatch):
    with pytest.raises(ValueError):
        load_with(monkeypatch, EDUKINDLE_PROVIDER="gemini")


def test_no_keys_is_offline(monkeypatch):
    cfg = load_with(monkeypatch)
    assert not cfg.llm_enabled


class CapturingCompletions:
    def __init__(self, content: str):
        self.content = content
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        msg = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(total_tokens=1))


def make_llm(monkeypatch, content='{"ok": true}', **env) -> tuple[LLM, CapturingCompletions]:
    cfg = load_with(monkeypatch, **env)
    llm = LLM(cfg)
    comp = CapturingCompletions(content)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return llm, comp


def call(llm: LLM) -> dict:
    return asyncio.run(llm.json(system="s", user="u", name="t",
                                schema={"type": "object"}))


def test_openrouter_request_shape(monkeypatch):
    llm, comp = make_llm(monkeypatch, OPENROUTER_API_KEY="or")
    assert llm.provider == "OpenRouter"
    assert call(llm) == {"ok": True}
    assert comp.kwargs["model"] == "openai/gpt-5-mini"
    assert comp.kwargs["extra_body"] == {"reasoning": {"effort": "low"}}
    assert "reasoning_effort" not in comp.kwargs
    assert comp.kwargs["response_format"]["type"] == "json_schema"


def test_openai_request_shape(monkeypatch):
    llm, comp = make_llm(monkeypatch, OPENAI_API_KEY="oa")
    call(llm)
    assert comp.kwargs["reasoning_effort"] == "low"
    assert "extra_body" not in comp.kwargs


def test_openrouter_client_uses_base_url_and_headers(monkeypatch):
    llm = LLM(load_with(monkeypatch, OPENROUTER_API_KEY="or"))
    assert str(llm._client.base_url).rstrip("/") == config.OPENROUTER_URL
    assert llm._client.default_headers["X-Title"] == "EduKindle"


@pytest.mark.parametrize("content", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Here you go:\n```\n{"a": 1}\n```',
    'Sure! {"a": 1} Hope that helps.',
])
def test_parse_json_tolerates_wrappers(content):
    assert parse_json(content) == {"a": 1}


@pytest.mark.parametrize("content", ["", "no json here", "[1, 2]", "{broken"])
def test_parse_json_rejects_garbage(content):
    with pytest.raises(LLMError):
        parse_json(content)


def test_fake_llm_in_service_has_provider():
    # The dashboard and /api/home read llm.provider; keep test doubles in sync.
    from tests.test_llm_paths import FakeLLM
    assert hasattr(FakeLLM, "provider")
