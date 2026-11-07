"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

OPENROUTER_URL = "https://openrouter.ai/api/v1"

# Per provider: (key env var, default model, display name).
PROVIDERS = {
    "openai": ("OPENAI_API_KEY", "gpt-5-mini", "OpenAI"),
    "openrouter": ("OPENROUTER_API_KEY", "openai/gpt-5-mini", "OpenRouter"),
}


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    data_dir: Path
    provider: str            # "openai" | "openrouter"
    api_key: str | None
    base_url: str | None     # None = the SDK default (or $OPENAI_BASE_URL)
    model: str
    reasoning_effort: str | None
    llm_timeout: float
    offline: bool

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key) and not self.offline

    @property
    def provider_name(self) -> str:
        return PROVIDERS[self.provider][2]


def pick_provider(env) -> str:
    """$EDUKINDLE_PROVIDER if set, else whichever API key is present (OpenAI first)."""
    explicit = (env("EDUKINDLE_PROVIDER") or "").strip().lower()
    if explicit:
        if explicit not in PROVIDERS:
            raise ValueError(f"EDUKINDLE_PROVIDER must be one of {', '.join(PROVIDERS)}")
        return explicit
    if not env("OPENAI_API_KEY") and env("OPENROUTER_API_KEY"):
        return "openrouter"
    return "openai"


def load() -> Config:
    env = os.environ.get
    provider = pick_provider(env)
    key_var, default_model, _ = PROVIDERS[provider]
    effort = env("EDUKINDLE_REASONING", "low").strip()
    return Config(
        host=env("EDUKINDLE_HOST", "0.0.0.0"),
        port=int(env("EDUKINDLE_PORT", "8080")),
        data_dir=Path(env("EDUKINDLE_DATA", "data")).resolve(),
        provider=provider,
        api_key=env(key_var) or None,
        base_url=OPENROUTER_URL if provider == "openrouter" else None,
        model=env("EDUKINDLE_MODEL", default_model),
        reasoning_effort=effort or None,
        llm_timeout=float(env("EDUKINDLE_LLM_TIMEOUT", "45")),
        offline=env("EDUKINDLE_OFFLINE", "") not in ("", "0", "false"),
    )
