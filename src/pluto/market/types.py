"""Shared types for market data providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from pluto.core.model import FxRate, Instrument, Quote


class ProviderError(Exception):
    """The provider failed (network, HTTP error, unparsable body). Counts against health."""


class NoQuote(ProviderError):
    """The provider answered but has nothing for this instrument. Not a health failure."""


class QuoteProvider(Protocol):
    name: str

    def supports(self, instrument: Instrument) -> bool: ...

    async def quote(self, instrument: Instrument) -> Quote: ...


class FxProvider(Protocol):
    name: str

    async def rate(self, base: str, quote: str) -> FxRate: ...


@dataclass
class ProviderHealth:
    name: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_calls: int = 0
    last_error: str | None = None
    last_success: datetime | None = None
    cooldown_until: datetime | None = None
    history: list[bool] = field(default_factory=list)  # last N outcomes

    FAILURES_BEFORE_COOLDOWN = 3
    COOLDOWN_BASE_S = 60
    COOLDOWN_MAX_S = 900

    def available(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return self.cooldown_until is None or now >= self.cooldown_until

    def record(self, ok: bool, error: str | None = None) -> None:
        self.total_calls += 1
        self.history = [*self.history, ok][-20:]
        if ok:
            self.consecutive_failures = 0
            self.cooldown_until = None
            self.last_success = datetime.now(UTC)
            return
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_error = error
        if self.consecutive_failures >= self.FAILURES_BEFORE_COOLDOWN:
            n = self.consecutive_failures - self.FAILURES_BEFORE_COOLDOWN
            secs = min(self.COOLDOWN_BASE_S * 2**n, self.COOLDOWN_MAX_S)
            self.cooldown_until = datetime.now(UTC) + timedelta(seconds=secs)


class Candidate(BaseModel):
    """One listing found while resolving a user's description of an instrument."""

    symbol: str
    exchange: str
    currency: str | None = None
    name: str | None = None
    quote_type: str | None = None  # ETF | EQUITY | MUTUALFUND | ...
    isin: str | None = None
    source: str
