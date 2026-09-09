"""Market value, weights and allocation breakdowns. Pure: quotes and FX are inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from pluto.core.holdings import Position, compute_holdings
from pluto.core.model import ZERO, FxRate, Instrument, Portfolio, Quote

BreakdownKey = str  # "instrument" | "asset_type" | "asset_class" | "currency"


@dataclass
class ValuedPosition:
    instrument: Instrument
    position: Position
    quote: Quote | None
    fx: FxRate | None  # instrument currency -> base currency, None when same currency
    market_value: Decimal | None  # base currency
    cost_basis: Decimal | None  # base currency, converted at the current rate
    weight: Decimal = ZERO  # 0..1 of total portfolio value

    @property
    def unrealized_pnl(self) -> Decimal | None:
        if self.market_value is None or self.cost_basis is None:
            return None
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> Decimal | None:
        pnl = self.unrealized_pnl
        if pnl is None or not self.cost_basis:
            return None
        return pnl / self.cost_basis * 100


@dataclass
class Slice:
    label: str
    value: Decimal
    weight: Decimal


@dataclass
class Valuation:
    as_of: datetime
    base_currency: str
    positions: list[ValuedPosition]
    cash: dict[str, Decimal]  # per currency
    cash_value: Decimal  # base currency
    total_value: Decimal  # positions + cash, base currency
    missing: list[str] = field(default_factory=list)  # instrument ids without a price
    stale: list[str] = field(default_factory=list)  # instrument ids priced from cache

    def breakdown(self, key: BreakdownKey) -> list[Slice]:
        buckets: dict[str, Decimal] = {}
        for vp in self.positions:
            if vp.market_value is None:
                continue
            label = {
                "instrument": vp.instrument.name,
                "asset_type": vp.instrument.asset_type.value,
                "asset_class": vp.instrument.asset_class.value,
                "currency": vp.instrument.currency,
            }[key]
            buckets[label] = buckets.get(label, ZERO) + vp.market_value
        if self.cash_value > 0:
            label = {
                "instrument": "Cash",
                "asset_type": "cash",
                "asset_class": "cash",
                "currency": self.base_currency,
            }[key]
            buckets[label] = buckets.get(label, ZERO) + self.cash_value
        total = sum(buckets.values(), ZERO) or Decimal("1")
        slices = [Slice(k, v, v / total) for k, v in buckets.items()]
        slices.sort(key=lambda s: s.value, reverse=True)
        return slices


def convert(amount: Decimal, currency: str, base: str, fx: Mapping[str, FxRate]) -> Decimal | None:
    """Convert amount from currency to base using fx keyed by "CUR/BASE"."""
    if currency == base:
        return amount
    rate = fx.get(f"{currency}/{base}")
    return amount * rate.rate if rate else None


def value_portfolio(
    portfolio: Portfolio,
    quotes: Mapping[str, Quote],  # keyed by instrument id
    fx: Mapping[str, FxRate],  # keyed by "CUR/BASE"
    as_of: datetime | None = None,
) -> Valuation:
    base = portfolio.base_currency
    holdings = compute_holdings(portfolio)
    valued: list[ValuedPosition] = []
    missing: list[str] = []
    stale: list[str] = []
    for pos in holdings.open_positions():
        ins = portfolio.instrument(pos.instrument_id)
        quote = quotes.get(ins.id)
        rate = fx.get(f"{ins.currency}/{base}") if ins.currency != base else None
        mv = cb = None
        if quote is not None:
            mv = convert(pos.quantity * quote.price, quote.currency, base, fx)
            cb = convert(pos.cost_basis, ins.currency, base, fx)
            if mv is None:
                missing.append(ins.id)
            elif quote.is_stale:
                stale.append(ins.id)
        else:
            missing.append(ins.id)
        valued.append(ValuedPosition(ins, pos, quote, rate, mv, cb))

    cash_value = ZERO
    for cur, amt in holdings.cash.items():
        if amt == 0:
            continue
        c = convert(amt, cur, base, fx)
        if c is not None:
            cash_value += c
    total = sum((vp.market_value for vp in valued if vp.market_value is not None), ZERO)
    total += cash_value
    if total:
        for vp in valued:
            if vp.market_value is not None:
                vp.weight = vp.market_value / total
    valued.sort(key=lambda v: v.market_value or ZERO, reverse=True)
    return Valuation(
        as_of=as_of or datetime.now(UTC),
        base_currency=base,
        positions=valued,
        cash={k: v for k, v in holdings.cash.items() if v != 0},
        cash_value=cash_value,
        total_value=total,
        missing=missing,
        stale=stale,
    )
