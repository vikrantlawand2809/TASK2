"""Quote selection (one documented method for BOTH legs) and premium difference."""
from __future__ import annotations

from dataclasses import dataclass

from broker.market_data import Quote
from config import VALID_QUOTE_METHODS

BUY, SELL = "BUY", "SELL"


class QuoteSelector:
    """Maps a quote + trade side to a price.

    executable: BUY at ask, SELL at bid (conservative; LTP is never assumed executable)
    mid       : (bid+ask)/2 for both sides
    ltp       : last traded price for both sides (least preferred)
    The same method is always applied to both legs.
    """

    def __init__(self, method: str):
        if method not in VALID_QUOTE_METHODS:
            raise ValueError(f"Unknown quote method {method}")
        self.method = method

    def price(self, quote: Quote, side: str) -> float:
        if self.method == "ltp":
            value = quote.ltp
        elif self.method == "mid":
            value = (quote.bid + quote.ask) / 2
        else:
            value = quote.ask if side == BUY else quote.bid
        if value is None:
            raise ValueError("Quote lacks the field required by the selected method")
        return round(float(value), 4)


def premium_difference(far_premium: float, near_premium: float) -> float:
    """Premium Difference = Far-expiry premium - Near-expiry premium."""
    return round(far_premium - near_premium, 4)


@dataclass(frozen=True)
class PremiumResult:
    far_price: float      # price at which the far (long) leg would be BOUGHT
    near_price: float     # price at which the near (short) leg would be SOLD
    difference: float
    method: str


class PremiumCalculator:
    """Entry-side premium difference: far BUY price - near SELL price."""

    def __init__(self, selector: QuoteSelector):
        self._selector = selector

    def entry_difference(self, far_quote: Quote, near_quote: Quote) -> PremiumResult:
        far = self._selector.price(far_quote, BUY)
        near = self._selector.price(near_quote, SELL)
        return PremiumResult(far, near, premium_difference(far, near), self._selector.method)
