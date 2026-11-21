"""ATM strike selection from the NEAR-expiry option chain."""
from __future__ import annotations

from typing import Iterable, Optional

from broker.market_data import OptionChain


class ATMSelectionError(ValueError):
    pass


def select_atm(strikes: Iterable[float], spot: Optional[float], tie_break: str = "lower") -> float:
    """Return the available strike closest to `spot`.

    Only strikes that exist in the supplied chain are considered - intermediate
    levels are never manufactured. Exact mid-point ties use `tie_break`.
    """
    available = sorted(set(float(s) for s in strikes))
    if not available:
        raise ATMSelectionError("Near-expiry chain has no strikes")
    if spot is None or spot <= 0:
        raise ATMSelectionError(f"Invalid underlying value: {spot}")
    best = min(available, key=lambda k: (abs(k - spot), k if tie_break == "lower" else -k))
    return best


def select_atm_from_chain(near_chain: OptionChain, spot: Optional[float], tie_break: str = "lower") -> float:
    return select_atm(near_chain.strikes, spot, tie_break)
