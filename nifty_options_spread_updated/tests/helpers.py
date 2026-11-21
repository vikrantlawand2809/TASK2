"""Shared test helpers (mock universe: near 24-Nov-2026, far 01-Dec-2026)."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from broker.mock_data_provider import make_default_mock
from config import IST, load_config
from database.db_manager import DatabaseManager
from shadow_engine import ShadowEngine

NEAR, FAR = date(2026, 11, 24), date(2026, 12, 1)
T0 = datetime(2026, 11, 17, 15, 5, tzinfo=IST)        # expiry day, after the 15:00 roll
NEXT_DAY = datetime(2026, 11, 18, 9, 30, tzinfo=IST)


def set_diff(provider, strike: float, opt: str, diff: float, near_bid: float = 100.0) -> None:
    """Quotes such that executable difference (far ask - near bid) == diff."""
    provider.set_quote(NEAR, strike, opt, near_bid, near_bid + 1)
    provider.set_quote(FAR, strike, opt, near_bid + diff - 1, near_bid + diff)


def make_env(config=None, now=T0, spot: float = 25500.0) -> SimpleNamespace:
    cfg = config or load_config()
    db = DatabaseManager(":memory:")
    provider = make_default_mock(now)
    provider.set_spot(spot)
    engine = ShadowEngine(provider, cfg, db)
    return SimpleNamespace(cfg=cfg, db=db, provider=provider, engine=engine)
