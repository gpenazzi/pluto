import math
from datetime import date, timedelta

from pluto.core.analytics import weekdays
from pluto.core.risk import returns_in_base, risk_report
from pluto.market.history import PriceSeries


def series(symbol: str, currency: str, start: date, closes: list[float]) -> PriceSeries:
    ps = PriceSeries(symbol=symbol, currency=currency)
    ps.merge([(start + timedelta(days=i), c, c) for i, c in enumerate(closes)])
    return ps


START = date(2026, 1, 5)
N = 120


def wave(amplitude: float, phase: float = 0.0, drift: float = 0.0) -> list[float]:
    return [100 + amplitude * math.sin(i / 3 + phase) + drift * i for i in range(N)]


def test_returns_in_base_converts_and_requires_full_history():
    dates = weekdays(START, START + timedelta(days=4))
    ps = series("U", "USD", START, [100, 110, 121, 121, 121])
    fx = {"USD/EUR": series("USDEUR=X", "EUR", START, [1.0, 1.0, 0.5, 0.5, 0.5])}
    r = returns_in_base(ps, dates, "EUR", fx)
    assert r is not None and [round(x, 4) for x in r] == [0.1, -0.45, 0.0, 0.0]
    assert returns_in_base(ps, weekdays(START - timedelta(days=5), START), "EUR", fx) is None


def test_risk_report_decomposes_variance_and_correlations():
    dates = weekdays(START, START + timedelta(days=N - 1))
    prices = {
        "A": series("A", "EUR", START, wave(5.0)),
        "B": series("B", "EUR", START, wave(5.0)),  # identical to A: correlation 1
        "C": series("C", "EUR", START, wave(5.0, phase=math.pi)),  # opposite: correlation -1
        "D": series("D", "EUR", START, [100.0] * N),  # flat: zero vol
    }
    bench = series("BM", "EUR", START, wave(2.5))
    q = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}
    rep = risk_report(q, "EUR", dates, prices, {}, benchmark=bench)
    assert rep is not None and rep.days == len(dates) and not rep.excluded
    ids = [h.instrument_id for h in rep.holdings]
    i, j, k = ids.index("A"), ids.index("B"), ids.index("C")
    assert abs(rep.correlation[i][j] - 1) < 1e-9 and abs(rep.correlation[i][k] + 1) < 0.01
    assert abs(sum(h.contribution for h in rep.holdings) - 1) < 1e-9
    assert abs(sum(h.weight for h in rep.holdings) - 1) < 1e-9
    d = next(h for h in rep.holdings if h.instrument_id == "D")
    assert d.volatility == 0.0 and abs(d.contribution) < 1e-12
    a = next(h for h in rep.holdings if h.instrument_id == "A")
    assert a.beta is not None and abs(a.beta - 2.0) < 0.05  # A moves twice the benchmark
    assert rep.diversification_ratio > 1  # A and C hedge each other
    assert rep.benchmark_volatility is not None and rep.benchmark_correlation is not None


def test_risk_report_excludes_short_history_and_needs_two_holdings():
    dates = weekdays(START, START + timedelta(days=N - 1))
    prices = {
        "A": series("A", "EUR", START, wave(5.0)),
        "B": series("B", "EUR", START, wave(3.0, 1.0)),
        "S": series("S", "EUR", START + timedelta(days=60), wave(3.0)[:60]),  # starts late
    }
    rep = risk_report({"A": 1, "B": 1, "S": 1}, "EUR", dates, prices, {})
    assert rep is not None and rep.excluded == ["S"] and rep.warnings
    assert risk_report({"A": 1, "S": 1}, "EUR", dates, prices, {}) is None
    assert risk_report({"A": 1, "B": 1}, "EUR", dates[:10], prices, {}) is None
