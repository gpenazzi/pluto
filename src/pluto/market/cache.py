"""Last-known quotes and FX rates on disk, so an outage degrades to 'stale' not 'missing'."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from pluto.core.model import FxRate, Quote


class CachedQuote(BaseModel):
    fetched_at: datetime
    quote: Quote


class CachedFx(BaseModel):
    fetched_at: datetime
    rate: FxRate


class CacheData(BaseModel):
    quotes: dict[str, CachedQuote] = Field(default_factory=dict)  # by instrument id
    fx: dict[str, CachedFx] = Field(default_factory=dict)  # by "BASE/QUOTE"


class QuoteCache:
    def __init__(self, path: Path | None):
        self.path = path
        self.data = CacheData()
        if path and path.exists():
            try:
                self.data = CacheData.model_validate_json(path.read_text())
            except ValueError:
                self.data = CacheData()

    def get_quote(self, instrument_id: str) -> CachedQuote | None:
        return self.data.quotes.get(instrument_id)

    def put_quote(self, instrument_id: str, quote: Quote) -> None:
        self.data.quotes[instrument_id] = CachedQuote(fetched_at=datetime.now(UTC), quote=quote)

    def get_fx(self, pair: str) -> CachedFx | None:
        return self.data.fx.get(pair)

    def put_fx(self, pair: str, rate: FxRate) -> None:
        self.data.fx[pair] = CachedFx(fetched_at=datetime.now(UTC), rate=rate)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.data.model_dump_json(indent=1))
        tmp.replace(self.path)
