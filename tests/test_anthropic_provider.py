"""AnthropicApiProvider against a fake SDK client: no network, no key."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from pluto.chat.providers import make_provider
from pluto.chat.providers.anthropic_api import AnthropicApiProvider
from pluto.chat.tools import ChatContext, ToolRegistry
from pluto.core.demo import demo_portfolio
from pluto.market.resolver import InstrumentResolver
from pluto.market.service import QuoteService
from pluto.store.versions import VersionStore


def msg(*blocks: Any, stop: Any = "end_turn") -> Message:
    return Message(
        id="m",
        type="message",
        role="assistant",
        model="fake",
        content=list(blocks),
        stop_reason=stop,
        usage=Usage(input_tokens=10, output_tokens=5),  # type: ignore[arg-type]
    )


class FakeStream:
    def __init__(self, message: Message):
        self.message = message

    async def get_final_message(self) -> Message:
        return self.message


class FakeMessages:
    def __init__(self, responses: list[Message]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    @asynccontextmanager
    async def stream(self, **kw: Any):
        self.calls.append({**kw, "messages": list(kw["messages"])})  # snapshot, the list mutates
        yield FakeStream(self.responses.pop(0))


class FakeClient:
    def __init__(self, responses: list[Message]):
        self.messages = FakeMessages(responses)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
async def registry(tmp_path: Path):
    store = VersionStore(tmp_path / "p")
    store.commit(demo_portfolio(), "Demo")
    async with httpx.AsyncClient() as client:
        ctx = ChatContext(
            store=store,
            quotes=QuoteService(client, ttl=timedelta(0)),
            resolver=InstrumentResolver(client),
        )
        yield ToolRegistry(ctx)


async def test_tool_loop_records_transaction_and_keeps_history(registry: ToolRegistry):
    fake = FakeClient(
        [
            msg(
                ToolUseBlock(
                    type="tool_use",
                    id="t1",
                    name="add_transaction",
                    input={"type": "buy", "instrument": "ENI.MI", "quantity": "10", "price": "24"},
                ),
                stop="tool_use",
            ),
            msg(TextBlock(type="text", text="Bought 10 Eni at 24 EUR. Version 2.")),
        ]
    )
    prov = AnthropicApiProvider(registry, "SYSTEM", model="claude-opus-5", client=fake)
    events = [e async for e in prov.send("bought 10 eni at 24")]
    assert [e.type for e in events] == ["tool_call", "tool_result", "text", "done"]
    assert events[1].ok and '"version": 2' in events[1].text
    assert events[-1].meta["model"] == "claude-opus-5" and events[-1].meta["input_tokens"] == 20
    assert registry.ctx.store.head() == 2

    # request shape: cached system block, tools from the registry, effort, full history
    first, second = fake.messages.calls
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in first["tools"]} == set(registry.names())
    assert first["output_config"] == {"effort": "medium"}
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert second["messages"][2]["content"][0]["tool_use_id"] == "t1"
    assert "is_error" not in second["messages"][2]["content"][0]

    # next turn carries the whole conversation
    fake.messages.responses.append(msg(TextBlock(type="text", text="Yes.")))
    events = [e async for e in prov.send("did that work?")]
    assert events[0].text == "Yes."
    assert [m["role"] for m in fake.messages.calls[-1]["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]


async def test_failed_tool_is_reported_as_error_result(registry: ToolRegistry):
    fake = FakeClient(
        [
            msg(
                ToolUseBlock(
                    type="tool_use",
                    id="t1",
                    name="add_transaction",
                    input={"type": "sell", "instrument": "AAPL", "quantity": "999", "price": "1"},
                ),
                stop="tool_use",
            ),
            msg(TextBlock(type="text", text="You only hold 15 Apple shares.")),
        ]
    )
    prov = AnthropicApiProvider(registry, "S", client=fake)
    events = [e async for e in prov.send("sell 999 apple")]
    assert events[1].type == "tool_result" and not events[1].ok
    assert fake.messages.calls[1]["messages"][2]["content"][0]["is_error"] is True
    assert registry.ctx.store.head() == 1


async def test_parallel_tool_calls_return_one_result_message(registry: ToolRegistry):
    fake = FakeClient(
        [
            msg(
                ToolUseBlock(type="tool_use", id="a", name="get_portfolio", input={}),
                ToolUseBlock(type="tool_use", id="b", name="list_versions", input={}),
                stop="tool_use",
            ),
            msg(TextBlock(type="text", text="done")),
        ]
    )
    prov = AnthropicApiProvider(registry, "S", client=fake)
    events = [e async for e in prov.send("status?")]
    assert [e.type for e in events] == [
        "tool_call",
        "tool_call",
        "tool_result",
        "tool_result",
        "text",
        "done",
    ]
    results = fake.messages.calls[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


async def test_api_errors_become_error_events(registry: ToolRegistry):
    import anthropic

    class Boom(FakeMessages):
        @asynccontextmanager
        async def stream(self, **kw: Any):
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "http://x"))  # type: ignore[arg-type]
            yield  # pragma: no cover

    fake = FakeClient([])
    fake.messages = Boom([])
    prov = AnthropicApiProvider(registry, "S", client=fake)
    events = [e async for e in prov.send("hi")]
    assert events[0].type == "error" and "connection error" in events[0].text
    await prov.close()
    assert fake.closed


def test_factory_selects_provider(registry: ToolRegistry, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLUTO_LLM_PROVIDER", "anthropic_api")
    assert make_provider(registry, "S").name == "anthropic_api"
    assert make_provider(registry, "S", name="claude_agent").name == "claude_agent"
    monkeypatch.setenv("PLUTO_LLM_PROVIDER", "nope")
    with pytest.raises(ValueError, match="unknown provider"):
        make_provider(registry, "S")
