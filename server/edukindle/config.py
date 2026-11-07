"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    data_dir: Path
    api_key: str | None
    model: str
    reasoning_effort: str | None
    llm_timeout: float
    offline: bool

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key) and not self.offline


def load() -> Config:
    env = os.environ.get
    effort = env("EDUKINDLE_REASONING", "low").strip()
    return Config(
        host=env("EDUKINDLE_HOST", "0.0.0.0"),
        port=int(env("EDUKINDLE_PORT", "8080")),
        data_dir=Path(env("EDUKINDLE_DATA", "data")).resolve(),
        api_key=env("OPENAI_API_KEY") or None,
        model=env("EDUKINDLE_MODEL", "gpt-5-mini"),
        reasoning_effort=effort or None,
        llm_timeout=float(env("EDUKINDLE_LLM_TIMEOUT", "45")),
        offline=env("EDUKINDLE_OFFLINE", "") not in ("", "0", "false"),
    )
