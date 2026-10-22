"""Provider-agnostic LLM interface.

The rest of the app depends only on `LLMProvider.complete_json`. Swapping providers means
adding one adapter class and registering it in `get_provider`.
"""
from __future__ import annotations

from typing import Any, Protocol


class LLMError(Exception):
    """Any provider failure (network, auth, refusal, invalid output). Callers fall back to deterministic mode."""


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any], max_tokens: int = 4000) -> dict[str, Any]:
        """Return a JSON object conforming to `schema`, or raise LLMError."""
        ...


def get_provider() -> LLMProvider | None:
    from app.config import get_settings

    settings = get_settings()
    if not settings.llm_enabled:
        return None
    if settings.llm_provider in ("auto", "anthropic"):
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.llm_model,
                                 timeout=settings.llm_timeout_s)
    return None
