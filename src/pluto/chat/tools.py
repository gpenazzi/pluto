# ruff: noqa: E501
"""Provider-agnostic tools the LLM can call. One registry, any backend.

Each tool has a pydantic input model (its JSON schema is what the LLM sees) and an async
handler that works on a ChatContext. Handlers return plain JSON-able dicts; the registry
wraps errors so a bad call never crashes the chat.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from pluto.core.holdings import compute_holdings
from pluto.core.model import Portfolio, PortfolioError, Transaction, TxType
from pluto.market.resolver import InstrumentResolver, ResolvedInstrument, is_isin
from pluto.market.service import QuoteService, make_service
from pluto.store.versions import StoreError, VersionStore


class ToolError(Exception):
    pass


@dataclass
class ChatContext:
    store: VersionStore
    quotes: QuoteService
    resolver: InstrumentResolver
    source: str = "chat"

    @classmethod
    def create(cls, store: VersionStore, client: httpx.AsyncClient | None = None) -> ChatContext:
        svc = make_service(client)
        return cls(store=store, quotes=svc, resolver=InstrumentResolver(svc.client))

    def portfolio(self) -> Portfolio:
        return self.store.load()

    async def close(self) -> None:
        await self.quotes.client.aclose()


@dataclass
class ToolResult:
    ok: bool
    data: Any

    def as_text(self) -> str:
        return json.dumps(self.data, default=_json_default, ensure_ascii=False)


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json")
    return str(o)


Handler = Callable[[ChatContext, Any], Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Handler
    read_only: bool = True

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return schema


class ToolRegistry:
    def __init__(self, ctx: ChatContext, specs: list[ToolSpec] | None = None):
        self.ctx = ctx
        self.specs: dict[str, ToolSpec] = {s.name: s for s in (specs or default_tools())}

    def names(self) -> list[str]:
        return list(self.specs)

    async def call(self, name: str, args: dict[str, Any] | None) -> ToolResult:
        spec = self.specs.get(name)
        if spec is None:
            return ToolResult(False, {"error": f"unknown tool {name!r}"})
        try:
            parsed = spec.input_model.model_validate(args or {})
        except ValidationError as e:
            return ToolResult(
                False, {"error": "invalid arguments", "details": e.errors(include_url=False)}
            )
        try:
            data = await spec.handler(self.ctx, parsed)
        except (ToolError, PortfolioError, StoreError, ValueError) as e:
            return ToolResult(False, {"error": str(e)})
        return ToolResult(True, data)


# --- helpers -----------------------------------------------------------------------------


def _dec(value: str | float | int | None, what: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ToolError(f"{what} is not a number: {value!r}") from None


def _date(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError(f"date must be YYYY-MM-DD, got {value!r}") from None


def _instrument_summary(p: Portfolio, ins_id: str) -> dict[str, Any]:
    ins = p.instrument(ins_id)
    return {
        "id": ins.id,
        "name": ins.name,
        "isin": ins.isin,
        "type": ins.asset_type.value,
        "asset_class": ins.asset_class.value,
        "currency": ins.currency,
        "symbols": ins.symbols_in_order(),
    }


def _tx_summary(p: Portfolio, tx: Transaction) -> dict[str, Any]:
    name = p.instruments[tx.instrument_id].name if tx.instrument_id in p.instruments else None
    return {
        "id": tx.id,
        "date": tx.date.isoformat(),
        "type": tx.type.value,
        "instrument_id": tx.instrument_id,
        "instrument": name,
        "quantity": tx.quantity,
        "price": tx.price,
        "amount": tx.amount,
        "currency": tx.currency,
        "fees": tx.fees,
        "note": tx.note or None,
        "source": tx.source,
    }


def _resolved_summary(m: ResolvedInstrument) -> dict[str, Any]:
    return {
        "name": m.name,
        "isin": m.isin,
        "type": m.asset_type.value,
        "asset_class": m.asset_class.value,
        "confidence": m.confidence,
        "preferred_symbol": m.preferred_symbol,
        "currency": m.currency,
        "listings": [
            {"symbol": ls.symbol, "exchange": ls.exchange, "currency": ls.currency}
            for ls in m.listings
        ],
        "notes": m.notes,
    }


# --- tool inputs and handlers --------------------------------------------------------------


class NoArgs(BaseModel):
    pass


async def get_portfolio(ctx: ChatContext, _: NoArgs) -> dict[str, Any]:
    p = ctx.portfolio()
    h = compute_holdings(p)
    return {
        "name": p.name,
        "base_currency": p.base_currency,
        "version": ctx.store.head(),
        "instruments": [_instrument_summary(p, i) for i in p.instruments],
        "positions": [
            {
                "instrument_id": pos.instrument_id,
                "name": p.instrument(pos.instrument_id).name,
                "quantity": pos.quantity,
                "avg_cost": pos.avg_cost.quantize(Decimal("0.0001")),
                "cost_basis": pos.cost_basis.quantize(Decimal("0.01")),
                "currency": p.instrument(pos.instrument_id).currency,
                "realized_pnl": pos.realized_pnl.quantize(Decimal("0.01")),
            }
            for pos in h.open_positions()
        ],
        "cash": {k: v.quantize(Decimal("0.01")) for k, v in h.cash.items() if v},
        "transactions_count": len(p.transactions),
    }


class ValuationArgs(BaseModel):
    force_refresh: bool = Field(False, description="Bypass the 60s quote cache")


async def get_valuation(ctx: ChatContext, a: ValuationArgs) -> dict[str, Any]:
    p = ctx.portfolio()
    v = await ctx.quotes.value(p, force=a.force_refresh)
    q2 = Decimal("0.01")
    return {
        "as_of": v.as_of.isoformat(timespec="minutes"),
        "base_currency": v.base_currency,
        "total_value": v.total_value.quantize(q2),
        "cash_value": v.cash_value.quantize(q2),
        "positions": [
            {
                "instrument_id": vp.instrument.id,
                "name": vp.instrument.name,
                "quantity": vp.position.quantity,
                "price": vp.quote.price if vp.quote else None,
                "price_currency": vp.quote.currency if vp.quote else None,
                "quote_source": vp.quote.source if vp.quote else None,
                "quote_time": vp.quote.as_of.isoformat(timespec="minutes") if vp.quote else None,
                "stale": vp.quote.is_stale if vp.quote else None,
                "day_change_pct": vp.quote.change_pct.quantize(q2)
                if vp.quote and vp.quote.change_pct is not None
                else None,
                "market_value": vp.market_value.quantize(q2)
                if vp.market_value is not None
                else None,
                "weight_pct": (vp.weight * 100).quantize(q2),
                "unrealized_pnl": vp.unrealized_pnl.quantize(q2)
                if vp.unrealized_pnl is not None
                else None,
                "unrealized_pnl_pct": vp.unrealized_pnl_pct.quantize(q2)
                if vp.unrealized_pnl_pct is not None
                else None,
            }
            for vp in v.positions
        ],
        "breakdown": {
            key: [
                {
                    "label": s.label,
                    "value": s.value.quantize(q2),
                    "weight_pct": (s.weight * 100).quantize(q2),
                }
                for s in v.breakdown(key)
            ]
            for key in ("asset_type", "asset_class", "currency")
        },
        "missing_prices": v.missing,
        "stale_prices": v.stale,
    }


class ResolveArgs(BaseModel):
    query: str | None = Field(None, description="Name, ticker or ISIN as the user wrote it")
    isin: str | None = Field(None, description="ISIN if known (12 characters)")


async def resolve_instrument(ctx: ChatContext, a: ResolveArgs) -> dict[str, Any]:
    if not a.query and not a.isin:
        raise ToolError("give a query or an isin")
    p = ctx.portfolio()
    known = p.find_instrument(a.isin or a.query or "")
    res = await ctx.resolver.resolve(a.query, a.isin, p.base_currency)
    u = res.unique
    return {
        "already_in_portfolio": _instrument_summary(p, known.id) if known else None,
        "unique": _resolved_summary(u) if u else None,
        "candidates": [_resolved_summary(m) for m in res.matches],
        "notes": res.notes,
        "hint": None
        if u
        else (
            "Ambiguous or unknown. Retry with the official fund name, or ask the user for the ISIN."
        ),
    }


class AddInstrumentArgs(BaseModel):
    isin: str | None = Field(None, description="Preferred: identifies the instrument uniquely")
    symbol: str | None = Field(
        None, description="A listing symbol chosen from resolve_instrument candidates, e.g. VWCE.MI"
    )


async def _resolve_new(
    ctx: ChatContext, p: Portfolio, *, isin: str | None = None, symbol: str | None = None
) -> tuple[ResolvedInstrument | None, list[ResolvedInstrument], list[str]]:
    """Resolve an instrument not yet in the portfolio. A symbol names one listing, so a
    unique match must carry it and it becomes the preferred listing."""
    if isin:
        res = await ctx.resolver.resolve(None, isin, p.base_currency)
    else:
        res = await ctx.resolver.resolve(symbol, None, p.base_currency)
        res.matches = [
            m
            for m in res.matches
            if any(ls.symbol.upper() == (symbol or "").upper() for ls in m.listings)
        ]
        if len(res.matches) == 1:
            res.matches[0].confidence = 0.9
    u = res.unique
    if u is not None and symbol:
        chosen = next(ls for ls in u.listings if ls.symbol.upper() == symbol.upper())
        u.preferred_symbol, u.currency = chosen.symbol, chosen.currency
    return u, res.matches, res.notes


async def add_instrument(ctx: ChatContext, a: AddInstrumentArgs) -> dict[str, Any]:
    p = ctx.portfolio()
    key = a.isin or a.symbol
    if not key:
        raise ToolError("give an isin or a symbol")
    known = p.find_instrument(key)
    if known:
        return {"added": False, "instrument": _instrument_summary(p, known.id)}
    u, matches, notes = await _resolve_new(ctx, p, isin=a.isin, symbol=a.symbol)
    if u is None:
        return {
            "added": False,
            "candidates": [_resolved_summary(m) for m in matches],
            "notes": notes,
        }
    ins = p.add_instrument(u.to_instrument())
    info = ctx.store.commit(p, f"Add instrument {ins.name}")
    return {"added": True, "instrument": _instrument_summary(p, ins.id), "version": info.version}


class AddTransactionArgs(BaseModel):
    type: TxType = Field(description="buy, sell, dividend, fee, deposit or withdrawal")
    instrument: str | None = Field(
        None,
        description="ISIN, symbol or id of an instrument in the portfolio (add_instrument first if missing). Not needed for deposit/withdrawal/fee.",
    )
    quantity: str | None = Field(None, description="Units, for buy/sell")
    price: str | None = Field(
        None, description="Price per unit in the instrument's currency, for buy/sell"
    )
    amount: str | None = Field(
        None, description="Total cash amount, for dividend/fee/deposit/withdrawal"
    )
    currency: str | None = Field(
        None, description="Defaults to the instrument's currency or the portfolio base currency"
    )
    date: str | None = Field(None, description="YYYY-MM-DD, defaults to today")
    fees: str | None = Field(None, description="Commission/fees for the trade, default 0")
    note: str | None = None


async def add_transaction(ctx: ChatContext, a: AddTransactionArgs) -> dict[str, Any]:
    p = ctx.portfolio()
    ins = None
    if a.instrument:
        ins = p.find_instrument(a.instrument)
        if ins is None:
            key = a.instrument.strip()
            u, matches, _ = await _resolve_new(
                ctx, p, **({"isin": key} if is_isin(key) else {"symbol": key})
            )
            if u is not None:
                ins = p.add_instrument(u.to_instrument())
            elif matches:
                raise ToolError(
                    f"{a.instrument!r} matches several instruments; use resolve_instrument"
                    " and add_instrument with the chosen listing symbol first"
                )
        if ins is None:
            raise ToolError(
                f"{a.instrument!r} is not in the portfolio and could not be resolved; "
                "call resolve_instrument first"
            )
    elif a.type in (TxType.BUY, TxType.SELL, TxType.DIVIDEND):
        raise ToolError(f"{a.type.value} needs an instrument")
    tx = Transaction(
        date=_date(a.date),
        type=a.type,
        instrument_id=ins.id if ins else None,
        quantity=_dec(a.quantity, "quantity"),
        price=_dec(a.price, "price"),
        amount=_dec(a.amount, "amount"),
        currency=(a.currency or (ins.currency if ins else p.base_currency)).upper(),
        fees=_dec(a.fees, "fees") or Decimal(0),
        note=a.note or "",
        source=ctx.source,
    )
    p.add_transaction(tx)
    info = ctx.store.commit(p, tx.describe(ins.name if ins else None))
    h = compute_holdings(p)
    pos = h.positions.get(ins.id) if ins else None
    return {
        "transaction": _tx_summary(p, tx),
        "version": info.version,
        "position_after": {
            "quantity": pos.quantity,
            "avg_cost": pos.avg_cost.quantize(Decimal("0.0001")),
        }
        if pos
        else None,
        "undo_hint": f"revert_to_version({info.version - 1}) undoes this",
    }


class ListTransactionsArgs(BaseModel):
    last: int = Field(20, description="How many most recent transactions to return", ge=1, le=500)
    instrument: str | None = Field(None, description="Filter by ISIN, symbol or id")


async def list_transactions(ctx: ChatContext, a: ListTransactionsArgs) -> dict[str, Any]:
    p = ctx.portfolio()
    txs = p.transactions
    if a.instrument:
        ins = p.find_instrument(a.instrument)
        if ins is None:
            raise ToolError(f"unknown instrument {a.instrument!r}")
        txs = [t for t in txs if t.instrument_id == ins.id]
    return {"total": len(txs), "transactions": [_tx_summary(p, t) for t in txs[-a.last :]]}


class RemoveTransactionArgs(BaseModel):
    transaction_id: str


async def remove_transaction(ctx: ChatContext, a: RemoveTransactionArgs) -> dict[str, Any]:
    p = ctx.portfolio()
    tx = p.remove_transaction(a.transaction_id)
    info = ctx.store.commit(p, f"Remove {tx.describe()}")
    return {"removed": _tx_summary(p, tx), "version": info.version}


class QuoteArgs(BaseModel):
    instrument: str = Field(description="ISIN, symbol or id of an instrument in the portfolio")


async def get_quote(ctx: ChatContext, a: QuoteArgs) -> dict[str, Any]:
    p = ctx.portfolio()
    ins = p.find_instrument(a.instrument)
    if ins is None:
        raise ToolError(f"unknown instrument {a.instrument!r}; resolve_instrument first")
    q = await ctx.quotes.quote(ins, force=True)
    ctx.quotes.cache.save()
    if q is None:
        return {"instrument": ins.name, "quote": None, "error": "no provider returned a price"}
    return {
        "instrument": ins.name,
        "symbol": q.symbol,
        "price": q.price,
        "currency": q.currency,
        "as_of": q.as_of.isoformat(timespec="minutes"),
        "source": q.source,
        "stale": q.is_stale,
        "previous_close": q.previous_close,
        "day_change_pct": q.change_pct.quantize(Decimal("0.01"))
        if q.change_pct is not None
        else None,
    }


async def list_versions(ctx: ChatContext, _: NoArgs) -> dict[str, Any]:
    head = ctx.store.head()
    return {
        "head": head,
        "versions": [
            {
                "version": v.version,
                "created_at": v.created_at.isoformat(timespec="seconds"),
                "message": v.message,
                "transactions": v.transactions,
                "reverted_from": v.reverted_from,
            }
            for v in ctx.store.history()[-30:]
        ],
    }


class RevertArgs(BaseModel):
    version: int = Field(description="Version number to go back to (see list_versions)")


async def revert_to_version(ctx: ChatContext, a: RevertArgs) -> dict[str, Any]:
    info = ctx.store.revert(a.version)
    return {"message": info.message, "version": info.version}


async def undo(ctx: ChatContext, _: NoArgs) -> dict[str, Any]:
    head = ctx.store.head()
    if head <= 1:
        raise ToolError("nothing to undo")
    info = ctx.store.revert(head - 1)
    return {"message": info.message, "version": info.version}


def default_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            "get_portfolio",
            "Instruments, positions (quantity, average cost) and cash of the portfolio. No market data.",
            NoArgs,
            get_portfolio,
        ),
        ToolSpec(
            "get_valuation",
            "Intraday valuation: prices, market values, weights, P&L, allocation breakdowns. Says which prices are missing or stale.",
            ValuationArgs,
            get_valuation,
        ),
        ToolSpec(
            "resolve_instrument",
            "Find an ETF or stock from a name, ticker or ISIN. Returns a unique match or candidates to ask about.",
            ResolveArgs,
            resolve_instrument,
        ),
        ToolSpec(
            "add_instrument",
            "Add an instrument to the portfolio by ISIN (preferred) or by a listing symbol chosen from resolve_instrument candidates.",
            AddInstrumentArgs,
            add_instrument,
            read_only=False,
        ),
        ToolSpec(
            "add_transaction",
            "Record a buy, sell, dividend, fee, deposit or withdrawal. Creates a new portfolio version.",
            AddTransactionArgs,
            add_transaction,
            read_only=False,
        ),
        ToolSpec(
            "list_transactions",
            "Most recent transactions, optionally for one instrument.",
            ListTransactionsArgs,
            list_transactions,
        ),
        ToolSpec(
            "remove_transaction",
            "Delete a transaction by id. Creates a new version.",
            RemoveTransactionArgs,
            remove_transaction,
            read_only=False,
        ),
        ToolSpec(
            "get_quote", "Fresh quote for one instrument in the portfolio.", QuoteArgs, get_quote
        ),
        ToolSpec("list_versions", "Version history of the portfolio.", NoArgs, list_versions),
        ToolSpec(
            "revert_to_version",
            "Go back to an earlier version. Nothing is deleted: a new version with the old content is created.",
            RevertArgs,
            revert_to_version,
            read_only=False,
        ),
        ToolSpec("undo", "Revert the most recent change.", NoArgs, undo, read_only=False),
    ]
