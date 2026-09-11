from __future__ import annotations

import os
from pathlib import Path

from pluto.chat.providers.base import ChatEvent, ChatProvider
from pluto.chat.tools import ToolRegistry

PROVIDERS = ("claude_agent", "anthropic_api")


def make_provider(
    registry: ToolRegistry,
    system_prompt: str,
    name: str | None = None,
    model: str | None = None,
    cwd: Path | None = None,
) -> ChatProvider:
    """Pick the LLM backend. Explicit `name` wins, then PLUTO_LLM_PROVIDER, then the
    Claude Code subscription (claude_agent). Set PLUTO_LLM_PROVIDER=anthropic_api to use
    an API key instead."""
    name = name or os.environ.get("PLUTO_LLM_PROVIDER") or "claude_agent"
    if name == "claude_agent":
        from pluto.chat.providers.claude_agent import ClaudeAgentProvider

        return ClaudeAgentProvider(registry, system_prompt, model=model, cwd=cwd)
    if name == "anthropic_api":
        from pluto.chat.providers.anthropic_api import AnthropicApiProvider

        return AnthropicApiProvider(registry, system_prompt, model=model)
    raise ValueError(f"unknown provider {name!r}; known: {PROVIDERS}")


__all__ = ["PROVIDERS", "ChatEvent", "ChatProvider", "make_provider"]
