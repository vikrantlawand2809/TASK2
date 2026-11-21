"""Quote validation + live/delayed/stale/missing classification (brief s.7)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from broker.market_data import FeedStatus, IndexQuote, Quote
from config import QuoteConfig


@dataclass(frozen=True)
class QuoteCheck:
    ok: bool
    status: FeedStatus
    reasons: tuple[str, ...] = ()
    age_seconds: Optional[float] = None


class QuoteValidator:
    def __init__(self, cfg: QuoteConfig):
        self._cfg = cfg

    def _classify_age(self, ts: Optional[datetime], source: str, now: datetime, declared_feed: Optional[str],
                      label: str) -> tuple[FeedStatus, list[str], Optional[float]]:
        reasons: list[str] = []
        if ts is None:
            return FeedStatus.MISSING, [f"{label}: no timestamp"], None
        age = (now - ts).total_seconds()
        if age < -self._cfg.future_tolerance_seconds:
            return FeedStatus.INVALID, [f"{label}: timestamp {abs(age):.0f}s in the future"], age
        if source in ("fetch_time", "unknown") or not ts.tzinfo:
            if self._cfg.reject_unverifiable_freshness:
                return FeedStatus.UNVERIFIABLE, [f"{label}: freshness unverifiable ({source} timestamp)"], age
            return FeedStatus.UNVERIFIABLE, [f"{label}: freshness unverifiable ({source} timestamp)"], age
        if age <= self._cfg.live_max_age_seconds:
            status = FeedStatus.DELAYED if declared_feed == "delayed" else FeedStatus.LIVE
        elif age <= self._cfg.delayed_max_age_seconds:
            status = FeedStatus.DELAYED
        else:
            status = FeedStatus.STALE
        if status == FeedStatus.DELAYED and not self._cfg.accept_delayed:
            reasons.append(f"{label}: delayed data ({age:.0f}s old) not accepted")
        elif status == FeedStatus.STALE:
            reasons.append(f"{label}: stale quote ({age:.0f}s old)")
        return status, reasons, age

    def check(self, quote: Optional[Quote], now: datetime, label: str) -> QuoteCheck:
        """Validate one option quote for the configured quote-selection method."""
        if quote is None:
            return QuoteCheck(False, FeedStatus.MISSING, (f"{label}: no quote returned",))
        method = self._cfg.method
        problems: list[str] = []
        if method == "ltp":
            if quote.ltp is None or quote.ltp <= 0:
                problems.append(f"{label}: LTP missing/non-positive")
        else:
            if quote.bid is None or quote.ask is None:
                problems.append(f"{label}: bid/ask missing")
            elif quote.bid <= 0 or quote.ask <= 0:
                problems.append(f"{label}: non-positive bid/ask ({quote.bid}/{quote.ask})")
            elif quote.bid > quote.ask:
                return QuoteCheck(False, FeedStatus.INVALID, (f"{label}: crossed market bid {quote.bid} > ask {quote.ask}",))
        if problems:
            return QuoteCheck(False, FeedStatus.MISSING, tuple(problems))
        status, reasons, age = self._classify_age(quote.timestamp, quote.timestamp_source, now, quote.feed, label)
        ok = status == FeedStatus.LIVE or (status == FeedStatus.DELAYED and self._cfg.accept_delayed)
        return QuoteCheck(ok, status, tuple(reasons), age)

    def check_index(self, index: IndexQuote, now: datetime) -> QuoteCheck:
        if index.value is None or index.value <= 0:
            return QuoteCheck(False, FeedStatus.MISSING, (f"{index.symbol}: value missing/non-positive",))
        status, reasons, age = self._classify_age(index.timestamp, index.timestamp_source, now, None, index.symbol)
        ok = status == FeedStatus.LIVE or (status == FeedStatus.DELAYED and self._cfg.accept_delayed)
        return QuoteCheck(ok, status, tuple(reasons), age)
