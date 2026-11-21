from datetime import date, datetime

import pytest

from config import IST, CycleConfig
from strategy.expiry_manager import ExpiryError, ExpiryManager

EXPS = [date(2026, 12, 8), date(2026, 11, 24), date(2026, 12, 1), date(2026, 11, 10)]  # unsorted + past


def mgr(**kw):
    return ExpiryManager(CycleConfig(**kw))


def test_near_expiry_is_nearest_upcoming():
    p = mgr().resolve(EXPS, datetime(2026, 11, 18, 10, 0, tzinfo=IST))
    assert p.near == date(2026, 11, 24)


def test_far_expiry_is_subsequent_expiry():
    p = mgr().resolve(EXPS, datetime(2026, 11, 18, 10, 0, tzinfo=IST))
    assert p.far == date(2026, 12, 1) and p.far > p.near


def test_dates_come_from_input_not_hardcoded():
    other = [date(2027, 3, 4), date(2027, 3, 11), date(2027, 3, 18)]
    p = mgr().resolve(other, datetime(2027, 3, 1, 10, 0, tzinfo=IST))
    assert (p.near, p.far) == (other[0], other[1])


def test_expiry_day_before_roll_time_keeps_expiring_contract_as_near():
    p = mgr().resolve(EXPS, datetime(2026, 11, 24, 14, 59, tzinfo=IST))
    assert p.near == date(2026, 11, 24)


def test_expiry_day_after_roll_time_starts_new_cycle():
    p = mgr().resolve(EXPS, datetime(2026, 11, 24, 15, 0, tzinfo=IST))
    assert (p.near, p.far) == (date(2026, 12, 1), date(2026, 12, 8))
    assert p.cycle_id == "CYC-20261201"


def test_roll_can_be_disabled():
    p = mgr(roll_on_expiry_day=False).resolve(EXPS, datetime(2026, 11, 24, 15, 30, tzinfo=IST))
    assert p.near == date(2026, 11, 24)


def test_needs_two_expiries():
    with pytest.raises(ExpiryError):
        mgr().resolve([date(2026, 11, 24)], datetime(2026, 11, 18, 10, 0, tzinfo=IST))
