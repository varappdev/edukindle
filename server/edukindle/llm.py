"""Thin async wrapper around the OpenAI chat API that always returns JSON.

Every agent call goes through `LLM.json()`, which requests a strict JSON-schema
response. Models or OpenAI-compatible servers that reject `reasoning_effort` or
`json_schema` are retried with progressively plainer requests, so the same code
works against OpenAI (gpt-5-*, gpt-4.1-*), OpenRouter and local
OpenAI-compatible endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import openai

from .config import Config

log = logging.getLogger("edukindle.llm")


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.enabled = cfg.llm_enabled
        self.model = cfg.model
        self.provider = cfg.provider_name
        self._openrouter = cfg.provider == "openrouter"
        # OpenRouter uses these to attribute traffic to the app on its dashboard.
        headers = ({"HTTP-Referer": "https://github.com/edukindle", "X-Title": "EduKindle"}
                   if self._openrouter else None)
        self._client = (
            openai.AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url,
                               default_headers=headers, timeout=cfg.llm_timeout,
                               max_retries=1)
            if self.enabled
            else None
        )
        # The OpenAI API is the slow part of every request; cap fan-out so a
        # burst of prefetches can't starve an interactive explain call.
        self._sem = asyncio.Semaphore(4)
        # Downgrades learned at runtime (e.g. model rejects reasoning_effort).
        self._use_effort = cfg.reasoning_effort is not None
        self._use_schema = True

    async def json(
        self,
        *,
        system: str,
        user: str,
        name: str,
        schema: dict[str, Any],
        max_tokens: int = 4000,
    ) -> dict[str, Any]:
        if not self._client:
            raise LLMError("LLM disabled (no API key)")

        async with self._sem:
            for _ in range(3):
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_completion_tokens": max_tokens,
                }
                if self._use_effort:
                    if self._openrouter:
                        # OpenRouter's unified form, translated per upstream model.
                        kwargs["extra_body"] = {"reasoning": {"effort": self.cfg.reasoning_effort}}
                    else:
                        kwargs["reasoning_effort"] = self.cfg.reasoning_effort
                if self._use_schema:
                    kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": name, "schema": schema, "strict": True},
                    }
                else:
                    kwargs["response_format"] = {"type": "json_object"}
                    kwargs["messages"][0]["content"] += (
                        "\n\nRespond with a single JSON object matching this JSON schema:\n"
                        + json.dumps(schema)
                    )

                t0 = time.perf_counter()
                try:
                    resp = await self._client.chat.completions.create(**kwargs)
                except openai.BadRequestError as e:
                    msg = str(e).lower()
                    if self._use_effort and "reasoning" in msg:
                        log.warning("model rejected reasoning_effort; disabling it")
                        self._use_effort = False
                        continue
                    if self._use_schema and ("json_schema" in msg or "response_format" in msg):
                        log.warning("model rejected json_schema; falling back to json_object")
                        self._use_schema = False
                        continue
                    raise LLMError(str(e)) from e
                except openai.APIError as e:
                    raise LLMError(str(e)) from e

                if not resp.choices:
                    raise LLMError(f"{name}: empty response from {self.provider}")
                content = resp.choices[0].message.content or ""
                log.info("%s: %.2fs, %s tokens", name, time.perf_counter() - t0,
                         getattr(resp.usage, "total_tokens", "?"))
                return parse_json(content, name)
            raise LLMError(f"{name}: no request variant accepted by the model")


def parse_json(content: str, name: str = "response") -> dict[str, Any]:
    """Parse a JSON object, tolerating the Markdown fences or preamble that some
    OpenRouter upstreams add when they ignore response_format."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return data
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S)
    candidate = fenced.group(1) if fenced else content[content.find("{"):content.rfind("}") + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LLMError(f"{name}: model returned invalid JSON") from e
    if not isinstance(data, dict):
        raise LLMError(f"{name}: model returned JSON that isn't an object")
    return data


def obj(props: dict[str, Any]) -> dict[str, Any]:
    """Strict-mode object schema: every property required, nothing extra."""
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def arr(items: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"type": "array", "items": items, **kw}


STR = {"type": "string"}
INT = {"type": "integer"}
