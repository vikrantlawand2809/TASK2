"""Simulated P&L for a calendar spread (far = LONG/BUY, near = SHORT/SELL).

Pure functions + a cost model; no I/O, fully unit-testable.

Price assumptions (documented, configurable through quote method):
  entry : BUY far at its entry price, SELL near at its entry price
  exit  : SELL far at its exit price, BUY near back at its exit price
"""
from __future__ import annotations

from dataclasses import dataclass

from config import CostConfig


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0

    @property
    def total(self) -> float:
        return round(self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst, 2)

    def __add__(self, other: "CostBreakdown") -> "CostBreakdown":
        return CostBreakdown(*(a + b for a, b in zip(self.__dict__.values(), other.__dict__.values())))


class CostModel:
    """ESTIMATED costs per order. Rates come from CostConfig (placeholders)."""

    def __init__(self, cfg: CostConfig):
        self._cfg = cfg

    def order_cost(self, price: float, quantity: int, side: str) -> CostBreakdown:
        turnover = price * quantity
        c = self._cfg
        exchange = turnover * c.exchange_txn_rate
        sebi = turnover * c.sebi_rate
        return CostBreakdown(
            brokerage=c.brokerage_per_order,
            stt=turnover * c.stt_sell_rate if side == "SELL" else 0.0,
            exchange=exchange,
            sebi=sebi,
            stamp=turnover * c.stamp_buy_rate if side == "BUY" else 0.0,
            gst=(c.brokerage_per_order + exchange + sebi) * c.gst_rate,
        )


@dataclass(frozen=True)
class PnLResult:
    long_pnl: float
    short_pnl: float
    gross_pnl: float
    costs: float
    net_pnl: float


def calculate_spread_pnl(far_entry: float, far_exit: float, near_entry: float, near_exit: float,
                         quantity: int, cost_model: CostModel) -> PnLResult:
    """Gross and net P&L in INR for `quantity` (= lots x lot size) units."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    long_pnl = (far_exit - far_entry) * quantity       # bought far, sold it back
    short_pnl = (near_entry - near_exit) * quantity    # sold near, bought it back
    gross = long_pnl + short_pnl
    costs = (cost_model.order_cost(far_entry, quantity, "BUY")
             + cost_model.order_cost(near_entry, quantity, "SELL")
             + cost_model.order_cost(far_exit, quantity, "SELL")
             + cost_model.order_cost(near_exit, quantity, "BUY")).total
    return PnLResult(round(long_pnl, 2), round(short_pnl, 2), round(gross, 2), costs, round(gross - costs, 2))
