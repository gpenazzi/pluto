"""Domain model. Pure data, no I/O."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

ZERO = Decimal("0")


class AssetType(StrEnum):
    ETF = "etf"
    STOCK = "stock"


class AssetClass(StrEnum):
    EQUITY = "equity"
    BOND = "bond"
    MONEY_MARKET = "money_market"
    COMMODITY = "commodity"
    REAL_ESTATE = "real_estate"
    MULTI_ASSET = "multi_asset"
    OTHER = "other"  # nothing in the name says what it holds: better honest than "equity"


class Listing(BaseModel):
    """One tradable line of an instrument on one exchange."""

    symbol: str  # Yahoo-style symbol, e.g. "VWCE.MI"
    exchange: str  # short exchange code as used by Yahoo, e.g. "MIL", "GER", "LSE", "NMS"
    currency: str  # ISO 4217


class Instrument(BaseModel):
    id: str  # ISIN when known, else the preferred symbol
    name: str
    asset_type: AssetType
    asset_class: AssetClass = AssetClass.EQUITY
    asset_class_confirmed: bool = False  # True once the user set it; guesses never overwrite
    isin: str | None = None
    currency: str  # currency of the preferred listing (and of prices in transactions)
    listings: list[Listing] = Field(default_factory=list)
    preferred_symbol: str | None = None  # which listing to quote first
    tags: dict[str, str] = Field(default_factory=dict)  # region, sector, ...

    @model_validator(mode="after")
    def _check(self) -> Instrument:
        if self.isin is not None:
            self.isin = self.isin.upper().strip()
            if len(self.isin) != 12:
                raise ValueError(f"invalid ISIN {self.isin!r}")
        if self.preferred_symbol is None and self.listings:
            self.preferred_symbol = self.listings[0].symbol
        if self.preferred_symbol and self.listings:
            syms = {ls.symbol for ls in self.listings}
            if self.preferred_symbol not in syms:
                raise ValueError(f"preferred symbol {self.preferred_symbol} not in listings")
        return self

    @property
    def preferred_listing(self) -> Listing | None:
        for ls in self.listings:
            if ls.symbol == self.preferred_symbol:
                return ls
        return self.listings[0] if self.listings else None

    def symbols_in_order(self) -> list[str]:
        """Preferred symbol first, then the others: what a quote provider should try."""
        rest = [ls.symbol for ls in self.listings if ls.symbol != self.preferred_symbol]
        return ([self.preferred_symbol] if self.preferred_symbol else []) + rest


class TxType(StrEnum):
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    FEE = "fee"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Transaction(BaseModel):
    id: str = Field(default_factory=new_id)
    date: date
    type: TxType
    instrument_id: str | None = None
    quantity: Decimal | None = None  # units, for buy/sell
    price: Decimal | None = None  # per unit in `currency`, for buy/sell
    amount: Decimal | None = None  # total cash amount, for dividend/fee/deposit/withdrawal
    currency: str
    fees: Decimal = ZERO  # extra costs of a buy/sell, in `currency`
    note: str = ""
    source: str = "manual"  # chat | gui | cli | demo | import
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _check(self) -> Transaction:
        t = self.type
        if t in (TxType.BUY, TxType.SELL):
            if not self.instrument_id:
                raise ValueError(f"{t} needs instrument_id")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError(f"{t} needs quantity > 0")
            if self.price is None or self.price < 0:
                raise ValueError(f"{t} needs price >= 0")
        else:
            if self.amount is None or self.amount <= 0:
                raise ValueError(f"{t} needs amount > 0")
            if t == TxType.DIVIDEND and not self.instrument_id:
                raise ValueError("dividend needs instrument_id")
        if self.fees < 0:
            raise ValueError("fees must be >= 0")
        return self

    @property
    def gross(self) -> Decimal:
        """Cash moved by the transaction, before fees, in `currency`."""
        if self.type in (TxType.BUY, TxType.SELL):
            assert self.quantity is not None and self.price is not None
            return self.quantity * self.price
        assert self.amount is not None
        return self.amount

    def describe(self, instrument_name: str | None = None) -> str:
        name = instrument_name or self.instrument_id or ""
        if self.type in (TxType.BUY, TxType.SELL):
            return (
                f"{self.date} {self.type.upper()} {self.quantity} {name}"
                f" @ {self.price} {self.currency}"
            )
        return f"{self.date} {self.type.upper()} {self.amount} {self.currency} {name}".rstrip()


class PortfolioError(ValueError):
    pass


class Portfolio(BaseModel):
    schema_version: int = 1
    name: str
    base_currency: str = "EUR"
    instruments: dict[str, Instrument] = Field(default_factory=dict)
    transactions: list[Transaction] = Field(default_factory=list)
    # None = automatic: cash is tracked once a deposit or withdrawal has been recorded.
    # Many people only record holdings; for them cash would just go negative with every buy.
    track_cash: bool | None = None
    benchmark: str | None = None  # Yahoo symbol to compare against; None = default for base ccy

    def tracks_cash(self) -> bool:
        if self.track_cash is not None:
            return self.track_cash
        return any(t.type in (TxType.DEPOSIT, TxType.WITHDRAWAL) for t in self.transactions)

    # --- instruments -----------------------------------------------------------------
    def instrument(self, instrument_id: str) -> Instrument:
        try:
            return self.instruments[instrument_id]
        except KeyError:
            raise PortfolioError(f"unknown instrument {instrument_id!r}") from None

    def find_instrument(self, key: str) -> Instrument | None:
        """Look up by id, ISIN or any listing symbol (case-insensitive)."""
        k = key.upper().strip()
        for ins in self.instruments.values():
            if ins.id.upper() == k or (ins.isin and ins.isin == k):
                return ins
            if any(ls.symbol.upper() == k for ls in ins.listings):
                return ins
        return None

    def add_instrument(self, instrument: Instrument) -> Instrument:
        existing = self.find_instrument(instrument.id) or (
            self.find_instrument(instrument.isin) if instrument.isin else None
        )
        if existing is not None:
            return existing
        self.instruments[instrument.id] = instrument
        return instrument

    # --- transactions ----------------------------------------------------------------
    def add_transaction(self, tx: Transaction) -> Transaction:
        if tx.instrument_id is not None:
            ins = self.instrument(tx.instrument_id)
            if tx.type in (TxType.BUY, TxType.SELL) and tx.currency != ins.currency:
                raise PortfolioError(
                    f"{ins.name} is priced in {ins.currency}, transaction is in {tx.currency}"
                )
        if tx.type == TxType.SELL:
            held = self.quantity_held(tx.instrument_id or "", as_of=tx.date)
            assert tx.quantity is not None
            if tx.quantity > held:
                raise PortfolioError(
                    f"cannot sell {tx.quantity}: only {held} of {tx.instrument_id}"
                    f" held on {tx.date}"
                )
        if any(t.id == tx.id for t in self.transactions):
            raise PortfolioError(f"duplicate transaction id {tx.id}")
        self.transactions.append(tx)
        self.transactions.sort(key=lambda t: (t.date, t.created_at))
        return tx

    def remove_transaction(self, tx_id: str) -> Transaction:
        for i, t in enumerate(self.transactions):
            if t.id == tx_id:
                return self.transactions.pop(i)
        raise PortfolioError(f"unknown transaction {tx_id!r}")

    def quantity_held(self, instrument_id: str, as_of: date | None = None) -> Decimal:
        q = ZERO
        for t in self.transactions:
            if t.instrument_id != instrument_id or (as_of and t.date > as_of):
                continue
            if t.type == TxType.BUY:
                q += t.quantity or ZERO
            elif t.type == TxType.SELL:
                q -= t.quantity or ZERO
        return q


class Quote(BaseModel):
    """A price observation. Always carries where and when it came from."""

    symbol: str
    price: Decimal
    currency: str
    as_of: datetime
    source: str
    previous_close: Decimal | None = None
    is_stale: bool = False  # served from cache because no provider answered

    @property
    def change_pct(self) -> Decimal | None:
        if self.previous_close in (None, ZERO):
            return None
        assert self.previous_close is not None
        return (self.price - self.previous_close) / self.previous_close * 100


class FxRate(BaseModel):
    base: str
    quote: str
    rate: Decimal  # 1 base = rate quote
    as_of: datetime
    source: str
    is_stale: bool = False
