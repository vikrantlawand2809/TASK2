"""Independent position management (one record per ATM box + option type).

Never imports anything from the broker package: simulated trading is strictly
separated from market-data access.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Callable, Optional

from database.db_manager import DatabaseManager
from positions.models import (NewPositionSpec, Position, PositionStatus, VALID_TRANSITIONS)
from positions.pnl_calculator import CostModel, calculate_spread_pnl

log = logging.getLogger("nifty_spread.positions")


class DuplicatePositionError(RuntimeError):
    pass


class InvalidTransitionError(RuntimeError):
    pass


class PositionManager:
    def __init__(self, db: DatabaseManager, cost_model: CostModel):
        self._db = db
        self._cost = cost_model

    # ---- queries -----------------------------------------------------------
    def has_active_position(self, cycle_id: str, strike: float, option_type: str) -> bool:
        return self._db.has_active_position(cycle_id, strike, option_type)

    def get(self, position_id: str) -> Position:
        pos = self._db.get_position(position_id)
        if pos is None:
            raise KeyError(position_id)
        return pos

    def list_open(self, cycle_id: Optional[str] = None) -> list[Position]:
        return self._db.list_positions([PositionStatus.OPEN], cycle_id)

    def list_all(self) -> list[Position]:
        return self._db.list_positions()

    # ---- lifecycle ---------------------------------------------------------
    def _transition(self, pos: Position, new: PositionStatus) -> None:
        if new not in VALID_TRANSITIONS[pos.status]:
            raise InvalidTransitionError(f"{pos.position_id}: {pos.status.value} -> {new.value} not allowed")
        log.info("Position %s: %s -> %s", pos.position_id, pos.status.value, new.value)
        pos.status = new

    def create_pending(self, spec: NewPositionSpec, now: datetime) -> Position:
        if self._db.has_active_position(spec.cycle_id, spec.atm_strike, spec.option_type):
            raise DuplicatePositionError(
                f"Active position already exists for {spec.cycle_id} {spec.atm_strike:g} {spec.option_type}")
        seq = self._db.next_sequence(spec.cycle_id, spec.atm_strike, spec.option_type)
        pos = Position(
            position_id=f"{spec.cycle_id}-{int(spec.atm_strike)}-{spec.option_type}-{seq:02d}",
            cycle_id=spec.cycle_id, atm_strike=spec.atm_strike, option_type=spec.option_type,
            near_expiry=spec.near_expiry, far_expiry=spec.far_expiry,
            near_symbol=spec.near_symbol, far_symbol=spec.far_symbol,
            near_instrument_key=spec.near_instrument_key, far_instrument_key=spec.far_instrument_key,
            lot_size=spec.lot_size, lots=spec.lots, quote_method=spec.quote_method,
            near_entry_premium=spec.near_entry_premium, far_entry_premium=spec.far_entry_premium,
            initial_diff=spec.initial_diff, status=PositionStatus.PENDING, created_ts=now)
        try:
            self._db.insert_position(pos)
        except sqlite3.IntegrityError as exc:   # unique partial index = last line of defence
            raise DuplicatePositionError(str(exc)) from exc
        return pos

    def open(self, position_id: str, now: datetime) -> Position:
        pos = self.get(position_id)
        self._transition(pos, PositionStatus.OPEN)
        pos.entry_ts = now
        pos.current_diff = pos.initial_diff
        pos.current_diff_ts = now
        self._db.update_position(pos)
        return pos

    def open_new(self, spec: NewPositionSpec, now: datetime) -> Position:
        """PENDING -> OPEN in one step (simulated fill at the spec's entry prices)."""
        pos = self.create_pending(spec, now)
        pos = self.open(pos.position_id, now)
        log.info("SIMULATED ENTRY %s: BUY far %s @ %.2f / SELL near %s @ %.2f, diff %.2f, qty %d",
                 pos.position_id, pos.far_symbol, pos.far_entry_premium, pos.near_symbol,
                 pos.near_entry_premium, pos.initial_diff, pos.quantity)
        return pos

    def mark_rejected(self, position_id: str, reason: str) -> Position:
        pos = self.get(position_id)
        self._transition(pos, PositionStatus.REJECTED)
        pos.notes = reason
        self._db.update_position(pos)
        return pos

    def mark_data_unavailable(self, position_id: str, reason: str) -> Position:
        pos = self.get(position_id)
        self._transition(pos, PositionStatus.DATA_UNAVAILABLE)
        pos.notes = reason
        self._db.update_position(pos)
        return pos

    def update_mark(self, position_id: str, current_diff: float, ts: datetime,
                    near_exit_price: float, far_exit_price: float) -> Position:
        """Refresh current premium difference + unrealised (mark-to-exit) P&L."""
        pos = self.get(position_id)
        if pos.status != PositionStatus.OPEN:
            raise InvalidTransitionError(f"{position_id} is {pos.status.value}; cannot update mark")
        res = calculate_spread_pnl(pos.far_entry_premium, far_exit_price, pos.near_entry_premium,
                                   near_exit_price, pos.quantity, self._cost)
        pos.current_diff, pos.current_diff_ts = current_diff, ts
        pos.near_mark_exit, pos.far_mark_exit = near_exit_price, far_exit_price
        pos.unrealized_gross_pnl, pos.unrealized_net_pnl = res.gross_pnl, res.net_pnl
        self._db.update_position(pos)
        return pos

    def close(self, position_id: str, now: datetime, near_exit_price: float, far_exit_price: float,
              reason: str) -> Position:
        """Close ONE position; every other position is left untouched."""
        pos = self.get(position_id)
        self._transition(pos, PositionStatus.CLOSED)
        res = calculate_spread_pnl(pos.far_entry_premium, far_exit_price, pos.near_entry_premium,
                                   near_exit_price, pos.quantity, self._cost)
        pos.exit_ts, pos.exit_reason = now, reason
        pos.near_exit_premium, pos.far_exit_premium = near_exit_price, far_exit_price
        pos.long_pnl, pos.short_pnl, pos.gross_pnl = res.long_pnl, res.short_pnl, res.gross_pnl
        pos.est_costs, pos.net_pnl = res.costs, res.net_pnl
        pos.unrealized_gross_pnl = pos.unrealized_net_pnl = None
        self._db.update_position(pos)
        log.info("SIMULATED EXIT %s reason=%s gross=%.2f costs=%.2f net=%.2f",
                 pos.position_id, reason, res.gross_pnl, res.costs, res.net_pnl)
        return pos

    def close_cycle(self, cycle_id: str, now: datetime,
                    price_lookup: Callable[[Position], Optional[tuple[float, float]]],
                    reason: str = "CYCLE_CLOSURE") -> tuple[list[Position], list[Position]]:
        """Close all OPEN positions of a cycle. price_lookup returns (near_exit, far_exit)
        or None when no valid quotes exist (position then stays open and is reported)."""
        closed, skipped = [], []
        for pos in self.list_open(cycle_id):
            prices = price_lookup(pos)
            if prices is None:
                log.warning("Cycle closure: no valid exit quotes for %s; left OPEN", pos.position_id)
                skipped.append(pos)
                continue
            closed.append(self.close(pos.position_id, now, prices[0], prices[1], reason))
        return closed, skipped
