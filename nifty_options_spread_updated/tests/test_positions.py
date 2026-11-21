import sqlite3
from dataclasses import replace
from datetime import datetime

import pytest

from config import CostConfig, ExitConfig, load_config, AppConfig
from database.db_manager import DatabaseManager
from positions.models import NewPositionSpec, PositionStatus
from positions.pnl_calculator import CostModel
from positions.position_manager import DuplicatePositionError, InvalidTransitionError, PositionManager
from shadow_engine import ManualCloseError
from tests.helpers import FAR, NEAR, NEXT_DAY, T0, make_env, set_diff


def spec(strike=25500.0, opt="CE", cycle="CYC-20261124", **kw):
    base = dict(cycle_id=cycle, atm_strike=strike, option_type=opt, near_expiry=NEAR, far_expiry=FAR,
                near_symbol="N", far_symbol="F", near_instrument_key="nk", far_instrument_key="fk",
                lot_size=75, lots=1, quote_method="executable", near_entry_premium=100.0,
                far_entry_premium=170.0, initial_diff=70.0)
    base.update(kw)
    return NewPositionSpec(**base)


@pytest.fixture
def pm():
    return PositionManager(DatabaseManager(":memory:"), CostModel(CostConfig()))


# ---- manager-level ---------------------------------------------------------------------
def test_duplicate_active_position_prevented(pm):
    pm.open_new(spec(), T0)
    with pytest.raises(DuplicatePositionError):
        pm.open_new(spec(), T0)
    assert len(pm.list_all()) == 1


def test_db_unique_index_is_last_line_of_defence():
    db = DatabaseManager(":memory:")
    PositionManager(db, CostModel(CostConfig())).open_new(spec(), T0)
    clone = db.list_positions()[0]
    clone.position_id = "OTHER"
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_position(clone)


def test_closed_box_can_be_reopened_with_new_id(pm):
    p1 = pm.open_new(spec(), T0)
    pm.close(p1.position_id, T0, 90.0, 175.0, "MANUAL")
    p2 = pm.open_new(spec(), NEXT_DAY)
    assert p2.position_id != p1.position_id and p2.position_id.endswith("-02")
    assert pm.get(p1.position_id).status == PositionStatus.CLOSED
    assert pm.get(p2.position_id).status == PositionStatus.OPEN


def test_ce_and_pe_on_same_strike_and_different_strikes_are_independent(pm):
    ids = [pm.open_new(spec(k, o), T0).position_id for k, o in
           [(25500.0, "CE"), (25500.0, "PE"), (25550.0, "CE"), (25700.0, "CE")]]
    assert len(set(ids)) == 4 and len(pm.list_open()) == 4


def test_closing_one_position_leaves_others_untouched(pm):
    a = pm.open_new(spec(25500.0), T0)
    b = pm.open_new(spec(25550.0), T0)
    c = pm.open_new(spec(25700.0), T0)
    pm.close(b.position_id, T0, 95.0, 180.0, "MANUAL")
    assert pm.get(a.position_id).status == PositionStatus.OPEN
    assert pm.get(b.position_id).status == PositionStatus.CLOSED
    assert pm.get(c.position_id).status == PositionStatus.OPEN


def test_state_machine_transitions(pm):
    p = pm.create_pending(spec(), T0)
    assert p.status == PositionStatus.PENDING
    pm.mark_data_unavailable(p.position_id, "no data")
    assert pm.get(p.position_id).status == PositionStatus.DATA_UNAVAILABLE
    pm.mark_rejected(p.position_id, "gave up")
    assert pm.get(p.position_id).status == PositionStatus.REJECTED
    with pytest.raises(InvalidTransitionError):
        pm.open(p.position_id, T0)
    q = pm.open_new(spec(25550.0), T0)
    with pytest.raises(InvalidTransitionError):
        pm.close(p.position_id, T0, 1, 1, "x")           # REJECTED cannot close
    pm.close(q.position_id, T0, 100, 170, "MANUAL")
    with pytest.raises(InvalidTransitionError):
        pm.close(q.position_id, T0, 100, 170, "MANUAL")  # CLOSED is terminal


def test_pending_blocks_duplicates_too(pm):
    pm.create_pending(spec(), T0)
    with pytest.raises(DuplicatePositionError):
        pm.create_pending(spec(), T0)


def test_position_stores_required_lifecycle_fields(pm):
    p = pm.open_new(spec(), T0)
    for attr in ("position_id", "cycle_id", "atm_strike", "option_type", "near_symbol", "far_symbol", "entry_ts",
                 "near_entry_premium", "far_entry_premium", "initial_diff", "current_diff", "status"):
        assert getattr(p, attr) is not None
    pm.close(p.position_id, NEXT_DAY, 95, 172, "MANUAL")
    p = pm.get(p.position_id)
    assert p.exit_ts == NEXT_DAY and p.exit_reason == "MANUAL"
    assert p.net_pnl is not None and p.est_costs is not None and p.gross_pnl is not None


# ---- engine-level independence ---------------------------------------------------------------
def tick(e, spot, diffs, when=None):
    if when:
        e.provider.set_time(when)
    e.provider.set_spot(spot)
    for (k, o), d in diffs.items():
        set_diff(e.provider, k, o, d)
    return e.engine.run_once()


def test_engine_blocks_duplicate_same_atm():
    e = make_env()
    r1 = tick(e, 25500, {(25500, "CE"): 70})
    r2 = tick(e, 25502, {(25500, "CE"): 71})
    assert len(r1.opened) == 1 and not r2.opened
    assert next(x for x in r2.evaluations if x.option_type == "CE").outcome == "REJECTED_DUPLICATE"
    assert len(e.engine.positions.list_open()) == 1


def test_new_boxes_do_not_close_old_and_no_skipped_boxes_on_gap():
    e = make_env()
    tick(e, 25500, {(25500, "CE"): 70})
    tick(e, 25550, {(25550, "CE"): 72}, NEXT_DAY)
    tick(e, 25700, {(25700, "CE"): 68}, NEXT_DAY.replace(hour=11))
    strikes = sorted({p.atm_strike for p in e.engine.positions.list_all()})
    assert strikes == [25500.0, 25550.0, 25700.0]            # 25600/25650 never created
    assert len(e.engine.positions.list_open()) == 3          # earlier ones stayed open


def test_engine_reopens_closed_box_when_eligible_again():
    e = make_env()
    tick(e, 25500, {(25500, "CE"): 70})
    pid = e.engine.positions.list_open()[0].position_id
    e.engine.manual_close(pid)
    r = tick(e, 25500, {(25500, "CE"): 66}, NEXT_DAY)
    assert len(r.opened) == 1 and r.opened[0].position_id.endswith("-02")


def test_manual_close_one_of_many_via_engine():
    e = make_env()
    tick(e, 25500, {(25500, "CE"): 70, (25500, "PE"): 65})
    ce = next(p for p in e.engine.positions.list_open() if p.option_type == "CE")
    e.engine.manual_close(ce.position_id)
    open_now = e.engine.positions.list_open()
    assert [p.option_type for p in open_now] == ["PE"]


def test_manual_close_without_valid_quotes_refused_and_position_stays_open():
    e = make_env()
    tick(e, 25500, {(25500, "CE"): 70})
    pid = e.engine.positions.list_open()[0].position_id
    e.provider.make_stale(FAR, 25500, "CE", 900)
    with pytest.raises(ManualCloseError):
        e.engine.manual_close(pid)
    assert e.engine.positions.get(pid).status == PositionStatus.OPEN


def test_monitoring_updates_current_difference_and_skips_update_on_bad_data():
    e = make_env()
    tick(e, 25500, {(25500, "CE"): 70})
    tick(e, 25500, {(25500, "CE"): 90}, NEXT_DAY)
    pos = e.engine.positions.list_open()[0]
    assert pos.initial_diff == 70 and pos.current_diff == 90
    e.provider.set_time(NEXT_DAY.replace(hour=10))
    set_diff(e.provider, 25500, "CE", 120)
    e.provider.make_stale(FAR, 25500, "CE", 900)
    e.engine.run_once()
    assert e.engine.positions.list_open()[0].current_diff == 90      # unchanged: data invalid


# ---- exits: nothing automatic unless configured ---------------------------------------------
def test_no_automatic_exit_by_default_even_with_huge_move():
    e = make_env()
    tick(e, 25500, {(25500, "CE"): 70})
    tick(e, 25500, {(25500, "CE"): 400}, NEXT_DAY)
    assert len(e.engine.positions.list_open()) == 1


def test_profit_target_and_stop_loss_only_when_configured():
    cfg = replace(load_config(), exits=ExitConfig(profit_target_pnl=500.0, stop_loss_pnl=500.0))
    e = make_env(cfg)
    tick(e, 25500, {(25500, "CE"): 70, (25500, "PE"): 70})
    # CE far leg gains a lot -> profit target; PE far leg collapses -> stop loss
    e.provider.set_time(NEXT_DAY)
    set_diff(e.provider, 25500, "CE", 100)
    set_diff(e.provider, 25500, "PE", 40)
    res = e.engine.run_once()
    reasons = {p.option_type: p.exit_reason for p in res.closed}
    assert reasons == {"CE": "PROFIT_TARGET", "PE": "STOP_LOSS"}


def _cycle_roll_env(enabled):
    cfg = replace(load_config(), exits=ExitConfig(cycle_closure_enabled=enabled))
    e = make_env(cfg)
    tick(e, 25500, {(25500, "CE"): 70, (25500, "PE"): 70})
    # roll: 24-Nov 15:05 -> new cycle (near 01-Dec). Old contracts still quoted.
    e.provider.set_time(datetime(2026, 11, 24, 15, 5, tzinfo=T0.tzinfo))
    return e


def test_cycle_closure_disabled_by_default_positions_stay_open():
    e = _cycle_roll_env(False)
    res = e.engine.run_once()
    assert res.cycle_id == "CYC-20261201" and res.new_cycle
    assert len(e.engine.positions.list_open(cycle_id="CYC-20261124")) == 2


def test_cycle_closure_when_enabled_closes_only_finished_cycle():
    e = _cycle_roll_env(True)
    res = e.engine.run_once()
    assert {p.exit_reason for p in res.closed} == {"CYCLE_CLOSURE"} and len(res.closed) == 2
    assert e.engine.positions.list_open() == []
