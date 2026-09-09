"""The demo portfolio: a plausible European retail portfolio in EUR."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from pluto.core.model import (
    AssetClass,
    AssetType,
    Instrument,
    Listing,
    Portfolio,
    Transaction,
    TxType,
)

DEMO_INSTRUMENTS: list[Instrument] = [
    Instrument(
        id="IE00BK5BQT80",
        isin="IE00BK5BQT80",
        name="Vanguard FTSE All-World UCITS ETF (Acc)",
        asset_type=AssetType.ETF,
        asset_class=AssetClass.EQUITY,
        currency="EUR",
        listings=[
            Listing(symbol="VWCE.MI", exchange="MIL", currency="EUR"),
            Listing(symbol="VWCE.DE", exchange="GER", currency="EUR"),
            Listing(symbol="VWRA.L", exchange="LSE", currency="USD"),
        ],
        tags={"region": "world"},
    ),
    Instrument(
        id="IE00B5BMR087",
        isin="IE00B5BMR087",
        name="iShares Core S&P 500 UCITS ETF (Acc)",
        asset_type=AssetType.ETF,
        asset_class=AssetClass.EQUITY,
        currency="EUR",
        listings=[
            Listing(symbol="CSSPX.MI", exchange="MIL", currency="EUR"),
            Listing(symbol="SXR8.DE", exchange="GER", currency="EUR"),
            Listing(symbol="CSPX.L", exchange="LSE", currency="USD"),
        ],
        tags={"region": "us"},
    ),
    Instrument(
        id="IE00BDBRDM35",
        isin="IE00BDBRDM35",
        name="iShares Core Global Aggregate Bond UCITS ETF EUR Hedged (Acc)",
        asset_type=AssetType.ETF,
        asset_class=AssetClass.BOND,
        currency="EUR",
        listings=[
            Listing(symbol="AGGH.MI", exchange="MIL", currency="EUR"),
            Listing(symbol="AGGH.DE", exchange="GER", currency="EUR"),
        ],
        tags={"region": "world"},
    ),
    Instrument(
        id="US0378331005",
        isin="US0378331005",
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        currency="USD",
        listings=[Listing(symbol="AAPL", exchange="NMS", currency="USD")],
        tags={"region": "us", "sector": "technology"},
    ),
    Instrument(
        id="US5949181045",
        isin="US5949181045",
        name="Microsoft Corp.",
        asset_type=AssetType.STOCK,
        currency="USD",
        listings=[Listing(symbol="MSFT", exchange="NMS", currency="USD")],
        tags={"region": "us", "sector": "technology"},
    ),
    Instrument(
        id="IT0003132476",
        isin="IT0003132476",
        name="Eni S.p.A.",
        asset_type=AssetType.STOCK,
        currency="EUR",
        listings=[Listing(symbol="ENI.MI", exchange="MIL", currency="EUR")],
        tags={"region": "europe", "sector": "energy"},
    ),
]


def _tx(
    d: str, t: TxType, ins: str | None, q: str | None, p: str | None, cur: str, **kw
) -> Transaction:
    return Transaction(
        date=date.fromisoformat(d),
        type=t,
        instrument_id=ins,
        quantity=D(q) if q else None,
        price=D(p) if p else None,
        currency=cur,
        source="demo",
        **kw,
    )


def demo_portfolio(name: str = "demo") -> Portfolio:
    p = Portfolio(name=name, base_currency="EUR")
    for ins in DEMO_INSTRUMENTS:
        p.add_instrument(ins)
    B, S, DV, DP = TxType.BUY, TxType.SELL, TxType.DIVIDEND, TxType.DEPOSIT
    rows = [
        _tx("2024-01-08", DP, None, None, None, "EUR", amount=D("25000")),
        _tx("2024-01-10", B, "IE00BK5BQT80", "60", "108.40", "EUR", fees=D("2.95")),
        _tx("2024-01-10", B, "IE00B5BMR087", "25", "460.10", "EUR", fees=D("2.95")),
        _tx("2024-02-15", B, "IE00BDBRDM35", "600", "4.71", "EUR", fees=D("2.95")),
        _tx("2024-03-20", DP, None, None, None, "USD", amount=D("6000")),
        _tx("2024-03-21", B, "US0378331005", "15", "171.30", "USD", fees=D("1.00")),
        _tx("2024-03-21", B, "US5949181045", "7", "418.50", "USD", fees=D("1.00")),
        _tx("2024-06-01", DP, None, None, None, "EUR", amount=D("8000")),
        _tx("2024-06-03", B, "IT0003132476", "400", "14.60", "EUR", fees=D("2.95")),
        _tx("2024-07-05", B, "IE00BK5BQT80", "40", "117.20", "EUR", fees=D("2.95")),
        _tx("2024-09-20", DV, "IT0003132476", None, None, "EUR", amount=D("94.00")),
        _tx("2025-01-10", DP, None, None, None, "EUR", amount=D("8000")),
        _tx("2025-01-14", B, "IE00BK5BQT80", "50", "129.80", "EUR", fees=D("2.95")),
        _tx("2025-04-08", S, "US5949181045", "2", "355.00", "USD", fees=D("1.00")),
        _tx("2025-05-20", DV, "IT0003132476", None, None, "EUR", amount=D("100.00")),
        _tx("2025-09-01", DP, None, None, None, "EUR", amount=D("7000")),
        _tx("2025-09-11", B, "IE00B5BMR087", "10", "580.30", "EUR", fees=D("2.95")),
        _tx("2026-02-01", DP, None, None, None, "EUR", amount=D("5000")),
        _tx("2026-02-03", B, "IE00BK5BQT80", "30", "152.10", "EUR", fees=D("2.95")),
    ]
    for tx in rows:
        p.add_transaction(tx)
    return p
