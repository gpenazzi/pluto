"""QuoteService: provider chain + cache + health. The one place that decides what a price is."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from pluto.core.model import FxRate, Instrument, Portfolio, Quote
from pluto.core.valuation import Valuation, value_portfolio
from pluto.market.cache import QuoteCache
from pluto.market.providers.frankfurter import FrankfurterProvider
from pluto.market.providers.justetf import JustEtfProvider
from pluto.market.providers.yahoo import YahooProvider
from pluto.market.types import FxProvider, NoQuote, ProviderError, ProviderHealth, QuoteProvider
from pluto.store import paths


@dataclass
class DoctorRow:
    instrument_id: str
    provider: str
    ok: bool
    price: str | None
    currency: str | None
    as_of: datetime | None
    ms: int
    error: str | None


class QuoteService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        providers: Sequence[QuoteProvider] | None = None,
        fx_providers: Sequence[FxProvider] | None = None,
        cache: QuoteCache | None = None,
        ttl: timedelta = timedelta(seconds=60),
        concurrency: int = 6,
    ):
        self.client = client
        yahoo = YahooProvider(client)
        self.providers: list[QuoteProvider] = (
            list(providers) if providers is not None else [yahoo, JustEtfProvider(client)]
        )
        self.fx_providers: list[FxProvider] = (
            list(fx_providers) if fx_providers is not None else [yahoo, FrankfurterProvider(client)]
        )
        self.cache = cache if cache is not None else QuoteCache(None)
        self.ttl = ttl
        self.health: dict[str, ProviderHealth] = {}
        for p in [*self.providers, *self.fx_providers]:
            self.health.setdefault(p.name, ProviderHealth(p.name))
        self._sem = asyncio.Semaphore(concurrency)

    # --- quotes ----------------------------------------------------------------------
    async def quote(self, instrument: Instrument, *, force: bool = False) -> Quote | None:
        cached = self.cache.get_quote(instrument.id)
        now = datetime.now(UTC)
        if (
            cached
            and not force
            and now - cached.fetched_at < self.ttl
            and not cached.quote.is_stale
        ):
            return cached.quote
        async with self._sem:
            for provider in self.providers:
                if not provider.supports(instrument):
                    continue
                health = self.health[provider.name]
                if not health.available(now):
                    continue
                try:
                    q = await provider.quote(instrument)
                except NoQuote:
                    continue  # provider is fine, it just has nothing for this instrument
                except ProviderError as e:
                    health.record(False, str(e))
                    continue
                health.record(True)
                self.cache.put_quote(instrument.id, q)
                return q
        if cached:
            stale = cached.quote.model_copy(update={"is_stale": True})
            return stale
        return None

    async def quotes(
        self, instruments: Sequence[Instrument], *, force: bool = False
    ) -> dict[str, Quote]:
        results = await asyncio.gather(*(self.quote(i, force=force) for i in instruments))
        out = {i.id: q for i, q in zip(instruments, results, strict=True) if q is not None}
        self.cache.save()
        return out

    # --- fx --------------------------------------------------------------------------
    async def fx(self, base: str, quote: str, *, force: bool = False) -> FxRate | None:
        if base == quote:
            return None
        pair = f"{base}/{quote}"
        cached = self.cache.get_fx(pair)
        now = datetime.now(UTC)
        if cached and not force and now - cached.fetched_at < self.ttl and not cached.rate.is_stale:
            return cached.rate
        for provider in self.fx_providers:
            health = self.health[provider.name]
            if not health.available(now):
                continue
            try:
                r = await provider.rate(base, quote)
            except ProviderError as e:
                health.record(False, str(e))
                continue
            health.record(True)
            self.cache.put_fx(pair, r)
            return r
        if cached:
            return cached.rate.model_copy(update={"is_stale": True})
        return None

    async def fx_rates(self, currencies: set[str], base: str) -> dict[str, FxRate]:
        pairs = sorted(c for c in currencies if c != base)
        rates = await asyncio.gather(*(self.fx(c, base) for c in pairs))
        out = {f"{c}/{base}": r for c, r in zip(pairs, rates, strict=True) if r is not None}
        self.cache.save()
        return out

    # --- valuation -------------------------------------------------------------------
    async def value(self, portfolio: Portfolio, *, force: bool = False) -> Valuation:
        from pluto.core.holdings import compute_holdings

        holdings = compute_holdings(portfolio)
        held = [portfolio.instrument(p.instrument_id) for p in holdings.open_positions()]
        quotes = await self.quotes(held, force=force)
        currencies = {i.currency for i in held} | {q.currency for q in quotes.values()}
        currencies |= {c for c, v in holdings.cash.items() if v}
        fx = await self.fx_rates(currencies, portfolio.base_currency)
        return value_portfolio(portfolio, quotes, fx)

    # --- diagnostics -----------------------------------------------------------------
    async def doctor(self, instruments: Sequence[Instrument]) -> list[DoctorRow]:
        async def one(ins: Instrument, p: QuoteProvider) -> DoctorRow:
            if not p.supports(ins):
                return DoctorRow(ins.id, p.name, False, None, None, None, 0, "not supported")
            t0 = time.perf_counter()
            try:
                q = await p.quote(ins)
            except ProviderError as e:
                ms = int((time.perf_counter() - t0) * 1000)
                return DoctorRow(ins.id, p.name, False, None, None, None, ms, str(e))
            ms = int((time.perf_counter() - t0) * 1000)
            return DoctorRow(ins.id, p.name, True, str(q.price), q.currency, q.as_of, ms, None)

        rows = await asyncio.gather(*(one(i, p) for i in instruments for p in self.providers))
        return list(rows)


def default_cache_path() -> Path:
    return paths.home() / "cache" / "quotes.json"


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True)


def make_service(client: httpx.AsyncClient | None = None, **kw: object) -> QuoteService:
    return QuoteService(client or make_client(), cache=QuoteCache(default_cache_path()), **kw)  # type: ignore[arg-type]
