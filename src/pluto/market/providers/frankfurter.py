"""ECB reference rates via frankfurter.dev (daily, very reliable, no key)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

from pluto.core.model import FxRate
from pluto.market.providers.base import get_json
from pluto.market.types import ProviderError

URL = "https://api.frankfurter.dev/v1/latest"


class FrankfurterProvider:
    name = "frankfurter"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def rate(self, base: str, quote: str) -> FxRate:
        data = await get_json(self.client, URL, params={"base": base, "symbols": quote})
        rates = data.get("rates") or {}
        if quote not in rates:
            raise ProviderError(f"frankfurter: no rate {base}/{quote}")
        return FxRate(
            base=base,
            quote=quote,
            rate=Decimal(str(rates[quote])),
            as_of=datetime.fromisoformat(data["date"]).replace(tzinfo=UTC),
            source=self.name,
        )
