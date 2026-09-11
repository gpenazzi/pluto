from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from pluto.core.demo import demo_portfolio
from pluto.core.holdings import compute_holdings
from pluto.core.model import (
    AssetType,
    FxRate,
    Instrument,
    Listing,
    Portfolio,
    PortfolioError,
    Quote,
    Transaction,
    TxType,
)
from pluto.core.valuation import value_portfolio

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def vwce() -> Instrument:
    return Instrument(
        id="IE00BK5BQT80",
        isin="ie00bk5bqt80",
        name="VWCE",
        asset_type=AssetType.ETF,
        currency="EUR",
        listings=[Listing(symbol="VWCE.MI", exchange="MIL", currency="EUR")],
    )


def buy(ins: str, q: str, p: str, d: str = "2025-01-01", fees: str = "0", cur: str = "EUR"):
    return Transaction(
        date=date.fromisoformat(d),
        type=TxType.BUY,
        instrument_id=ins,
        quantity=D(q),
        price=D(p),
        currency=cur,
        fees=D(fees),
    )


def test_instrument_normalises_isin_and_preferred_symbol():
    ins = vwce()
    assert ins.isin == "IE00BK5BQT80"
    assert ins.preferred_symbol == "VWCE.MI"
    assert ins.symbols_in_order() == ["VWCE.MI"]


def test_transaction_validation():
    with pytest.raises(ValueError):
        Transaction(date=date(2025, 1, 1), type=TxType.BUY, currency="EUR")
    with pytest.raises(ValueError):
        Transaction(date=date(2025, 1, 1), type=TxType.DEPOSIT, currency="EUR", amount=D("-1"))


def test_find_instrument_by_isin_or_symbol():
    p = Portfolio(name="t")
    p.add_instrument(vwce())
    assert p.find_instrument("vwce.mi") is not None
    assert p.find_instrument("IE00BK5BQT80") is not None
    assert p.find_instrument("nope") is None


def test_add_instrument_is_idempotent():
    p = Portfolio(name="t")
    p.add_instrument(vwce())
    p.add_instrument(vwce())
    assert len(p.instruments) == 1


def test_average_cost_and_realized_pnl():
    p = Portfolio(name="t")
    p.add_instrument(vwce())
    p.add_transaction(buy("IE00BK5BQT80", "10", "100", "2025-01-01", fees="5"))
    p.add_transaction(buy("IE00BK5BQT80", "10", "120", "2025-02-01", fees="5"))
    p.add_transaction(
        Transaction(
            date=date(2025, 3, 1),
            type=TxType.SELL,
            instrument_id="IE00BK5BQT80",
            quantity=D("5"),
            price=D("130"),
            currency="EUR",
            fees=D("5"),
        )
    )
    pos = compute_holdings(p).positions["IE00BK5BQT80"]
    assert pos.quantity == D("15")
    assert pos.avg_cost == D("110.5")  # (1005 + 1205) / 20
    assert pos.cost_basis == D("1657.5")
    assert pos.realized_pnl == D("650") - D("5") - D("552.5")


def test_cannot_sell_more_than_held():
    p = Portfolio(name="t")
    p.add_instrument(vwce())
    p.add_transaction(buy("IE00BK5BQT80", "10", "100"))
    with pytest.raises(PortfolioError, match="cannot sell"):
        p.add_transaction(
            Transaction(
                date=date(2025, 6, 1),
                type=TxType.SELL,
                instrument_id="IE00BK5BQT80",
                quantity=D("11"),
                price=D("1"),
                currency="EUR",
            )
        )


def test_currency_mismatch_is_rejected():
    p = Portfolio(name="t")
    p.add_instrument(vwce())
    with pytest.raises(PortfolioError, match="priced in EUR"):
        p.add_transaction(buy("IE00BK5BQT80", "1", "1", cur="USD"))


def test_transactions_are_kept_sorted_by_date():
    p = Portfolio(name="t")
    p.add_instrument(vwce())
    p.add_transaction(buy("IE00BK5BQT80", "1", "1", "2025-05-01"))
    p.add_transaction(buy("IE00BK5BQT80", "1", "1", "2025-01-01"))
    assert [t.date.month for t in p.transactions] == [1, 5]


def test_cash_from_deposits_buys_and_dividends():
    h = compute_holdings(demo_portfolio())
    # EUR: 25000 - buys - fees + dividends
    assert h.cash["EUR"] > 0
    assert h.cash["USD"] == D("6000") - (D("15") * D("171.30") + 1) - (D("7") * D("418.50") + 1) + (
        D("2") * D("355") - 1
    )


def test_demo_portfolio_round_trips_through_json():
    p = demo_portfolio()
    again = Portfolio.model_validate_json(p.model_dump_json())
    assert again == p


def test_valuation_with_fx_and_missing_quotes():
    p = demo_portfolio()
    quotes = {
        "IE00BK5BQT80": Quote(
            symbol="VWCE.MI", price=D("166"), currency="EUR", as_of=NOW, source="t"
        ),
        "US0378331005": Quote(
            symbol="AAPL", price=D("200"), currency="USD", as_of=NOW, source="t", is_stale=True
        ),
    }
    fx = {"USD/EUR": FxRate(base="USD", quote="EUR", rate=D("0.5"), as_of=NOW, source="t")}
    v = value_portfolio(p, quotes, fx, as_of=NOW)
    by_id = {vp.instrument.id: vp for vp in v.positions}
    assert by_id["IE00BK5BQT80"].market_value == D("180") * D("166")
    assert by_id["US0378331005"].market_value == D("15") * D("200") * D("0.5")
    assert "US0378331005" in v.stale
    assert set(v.missing) == {"IE00B5BMR087", "IE00BDBRDM35", "US5949181045", "IT0003132476"}
    assert abs(sum((s.weight for s in v.breakdown("instrument")), D(0)) - 1) < D("0.0001")
    assert {s.label for s in v.breakdown("asset_type")} == {"etf", "stock", "cash"}
    assert v.total_value == sum(vp.market_value or 0 for vp in v.positions) + v.cash_value


def test_cash_ignored_until_a_deposit_exists_and_negative_cash_never_subtracted():
    p = Portfolio(name="t")
    p.add_instrument(vwce())
    p.add_transaction(buy("IE00BK5BQT80", "10", "100"))
    quotes = {
        "IE00BK5BQT80": Quote(
            symbol="VWCE.MI", price=D("120"), currency="EUR", as_of=NOW, source="t"
        )
    }
    v = value_portfolio(p, quotes, {}, as_of=NOW)
    assert not p.tracks_cash() and not v.cash_tracked
    assert v.cash == {} and v.cash_value == 0 and v.total_value == D("1200") and not v.warnings
    assert v.positions[0].weight == 1

    p.add_transaction(
        Transaction(date=date(2025, 1, 2), type=TxType.DEPOSIT, currency="EUR", amount=D("500"))
    )
    v = value_portfolio(p, quotes, {}, as_of=NOW)
    assert v.cash_tracked and v.cash == {"EUR": D("-500")}
    assert v.total_value == D("1200") and v.cash_value == 0
    assert v.warnings and "negative" in v.warnings[0]
    assert v.positions[0].weight == 1

    p.track_cash = False
    v = value_portfolio(p, quotes, {}, as_of=NOW)
    assert not v.cash_tracked and not v.warnings

    p.track_cash = True
    p.add_transaction(
        Transaction(date=date(2025, 1, 3), type=TxType.DEPOSIT, currency="EUR", amount=D("800"))
    )
    v = value_portfolio(p, quotes, {}, as_of=NOW)
    assert v.cash == {"EUR": D("300")} and v.total_value == D("1500") and not v.warnings
