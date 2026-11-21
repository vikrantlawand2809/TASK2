import pytest

from config import CostConfig
from positions.pnl_calculator import CostModel, calculate_spread_pnl

ZERO = CostModel(CostConfig(0, 0, 0, 0, 0, 0))


def test_long_short_gross_and_net_with_zero_costs():
    r = calculate_spread_pnl(far_entry=180, far_exit=190, near_entry=100, near_exit=85, quantity=75, cost_model=ZERO)
    assert r.long_pnl == 750          # (190-180)*75   : far leg is LONG
    assert r.short_pnl == 1125        # (100-85)*75    : near leg is SHORT
    assert r.gross_pnl == 1875 and r.costs == 0 and r.net_pnl == 1875


def test_losing_trade():
    r = calculate_spread_pnl(180, 170, 100, 115, 75, ZERO)
    assert r.long_pnl == -750 and r.short_pnl == -1125 and r.gross_pnl == -1875


def test_quantity_scales_with_lots_and_lot_size():
    one = calculate_spread_pnl(180, 190, 100, 90, 75, ZERO).gross_pnl
    two = calculate_spread_pnl(180, 190, 100, 90, 150, ZERO).gross_pnl
    assert two == 2 * one


def test_brokerage_only_cost_four_orders_plus_gst():
    cm = CostModel(CostConfig(brokerage_per_order=20, stt_sell_rate=0, exchange_txn_rate=0,
                              sebi_rate=0, stamp_buy_rate=0, gst_rate=0.18))
    r = calculate_spread_pnl(180, 190, 100, 90, 75, cm)
    assert r.costs == pytest.approx(4 * 20 * 1.18, abs=0.01)
    assert r.net_pnl == pytest.approx(r.gross_pnl - r.costs, abs=0.01)


def test_stt_only_on_sell_side_stamp_only_on_buy_side():
    cm = CostModel(CostConfig(0, 0.001, 0, 0, 0.0001, 0))
    sell = cm.order_cost(100, 75, "SELL")
    buy = cm.order_cost(100, 75, "BUY")
    assert sell.stt == pytest.approx(7.5) and sell.stamp == 0
    assert buy.stt == 0 and buy.stamp == pytest.approx(0.75)


def test_invalid_quantity():
    with pytest.raises(ValueError):
        calculate_spread_pnl(1, 1, 1, 1, 0, ZERO)


def test_realised_pnl_does_not_add_costs_twice():
    cm = CostModel(CostConfig(brokerage_per_order=20, stt_sell_rate=0, exchange_txn_rate=0,
                              sebi_rate=0, stamp_buy_rate=0, gst_rate=0))
    r = calculate_spread_pnl(180, 190, 100, 85, 75, cm)
    assert r.gross_pnl == 1875
    assert r.costs == 80
    assert r.net_pnl == r.gross_pnl - r.costs
