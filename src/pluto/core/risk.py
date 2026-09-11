"""Risk decomposition of the current holdings from daily price history. Pure."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from pluto.core.analytics import TRADING_DAYS, Series, convert


@dataclass
class HoldingRisk:
    instrument_id: str
    weight: float
    volatility: float  # annualized
    contribution: float  # share of portfolio variance (sums to 1 across holdings)
    beta: float | None  # vs the benchmark, None without one


@dataclass
class RiskReport:
    start: date
    end: date
    days: int
    portfolio_volatility: float
    holdings: list[HoldingRisk]
    correlation: list[list[float]]  # same order as holdings
    diversification_ratio: float  # weighted avg vol / portfolio vol (>1 = diversification)
    benchmark_volatility: float | None = None
    benchmark_correlation: float | None = None
    excluded: list[str] = field(default_factory=list)  # ids without enough history
    warnings: list[str] = field(default_factory=list)


def returns_in_base(
    series: Series, dates: Sequence[date], base: str, fx: Mapping[str, Series]
) -> list[float] | None:
    """Daily total returns in base currency on the given dates; None if any date lacks a price."""
    vals = []
    for d in dates:
        px = series.at(d, adjusted=True)
        v = convert(px, series.currency, base, fx, d) if px is not None else None
        if v is None or v <= 0:
            return None
        vals.append(v)
    return [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]


def _mean(x: Sequence[float]) -> float:
    return sum(x) / len(x)


def _cov(a: Sequence[float], b: Sequence[float]) -> float:
    ma, mb = _mean(a), _mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (len(a) - 1)


def risk_report(
    quantities: Mapping[str, float],
    base: str,
    dates: Sequence[date],
    prices: Mapping[str, Series],
    fx: Mapping[str, Series],
    benchmark: Series | None = None,
    min_days: int = 40,
) -> RiskReport | None:
    """Weights are the current market values (last date). Holdings without a full return
    history over the window are excluded and reported; None if fewer than two remain."""
    if len(dates) < min_days:
        return None
    end = dates[-1]
    rets: dict[str, list[float]] = {}
    values: dict[str, float] = {}
    excluded: list[str] = []
    for ins_id, qty in quantities.items():
        ps = prices.get(ins_id)
        r = returns_in_base(ps, dates, base, fx) if ps else None
        px = ps.at(end) if ps else None
        v = convert(qty * px, ps.currency, base, fx, end) if (ps and px is not None) else None
        if r is None or v is None:
            excluded.append(ins_id)
            continue
        rets[ins_id] = r
        values[ins_id] = v
    if len(rets) < 2:
        return None
    total = sum(values.values())
    ids = sorted(rets, key=lambda i: -values[i])
    w = {i: values[i] / total for i in ids}
    n = len(rets[ids[0]])
    port = [sum(w[i] * rets[i][k] for i in ids) for k in range(n)]
    var_p = _cov(port, port)
    vol_p = math.sqrt(var_p) * math.sqrt(TRADING_DAYS) if var_p > 0 else 0.0
    bench_r = returns_in_base(benchmark, dates, base, fx) if benchmark else None
    bench_var = _cov(bench_r, bench_r) if bench_r else None
    holdings: list[HoldingRisk] = []
    for i in ids:
        var_i = _cov(rets[i], rets[i])
        contrib = (w[i] * _cov(rets[i], port) / var_p) if var_p > 0 else 0.0
        beta = (_cov(rets[i], bench_r) / bench_var) if (bench_r and bench_var) else None
        holdings.append(
            HoldingRisk(i, w[i], math.sqrt(var_i) * math.sqrt(TRADING_DAYS), contrib, beta)
        )
    corr = []
    for a in ids:
        row = []
        for b in ids:
            va, vb = _cov(rets[a], rets[a]), _cov(rets[b], rets[b])
            row.append(_cov(rets[a], rets[b]) / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0)
        corr.append(row)
    avg_vol = sum(h.weight * h.volatility for h in holdings)
    warnings = []
    if excluded:
        warnings.append(
            f"{len(excluded)} holding(s) without full price history over the window are excluded"
        )
    return RiskReport(
        start=dates[0],
        end=end,
        days=len(dates),
        portfolio_volatility=vol_p,
        holdings=holdings,
        correlation=corr,
        diversification_ratio=(avg_vol / vol_p) if vol_p > 0 else 1.0,
        benchmark_volatility=(math.sqrt(bench_var) * math.sqrt(TRADING_DAYS))
        if bench_var
        else None,
        benchmark_correlation=(
            _cov(port, bench_r) / math.sqrt(var_p * bench_var)
            if (bench_r and bench_var and var_p > 0)
            else None
        ),
        excluded=excluded,
        warnings=warnings,
    )
