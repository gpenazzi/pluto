"""Yahoo Finance via the public chart endpoint (no auth, no crumb).

Verified 2026-09-09: `/v8/finance/chart/{symbol}` returns meta with regularMarketPrice,
currency, regularMarketTime, previousClose. `/v7/finance/quote` needs a crumb and is not used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from pluto.core.model import FxRate, Instrument, Quote
from pluto.market.providers.base import get_json
from pluto.market.types import Candidate, NoQuote, ProviderError

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) pluto/0.1", "Accept": "application/json"}


def _normalise(price: Decimal, currency: str) -> tuple[Decimal, str]:
    """Pence and cents quoted symbols (GBp, ZAc, ILA) -> major unit."""
    if currency in ("GBp", "GBX"):
        return price / 100, "GBP"
    if currency == "ZAc":
        return price / 100, "ZAR"
    if currency == "ILA":
        return price / 100, "ILS"
    return price, currency


class YahooProvider:
    name = "yahoo"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    def supports(self, instrument: Instrument) -> bool:
        return bool(instrument.listings)

    async def chart_meta(self, symbol: str) -> dict[str, Any]:
        url = CHART_URL.format(symbol=symbol)
        try:
            data = await get_json(
                self.client, url, params={"range": "1d", "interval": "5m"}, headers=HEADERS
            )
        except ProviderError as e:
            if getattr(e, "status", None) == 404:
                raise NoQuote(f"yahoo: no data for {symbol}") from None
            raise
        chart = data.get("chart") or {}
        if chart.get("error") or not chart.get("result"):
            raise NoQuote(f"yahoo: {symbol}: {chart.get('error') or 'empty result'}")
        meta = chart["result"][0].get("meta") or {}
        if meta.get("regularMarketPrice") is None:
            raise NoQuote(f"yahoo: {symbol}: no regularMarketPrice")
        return meta

    def _quote_from_meta(self, symbol: str, meta: dict[str, Any]) -> Quote:
        price, currency = _normalise(Decimal(str(meta["regularMarketPrice"])), meta["currency"])
        prev = meta.get("previousClose") or meta.get("chartPreviousClose")
        prev_dec = _normalise(Decimal(str(prev)), meta["currency"])[0] if prev is not None else None
        ts = meta.get("regularMarketTime")
        as_of = datetime.fromtimestamp(ts, tz=UTC) if ts else datetime.now(UTC)
        return Quote(
            symbol=symbol,
            price=price,
            currency=currency,
            as_of=as_of,
            source=self.name,
            previous_close=prev_dec,
        )

    async def quote_symbol(self, symbol: str) -> Quote:
        return self._quote_from_meta(symbol, await self.chart_meta(symbol))

    async def quote(self, instrument: Instrument) -> Quote:
        last: Exception | None = None
        for symbol in instrument.symbols_in_order():
            try:
                q = await self.quote_symbol(symbol)
            except NoQuote as e:
                last = e
                continue
            listing = next((ls for ls in instrument.listings if ls.symbol == symbol), None)
            if listing and listing.currency != q.currency:
                # keep the quote but do not silently mix currencies: the valuation uses q.currency
                q.source = f"{self.name}({symbol}:{q.currency})"
            return q
        raise NoQuote(str(last) if last else f"yahoo: no symbols for {instrument.id}")

    async def rate(self, base: str, quote: str) -> FxRate:
        symbol = f"{base}{quote}=X"
        meta = await self.chart_meta(symbol)
        ts = meta.get("regularMarketTime")
        return FxRate(
            base=base,
            quote=quote,
            rate=Decimal(str(meta["regularMarketPrice"])),
            as_of=datetime.fromtimestamp(ts, tz=UTC) if ts else datetime.now(UTC),
            source=self.name,
        )

    async def search(self, query: str, limit: int = 10) -> list[Candidate]:
        data = await get_json(
            self.client,
            SEARCH_URL,
            params={"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0},
            headers=HEADERS,
        )
        out: list[Candidate] = []
        for q in data.get("quotes") or []:
            if not q.get("symbol"):
                continue
            out.append(
                Candidate(
                    symbol=q["symbol"],
                    exchange=q.get("exchange") or "?",
                    name=q.get("longname") or q.get("shortname"),
                    quote_type=q.get("quoteType"),
                    source=self.name,
                )
            )
        return out


async def yahoo_history(
    client: httpx.AsyncClient, symbol: str, start: date, end: date | None = None
) -> tuple[str, list[tuple[date, float, float]]]:
    """Daily bars (date, close, adjclose) for symbol from start to end. Returns the currency
    too. Bars with no close (holidays reported as null) are dropped."""
    from datetime import datetime as _dt
    from datetime import time as _time

    end = end or date.today()
    p1 = int(_dt.combine(start, _time(), tzinfo=UTC).timestamp())
    p2 = int(_dt.combine(end + timedelta(days=1), _time(), tzinfo=UTC).timestamp())
    url = CHART_URL.format(symbol=symbol)
    try:
        data = await get_json(
            client, url, params={"period1": p1, "period2": p2, "interval": "1d"}, headers=HEADERS
        )
    except ProviderError as e:
        if getattr(e, "status", None) == 404:
            raise NoQuote(f"yahoo: no history for {symbol}") from None
        raise
    chart = data.get("chart") or {}
    if chart.get("error") or not chart.get("result"):
        raise NoQuote(f"yahoo: {symbol}: {chart.get('error') or 'empty result'}")
    r = chart["result"][0]
    meta = r.get("meta") or {}
    ts = r.get("timestamp") or []
    quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    adj = (((r.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or closes
    _, currency = _normalise(Decimal(1), meta.get("currency") or "?")
    factor = 0.01 if meta.get("currency") in ("GBp", "GBX", "ZAc", "ILA") else 1.0
    bars: list[tuple[date, float, float]] = []
    for t, c, a in zip(ts, closes, adj, strict=False):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=UTC).date()
        bars.append((d, float(c) * factor, float(a if a is not None else c) * factor))
    return currency, bars
