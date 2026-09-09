"""A fake LLM for tests: replays scripted steps against the real tool registry."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pluto.chat.providers.base import ChatEvent
from pluto.chat.tools import ToolRegistry

Step = tuple[str, str, dict[str, Any]] | tuple[str, str]  # ("tool", name, args) | ("text", s)


class ScriptedProvider:
    name = "scripted"

    def __init__(self, registry: ToolRegistry, script: list[list[Step]]):
        self.registry = registry
        self.script = list(script)
        self.calls: list[tuple[str, dict[str, Any], Any]] = []

    async def start(self) -> None:
        pass

    async def send(self, message: str) -> AsyncIterator[ChatEvent]:
        if not self.script:
            yield ChatEvent("error", text="script exhausted")
            return
        for step in self.script.pop(0):
            if step[0] == "text":
                yield ChatEvent("text", text=step[1])
            else:
                _, name, args = step  # type: ignore[misc]
                yield ChatEvent("tool_call", name=name, args=args)
                res = await self.registry.call(name, args)
                self.calls.append((name, args, res.data))
                yield ChatEvent("tool_result", name=name, text=res.as_text(), ok=res.ok)
        yield ChatEvent("done")

    async def close(self) -> None:
        pass
