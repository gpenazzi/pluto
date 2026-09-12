"""Daily price history with an on-disk cache. Same listing-order logic as quotes."""

from __future__ import annotations

import asyncio
import bisect
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from pluto.core.model import Instrument
from pluto.market.providers.yahoo import yahoo_history
from pluto.market.types import NoQuote, ProviderError
from pluto.store import paths


@dataclass
class PriceSeries:
    symbol: str
    currency: str
    dates: list[date] = field(default_factory=list)
    close: list[float] = field(default_factory=list)
    adjclose: list[float] = field(default_factory=list)

    def at(self, d: date, *, adjusted: bool = False) -> float | None:
        """Last available close on or before d (forward fill). None before the first bar."""
        i = bisect.bisect_right(self.dates, d) - 1
        if i < 0:
            return None
        return (self.adjclose if adjusted else self.close)[i]

    @property
    def first(self) -> date | None:
        return self.dates[0] if self.dates else None

    @property
    def last(self) -> date | None:
        return self.dates[-1] if self.dates else None

    def merge(self, bars: list[tuple[date, float, float]]) -> None:
        by = dict(zip(self.dates, zip(self.close, self.adjclose, strict=True), strict=True))
        for d, c, a in bars:
            by[d] = (c, a)
        self.dates = sorted(by)
        self.close = [by[d][0] for d in self.dates]
        self.adjclose = [by[d][1] for d in self.dates]

    def to_json(self) -> str:
        return json.dumps(
            {
                "symbol": self.symbol,
                "currency": self.currency,
                "bars": [
                    [d.isoformat(), c, a]
                    for d, c, a in zip(self.dates, self.close, self.adjclose, strict=True)
                ],
            }
        )

    @classmethod
    def from_json(cls, text: str) -> PriceSeries:
        d = json.loads(text)
        ps = cls(symbol=d["symbol"], currency=d["currency"])
        ps.merge([(date.fromisoformat(b[0]), float(b[1]), float(b[2])) for b in d["bars"]])
        return ps


def default_history_dir() -> Path:
    return paths.home() / "cache" / "history"


class HistoryService:
    def __init__(self, client: httpx.AsyncClient, cache_dir: Path | None, concurrency: int = 4):
        self.client = client
        self.cache_dir = cache_dir
        self._mem: dict[str, PriceSeries] = {}
        self._sem = asyncio.Semaphore(concurrency)
        # one lock per symbol: concurrent callers (performance and risk cards load at the
        # same time) share one download instead of hitting Yahoo twice for the same series
        self._locks: dict[str, asyncio.Lock] = {}

    # --- cache -----------------------------------------------------------------------
    def _path(self, symbol: str) -> Path | None:
        if self.cache_dir is None:
            return None
        safe = symbol.replace("=", "_").replace("/", "_")
        return self.cache_dir / f"{safe}.json"

    def _load(self, symbol: str) -> PriceSeries | None:
        if symbol in self._mem:
            return self._mem[symbol]
        p = self._path(symbol)
        if p and p.exists():
            try:
                ps = PriceSeries.from_json(p.read_text())
            except (ValueError, KeyError):
                return None
            self._mem[symbol] = ps
            return ps
        return None

    def _save(self, ps: PriceSeries) -> None:
        self._mem[ps.symbol] = ps
        p = self._path(ps.symbol)
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(ps.to_json())
            tmp.replace(p)

    # --- fetching --------------------------------------------------------------------
    async def symbol_series(self, symbol: str, start: date) -> PriceSeries:
        """Series for one symbol covering start..today, refreshed incrementally."""
        lock = self._locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            return await self._symbol_series(symbol, start)

    async def _symbol_series(self, symbol: str, start: date) -> PriceSeries:
        today = date.today()
        ps = self._load(symbol)
        need_from: date | None = None
        first, last = (ps.first, ps.last) if ps else (None, None)
        if ps is None or first is None or last is None or first > start + timedelta(days=7):
            need_from = start
        elif last < _last_business_day(today):
            need_from = last - timedelta(days=7)
        if need_from is not None:
            async with self._sem:
                currency, bars = await yahoo_history(self.client, symbol, need_from, today)
            if ps is None:
                ps = PriceSeries(symbol=symbol, currency=currency)
            ps.merge(bars)
            self._save(ps)
        assert ps is not None
        return ps

    async def series(self, instrument: Instrument, start: date) -> PriceSeries | None:
        """The preferred listing if its history covers `start`; otherwise the listing with
        the longest history (thin listings on Yahoo can hold a single bar)."""
        best: PriceSeries | None = None
        for sym in instrument.symbols_in_order():
            try:
                ps = await self.symbol_series(sym, start)
            except NoQuote:
                continue
            except ProviderError:
                ps_cached = self._load(sym)
                if ps_cached is None:
                    continue
                ps = ps_cached
            if not ps.dates:
                continue
            first = ps.first
            if first is not None and first <= start + timedelta(days=7):
                return ps
            if best is None or (
                first is not None and best.first is not None and first < best.first
            ):
                best = ps
        return best

    async def fx_series(self, base: str, quote: str, start: date) -> PriceSeries | None:
        if base == quote:
            return None
        try:
            return await self.symbol_series(f"{base}{quote}=X", start)
        except ProviderError:
            return self._load(f"{base}{quote}=X")

    async def for_portfolio(
        self, instruments: list[Instrument], base: str, start: date
    ) -> tuple[dict[str, PriceSeries], dict[str, PriceSeries], list[str]]:
        """Price series by instrument id, FX series by "CUR/BASE", ids without history."""
        results = await asyncio.gather(*(self.series(i, start) for i in instruments))
        prices = {i.id: ps for i, ps in zip(instruments, results, strict=True) if ps is not None}
        missing = [i.id for i, ps in zip(instruments, results, strict=True) if ps is None]
        currencies = {ps.currency for ps in prices.values()} | {i.currency for i in instruments}
        pairs = sorted(c for c in currencies if c != base)
        fx_results = await asyncio.gather(*(self.fx_series(c, base, start) for c in pairs))
        fx = {f"{c}/{base}": ps for c, ps in zip(pairs, fx_results, strict=True) if ps is not None}
        return prices, fx, missing


def _last_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def now_utc() -> datetime:
    return datetime.now(UTC)
