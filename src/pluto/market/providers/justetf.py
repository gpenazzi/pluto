"""justETF quote API: ISIN in, latest XETRA-ish quote out. ETFs only, ~15 min delayed.

Verified 2026-09-09: GET https://www.justetf.com/api/etfs/{isin}/quote?locale=en&currency=EUR
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

from pluto.core.model import AssetType, Instrument, Quote
from pluto.market.providers.base import get_json
from pluto.market.types import NoQuote

URL = "https://www.justetf.com/api/etfs/{isin}/quote"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) pluto/0.1", "Accept": "application/json"}


class JustEtfProvider:
    name = "justetf"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    def supports(self, instrument: Instrument) -> bool:
        return instrument.asset_type == AssetType.ETF and instrument.isin is not None

    async def quote(self, instrument: Instrument) -> Quote:
        assert instrument.isin
        data = await get_json(
            self.client,
            URL.format(isin=instrument.isin),
            params={"locale": "en", "currency": instrument.currency},
            headers=HEADERS,
        )
        latest = (data or {}).get("latestQuote") or {}
        if latest.get("raw") is None:
            raise NoQuote(f"justetf: no quote for {instrument.isin}")
        prev = (data.get("previousQuote") or {}).get("raw")
        day = data.get("latestQuoteDate")
        as_of = datetime.fromisoformat(day).replace(tzinfo=UTC) if day else datetime.now(UTC)
        venue = data.get("quoteTradingVenue") or "?"
        return Quote(
            symbol=f"{instrument.isin}@{venue}",
            price=Decimal(str(latest["raw"])),
            currency=instrument.currency,
            as_of=as_of,
            source=self.name,
            previous_close=Decimal(str(prev)) if prev is not None else None,
        )
