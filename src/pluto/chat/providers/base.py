"""ChatProvider: the seam between Pluto and any LLM backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

EventType = Literal["text", "tool_call", "tool_result", "done", "error"]


@dataclass
class ChatEvent:
    type: EventType
    text: str = ""  # text | error message
    name: str = ""  # tool name for tool_call / tool_result
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True  # tool_result success
    meta: dict[str, Any] = field(default_factory=dict)  # provider specifics (cost, session id)


class ChatProvider(Protocol):
    name: str

    async def start(self) -> None: ...

    def send(self, message: str) -> AsyncIterator[ChatEvent]: ...

    async def close(self) -> None: ...
