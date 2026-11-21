"""Near/far expiry identification. Dates always come from provider data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from config import CycleConfig, parse_hhmm


class ExpiryError(ValueError):
    pass


@dataclass(frozen=True)
class ExpiryPair:
    near: date      # sooner expiry  -> SHORT leg
    far: date       # next expiry    -> LONG leg
    cycle_id: str


class ExpiryManager:
    def __init__(self, cfg: CycleConfig):
        self._cfg = cfg

    def resolve(self, expiries: Iterable[date], now: datetime) -> ExpiryPair:
        """Near = nearest tradable expiry; far = the subsequent expiry.

        ASSUMPTION (open question #1): on an expiry day, from `roll_time` onward
        the expiring contract is treated as finished and the next expiry becomes
        'near' (this is the start of the new cycle, per the ~3:00 PM reference).
        """
        today = now.date()
        upcoming = sorted({e for e in expiries if e >= today})
        if (self._cfg.roll_on_expiry_day and upcoming and upcoming[0] == today
                and now.time() >= parse_hhmm(self._cfg.roll_time)):
            upcoming = upcoming[1:]
        if len(upcoming) < 2:
            raise ExpiryError(f"Need at least two upcoming expiries, found {len(upcoming)}")
        near, far = upcoming[0], upcoming[1]
        return ExpiryPair(near=near, far=far, cycle_id=f"CYC-{near:%Y%m%d}")
