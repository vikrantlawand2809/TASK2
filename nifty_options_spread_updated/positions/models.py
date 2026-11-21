"""Position + evaluation records (pure data, no I/O)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class PositionStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


# Allowed lifecycle transitions (each position moves independently).
VALID_TRANSITIONS: dict[PositionStatus, set[PositionStatus]] = {
    PositionStatus.PENDING: {PositionStatus.OPEN, PositionStatus.REJECTED, PositionStatus.DATA_UNAVAILABLE},
    PositionStatus.DATA_UNAVAILABLE: {PositionStatus.PENDING, PositionStatus.REJECTED},
    PositionStatus.OPEN: {PositionStatus.CLOSED},
    PositionStatus.CLOSED: set(),
    PositionStatus.REJECTED: set(),
}
ACTIVE_STATUSES = (PositionStatus.PENDING, PositionStatus.OPEN)


@dataclass(frozen=True)
class NewPositionSpec:
    """Everything needed to create a simulated calendar spread (far BUY / near SELL)."""
    cycle_id: str
    atm_strike: float
    option_type: str
    near_expiry: date
    far_expiry: date
    near_symbol: str
    far_symbol: str
    near_instrument_key: str
    far_instrument_key: str
    lot_size: int
    lots: int
    quote_method: str
    near_entry_premium: float     # price the SHORT near leg is sold at
    far_entry_premium: float      # price the LONG far leg is bought at
    initial_diff: float


@dataclass
class Position:
    position_id: str
    cycle_id: str
    atm_strike: float
    option_type: str
    near_expiry: date
    far_expiry: date
    near_symbol: str
    far_symbol: str
    near_instrument_key: str
    far_instrument_key: str
    lot_size: int
    lots: int
    quote_method: str
    near_entry_premium: float
    far_entry_premium: float
    initial_diff: float
    status: PositionStatus = PositionStatus.PENDING
    created_ts: Optional[datetime] = None
    entry_ts: Optional[datetime] = None
    current_diff: Optional[float] = None
    current_diff_ts: Optional[datetime] = None
    near_mark_exit: Optional[float] = None        # latest exit-side price (buy back near)
    far_mark_exit: Optional[float] = None         # latest exit-side price (sell far)
    unrealized_gross_pnl: Optional[float] = None
    unrealized_net_pnl: Optional[float] = None
    exit_ts: Optional[datetime] = None
    exit_reason: Optional[str] = None
    near_exit_premium: Optional[float] = None
    far_exit_premium: Optional[float] = None
    long_pnl: Optional[float] = None
    short_pnl: Optional[float] = None
    gross_pnl: Optional[float] = None
    est_costs: Optional[float] = None
    net_pnl: Optional[float] = None
    notes: str = ""

    @property
    def quantity(self) -> int:
        return self.lots * self.lot_size


@dataclass(frozen=True)
class EvaluationRecord:
    """One entry evaluation (every ATM x option-type check is logged)."""
    ts: datetime
    cycle_id: str
    atm_strike: float
    option_type: str
    outcome: str
    band: Optional[str]
    reason: str
    quote_method: str
    near_symbol: Optional[str] = None
    far_symbol: Optional[str] = None
    near_price: Optional[float] = None
    far_price: Optional[float] = None
    premium_diff: Optional[float] = None
    near_status: Optional[str] = None
    far_status: Optional[str] = None
    position_id: Optional[str] = None
