"""Anthropic (Claude) adapter using the official SDK with JSON-schema structured outputs."""
from __future__ import annotations

import json
from typing import Any

from app.llm.base import LLMError


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str, timeout: float = 60.0, client: Any | None = None):
        """`client` lets tests inject an SDK client whose HTTP transport is mocked."""
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency is optional
            raise LLMError("anthropic package not installed") from exc
        self._anthropic = anthropic
        self.model = model
        self._client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)

    def complete_json(self, system: str, user: str, schema: dict[str, Any], max_tokens: int = 4000) -> dict[str, Any]:
        a = self._anthropic
        try:
            # Server-side refusal fallback ("default" routing) is enabled; structured output guarantees JSON.
            resp = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as exc:
            raise LLMError("LLM authentication failed (check ANTHROPIC_API_KEY)") from exc
        except a.RateLimitError as exc:
            raise LLMError("LLM rate limited") from exc
        except a.BadRequestError as exc:
            # The API's own message (e.g. which parameter or schema feature was rejected) is not secret and is
            # what makes a live failure diagnosable.
            raise LLMError(f"LLM request rejected (400): {str(getattr(exc, 'message', exc))[:300]}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"LLM API error {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("LLM provider unreachable") from exc
        if resp.stop_reason == "refusal":
            raise LLMError("LLM declined the request")
        if resp.stop_reason == "max_tokens":
            raise LLMError("LLM output truncated")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise LLMError("LLM returned no text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("LLM returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("LLM returned a non-object JSON value")
        return data
