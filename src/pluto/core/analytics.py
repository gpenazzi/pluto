"""Performance and risk analytics. Pure: price series are inputs.

Two views:
- actual: what the portfolio was worth each day given its transactions (external cash flows
  removed for the time-weighted return, included for the money-weighted one);
- composition: how today's holdings would have behaved over the window (a static backtest on
  total-return prices), useful when transactions were recorded at their current state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from pluto.core.holdings import compute_holdings
from pluto.core.model import Portfolio, TxType

TRADING_DAYS = 252
PERIODS: dict[str, int | None] = {
    "1m": 30,
    "3m": 91,
    "6m": 182,
    "ytd": None,
    "1y": 365,
    "3y": 3 * 365,
    "5y": 5 * 365,
    "all": None,
}


class Series(Protocol):
    currency: str

    def at(self, d: date, *, adjusted: bool = False) -> float | None: ...


def weekdays(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    if end not in out and start <= end:
        out.append(end)
    return out


def period_start(period: str, today: date, earliest: date) -> date:
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}; one of {list(PERIODS)}")
    days = PERIODS[period]
    if period == "ytd":
        return max(earliest, date(today.year, 1, 1))
    if days is None:
        return earliest
    return max(earliest, today - timedelta(days=days))


# --- building blocks --------------------------------------------------------------------


def quantities_on(portfolio: Portfolio, d: date) -> dict[str, float]:
    q: dict[str, float] = {}
    for t in portfolio.transactions:
        if t.date > d or t.instrument_id is None or t.quantity is None:
            continue
        if t.type == TxType.BUY:
            q[t.instrument_id] = q.get(t.instrument_id, 0.0) + float(t.quantity)
        elif t.type == TxType.SELL:
            q[t.instrument_id] = q.get(t.instrument_id, 0.0) - float(t.quantity)
    return {k: v for k, v in q.items() if v > 1e-12}


def cash_on(portfolio: Portfolio, d: date) -> dict[str, float]:
    h = compute_holdings(portfolio, as_of=d)
    return {k: float(v) for k, v in h.cash.items() if v}


def convert(
    amount: float, currency: str, base: str, fx: Mapping[str, Series], d: date
) -> float | None:
    if currency == base:
        return amount
    s = fx.get(f"{currency}/{base}")
    rate = s.at(d) if s else None
    return amount * rate if rate is not None else None


@dataclass
class ValueSeries:
    dates: list[date]
    values: list[float]  # base currency, positions + tracked cash
    flows: list[float]  # external money in (+) / out (-) on that date, base currency
    missing: set[str] = field(default_factory=set)  # instrument ids with no price on some day


def external_flows(
    portfolio: Portfolio, dates: Sequence[date], fx: Mapping[str, Series]
) -> list[float]:
    """Money crossing the portfolio boundary. With cash tracked: deposits and withdrawals.
    Without: every buy is money in, every sell or dividend is money out."""
    base = portfolio.base_currency
    tracked = portfolio.tracks_cash()
    by_date: dict[date, float] = {}
    for t in portfolio.transactions:
        amt: float | None = None
        if tracked:
            if t.type == TxType.DEPOSIT:
                amt = float(t.gross)
            elif t.type == TxType.WITHDRAWAL:
                amt = -float(t.gross)
        else:
            if t.type == TxType.BUY:
                amt = float(t.gross + t.fees)
            elif t.type == TxType.SELL or t.type == TxType.DIVIDEND:
                amt = -float(t.gross - t.fees)
        if amt is None:
            continue
        conv = convert(amt, t.currency, base, fx, t.date)
        by_date[t.date] = by_date.get(t.date, 0.0) + (conv if conv is not None else 0.0)
    # flows on non-listed days (weekends) roll forward to the next listed date
    out = [0.0] * len(dates)
    j = 0
    for d in sorted(by_date):
        while j < len(dates) and dates[j] < d:
            j += 1
        idx = min(j, len(dates) - 1)
        out[idx] += by_date[d]
    return out


def value_series(
    portfolio: Portfolio,
    dates: Sequence[date],
    prices: Mapping[str, Series],
    fx: Mapping[str, Series],
) -> ValueSeries:
    base = portfolio.base_currency
    tracked = portfolio.tracks_cash()
    values: list[float] = []
    missing: set[str] = set()
    for d in dates:
        total = 0.0
        for ins_id, qty in quantities_on(portfolio, d).items():
            ps = prices.get(ins_id)
            px = ps.at(d) if ps else None
            v = convert(qty * px, ps.currency, base, fx, d) if (ps and px is not None) else None
            if v is None:
                missing.add(ins_id)
                continue
            total += v
        if tracked:
            for cur, amt in cash_on(portfolio, d).items():
                if amt > 0:
                    total += convert(amt, cur, base, fx, d) or 0.0
        values.append(total)
    return ValueSeries(list(dates), values, external_flows(portfolio, dates, fx), missing)


def composition_series(
    quantities: Mapping[str, float],
    base: str,
    dates: Sequence[date],
    prices: Mapping[str, Series],
    fx: Mapping[str, Series],
) -> ValueSeries:
    """Static backtest of a fixed set of holdings on total-return (adjusted) prices."""
    values: list[float] = []
    missing: set[str] = set()
    for d in dates:
        total = 0.0
        for ins_id, qty in quantities.items():
            ps = prices.get(ins_id)
            px = ps.at(d, adjusted=True) if ps else None
            v = convert(qty * px, ps.currency, base, fx, d) if (ps and px is not None) else None
            if v is None:
                missing.add(ins_id)
                continue
            total += v
        values.append(total)
    return ValueSeries(list(dates), values, [0.0] * len(dates), missing)


# --- metrics ----------------------------------------------------------------------------


def twr_index(values: Sequence[float], flows: Sequence[float]) -> list[float]:
    """Chain-linked time-weighted index, 1.0 on the baseline day. Flows are assumed at the
    start of their day, so day t's return is (V_t - F_t) / V_{t-1} - 1. A day whose
    previous value is zero (portfolio did not exist yet) contributes no return."""
    idx = [1.0]
    for i in range(1, len(values)):
        prev = values[i - 1]
        if prev <= 0:
            idx.append(idx[-1])
            continue
        r = (values[i] - flows[i]) / prev
        idx.append(idx[-1] * (r if r > 0 else 1.0))
    return idx


def baseline(start: date) -> date:
    """The weekday before the window start: the day whose value is the start value."""
    d = start - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def total_return(index: Sequence[float]) -> float | None:
    return index[-1] / index[0] - 1 if len(index) >= 2 and index[0] > 0 else None


def annualized(total: float | None, days: int) -> float | None:
    if total is None or days < 30:
        return None
    return (1 + total) ** (365 / days) - 1


def daily_returns(index: Sequence[float]) -> list[float]:
    return [index[i] / index[i - 1] - 1 for i in range(1, len(index)) if index[i - 1] > 0]


def volatility(index: Sequence[float]) -> float | None:
    r = daily_returns(index)
    if len(r) < 20:
        return None
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def max_drawdown(
    values: Sequence[float], dates: Sequence[date]
) -> tuple[float, date | None, date | None]:
    peak, peak_d = -math.inf, None
    worst, worst_from, worst_to = 0.0, None, None
    for v, d in zip(values, dates, strict=True):
        if v > peak:
            peak, peak_d = v, d
        if peak > 0:
            dd = v / peak - 1
            if dd < worst:
                worst, worst_from, worst_to = dd, peak_d, d
    return worst, worst_from, worst_to


def xirr(cashflows: Sequence[tuple[date, float]]) -> float | None:
    """Annualized money-weighted return. Investor convention: money in is negative,
    money out (and the final value) positive. Bisection on a bracketed root."""
    if len(cashflows) < 2:
        return None
    t0 = cashflows[0][0]
    if not (any(a < 0 for _, a in cashflows) and any(a > 0 for _, a in cashflows)):
        return None
    years = [(d - t0).days / 365.0 for d, _ in cashflows]
    if max(years) <= 0:
        return None

    def npv(rate: float) -> float:
        return sum(a / (1 + rate) ** y for (_, a), y in zip(cashflows, years, strict=True))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def money_weighted(vs: ValueSeries) -> float | None:
    """vs.dates[0] is the baseline day: its value is money in, its flows are already in it."""
    if len(vs.values) < 2:
        return None
    flows = [(d, -f) for d, f in zip(vs.dates[1:], vs.flows[1:], strict=True) if f]
    if vs.values[0] > 0:
        flows.insert(0, (vs.dates[0], -vs.values[0]))
    if not flows:
        return None
    flows.append((vs.dates[-1], vs.values[-1]))
    return xirr(flows)


def benchmark_series(
    bench: Series, vs: ValueSeries, base: str, fx: Mapping[str, Series]
) -> list[float] | None:
    """Same money, same dates, invested in the benchmark (total return)."""
    units = 0.0
    out: list[float] = []
    for i, d in enumerate(vs.dates):
        px = bench.at(d, adjusted=True)
        px_base = convert(px, bench.currency, base, fx, d) if px is not None else None
        if px_base is None or px_base <= 0:
            out.append(out[-1] if out else 0.0)
            continue
        # baseline day: the whole starting value is invested; later days: the external flows
        money = vs.values[0] if i == 0 else vs.flows[i]
        if money:
            units += money / px_base
        out.append(units * px_base)
    return out if any(out) else None


@dataclass
class Metrics:
    start: date
    end: date
    start_value: float
    end_value: float
    net_flows: float
    gain: float  # end - start - flows
    twr: float | None  # total over the window
    twr_annualized: float | None
    mwr: float | None  # money-weighted, annualized
    volatility: float | None
    max_drawdown: float | None
    drawdown_from: date | None
    drawdown_to: date | None
    index: list[float]


def metrics(vs: ValueSeries) -> Metrics:
    """vs.dates[0] is the baseline (the day before the window): its value is the start
    value and any flow on it is already inside that value."""
    idx = twr_index(vs.values, vs.flows)
    days = (vs.dates[-1] - vs.dates[0]).days if vs.dates else 0
    tr = total_return(idx)
    dd, d_from, d_to = max_drawdown(idx, vs.dates) if vs.dates else (0.0, None, None)
    start_value = vs.values[0] if vs.values else 0.0
    net_flows = sum(vs.flows[1:])
    end_value = vs.values[-1] if vs.values else 0.0
    return Metrics(
        start=vs.dates[0],
        end=vs.dates[-1],
        start_value=start_value,
        end_value=end_value,
        net_flows=net_flows,
        gain=end_value - start_value - net_flows,
        twr=tr,
        twr_annualized=annualized(tr, days),
        mwr=money_weighted(vs),
        volatility=volatility(idx),
        max_drawdown=dd if vs.dates else None,
        drawdown_from=d_from,
        drawdown_to=d_to,
        index=idx,
    )


def current_quantities(portfolio: Portfolio) -> dict[str, float]:
    return {
        p.instrument_id: float(p.quantity) for p in compute_holdings(portfolio).open_positions()
    }


def contributions(
    quantities: Mapping[str, float],
    base: str,
    start: date,
    end: date,
    prices: Mapping[str, Series],
    fx: Mapping[str, Series],
) -> list[tuple[str, float, float]]:
    """Per instrument: (id, gain in base over the window on adjusted prices, weight at end)."""
    out = []
    total_end = 0.0
    rows = []
    for ins_id, qty in quantities.items():
        ps = prices.get(ins_id)
        if not ps:
            continue
        p0, p1 = ps.at(start, adjusted=True), ps.at(end, adjusted=True)
        if p0 is None or p1 is None:
            continue
        v0 = convert(qty * p0, ps.currency, base, fx, start)
        v1 = convert(qty * p1, ps.currency, base, fx, end)
        if v0 is None or v1 is None:
            continue
        rows.append((ins_id, v1 - v0, v1))
        total_end += v1
    for ins_id, gain, v1 in rows:
        out.append((ins_id, gain, v1 / total_end if total_end else 0.0))
    out.sort(key=lambda r: -abs(r[1]))
    return out


def as_decimal(x: float | None, places: int = 2) -> Decimal | None:
    return None if x is None else Decimal(str(round(x, places)))
