"""Entry eligibility from the premium difference (brief section 5)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from config import EntryConfig


class PremiumBand(str, Enum):
    IN_RANGE = "IN_RANGE"          # 60..85 inclusive
    BELOW_RANGE = "BELOW_RANGE"    # < 60
    ABOVE_RANGE = "ABOVE_RANGE"    # > 85
    INVALID = "INVALID"            # data anomaly (negative difference)


class EntryOutcome(str, Enum):
    ENTERED = "ENTERED"
    REJECTED_ABOVE_RANGE = "REJECTED_ABOVE_RANGE"
    REJECTED_NEGATIVE_DIFFERENCE = "REJECTED_NEGATIVE_DIFFERENCE"
    REJECTED_DATA_MISSING = "REJECTED_DATA_MISSING"
    REJECTED_DATA_STALE = "REJECTED_DATA_STALE"
    REJECTED_DATA_UNVERIFIABLE = "REJECTED_DATA_UNVERIFIABLE"
    REJECTED_DATA_INVALID = "REJECTED_DATA_INVALID"
    REJECTED_CONTRACT_MISSING = "REJECTED_CONTRACT_MISSING"
    REJECTED_DUPLICATE = "REJECTED_DUPLICATE"


@dataclass(frozen=True)
class EntryAssessment:
    eligible: bool
    band: PremiumBand
    reason: str


class EntryConditions:
    def __init__(self, cfg: EntryConfig):
        self._cfg = cfg

    def assess(self, difference: float) -> EntryAssessment:
        d = round(difference, 4)
        c = self._cfg
        if d < 0 and c.reject_negative_difference:
            return EntryAssessment(False, PremiumBand.INVALID,
                                   f"Negative premium difference {d:g}: far < near is treated as a data anomaly")
        if d > c.in_range_max:
            return EntryAssessment(False, PremiumBand.ABOVE_RANGE,
                                   f"Difference {d:g} > {c.in_range_max:g}: no entry, continue monitoring")
        if d >= c.in_range_min:
            return EntryAssessment(True, PremiumBand.IN_RANGE,
                                   f"Difference {d:g} within {c.in_range_min:g}-{c.in_range_max:g}")
        if c.allow_below_range:
            return EntryAssessment(True, PremiumBand.BELOW_RANGE,
                                   f"Difference {d:g} < {c.in_range_min:g}: eligible per brief")
        return EntryAssessment(False, PremiumBand.BELOW_RANGE, f"Difference {d:g} below range and not allowed by config")
