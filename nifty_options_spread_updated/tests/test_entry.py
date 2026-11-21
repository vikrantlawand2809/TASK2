from datetime import timedelta

import pytest

from config import EntryConfig, load_config
from strategy.entry_conditions import EntryConditions, PremiumBand
from tests.helpers import FAR, NEAR, NEXT_DAY, make_env, set_diff


def band(d, **kw):
    return EntryConditions(EntryConfig(**kw)).assess(d)


# ---- pure band logic ----------------------------------------------------------
def test_below_60_is_eligible():
    a = band(45)
    assert a.eligible and a.band == PremiumBand.BELOW_RANGE


@pytest.mark.parametrize("d", [60, 72.5, 85])
def test_60_to_85_inclusive_is_eligible(d):
    a = band(d)
    assert a.eligible and a.band == PremiumBand.IN_RANGE


def test_above_85_not_eligible():
    a = band(85.01)
    assert not a.eligible and a.band == PremiumBand.ABOVE_RANGE


def test_negative_difference_treated_as_anomaly():
    assert band(-5).band == PremiumBand.INVALID and not band(-5).eligible


def test_thresholds_are_configurable():
    assert not band(70, in_range_max=65).eligible
    assert not band(50, allow_below_range=False).eligible


# ---- engine level -------------------------------------------------------------------
def outcome(res, opt):
    return next(e.outcome for e in res.evaluations if e.option_type == opt)


def test_engine_enters_below_60_and_in_range_but_not_above_85():
    e = make_env()
    set_diff(e.provider, 25500, "CE", 50)
    set_diff(e.provider, 25500, "PE", 90)
    res = e.engine.run_once()
    assert outcome(res, "CE") == "ENTERED"
    assert outcome(res, "PE") == "REJECTED_ABOVE_RANGE"
    assert len(e.engine.positions.list_open()) == 1


def test_no_forced_entry_then_enters_on_later_session_same_cycle():
    e = make_env()
    set_diff(e.provider, 25500, "CE", 95)
    set_diff(e.provider, 25500, "PE", 95)
    first = e.engine.run_once()
    assert not first.opened and e.engine.positions.list_open() == []
    e.provider.set_time(NEXT_DAY)
    set_diff(e.provider, 25500, "CE", 80)
    second = e.engine.run_once()
    assert len(second.opened) == 1 and second.cycle_id == first.cycle_id


# ---- data problems must never create a position ----------------------------------
def test_missing_market_data_rejected_with_reason():
    e = make_env()                       # no quotes loaded at all
    res = e.engine.run_once()
    assert {x.outcome for x in res.evaluations} == {"REJECTED_DATA_MISSING"}
    assert not res.opened and "no quote" in res.evaluations[0].reason


def test_one_leg_missing_rejected():
    e = make_env()
    set_diff(e.provider, 25500, "CE", 70)
    e.provider.drop_quote(FAR, 25500, "CE")
    assert outcome(e.engine.run_once(), "CE") == "REJECTED_DATA_MISSING"


def test_stale_quote_rejected():
    e = make_env()
    set_diff(e.provider, 25500, "CE", 70)
    e.provider.make_stale(NEAR, 25500, "CE", 400)
    res = e.engine.run_once()
    assert outcome(res, "CE") == "REJECTED_DATA_STALE"
    assert e.engine.positions.list_open() == []


def test_crossed_quote_rejected_as_invalid():
    e = make_env()
    set_diff(e.provider, 25500, "CE", 70)
    e.provider.set_quote(FAR, 25500, "CE", 190, 170)
    assert outcome(e.engine.run_once(), "CE") == "REJECTED_DATA_INVALID"


def test_stale_underlying_skips_tick():
    e = make_env()
    set_diff(e.provider, 25500, "CE", 70)
    e.provider.make_index_stale(600)
    res = e.engine.run_once()
    assert res.skipped_reason.startswith("UNDERLYING_DATA_INVALID") and not res.evaluations


def test_far_strike_missing_logged_as_contract_missing():
    e = make_env()
    e.provider.remove_strike(FAR, 25500)
    res = e.engine.run_once()
    assert {x.outcome for x in res.evaluations} == {"REJECTED_CONTRACT_MISSING"}


def test_provider_error_skips_tick_gracefully():
    e = make_env()
    e.provider.fail_next = "boom"
    assert "boom" in e.engine.run_once().skipped_reason


def test_outside_market_hours_skipped():
    e = make_env(now=NEXT_DAY.replace(hour=20))
    assert e.engine.run_once().skipped_reason == "OUTSIDE_MARKET_HOURS"
