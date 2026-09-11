"""Claude via the Anthropic API (`anthropic` SDK). Needs ANTHROPIC_API_KEY or an
`ant auth login` profile. Same tools, same events as the Agent SDK provider.

A manual tool loop is used on purpose: Pluto's ChatProvider contract wants a tool_call and
tool_result event per call, and the conversation history must be owned here so a session
survives across turns. Requests stream so long turns never hit the HTTP timeout.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from pluto.chat.providers.base import ChatEvent
from pluto.chat.tools import ToolRegistry

DEFAULT_MODEL = "claude-opus-5"
MAX_TOOL_ROUNDS = 30


class AnthropicApiProvider:
    name = "anthropic_api"

    def __init__(
        self,
        registry: ToolRegistry,
        system_prompt: str,
        model: str | None = None,
        effort: str | None = None,
        client: Any = None,  # anthropic.AsyncAnthropic; injectable for tests
    ):
        self.registry = registry
        self.system_prompt = system_prompt
        self.model = model or os.environ.get("PLUTO_MODEL") or DEFAULT_MODEL
        self.effort = effort or os.environ.get("PLUTO_EFFORT") or "medium"
        self._client: Any = client
        self.messages: list[dict[str, Any]] = []  # full history, sent on every request
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}

    def _tools(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "description": s.description, "input_schema": s.json_schema()}
            for s in self.registry.specs.values()
        ]

    async def start(self) -> None:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic()

    async def send(self, message: str) -> AsyncIterator[ChatEvent]:
        import anthropic

        if self._client is None:
            await self.start()
        assert self._client is not None
        self.messages.append({"role": "user", "content": message})
        tools = self._tools()
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                async with self._client.messages.stream(
                    model=self.model,
                    max_tokens=16000,
                    system=[
                        {
                            "type": "text",
                            "text": self.system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=tools,
                    messages=self.messages,
                    output_config={"effort": self.effort},
                ) as stream:
                    response = await stream.get_final_message()
            except anthropic.AuthenticationError:
                yield ChatEvent(
                    "error",
                    text="Anthropic API: authentication failed. "
                    "Set ANTHROPIC_API_KEY or run `ant auth login`.",
                )
                return
            except anthropic.RateLimitError as e:
                retry = e.response.headers.get("retry-after", "?")
                yield ChatEvent("error", text=f"Anthropic API: rate limited (retry after {retry}s)")
                return
            except anthropic.APIStatusError as e:
                yield ChatEvent("error", text=f"Anthropic API error {e.status_code}: {e.message}")
                return
            except anthropic.APIConnectionError as e:
                yield ChatEvent("error", text=f"Anthropic API: connection error: {e}")
                return

            self._count(response)
            # keep the full assistant content (incl. thinking blocks) so it replays unchanged
            self.messages.append({"role": "assistant", "content": response.content})
            tool_uses = []
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    yield ChatEvent("text", text=block.text)
                elif block.type == "tool_use":
                    tool_uses.append(block)
                    yield ChatEvent("tool_call", name=block.name, args=dict(block.input))

            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                why = getattr(details, "explanation", None) or "the model declined this request"
                yield ChatEvent("error", text=f"Anthropic API: {why}")
                break
            if response.stop_reason == "max_tokens":
                yield ChatEvent("error", text="response cut off at max_tokens")
                break
            if response.stop_reason == "pause_turn":
                continue  # resend history to let the model continue its turn
            if not tool_uses:
                break

            results = []
            for block in tool_uses:
                res = await self.registry.call(block.name, dict(block.input))
                yield ChatEvent("tool_result", name=block.name, text=res.as_text(), ok=res.ok)
                item: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": res.as_text(),
                }
                if not res.ok:
                    item["is_error"] = True
                results.append(item)
            self.messages.append({"role": "user", "content": results})  # all results, one message
        else:
            yield ChatEvent("error", text=f"stopped after {MAX_TOOL_ROUNDS} tool rounds")
        yield ChatEvent("done", meta={"model": self.model, **self.usage})

    def _count(self, response: Any) -> None:
        u = getattr(response, "usage", None)
        if u is None:
            return
        for key in self.usage:
            self.usage[key] += getattr(u, key, 0) or 0

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()
        self._client = None
