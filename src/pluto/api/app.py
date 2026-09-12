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

from pluto import __version__
from pluto.chat.prompt import system_prompt
from pluto.chat.providers import make_provider
from pluto.chat.providers.base import ChatEvent, ChatProvider
from pluto.chat.session import Transcript
from pluto.chat.tools import ChatContext, ToolRegistry
from pluto.core.model import Portfolio
from pluto.store import paths
from pluto.store.versions import VersionStore

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class ChatIn(BaseModel):
    message: str


class PortfolioIn(BaseModel):
    name: str
    base_currency: str = "EUR"


class AppState:
    """One process, one current portfolio. Switching rebuilds the store, the chat context and
    the LLM session (its system prompt names the portfolio)."""

    def __init__(self, portfolio_name: str, provider_name: str | None, model: str | None):
        self.provider_name = provider_name
        self.model = model
        self.provider: ChatProvider | None = None
        self.chat_lock = asyncio.Lock()
        self.portfolio_name = portfolio_name
        self.store = VersionStore(paths.portfolio_dir(portfolio_name))
        self.ctx = ChatContext.create(self.store)
        self.registry = ToolRegistry(self.ctx)
        self.messages: list[dict[str, Any]] = []  # chat history shown by the UI
        self.transcript = Transcript(portfolio_name)

    async def switch(self, name: str) -> None:
        if name == self.portfolio_name and self.store.exists():
            return
        store = VersionStore(paths.portfolio_dir(name))
        if not store.exists():
            raise HTTPException(status_code=404, detail={"error": f"no portfolio {name!r}"})
        if self.provider is not None:
            await self.provider.close()
            self.provider = None
        self.portfolio_name = name
        self.store = store
        self.ctx.store = store  # registry and quote service are shared; only the store changes
        self.messages = []
        self.transcript = Transcript(name)
        paths.write_config({**paths.read_config(), "portfolio": name})

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


def _portfolio_row(name: str) -> dict[str, Any]:
    store = VersionStore(paths.portfolio_dir(name))
    rec = store.record()
    return {
        "name": name,
        "base_currency": rec.portfolio.base_currency,
        "version": rec.version,
        "instruments": len(rec.portfolio.instruments),
        "transactions": len(rec.portfolio.transactions),
    }


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

    app = FastAPI(title="Pluto", version=__version__, lifespan=lifespan)
    app.state.pluto = state

    async def call(tool: str, args: dict[str, Any] | None = None) -> Any:
        res = await state.registry.call(tool, args)
        if not res.ok:
            raise HTTPException(status_code=400, detail=res.data)
        return json.loads(res.as_text())  # Decimals -> strings, same shape the LLM sees

    @app.get("/api/version")
    async def version() -> Any:
        return {"version": __version__}

    # --- portfolios ------------------------------------------------------------------
    @app.get("/api/portfolios")
    async def portfolios() -> Any:
        return {
            "current": state.portfolio_name,
            "portfolios": [_portfolio_row(n) for n in paths.list_portfolios()],
        }

    @app.post("/api/portfolios")
    async def create_portfolio(body: PortfolioIn) -> Any:
        pname = body.name.strip()
        if not pname or "/" in pname or pname.startswith("."):
            raise HTTPException(status_code=400, detail={"error": "invalid portfolio name"})
        if pname in paths.list_portfolios():
            raise HTTPException(status_code=400, detail={"error": f"portfolio {pname!r} exists"})
        if state.chat_lock.locked():
            raise HTTPException(status_code=409, detail={"error": "chat is busy"})
        store = VersionStore(paths.portfolio_dir(pname))
        store.commit(
            Portfolio(name=pname, base_currency=body.base_currency.upper()), "Empty portfolio"
        )
        await state.switch(pname)
        return {"current": pname, "portfolio": _portfolio_row(pname)}

    @app.post("/api/portfolios/{pname}/select")
    async def select_portfolio(pname: str) -> Any:
        if state.chat_lock.locked():
            raise HTTPException(status_code=409, detail={"error": "chat is busy"})
        await state.switch(pname)
        return {"current": pname}

    @app.delete("/api/portfolios/{pname}")
    async def delete_portfolio(pname: str) -> Any:
        if state.chat_lock.locked():
            raise HTTPException(status_code=409, detail={"error": "chat is busy"})
        try:
            dest = paths.delete_portfolio(pname)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": str(e)}) from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": str(e)}) from None
        if pname == state.portfolio_name:
            await state.switch(paths.default_portfolio())
        return {"trashed_to": str(dest), "current": state.portfolio_name}

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

    @app.get("/api/performance")
    async def performance(period: str = "1y", benchmark: str | None = None) -> Any:
        return await call("get_performance", {"period": period, "benchmark": benchmark})

    @app.get("/api/exposure")
    async def exposure(refresh: bool = False) -> Any:
        return await call("get_exposure", {"refresh": refresh})

    @app.get("/api/risk")
    async def risk(period: str = "1y", benchmark: str | None = None) -> Any:
        return await call("get_risk", {"period": period, "benchmark": benchmark})

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

    @app.patch("/api/instruments/{instrument_id}/listing")
    async def set_listing(instrument_id: str, body: dict[str, Any]) -> Any:
        return await call("set_instrument_listing", {"instrument": instrument_id, **body})

    @app.patch("/api/instruments/{instrument_id}/asset-class")
    async def set_asset_class(instrument_id: str, body: dict[str, Any]) -> Any:
        return await call("set_asset_class", {"instrument": instrument_id, **body})

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
