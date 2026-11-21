"""Extensible exit manager.

Only rules that are explicitly configured are active. The brief names profit,
risk and expiry-cycle exits but supplies NO numbers, so by default nothing here
closes a position automatically. Manual simulated closure and (opt-in)
cycle closure are provided for testing/demonstration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from config import ExitConfig
from positions.models import Position

MANUAL = "MANUAL"
PROFIT_TARGET = "PROFIT_TARGET"
STOP_LOSS = "STOP_LOSS"
CYCLE_CLOSURE = "CYCLE_CLOSURE"


@dataclass(frozen=True)
class ExitContext:
    position: Position
    mark_gross_pnl: float       # gross P&L if closed at current exit-side prices
    now: datetime


class ExitRule(Protocol):
    name: str

    def check(self, ctx: ExitContext) -> Optional[str]: ...


class ProfitTargetRule:
    name = PROFIT_TARGET

    def __init__(self, target_pnl: float):
        self._target = target_pnl

    def check(self, ctx: ExitContext) -> Optional[str]:
        return PROFIT_TARGET if ctx.mark_gross_pnl >= self._target else None


class StopLossRule:
    name = STOP_LOSS

    def __init__(self, max_loss: float):
        self._max_loss = abs(max_loss)

    def check(self, ctx: ExitContext) -> Optional[str]:
        return STOP_LOSS if ctx.mark_gross_pnl <= -self._max_loss else None


class ExitManager:
    def __init__(self, cfg: ExitConfig, extra_rules: Optional[list[ExitRule]] = None):
        self._cfg = cfg
        self.rules: list[ExitRule] = []
        if cfg.profit_target_pnl is not None:
            self.rules.append(ProfitTargetRule(cfg.profit_target_pnl))
        if cfg.stop_loss_pnl is not None:
            self.rules.append(StopLossRule(cfg.stop_loss_pnl))
        self.rules.extend(extra_rules or [])

    def check(self, position: Position, mark_gross_pnl: float, now: datetime) -> Optional[str]:
        """Return the exit reason of the first rule that fires, else None."""
        ctx = ExitContext(position, mark_gross_pnl, now)
        for rule in self.rules:
            reason = rule.check(ctx)
            if reason:
                return reason
        return None

    @property
    def cycle_closure_enabled(self) -> bool:
        return self._cfg.cycle_closure_enabled

    def positions_due_for_cycle_closure(self, open_positions: list[Position], active_cycle_id: str) -> list[Position]:
        """Open positions belonging to a finished cycle (only if rule enabled)."""
        if not self._cfg.cycle_closure_enabled:
            return []
        return [p for p in open_positions if p.cycle_id != active_cycle_id]
