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
    name = name or os.environ.get("PLUTO_LLM_PROVIDER") or "claude_agent"
    if name == "claude_agent":
        from pluto.chat.providers.claude_agent import ClaudeAgentProvider

        return ClaudeAgentProvider(registry, system_prompt, model=model, cwd=cwd)
    if name == "anthropic_api":
        raise NotImplementedError("anthropic_api provider arrives with milestone M5")
    raise ValueError(f"unknown provider {name!r}; known: {PROVIDERS}")


__all__ = ["ChatEvent", "ChatProvider", "make_provider"]
