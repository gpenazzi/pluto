"""Positions and cash from the transaction list. Average-cost method."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pluto.core.model import ZERO, Portfolio, TxType


@dataclass
class Position:
    instrument_id: str
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO  # total cost of the units held, incl. fees, instrument currency
    realized_pnl: Decimal = ZERO
    dividends: Decimal = ZERO
    first_buy: date | None = None

    @property
    def avg_cost(self) -> Decimal:
        return self.cost_basis / self.quantity if self.quantity else ZERO


@dataclass
class Holdings:
    positions: dict[str, Position] = field(default_factory=dict)
    cash: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.quantity > 0]


def compute_holdings(portfolio: Portfolio, as_of: date | None = None) -> Holdings:
    h = Holdings()
    for tx in portfolio.transactions:  # already sorted by date
        if as_of is not None and tx.date > as_of:
            break
        cash = h.cash
        if tx.type == TxType.DEPOSIT:
            cash[tx.currency] += tx.gross
            continue
        if tx.type == TxType.WITHDRAWAL:
            cash[tx.currency] -= tx.gross
            continue
        if tx.type == TxType.FEE:
            cash[tx.currency] -= tx.gross
            continue
        assert tx.instrument_id is not None
        pos = h.positions.setdefault(tx.instrument_id, Position(tx.instrument_id))
        if tx.type == TxType.DIVIDEND:
            pos.dividends += tx.gross
            cash[tx.currency] += tx.gross - tx.fees
            continue
        assert tx.quantity is not None
        if tx.type == TxType.BUY:
            pos.quantity += tx.quantity
            pos.cost_basis += tx.gross + tx.fees
            pos.first_buy = pos.first_buy or tx.date
            cash[tx.currency] -= tx.gross + tx.fees
        elif tx.type == TxType.SELL:
            avg = pos.avg_cost
            cost_out = avg * tx.quantity
            pos.realized_pnl += tx.gross - tx.fees - cost_out
            pos.quantity -= tx.quantity
            pos.cost_basis -= cost_out
            if pos.quantity == 0:
                pos.cost_basis = ZERO
            cash[tx.currency] += tx.gross - tx.fees
    return h
