"""Deterministic mock market-data provider.

Used for unit tests, the demo and for development before approved API access is
available. Prices are SYNTHETIC - they carry no market meaning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

import numpy as np

from broker.market_data import (ChainRow, IndexQuote, MarketDataError, MarketDataProvider,
                                OptionChain, OptionContract, OptionQuote, Quote)
from config import IST


@dataclass
class _Stored:
    bid: Optional[float]
    ask: Optional[float]
    ltp: Optional[float]
    ts_override: Optional[datetime] = None   # fixed timestamp (to simulate stale quotes)
    feed: Optional[str] = "live"


class MockMarketDataProvider(MarketDataProvider):
    name = "mock"

    def __init__(self, expiries: Iterable[date], strikes: Iterable[float], now: datetime,
                 spot: float = 25500.0, vix: float = 13.5, lot_size: int = 75,
                 underlying: str = "NIFTY"):
        self._expiries = sorted(expiries)
        self._strikes = sorted(float(s) for s in strikes)
        self._now = now
        self._spot = spot
        self._vix = vix
        self._lot_size = lot_size
        self._underlying = underlying
        self._quotes: dict[tuple[date, float, str], _Stored] = {}
        self._removed_strikes: set[tuple[date, float]] = set()
        self._index_ts_override: Optional[datetime] = None
        self.fail_next: Optional[str] = None      # set to raise MarketDataError on next call

    # ---- scenario controls -------------------------------------------------
    def set_time(self, now: datetime) -> None:
        self._now = now

    def set_spot(self, spot: float) -> None:
        self._spot = spot

    def set_vix(self, vix: float) -> None:
        self._vix = vix

    def set_expiries(self, expiries: Iterable[date]) -> None:
        self._expiries = sorted(expiries)

    def make_index_stale(self, age_seconds: float) -> None:
        self._index_ts_override = self._now - timedelta(seconds=age_seconds)

    def clear_index_stale(self) -> None:
        self._index_ts_override = None

    def set_quote(self, expiry: date, strike: float, option_type: str, bid: Optional[float],
                  ask: Optional[float], ltp: Optional[float] = None,
                  ts: Optional[datetime] = None, feed: Optional[str] = "live") -> None:
        if ltp is None and bid is not None and ask is not None:
            ltp = round((bid + ask) / 2, 2)
        self._quotes[(expiry, float(strike), option_type)] = _Stored(bid, ask, ltp, ts, feed)

    def drop_quote(self, expiry: date, strike: float, option_type: str) -> None:
        """Contract exists but the API returned no market data for it."""
        self._quotes.pop((expiry, float(strike), option_type), None)

    def remove_strike(self, expiry: date, strike: float) -> None:
        """Strike is absent from that expiry's chain entirely."""
        self._removed_strikes.add((expiry, float(strike)))

    def make_stale(self, expiry: date, strike: float, option_type: str, age_seconds: float) -> None:
        stored = self._quotes[(expiry, float(strike), option_type)]
        stored.ts_override = self._now - timedelta(seconds=age_seconds)

    def populate_synthetic(self, time_value: dict[tuple[date, str], float], width: float = 300.0,
                           half_spread: float = 0.5) -> None:
        """Fill every strike/expiry/type with intrinsic + bell-shaped time value."""
        strikes = np.array(self._strikes)
        for expiry in self._expiries:
            for opt in ("CE", "PE"):
                tv = time_value.get((expiry, opt))
                if tv is None:
                    continue
                intrinsic = np.maximum(self._spot - strikes, 0) if opt == "CE" else np.maximum(strikes - self._spot, 0)
                mid = intrinsic + tv * np.exp(-(((strikes - self._spot) / width) ** 2))
                for strike, m in zip(strikes, mid):
                    bid = max(round(float(m) - half_spread, 2), 0.05)
                    ask = round(float(m) + half_spread, 2)
                    self.set_quote(expiry, float(strike), opt, bid, ask)

    # ---- MarketDataProvider -----------------------------------------------
    def now(self) -> datetime:
        return self._now

    def _maybe_fail(self) -> None:
        if self.fail_next:
            msg, self.fail_next = self.fail_next, None
            raise MarketDataError(msg)

    def get_underlying_quote(self) -> IndexQuote:
        self._maybe_fail()
        return IndexQuote(f"{self._underlying}", self._spot, self._index_ts_override or self._now)

    def get_india_vix(self) -> IndexQuote:
        self._maybe_fail()
        return IndexQuote("INDIA VIX", self._vix, self._now)

    def get_expiries(self) -> list[date]:
        self._maybe_fail()
        return list(self._expiries)

    def get_option_chain(self, expiry: date) -> OptionChain:
        self._maybe_fail()
        if expiry not in self._expiries:
            raise MarketDataError(f"Unknown expiry {expiry}")
        rows = []
        for strike in self._strikes:
            if (expiry, strike) in self._removed_strikes:
                continue
            legs = {}
            for opt in ("CE", "PE"):
                contract = OptionContract(
                    instrument_key=f"MOCK|{self._underlying}{expiry:%y%m%d}{int(strike)}{opt}",
                    trading_symbol=f"{self._underlying} {int(strike)} {opt} {expiry:%d %b %y}".upper(),
                    underlying=self._underlying, expiry=expiry, strike=strike,
                    option_type=opt, lot_size=self._lot_size)
                stored = self._quotes.get((expiry, strike, opt))
                quote = None
                if stored is not None:
                    quote = Quote(bid=stored.bid, ask=stored.ask, ltp=stored.ltp,
                                  timestamp=stored.ts_override or self._now,
                                  timestamp_source="exchange", feed=stored.feed)
                legs[opt] = OptionQuote(contract, quote)
            rows.append(ChainRow(strike, legs["CE"], legs["PE"]))
        return OptionChain(self._underlying, expiry, self._spot, self._now, tuple(rows))


def make_default_mock(now: Optional[datetime] = None) -> MockMarketDataProvider:
    """Standard mock universe: Tuesday weekly expiries, strikes 24000..27000 step 50."""
    now = now or datetime(2026, 11, 17, 15, 5, tzinfo=IST)
    expiries = [date(2026, 11, 17), date(2026, 11, 24), date(2026, 12, 1), date(2026, 12, 8)]
    return MockMarketDataProvider(expiries, np.arange(24000, 27001, 50), now)


# ---- scripted demo scenario ------------------------------------------------
# Each step: label, IST time, spot, near time value, far CE/PE time value.
# Time values are tuned so that ATM premium differences land in the interesting bands.
DEMO_STEPS = [
    dict(label="Cycle start 15:00+ on expiry day; both differences ABOVE 85 -> no entry",
         at=(2026, 11, 17, 15, 5), spot=25512, tv_near=100, tv_far_ce=200, tv_far_pe=200),
    dict(label="CE difference drops into 60-85 band -> enter 25500 CE box",
         at=(2026, 11, 17, 15, 20), spot=25510, tv_near=100, tv_far_ce=178, tv_far_pe=200),
    dict(label="Next session: ATM moves to 25550. CE in band, PE BELOW 60 (also eligible)",
         at=(2026, 11, 18, 9, 30), spot=25560, tv_near=100, tv_far_ce=170, tv_far_pe=150),
    dict(label="Same ATM (25550): duplicate positions must be blocked",
         at=(2026, 11, 18, 10, 0), spot=25555, tv_near=100, tv_far_ce=170, tv_far_pe=150),
    dict(label="Gap to ATM 25700: box 25700 created directly, no skipped boxes",
         at=(2026, 11, 18, 11, 0), spot=25710, tv_near=100, tv_far_ce=165, tv_far_pe=165),
    dict(label="MANUAL close of 25500 CE only (others stay open); stale far PE quote at 25700",
         at=(2026, 11, 18, 12, 0), spot=25712, tv_near=100, tv_far_ce=165, tv_far_pe=165,
         manual_close=("CE", 25500.0), stale=("far", "PE", 25700.0)),
    dict(label="ATM returns to 25500: closed box becomes eligible again -> REOPEN allowed",
         at=(2026, 11, 18, 14, 0), spot=25498, tv_near=100, tv_far_ce=172, tv_far_pe=200),
]
