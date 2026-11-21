"""Central configuration.

All strategy thresholds live here (or in an optional JSON override file) and
nowhere else in the code base. Items the Pravrtti brief does NOT specify
(profit target, stop-loss, cycle-closure) default to disabled/None - they are
never invented.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import time
from pathlib import Path
from typing import Any, Final, Optional, get_type_hints
from zoneinfo import ZoneInfo

IST: Final = ZoneInfo("Asia/Kolkata")
PROJECT_ROOT: Final = Path(__file__).resolve().parent

# Hard safety switch. There is deliberately no code path that reads this as
# False: the application has no order-placement capability at all.
SHADOW_MODE_ONLY: Final[bool] = True

VALID_QUOTE_METHODS: Final = ("executable", "mid", "ltp")


def parse_hhmm(value: str) -> time:
    """Parse 'HH:MM' into a datetime.time."""
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


@dataclass(frozen=True)
class EntryConfig:
    """Premium-difference entry band (brief section 5)."""
    in_range_min: float = 60.0          # 60..85 inclusive = eligible
    in_range_max: float = 85.0          # > 85 = do not enter, keep monitoring
    allow_below_range: bool = True      # < 60 also eligible (subject to valid quotes)
    # ASSUMPTION (see docs): a negative difference (far < near, same strike) is
    # treated as a data anomaly, not as "below 60".
    reject_negative_difference: bool = True


@dataclass(frozen=True)
class QuoteConfig:
    """Quote selection + freshness rules (brief section 7)."""
    # executable: BUY at ask, SELL at bid | mid: (bid+ask)/2 | ltp: last price
    method: str = "executable"
    live_max_age_seconds: float = 15.0
    delayed_max_age_seconds: float = 120.0   # older than this = STALE
    accept_delayed: bool = False
    # Upstox option-chain rows carry no per-quote timestamp, so the adapter
    # stamps fetch time. Set True to refuse such quotes entirely.
    reject_unverifiable_freshness: bool = True
    future_tolerance_seconds: float = 5.0


@dataclass(frozen=True)
class CostConfig:
    """ESTIMATED transaction costs. Placeholders - verify against the broker
    contract note / current regulatory rates before relying on net P&L."""
    brokerage_per_order: float = 20.0
    stt_sell_rate: float = 0.001          # on sell-side premium turnover
    exchange_txn_rate: float = 0.0003503  # on premium turnover
    sebi_rate: float = 0.000001
    stamp_buy_rate: float = 0.00003       # on buy-side premium turnover
    gst_rate: float = 0.18                # on brokerage + exchange + sebi


@dataclass(frozen=True)
class ExitConfig:
    """Exit rules. The brief mentions profit/risk/cycle exits but gives no
    numbers, so every one is OFF until Pravrtti confirms it."""
    profit_target_pnl: Optional[float] = None   # gross P&L in INR, mark-to-exit
    stop_loss_pnl: Optional[float] = None       # positive number = max gross loss in INR
    cycle_closure_enabled: bool = False


@dataclass(frozen=True)
class CycleConfig:
    # ASSUMPTION: on an expiry day, from roll_time onwards the *next* expiry
    # becomes "near" and a new cycle starts (brief: reference ~3:00 PM on expiry day).
    roll_on_expiry_day: bool = True
    roll_time: str = "15:00"


@dataclass(frozen=True)
class SessionConfig:
    enforce_market_hours: bool = True
    open_time: str = "09:15"
    close_time: str = "15:30"


@dataclass(frozen=True)
class SizingConfig:
    lots: int = 1


@dataclass(frozen=True)
class PathsConfig:
    db_path: str = "database/tradebook.db"
    logs_dir: str = "logs"
    reports_dir: str = "output"


@dataclass(frozen=True)
class AppConfig:
    underlying: str = "NIFTY"
    underlying_key: str = "NSE_INDEX|Nifty 50"
    vix_key: str = "NSE_INDEX|India VIX"
    poll_interval_seconds: int = 30
    atm_tie_break: str = "lower"          # when spot is exactly between strikes
    entry: EntryConfig = field(default_factory=EntryConfig)
    quotes: QuoteConfig = field(default_factory=QuoteConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    exits: ExitConfig = field(default_factory=ExitConfig)
    cycle: CycleConfig = field(default_factory=CycleConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)

    def validate(self) -> "AppConfig":
        if self.quotes.method not in VALID_QUOTE_METHODS:
            raise ValueError(f"quotes.method must be one of {VALID_QUOTE_METHODS}")
        if self.entry.in_range_min > self.entry.in_range_max:
            raise ValueError("entry.in_range_min cannot exceed entry.in_range_max")
        if self.sizing.lots < 1:
            raise ValueError("sizing.lots must be >= 1")
        if self.atm_tie_break not in ("lower", "higher"):
            raise ValueError("atm_tie_break must be 'lower' or 'higher'")
        for name in (self.cycle.roll_time, self.session.open_time, self.session.close_time):
            parse_hhmm(name)
        return self


def _build(cls: type, data: dict[str, Any]):
    """Recursively build a (nested) frozen dataclass from a dict; reject typos."""
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown config keys for {cls.__name__}: {sorted(unknown)}")
    kwargs = {}
    for key, value in data.items():
        hint = hints[key]
        kwargs[key] = _build(hint, value) if is_dataclass(hint) and isinstance(value, dict) else value
    return cls(**kwargs)


def load_config(path: Optional[str | Path] = None) -> AppConfig:
    """Load defaults, optionally overridden by a JSON file."""
    if path is None:
        return AppConfig().validate()
    with open(path, "r", encoding="utf-8") as handle:
        return _build(AppConfig, json.load(handle)).validate()


def resolve_path(value: str) -> Path:
    """Resolve a config path relative to the project root."""
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class BrokerCredentials:
    """Credentials loaded from environment variables only. repr() is masked."""
    api_key: str = ""
    api_secret: str = ""
    access_token: str = ""

    @classmethod
    def from_env(cls) -> "BrokerCredentials":
        try:
            from dotenv import load_dotenv
            load_dotenv(PROJECT_ROOT / ".env")
        except ImportError:  # python-dotenv optional at runtime
            pass
        return cls(
            api_key=os.getenv("UPSTOX_API_KEY", ""),
            api_secret=os.getenv("UPSTOX_API_SECRET", ""),
            access_token=os.getenv("UPSTOX_ACCESS_TOKEN", ""),
        )

    @property
    def has_access_token(self) -> bool:
        return bool(self.access_token)

    def secret_values(self) -> tuple[str, ...]:
        return tuple(v for v in (self.api_key, self.api_secret, self.access_token) if v)

    def __repr__(self) -> str:  # never leak secrets via repr/logging
        return "BrokerCredentials(api_key=***, api_secret=***, access_token=***)"
