"""FastAPI app: JSON API over the same tool registry the chat uses, plus the chat stream
and the built web UI. One portfolio, one user, one process."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from pluto.chat.prompt import system_prompt
from pluto.chat.providers import make_provider
from pluto.chat.providers.base import ChatEvent, ChatProvider
from pluto.chat.session import Transcript
from pluto.chat.tools import ChatContext, ToolRegistry
from pluto.store import paths
from pluto.store.versions import VersionStore

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class ChatIn(BaseModel):
    message: str


class AppState:
    def __init__(self, portfolio_name: str, provider_name: str | None, model: str | None):
        self.portfolio_name = portfolio_name
        self.store = VersionStore(paths.portfolio_dir(portfolio_name))
        self.ctx = ChatContext.create(self.store)
        self.registry = ToolRegistry(self.ctx)
        self.provider_name = provider_name
        self.model = model
        self.provider: ChatProvider | None = None
        self.chat_lock = asyncio.Lock()
        self.messages: list[dict[str, Any]] = []  # chat history shown by the UI
        self.transcript = Transcript(portfolio_name)

    async def get_provider(self) -> ChatProvider:
        if self.provider is None:
            p = self.store.load()
            self.provider = make_provider(
                self.registry,
                system_prompt(p, self.store.head()),
                self.provider_name,
                self.model,
                cwd=paths.home(),
            )
            await self.provider.start()
        return self.provider

    async def close(self) -> None:
        if self.provider is not None:
            await self.provider.close()
        await self.ctx.close()


def create_app(
    portfolio_name: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    static_dir: Path | None = WEB_DIST,
) -> FastAPI:
    name = portfolio_name or paths.default_portfolio()
    state = AppState(name, provider, model)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not state.store.exists():
            raise RuntimeError(f"no portfolio {name!r}; run `pluto demo` first")
        yield
        await state.close()

    app = FastAPI(title="Pluto", lifespan=lifespan)
    app.state.pluto = state

    async def call(tool: str, args: dict[str, Any] | None = None) -> Any:
        res = await state.registry.call(tool, args)
        if not res.ok:
            raise HTTPException(status_code=400, detail=res.data)
        return json.loads(res.as_text())  # Decimals -> strings, same shape the LLM sees

    # --- reads -----------------------------------------------------------------------
    @app.get("/api/portfolio")
    async def portfolio() -> Any:
        return await call("get_portfolio")

    @app.get("/api/valuation")
    async def valuation(force: bool = False) -> Any:
        data = await call("get_valuation", {"force_refresh": force})
        data["providers"] = [
            {
                "name": h.name,
                "failures": h.total_failures,
                "calls": h.total_calls,
                "last_error": h.last_error,
                "in_cooldown": not h.available(),
            }
            for h in state.ctx.quotes.health.values()
        ]
        return data

    @app.get("/api/transactions")
    async def transactions(last: int = 100, instrument: str | None = None) -> Any:
        return await call("list_transactions", {"last": last, "instrument": instrument})

    @app.get("/api/versions")
    async def versions() -> Any:
        return await call("list_versions")

    @app.get("/api/instruments/resolve")
    async def resolve(query: str | None = None, isin: str | None = None) -> Any:
        return await call("resolve_instrument", {"query": query, "isin": isin})

    # --- writes ----------------------------------------------------------------------
    @app.post("/api/transactions")
    async def add_transaction(body: dict[str, Any]) -> Any:
        state.ctx.source = "gui"
        try:
            return await call("add_transaction", body)
        finally:
            state.ctx.source = "chat"

    @app.delete("/api/transactions/{tx_id}")
    async def remove_transaction(tx_id: str) -> Any:
        return await call("remove_transaction", {"transaction_id": tx_id})

    @app.post("/api/instruments")
    async def add_instrument(body: dict[str, Any]) -> Any:
        return await call("add_instrument", body)

    @app.post("/api/versions/{version}/revert")
    async def revert(version: int) -> Any:
        return await call("revert_to_version", {"version": version})

    @app.post("/api/undo")
    async def undo() -> Any:
        return await call("undo")

    # --- chat ------------------------------------------------------------------------
    @app.get("/api/chat/messages")
    async def chat_messages() -> Any:
        return {
            "messages": state.messages,
            "busy": state.chat_lock.locked(),
            "provider": state.provider_name or "claude_agent",
        }

    @app.post("/api/chat")
    async def chat(body: ChatIn, request: Request) -> EventSourceResponse:
        if state.chat_lock.locked():
            raise HTTPException(status_code=409, detail="a message is already being processed")

        async def stream():
            async with state.chat_lock:
                state.messages.append({"role": "user", "text": body.message})
                state.transcript.user(body.message)
                try:
                    prov = await state.get_provider()
                    async for ev in prov.send(body.message):
                        state.transcript.event(ev)
                        rec = _event_record(ev)
                        if ev.type != "done":
                            state.messages.append(rec)
                        yield {"event": ev.type, "data": json.dumps(rec)}
                        if await request.is_disconnected():
                            break
                except Exception as e:
                    rec = {"role": "assistant", "type": "error", "text": f"{type(e).__name__}: {e}"}
                    state.messages.append(rec)
                    yield {"event": "error", "data": json.dumps(rec)}
                    yield {"event": "done", "data": "{}"}

        # "\n" separators: the browser-side parser splits on blank lines and the default
        # "\r\n" framing silently dropped every event.
        return EventSourceResponse(stream(), sep="\n")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=detail)

    if static_dir and static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def _event_record(ev: ChatEvent) -> dict[str, Any]:
    return {
        "role": "assistant",
        "type": ev.type,
        "text": ev.text,
        "name": ev.name,
        "args": ev.args,
        "ok": ev.ok,
        "meta": ev.meta,
    }
