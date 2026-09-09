"""Claude via the Claude Agent SDK, i.e. the local Claude Code CLI and its subscription auth.

No API key involved. The Pluto tools are served to the model through an in-process MCP
server; every built-in Claude Code tool is disabled so the model can only touch the portfolio.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pluto.chat.providers.base import ChatEvent
from pluto.chat.tools import ToolRegistry, ToolSpec

SERVER = "pluto"
PREFIX = f"mcp__{SERVER}__"


def _make_sdk_tool(registry: ToolRegistry, spec: ToolSpec):
    from claude_agent_sdk import tool

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        res = await registry.call(spec.name, args)
        out: dict[str, Any] = {"content": [{"type": "text", "text": res.as_text()}]}
        if not res.ok:
            out["is_error"] = True
        return out

    return tool(spec.name, spec.description, spec.json_schema())(handler)


class ClaudeAgentProvider:
    name = "claude_agent"

    def __init__(
        self,
        registry: ToolRegistry,
        system_prompt: str,
        model: str | None = None,
        cwd: Path | None = None,
        resume: str | None = None,
    ):
        self.registry = registry
        self.system_prompt = system_prompt
        self.model = model or os.environ.get("PLUTO_MODEL") or None
        self.cwd = cwd
        self.resume = resume
        self.session_id: str | None = None
        self._client: Any = None

    async def start(self) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server

        tools = [_make_sdk_tool(self.registry, s) for s in self.registry.specs.values()]
        server = create_sdk_mcp_server(name=SERVER, version="1.0.0", tools=tools)
        options = ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            mcp_servers={SERVER: server},
            tools=[],  # no built-in Claude Code tools
            allowed_tools=[f"{PREFIX}{n}" for n in self.registry.names()],
            permission_mode="default",
            setting_sources=[],  # do not load the user's CLAUDE.md, hooks or plugins
            model=self.model,
            cwd=self.cwd,
            resume=self.resume,
            max_turns=30,
        )
        self._client = ClaudeSDKClient(options)
        await self._client.connect()

    async def send(self, message: str) -> AsyncIterator[ChatEvent]:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )

        if self._client is None:
            await self.start()
        assert self._client is not None
        await self._client.query(message)
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                if msg.error:
                    yield ChatEvent("error", text=f"model error: {msg.error}")
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        yield ChatEvent("text", text=block.text)
                    elif isinstance(block, ToolUseBlock):
                        yield ChatEvent(
                            "tool_call", name=block.name.removeprefix(PREFIX), args=block.input
                        )
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        yield ChatEvent(
                            "tool_result", text=_result_text(block.content), ok=not block.is_error
                        )
            elif isinstance(msg, ResultMessage):
                self.session_id = msg.session_id
                meta = {
                    "session_id": msg.session_id,
                    "cost_usd": msg.total_cost_usd,
                    "turns": msg.num_turns,
                    "duration_ms": msg.duration_ms,
                }
                if msg.is_error:
                    yield ChatEvent("error", text=msg.result or msg.subtype, meta=meta)
                yield ChatEvent("done", meta=meta)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


def _result_text(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
