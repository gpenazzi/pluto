"""The demo portfolio: a plausible European retail portfolio in EUR.

Eight holdings across four asset classes and two currencies, built up over three and a
half years of purchases so that every analytics view (performance, look-through, risk)
has something to show. Prices are the real closes on the transaction dates. There are no
deposits: cash is not tracked, so the pie shows holdings only.
"""

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
        asset_class=AssetClass.EQUITY,
        currency="USD",
        listings=[Listing(symbol="AAPL", exchange="NMS", currency="USD")],
        tags={"region": "us", "sector": "technology"},
    ),
    Instrument(
        id="US5949181045",
        isin="US5949181045",
        name="Microsoft Corp.",
        asset_type=AssetType.STOCK,
        asset_class=AssetClass.EQUITY,
        currency="USD",
        listings=[Listing(symbol="MSFT", exchange="NMS", currency="USD")],
        tags={"region": "us", "sector": "technology"},
    ),
    Instrument(
        id="IT0003132476",
        isin="IT0003132476",
        name="Eni S.p.A.",
        asset_type=AssetType.STOCK,
        asset_class=AssetClass.EQUITY,
        currency="EUR",
        listings=[Listing(symbol="ENI.MI", exchange="MIL", currency="EUR")],
        tags={"region": "europe", "sector": "energy"},
    ),
    Instrument(
        id="IE00B579F325",
        isin="IE00B579F325",
        name="Invesco Physical Gold ETC",
        asset_type=AssetType.ETF,
        asset_class=AssetClass.COMMODITY,
        asset_class_confirmed=True,
        currency="EUR",
        listings=[
            Listing(symbol="SGLD.MI", exchange="MIL", currency="EUR"),
            Listing(symbol="8PSG.DE", exchange="GER", currency="EUR"),
            Listing(symbol="SGLD.L", exchange="LSE", currency="USD"),
        ],
        tags={"sector": "gold"},
    ),
    Instrument(
        id="IE00B1FZS350",
        isin="IE00B1FZS350",
        name="iShares Developed Markets Property Yield UCITS ETF (Dist)",
        asset_type=AssetType.ETF,
        asset_class=AssetClass.REAL_ESTATE,
        asset_class_confirmed=True,
        currency="EUR",
        listings=[
            Listing(symbol="IWDP.MI", exchange="MIL", currency="EUR"),
            Listing(symbol="IQQ6.DE", exchange="GER", currency="EUR"),
        ],
        tags={"region": "world", "sector": "real_estate"},
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
    B, S, DV = TxType.BUY, TxType.SELL, TxType.DIVIDEND
    VWCE, CSSPX, AGGH = "IE00BK5BQT80", "IE00B5BMR087", "IE00BDBRDM35"
    AAPL, MSFT, ENI = "US0378331005", "US5949181045", "IT0003132476"
    GOLD, IWDP = "IE00B579F325", "IE00B1FZS350"
    etf, us = {"fees": D("2.95")}, {"fees": D("1.00")}
    # prices are the actual closes on those dates (Yahoo, Milan / Nasdaq)
    rows = [
        _tx("2023-01-16", B, VWCE, "60", "94.30", "EUR", **etf),
        _tx("2023-01-16", B, CSSPX, "15", "382.32", "EUR", **etf),
        _tx("2023-02-15", B, AGGH, "1500", "4.58", "EUR", **etf),
        _tx("2023-03-20", B, AAPL, "15", "157.40", "USD", **us),
        _tx("2023-03-20", B, MSFT, "7", "272.23", "USD", **us),
        _tx("2023-06-05", B, ENI, "400", "13.10", "EUR", **etf),
        _tx("2023-06-05", B, GOLD, "20", "176.49", "EUR", **etf),
        _tx("2023-09-11", B, IWDP, "250", "19.96", "EUR", **etf),
        _tx("2023-09-20", DV, ENI, None, None, "EUR", amount=D("94.00")),
        _tx("2024-01-15", B, VWCE, "40", "107.54", "EUR", **etf),
        _tx("2024-04-08", S, MSFT, "2", "424.59", "USD", **us),
        _tx("2024-05-22", DV, ENI, None, None, "EUR", amount=D("100.00")),
        _tx("2024-07-08", B, CSSPX, "10", "542.57", "EUR", **etf),
        _tx("2024-11-27", DV, IWDP, None, None, "EUR", amount=D("42.50")),
        _tx("2025-01-13", B, VWCE, "50", "133.47", "EUR", **etf),
        _tx("2025-05-21", DV, ENI, None, None, "EUR", amount=D("100.00")),
        _tx("2025-09-08", B, CSSPX, "10", "591.73", "EUR", **etf),
        _tx("2025-11-26", DV, IWDP, None, None, "EUR", amount=D("45.00")),
        _tx("2026-02-02", B, VWCE, "30", "148.50", "EUR", **etf),
        _tx("2026-05-20", DV, ENI, None, None, "EUR", amount=D("104.00")),
        _tx("2026-06-08", B, GOLD, "5", "361.39", "EUR", **etf),
    ]
    for tx in rows:
        p.add_transaction(tx)
    return p
