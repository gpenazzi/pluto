from datetime import date, timedelta
from decimal import Decimal as D

from pluto.core.analytics import (
    baseline,
    benchmark_series,
    composition_series,
    contributions,
    current_quantities,
    external_flows,
    max_drawdown,
    metrics,
    period_start,
    twr_index,
    value_series,
    volatility,
    weekdays,
    xirr,
)
from pluto.core.model import AssetType, Instrument, Listing, Portfolio, Transaction, TxType
from pluto.market.history import PriceSeries


def series(symbol: str, currency: str, start: date, closes: list[float]) -> PriceSeries:
    ps = PriceSeries(symbol=symbol, currency=currency)
    ps.merge([(start + timedelta(days=i), c, c) for i, c in enumerate(closes)])
    return ps


def portfolio_with(*txs: Transaction, track_cash: bool | None = None) -> Portfolio:
    p = Portfolio(name="t", track_cash=track_cash)
    p.add_instrument(
        Instrument(
            id="A",
            name="A",
            asset_type=AssetType.ETF,
            currency="EUR",
            listings=[Listing(symbol="A", exchange="MIL", currency="EUR")],
        )
    )
    p.add_instrument(
        Instrument(
            id="U",
            name="U",
            asset_type=AssetType.STOCK,
            currency="USD",
            listings=[Listing(symbol="U", exchange="NMS", currency="USD")],
        )
    )
    for t in txs:
        p.add_transaction(t)
    return p


def buy(ins: str, q: str, px: str, d: date, cur: str = "EUR") -> Transaction:
    return Transaction(
        date=d, type=TxType.BUY, instrument_id=ins, quantity=D(q), price=D(px), currency=cur
    )


D0 = date(2026, 1, 5)  # a Monday


def test_weekdays_and_period_start():
    days = weekdays(date(2026, 1, 2), date(2026, 1, 6))  # Fri..Tue
    assert [d.isoformat() for d in days] == ["2026-01-02", "2026-01-05", "2026-01-06"]
    assert period_start("1y", date(2026, 9, 11), date(2020, 1, 1)) == date(2025, 9, 11)
    assert period_start("ytd", date(2026, 9, 11), date(2020, 1, 1)) == date(2026, 1, 1)
    assert period_start("all", date(2026, 9, 11), date(2020, 1, 1)) == date(2020, 1, 1)
    assert period_start("5y", date(2026, 9, 11), date(2024, 1, 1)) == date(2024, 1, 1)


def test_twr_ignores_flows_and_mwr_sees_them():
    # 100 -> 110 (+10%), then deposit 110 doubling the size, then 220 -> 231 (+5%)
    values = [100.0, 110.0, 220.0, 231.0]
    flows = [0.0, 0.0, 110.0, 0.0]
    idx = twr_index(values, flows)
    assert round(idx[-1], 6) == round(1.10 * 1.05, 6)
    assert twr_index([0.0, 1000.0, 1100.0], [0.0, 1000.0, 0.0])[-1] == 1.1  # born on day 1


def test_xirr_known_answer():
    r = xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1100.0)])
    assert r is not None and abs(r - 0.10) < 1e-6
    assert xirr([(date(2025, 1, 1), -1000.0)]) is None
    assert xirr([(date(2025, 1, 1), -1000.0), (date(2025, 1, 1), 1100.0)]) is None


def test_drawdown_and_volatility():
    dates = [D0 + timedelta(days=i) for i in range(5)]
    dd, frm, to = max_drawdown([100, 120, 90, 100, 130], dates)
    assert dd == 90 / 120 - 1 and frm == dates[1] and to == dates[2]
    assert volatility([1.0] * 10) is None  # too short
    flat = [1.0] * 40
    assert volatility(flat) == 0.0


def test_value_series_without_cash_tracking_uses_buys_as_flows():
    p = portfolio_with(
        buy("A", "10", "100", D0), buy("U", "5", "50", D0 + timedelta(days=1), "USD")
    )
    dates = [baseline(D0), *weekdays(D0, D0 + timedelta(days=2))]  # Fri baseline, Mon..Wed
    prices = {
        "A": series("A", "EUR", D0, [100, 110, 120]),
        "U": series("U", "USD", D0, [50, 50, 60]),
    }
    fx = {"USD/EUR": series("USDEUR=X", "EUR", D0, [0.5, 0.5, 0.5])}
    vs = value_series(p, dates, prices, fx)
    assert vs.values == [0.0, 1000.0, 1100.0 + 125.0, 1200.0 + 150.0]
    assert vs.flows == [0.0, 1000.0, 125.0, 0.0]  # buys converted to base on their date
    assert not vs.missing
    m = metrics(vs)
    assert m.start_value == 0.0 and m.net_flows == 1125.0 and m.gain == 1350.0 - 1125.0
    # day 1 has no previous value (no return); day 2: (1225 - 125) / 1000; day 3: 1350 / 1225
    assert m.twr is not None and abs(m.twr - (1.10 * 1350 / 1225 - 1)) < 1e-9
    assert m.mwr is None  # a two-day window cannot be annualized sensibly


def test_money_weighted_over_a_year():
    p = portfolio_with(buy("A", "10", "100", D0))
    end = D0 + timedelta(days=365)
    dates = [baseline(D0), D0, end]
    prices = {"A": series("A", "EUR", D0, [100.0] * 365 + [110.0])}
    m = metrics(value_series(p, dates, prices, {}))
    assert m.mwr is not None and abs(m.mwr - 0.10) < 1e-6
    assert m.twr is not None and abs(m.twr - 0.10) < 1e-9


def test_value_series_with_cash_tracking_uses_deposits_as_flows():
    dep = Transaction(date=D0, type=TxType.DEPOSIT, currency="EUR", amount=D("2000"))
    p = portfolio_with(dep, buy("A", "10", "100", D0 + timedelta(days=1)))
    dates = weekdays(D0, D0 + timedelta(days=2))  # baseline = deposit day
    prices = {"A": series("A", "EUR", D0, [100, 100, 150])}
    vs = value_series(p, dates, prices, {})
    assert vs.values == [2000.0, 2000.0, 2500.0]  # cash + positions
    assert vs.flows == [2000.0, 0.0, 0.0]
    m = metrics(vs)
    assert m.twr == 0.25 and m.start_value == 2000.0 and m.net_flows == 0.0 and m.gain == 500.0


def test_missing_prices_are_reported_not_crashed():
    p = portfolio_with(buy("A", "10", "100", D0))
    dates = weekdays(D0, D0 + timedelta(days=1))
    vs = value_series(p, dates, {}, {})
    assert vs.values == [0.0, 0.0] and vs.missing == {"A"}


def test_composition_backtest_and_contributions():
    p = portfolio_with(
        buy("A", "10", "100", D0 + timedelta(days=4)),
        buy("U", "4", "50", D0 + timedelta(days=4), "USD"),
    )
    dates = weekdays(D0, D0 + timedelta(days=4))
    prices = {
        "A": series("A", "EUR", D0, [100, 100, 100, 100, 120]),
        "U": series("U", "USD", D0, [50, 50, 50, 50, 50]),
    }
    fx = {"USD/EUR": series("USDEUR=X", "EUR", D0, [1.0] * 5)}
    q = current_quantities(p)
    assert q == {"A": 10.0, "U": 4.0}
    vs = composition_series(q, "EUR", dates, prices, fx)
    assert vs.values[0] == 1200.0 and vs.values[-1] == 1400.0 and all(f == 0 for f in vs.flows)
    m = metrics(vs)
    assert m.twr == 1400 / 1200 - 1 and m.mwr is None
    rows = contributions(q, "EUR", dates[0], dates[-1], prices, fx)
    assert rows[0] == ("A", 200.0, 1200 / 1400)


def test_benchmark_with_same_flows():
    p = portfolio_with(buy("A", "10", "100", D0), buy("A", "10", "100", D0 + timedelta(days=2)))
    dates = weekdays(D0, D0 + timedelta(days=2))  # baseline day already holds 1000
    prices = {"A": series("A", "EUR", D0, [100, 100, 100])}
    vs = value_series(p, dates, prices, {})
    bench = series("B", "EUR", D0, [10, 11, 12.1])
    b = benchmark_series(bench, vs, "EUR", {})
    # 1000 buys 100 units at 10; day 3: +1000 buys 82.64 units at 12.1 -> 182.64 * 12.1
    assert b is not None and round(b[0]) == 1000 and round(b[1]) == 1100 and round(b[2]) == 2210


def test_external_flows_roll_weekend_transactions_forward():
    sat = date(2026, 1, 3)
    p = portfolio_with(buy("A", "1", "100", sat))
    dates = weekdays(date(2026, 1, 2), date(2026, 1, 6))
    flows = external_flows(p, dates, {})
    assert flows == [0.0, 100.0, 0.0]
