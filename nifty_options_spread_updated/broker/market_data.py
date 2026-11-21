"""Provider-neutral market-data models and the read-only provider interface.

Nothing in this module (or anywhere in the broker package) can place, modify or
cancel orders. The interface only exposes *read* operations.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class FeedStatus(str, Enum):
    """Data-quality classification required by brief section 7."""
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    MISSING = "MISSING"
    UNVERIFIABLE = "UNVERIFIABLE"
    INVALID = "INVALID"


class MarketDataError(RuntimeError):
    """Raised when market data cannot be retrieved or parsed."""


@dataclass(frozen=True)
class Quote:
    """Top-of-book quote for one instrument."""
    bid: Optional[float] = None
    ask: Optional[float] = None
    ltp: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    timestamp: Optional[datetime] = None      # tz-aware (IST)
    timestamp_source: str = "exchange"        # exchange | fetch_time | unknown
    feed: Optional[str] = None                # provider-declared: live | delayed | None


@dataclass(frozen=True)
class IndexQuote:
    """NIFTY / India VIX level."""
    symbol: str
    value: Optional[float]
    timestamp: Optional[datetime]
    timestamp_source: str = "exchange"


@dataclass(frozen=True)
class OptionContract:
    instrument_key: str
    trading_symbol: str
    underlying: str
    expiry: date
    strike: float
    option_type: str                    # "CE" | "PE"
    lot_size: Optional[int] = None


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    quote: Optional[Quote]


@dataclass(frozen=True)
class ChainRow:
    strike: float
    ce: Optional[OptionQuote] = None
    pe: Optional[OptionQuote] = None

    def get(self, option_type: str) -> Optional[OptionQuote]:
        return self.ce if option_type == "CE" else self.pe


@dataclass(frozen=True)
class OptionChain:
    underlying: str
    expiry: date
    underlying_spot: Optional[float]
    fetched_at: datetime
    rows: tuple[ChainRow, ...]

    @property
    def strikes(self) -> list[float]:
        return sorted(r.strike for r in self.rows)

    def row_at(self, strike: float) -> Optional[ChainRow]:
        for row in self.rows:
            if row.strike == strike:
                return row
        return None


class MarketDataProvider(ABC):
    """Read-only market data source (mock or broker-backed)."""

    name: str = "abstract"

    @abstractmethod
    def now(self) -> datetime:
        """Current tz-aware time as seen by the provider (mock may simulate)."""

    @abstractmethod
    def get_underlying_quote(self) -> IndexQuote: ...

    @abstractmethod
    def get_india_vix(self) -> IndexQuote: ...

    @abstractmethod
    def get_expiries(self) -> list[date]:
        """All available option expiry dates (exchange-supplied, never hardcoded)."""

    @abstractmethod
    def get_option_chain(self, expiry: date) -> OptionChain: ...
