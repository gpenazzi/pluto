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
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from pluto.core.holdings import compute_holdings
from pluto.core.model import AssetClass, Portfolio, PortfolioError, Transaction, TxType
from pluto.market.exposure import (
    ExposureCache,
    ExposureService,
    default_exposure_path,
    look_through,
)
from pluto.market.history import HistoryService, default_history_dir
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
    history: HistoryService | None = None
    exposure: ExposureService | None = None
    source: str = "chat"

    @classmethod
    def create(cls, store: VersionStore, client: httpx.AsyncClient | None = None) -> ChatContext:
        svc = make_service(client)
        return cls(
            store=store,
            quotes=svc,
            resolver=InstrumentResolver(svc.client),
            history=HistoryService(svc.client, default_history_dir()),
            exposure=ExposureService(svc.client, ExposureCache(default_exposure_path())),
        )

    def exposure_service(self) -> ExposureService:
        if self.exposure is None:
            self.exposure = ExposureService(self.quotes.client, ExposureCache(None))
        return self.exposure

    def history_service(self) -> HistoryService:
        if self.history is None:
            self.history = HistoryService(self.quotes.client, None)
        return self.history

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
        "asset_class_confirmed": ins.asset_class_confirmed,
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
        "cash_tracked": p.tracks_cash(),
        "cash": {k: v.quantize(Decimal("0.01")) for k, v in h.cash.items() if v}
        if p.tracks_cash()
        else {},
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
        "cash_tracked": v.cash_tracked,
        "warnings": v.warnings,
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


class SetListingArgs(BaseModel):
    instrument: str = Field(description="ISIN, symbol or id of an instrument in the portfolio")
    symbol: str = Field(description="Yahoo symbol to quote from, e.g. SNPS or VWCE.MI")


async def set_instrument_listing(ctx: ChatContext, a: SetListingArgs) -> dict[str, Any]:
    """Quote an instrument from another listing (added and verified if unknown). The
    instrument's own currency and its transactions are untouched; valuation converts."""
    from pluto.core.model import Listing
    from pluto.market.types import ProviderError

    p = ctx.portfolio()
    ins = p.find_instrument(a.instrument)
    if ins is None:
        raise ToolError(f"unknown instrument {a.instrument!r}")
    sym = a.symbol.strip().upper()
    listing = next((ls for ls in ins.listings if ls.symbol.upper() == sym), None)
    if listing is None:
        try:
            q = await ctx.resolver.yahoo.quote_symbol(sym)
            meta = await ctx.resolver.yahoo.chart_meta(sym)
        except ProviderError as e:
            raise ToolError(f"{sym} returned no price: {e}") from None
        listing = Listing(symbol=sym, exchange=meta.get("exchangeName") or "?", currency=q.currency)
        ins.listings.append(listing)
    ins.preferred_symbol = listing.symbol
    info = ctx.store.commit(p, f"Quote {ins.name} from {listing.symbol}")
    q2 = await ctx.quotes.quote(ins, force=True)
    ctx.quotes.cache.save()
    return {
        "instrument": _instrument_summary(p, ins.id),
        "version": info.version,
        "quote": {
            "price": q2.price,
            "currency": q2.currency,
            "as_of": q2.as_of.isoformat(timespec="minutes"),
            "source": q2.source,
        }
        if q2
        else None,
        "note": (
            f"prices now come from {listing.symbol} in {listing.currency}; the instrument's "
            f"transaction currency stays {ins.currency} and values are converted"
            if listing.currency != ins.currency
            else None
        ),
    }


class SetAssetClassArgs(BaseModel):
    instrument: str = Field(description="ISIN, symbol or id of an instrument in the portfolio")
    asset_class: AssetClass = Field(
        description="equity, bond, money_market, commodity, real_estate, multi_asset or other"
    )


async def set_asset_class(ctx: ChatContext, a: SetAssetClassArgs) -> dict[str, Any]:
    """Set an instrument's asset class explicitly. Guesses from the name never overwrite it."""
    p = ctx.portfolio()
    ins = p.find_instrument(a.instrument)
    if ins is None:
        raise ToolError(f"unknown instrument {a.instrument!r}")
    ins.asset_class = a.asset_class
    ins.asset_class_confirmed = True
    info = ctx.store.commit(p, f"Classify {ins.name} as {a.asset_class.value}")
    return {"instrument": _instrument_summary(p, ins.id), "version": info.version}


async def reclassify(ctx: ChatContext, _: NoArgs) -> dict[str, Any]:
    """Re-guess the asset class of every instrument the user has not classified explicitly."""
    from pluto.market.resolver import guess_asset_class

    p = ctx.portfolio()
    changes = []
    for ins in p.instruments.values():
        if ins.asset_class_confirmed:
            continue
        new = guess_asset_class(ins.name, ins.asset_type)
        if new != ins.asset_class:
            changes.append({"instrument": ins.name, "from": ins.asset_class.value, "to": new.value})
            ins.asset_class = new
    version = (
        ctx.store.commit(p, f"Reclassify {len(changes)} instruments").version if changes else None
    )
    return {
        "changed": changes,
        "version": version,
        "classes": {
            ins.name: ins.asset_class.value + (" (user)" if ins.asset_class_confirmed else "")
            for ins in p.instruments.values()
        },
    }


DEFAULT_BENCHMARK = {"EUR": "VWCE.MI", "USD": "VT", "GBP": "VWRP.L", "CHF": "VWRL.SW"}
MAX_POINTS = 300
MAX_LOOKBACK_DAYS = 15 * 365  # "all" for the composition view
MAX_EXCLUDED_WEIGHT = 0.25  # backtest may leave out short-history holdings up to this share


class PerformanceArgs(BaseModel):
    period: str = Field("1y", description="1m, 3m, 6m, ytd, 1y, 3y, 5y or all")
    benchmark: str | None = Field(
        None,
        description="Yahoo symbol to compare with; default is a world equity ETF in the base currency",
    )


async def get_performance(ctx: ChatContext, a: PerformanceArgs) -> dict[str, Any]:
    """Performance and risk over a window: actual history (from transactions) and a backtest
    of today's composition, each against a benchmark invested the same way."""
    from pluto.core import analytics as an

    p = ctx.portfolio()
    if not p.transactions:
        raise ToolError("no transactions yet")
    today = date.today()
    earliest = min(t.date for t in p.transactions)
    start = an.period_start(a.period, today, earliest)
    # the composition backtest is bounded by price history only, not by the transactions
    lookback = an.period_start(a.period, today, today - timedelta(days=MAX_LOOKBACK_DAYS))
    fetch_from = min(start, lookback) - timedelta(days=10)
    hist = ctx.history_service()
    q = an.current_quantities(p)
    held_ids = set(q) | {t.instrument_id for t in p.transactions if t.instrument_id}
    instruments = [p.instrument(i) for i in held_ids if i in p.instruments]
    prices, fx, missing = await hist.for_portfolio(instruments, p.base_currency, fetch_from)
    bench_symbol = a.benchmark or p.benchmark or DEFAULT_BENCHMARK.get(p.base_currency, "VWCE.MI")
    bench = None
    try:
        bench = await hist.symbol_series(bench_symbol, fetch_from)
    except Exception:
        bench = None
    if (
        bench is not None
        and bench.currency != p.base_currency
        and f"{bench.currency}/{p.base_currency}" not in fx
    ):
        fxs = await hist.fx_series(bench.currency, p.base_currency, fetch_from)
        if fxs is not None:
            fx = {**fx, f"{bench.currency}/{p.base_currency}": fxs}
    # Holdings whose history does not reach the window are left out of the backtest, but
    # only while they add up to a small share of the portfolio (a thin listing with a few
    # bars must not shrink the window for everything else). Beyond that share the window
    # shrinks to what the remaining holdings support.
    grace = lookback + timedelta(days=30)
    weights = _current_weights(p, q, prices, fx, today)
    q_comp: dict[str, float] = {}
    excluded: list[dict[str, Any]] = []
    left_out = 0.0
    with_history = []
    for ins_id in q:
        ps = prices.get(ins_id)
        if ps is None or ps.first is None:
            excluded.append(
                {
                    "instrument_id": ins_id,
                    "name": p.instrument(ins_id).name,
                    "history_from": None,
                    "weight_pct": an.as_decimal(weights.get(ins_id, 0.0) * 100, 1),
                }
            )
            left_out += weights.get(ins_id, 0.0)
        else:
            with_history.append((ps.first, ins_id))
    ordered = sorted(with_history, reverse=True)  # shortest history first
    for k, (first, ins_id) in enumerate(ordered):
        w = weights.get(ins_id, 0.0)
        rest_max = ordered[k + 1][0] if k + 1 < len(ordered) else None
        # leaving this one out only helps if it actually extends the window
        helps = rest_max is not None and first > rest_max
        if first > grace and helps and left_out + w <= MAX_EXCLUDED_WEIGHT:
            excluded.append(
                {
                    "instrument_id": ins_id,
                    "name": p.instrument(ins_id).name,
                    "history_from": first.isoformat(),
                    "weight_pct": an.as_decimal(w * 100, 1),
                }
            )
            left_out += w
        else:
            for _, rest_id in ordered[k:]:
                q_comp[rest_id] = q[rest_id]
            break
    comp_start = max([lookback, *[f for f, i in with_history if i in q_comp]])
    limiter = next((i for f, i in with_history if i in q_comp and f == comp_start), None)

    # actual history; the first date is the baseline day, whose value is the start value
    dates = [an.baseline(start), *an.weekdays(start, today)]
    actual_vs = an.value_series(p, dates, prices, fx)
    actual = _metrics_dict(an.metrics(actual_vs))
    actual_bench = an.benchmark_series(bench, actual_vs, p.base_currency, fx) if bench else None
    actual["series"] = _downsample(actual_vs.dates, actual_vs.values, actual_bench)
    actual["days"] = len(dates) - 1
    if (today - earliest).days < 30:
        actual["note"] = (
            f"only {(today - earliest).days} days since the first recorded transaction; "
            "the composition view shows how today's holdings behaved historically"
        )

    # composition backtest
    c_dates = [an.baseline(comp_start), *an.weekdays(comp_start, today)]
    comp_vs = an.composition_series(q_comp, p.base_currency, c_dates, prices, fx)
    comp = _metrics_dict(an.metrics(comp_vs))
    notes = []
    if comp_start > lookback and limiter is not None:
        notes.append(
            f"window starts {comp_start.isoformat()}: {p.instrument(limiter).name} has no "
            "earlier price history"
        )
    if excluded:
        comp["excluded"] = excluded
        notes.append(
            f"{len(excluded)} holding(s) worth {left_out * 100:.1f}% of the portfolio have no "
            f"price history back to {comp_start.isoformat()} and are left out of this backtest"
        )
    if notes:
        comp["note"] = "; ".join(notes)
    comp_bench = None
    if bench is not None and comp_vs.values and comp_vs.values[0] > 0:
        b0 = an.convert(
            bench.at(c_dates[0], adjusted=True) or 0.0,
            bench.currency,
            p.base_currency,
            fx,
            c_dates[0],
        )
        if b0:
            scale = comp_vs.values[0] / b0
            comp_bench = []
            for d in c_dates:
                bp = bench.at(d, adjusted=True)
                bv = (
                    an.convert(bp, bench.currency, p.base_currency, fx, d)
                    if bp is not None
                    else None
                )
                comp_bench.append(
                    bv * scale
                    if bv is not None
                    else (comp_bench[-1] if comp_bench else comp_vs.values[0])
                )
    comp["series"] = _downsample(comp_vs.dates, comp_vs.values, comp_bench)
    comp["contributions"] = [
        {
            "instrument_id": i,
            "name": p.instrument(i).name,
            "gain": an.as_decimal(g),
            "weight_pct": an.as_decimal(w * 100, 1),
        }
        for i, g, w in an.contributions(
            q_comp, p.base_currency, c_dates[0], c_dates[-1], prices, fx
        )
    ]
    comp["missing"] = sorted(comp_vs.missing)

    bench_out = None
    if bench is not None:
        b_idx = [bench.at(d, adjusted=True) for d in c_dates]
        vals = [x for x in b_idx if x is not None]
        bench_out = {
            "symbol": bench_symbol,
            "currency": bench.currency,
            "twr": an.as_decimal((vals[-1] / vals[0] - 1) * 100)
            if len(vals) > 1 and vals[0]
            else None,
        }
    return {
        "period": a.period,
        "base_currency": p.base_currency,
        "actual": actual,
        "composition": comp,
        "benchmark": bench_out,
        "missing_history": sorted(set(missing) | actual_vs.missing),
    }


def _current_weights(
    p: Portfolio, q: dict[str, float], prices: Any, fx: Any, today: date
) -> dict[str, float]:
    from pluto.core import analytics as an

    vals: dict[str, float] = {}
    for ins_id, qty in q.items():
        ps = prices.get(ins_id)
        px = ps.at(today) if ps else None
        v = (
            an.convert(qty * px, ps.currency, p.base_currency, fx, today)
            if (ps and px is not None)
            else None
        )
        if v is not None:
            vals[ins_id] = v
    total = sum(vals.values())
    return {k: v / total for k, v in vals.items()} if total else {}


def _metrics_dict(m: Any) -> dict[str, Any]:
    from pluto.core.analytics import as_decimal as dec

    pct = lambda x: dec(x * 100) if x is not None else None  # noqa: E731
    return {
        "start": m.start.isoformat(),
        "end": m.end.isoformat(),
        "start_value": dec(m.start_value),
        "end_value": dec(m.end_value),
        "net_flows": dec(m.net_flows),
        "gain": dec(m.gain),
        "twr_pct": pct(m.twr),
        "twr_annualized_pct": pct(m.twr_annualized),
        "mwr_annualized_pct": pct(m.mwr),
        "volatility_pct": pct(m.volatility),
        "max_drawdown_pct": pct(m.max_drawdown),
        "drawdown_from": m.drawdown_from.isoformat() if m.drawdown_from else None,
        "drawdown_to": m.drawdown_to.isoformat() if m.drawdown_to else None,
    }


def _downsample(
    dates: list[date], values: list[float], bench: list[float] | None
) -> list[list[Any]]:
    n = len(dates)
    step = max(1, -(-n // MAX_POINTS))
    idx = list(range(0, n, step))
    if n and idx[-1] != n - 1:
        idx.append(n - 1)
    return [
        [dates[i].isoformat(), round(values[i], 2), round(bench[i], 2) if bench else None]
        for i in idx
    ]


class ExposureArgs(BaseModel):
    refresh: bool = Field(False, description="Re-fetch holdings data instead of using the cache")


async def get_exposure(ctx: ChatContext, a: ExposureArgs) -> dict[str, Any]:
    """Look-through allocation by region, country and sector. Weights are current market
    values; holdings without data are listed with the reason. `available` is False only
    when no holding could be looked through at all."""
    import asyncio

    p = ctx.portfolio()
    v = await ctx.quotes.value(p)
    valued = [vp for vp in v.positions if vp.market_value is not None and vp.market_value > 0]
    if not valued:
        return {"available": False, "reason": "no priced holdings to look through"}
    total = sum(vp.market_value for vp in valued if vp.market_value is not None)
    weights = {vp.instrument.id: float(vp.market_value / total) for vp in valued if vp.market_value}
    svc = ctx.exposure_service()
    results = await asyncio.gather(*(svc.get(vp.instrument, refresh=a.refresh) for vp in valued))
    exposures = {vp.instrument.id: r for vp, r in zip(valued, results, strict=True)}
    lt = look_through(weights, p.instruments, exposures)
    any_data = any(r.exposure and (r.exposure.countries or r.exposure.sectors) for r in results)
    failures = sorted({r.error for r in results if r.error})
    if not any_data and failures:
        return {
            "available": False,
            "reason": "I was unable to retrieve holdings data for any instrument: "
            + "; ".join(failures),
        }
    rows = []
    for vp in valued:
        r = exposures[vp.instrument.id]
        e = r.exposure
        rows.append(
            {
                "instrument_id": vp.instrument.id,
                "name": vp.instrument.name,
                "weight_pct": round(weights[vp.instrument.id] * 100, 1),
                "source": e.source if e else None,
                "as_of": e.as_of.isoformat() if e and e.as_of else None,
                "countries": dict(list(e.countries.items())[:5]) if e else {},
                "sectors": dict(list(e.sectors.items())[:5]) if e else {},
                "note": (r.error or (e.note if e else None)),
            }
        )
    return {
        "available": True,
        "covered_pct": lt.covered_pct,
        "by_region": [{"label": k, "weight_pct": v_} for k, v_ in lt.by_region.items()],
        "by_country": [{"label": k, "weight_pct": v_} for k, v_ in lt.by_country.items()],
        "by_sector": [{"label": k, "weight_pct": v_} for k, v_ in lt.by_sector.items()],
        "unknown": lt.unknown,
        "notes": lt.notes,
        "instruments": rows,
    }


class SetExposureArgs(BaseModel):
    instrument: str = Field(description="ISIN, symbol or id of an instrument in the portfolio")
    countries: dict[str, float] | None = Field(
        None, description='Country -> percent, e.g. {"United States": 60, "Japan": 40}'
    )
    sectors: dict[str, float] | None = Field(None, description="Sector -> percent")


async def set_exposure(ctx: ChatContext, a: SetExposureArgs) -> dict[str, Any]:
    """Store a look-through breakdown by hand (or from the assistant's knowledge) when no
    source has it. Marked as user-provided and kept until refreshed explicitly."""
    from pluto.market.exposure import Exposure

    p = ctx.portfolio()
    ins = p.find_instrument(a.instrument)
    if ins is None:
        raise ToolError(f"unknown instrument {a.instrument!r}")
    if not a.countries and not a.sectors:
        raise ToolError("give countries and/or sectors")
    exp = Exposure(
        countries=a.countries or {}, sectors=a.sectors or {}, source="user", as_of=date.today()
    )
    ctx.exposure_service().cache.put(ins.id, exp)
    return {"instrument": ins.name, "exposure": exp.model_dump(mode="json")}


class RiskArgs(BaseModel):
    period: str = Field("1y", description="1m, 3m, 6m, ytd, 1y, 3y, 5y or all")
    benchmark: str | None = Field(None, description="Yahoo symbol; default world equity ETF")


async def get_risk(ctx: ChatContext, a: RiskArgs) -> dict[str, Any]:
    """Risk decomposition of today's holdings from daily history: portfolio volatility,
    each holding's volatility, share of portfolio variance and beta, correlation matrix."""
    from pluto.core import analytics as an
    from pluto.core.risk import risk_report

    p = ctx.portfolio()
    q = an.current_quantities(p)
    if len(q) < 2:
        return {"available": False, "reason": "risk decomposition needs at least two holdings"}
    today = date.today()
    start = an.period_start(a.period, today, today - timedelta(days=MAX_LOOKBACK_DAYS))
    hist = ctx.history_service()
    instruments = [p.instrument(i) for i in q]
    try:
        prices, fx, missing = await hist.for_portfolio(
            instruments, p.base_currency, start - timedelta(days=10)
        )
    except Exception as e:
        return {
            "available": False,
            "reason": f"I was unable to retrieve price history: {type(e).__name__}: {e}",
        }
    bench_symbol = a.benchmark or p.benchmark or DEFAULT_BENCHMARK.get(p.base_currency, "VWCE.MI")
    bench = None
    try:
        bench = await hist.symbol_series(bench_symbol, start - timedelta(days=10))
        if bench.currency != p.base_currency and f"{bench.currency}/{p.base_currency}" not in fx:
            fxs = await hist.fx_series(bench.currency, p.base_currency, start - timedelta(days=10))
            if fxs is not None:
                fx = {**fx, f"{bench.currency}/{p.base_currency}": fxs}
    except Exception:
        bench = None
    # window: from the latest first-date among holdings that reach at least 60% of it
    dates = an.weekdays(start, today)
    rep = risk_report(q, p.base_currency, dates, prices, fx, benchmark=bench)
    if rep is None:
        firsts = sorted((prices[i].first, i) for i in q if i in prices and prices[i].first)  # type: ignore[type-var]
        # shrink the window to what most holdings support and retry once
        if firsts:
            cut = firsts[len(firsts) // 2][0]
            if cut and cut > start:
                dates = an.weekdays(cut, today)
                rep = risk_report(q, p.base_currency, dates, prices, fx, benchmark=bench)
    if rep is None:
        return {
            "available": False,
            "reason": "I was unable to retrieve enough price history for at least two holdings "
            f"over this window (no history for: {', '.join(p.instrument(i).name for i in missing) or 'none missing'})",
        }
    pct = lambda x: an.as_decimal(x * 100) if x is not None else None  # noqa: E731
    return {
        "available": True,
        "start": rep.start.isoformat(),
        "end": rep.end.isoformat(),
        "days": rep.days,
        "portfolio_volatility_pct": pct(rep.portfolio_volatility),
        "diversification_ratio": an.as_decimal(rep.diversification_ratio),
        "benchmark": {
            "symbol": bench_symbol,
            "volatility_pct": pct(rep.benchmark_volatility),
            "correlation": an.as_decimal(rep.benchmark_correlation),
        }
        if bench is not None
        else None,
        "holdings": [
            {
                "instrument_id": h.instrument_id,
                "name": p.instrument(h.instrument_id).name,
                "weight_pct": pct(h.weight),
                "volatility_pct": pct(h.volatility),
                "contribution_pct": pct(h.contribution),
                "beta": an.as_decimal(h.beta),
            }
            for h in rep.holdings
        ],
        "correlation": {
            "ids": [h.instrument_id for h in rep.holdings],
            "names": [p.instrument(h.instrument_id).name for h in rep.holdings],
            "matrix": [[round(x, 2) for x in row] for row in rep.correlation],
        },
        "excluded": [{"instrument_id": i, "name": p.instrument(i).name} for i in rep.excluded],
        "warnings": rep.warnings,
    }


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
            "get_performance",
            "Performance and risk over a period (1m,3m,6m,ytd,1y,3y,5y,all): time- and money-weighted return, volatility, max drawdown, benchmark comparison, both for the actual history and for today's composition backtested.",
            PerformanceArgs,
            get_performance,
        ),
        ToolSpec(
            "get_exposure",
            "Look-through allocation by region, country and sector (from ETF holdings data). Reports which holdings lack data and why.",
            ExposureArgs,
            get_exposure,
        ),
        ToolSpec(
            "set_exposure",
            "Store a country/sector breakdown for an instrument by hand when no source provides it.",
            SetExposureArgs,
            set_exposure,
            read_only=False,
        ),
        ToolSpec(
            "get_risk",
            "Risk decomposition of today's holdings: portfolio volatility, per-holding volatility, contribution to variance, beta, correlation matrix, diversification ratio.",
            RiskArgs,
            get_risk,
        ),
        ToolSpec(
            "set_asset_class",
            "Set an instrument's asset class (equity, bond, money_market, commodity, real_estate, multi_asset, other) when the guessed one is wrong.",
            SetAssetClassArgs,
            set_asset_class,
            read_only=False,
        ),
        ToolSpec(
            "reclassify",
            "Re-guess asset classes from instrument names for instruments the user has not classified explicitly.",
            NoArgs,
            reclassify,
            read_only=False,
        ),
        ToolSpec(
            "set_instrument_listing",
            "Quote an instrument from a different listing, e.g. a US company from NASDAQ instead of a European exchange.",
            SetListingArgs,
            set_instrument_listing,
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
