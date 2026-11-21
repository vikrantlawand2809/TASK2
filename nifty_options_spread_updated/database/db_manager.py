"""SQLite persistence for cycles, positions, evaluations and market snapshots."""
from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence

from positions.models import ACTIVE_STATUSES, EvaluationRecord, Position, PositionStatus

_DATETIME_FIELDS = {"created_ts", "entry_ts", "current_diff_ts", "exit_ts"}
_DATE_FIELDS = {"near_expiry", "far_expiry"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id TEXT PRIMARY KEY, near_expiry TEXT, far_expiry TEXT, started_at TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, atm_strike REAL NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('CE','PE')),
    near_expiry TEXT, far_expiry TEXT, near_symbol TEXT, far_symbol TEXT,
    near_instrument_key TEXT, far_instrument_key TEXT,
    lot_size INTEGER, lots INTEGER, quote_method TEXT,
    near_entry_premium REAL, far_entry_premium REAL, initial_diff REAL,
    status TEXT NOT NULL, created_ts TEXT, entry_ts TEXT,
    current_diff REAL, current_diff_ts TEXT, near_mark_exit REAL, far_mark_exit REAL,
    unrealized_gross_pnl REAL, unrealized_net_pnl REAL,
    exit_ts TEXT, exit_reason TEXT, near_exit_premium REAL, far_exit_premium REAL,
    long_pnl REAL, short_pnl REAL, gross_pnl REAL, est_costs REAL, net_pnl REAL, notes TEXT
);
-- DB-level guard: at most ONE active (PENDING/OPEN) position per cycle+strike+type.
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_box
    ON positions(cycle_id, atm_strike, option_type) WHERE status IN ('PENDING','OPEN');
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, cycle_id TEXT, atm_strike REAL, option_type TEXT,
    outcome TEXT, band TEXT, reason TEXT, quote_method TEXT, near_symbol TEXT, far_symbol TEXT,
    near_price REAL, far_price REAL, premium_diff REAL, near_status TEXT, far_status TEXT, position_id TEXT
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, underlying REAL, india_vix REAL,
    underlying_status TEXT, cycle_id TEXT, near_expiry TEXT, far_expiry TEXT, atm_strike REAL, note TEXT
);
"""


def _ser(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class DatabaseManager:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- positions -----------------------------------------------------------
    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> Position:
        data = dict(row)
        for name in _DATETIME_FIELDS:
            data[name] = datetime.fromisoformat(data[name]) if data[name] else None
        for name in _DATE_FIELDS:
            data[name] = date.fromisoformat(data[name]) if data[name] else None
        data["status"] = PositionStatus(data["status"])
        data["notes"] = data["notes"] or ""
        return Position(**data)

    def insert_position(self, pos: Position) -> None:
        cols = [f.name for f in fields(Position)]
        sql = f"INSERT INTO positions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        with self._conn:
            self._conn.execute(sql, [_ser(getattr(pos, c)) for c in cols])

    def update_position(self, pos: Position) -> None:
        cols = [f.name for f in fields(Position) if f.name != "position_id"]
        sql = f"UPDATE positions SET {','.join(c + '=?' for c in cols)} WHERE position_id=?"
        with self._conn:
            self._conn.execute(sql, [_ser(getattr(pos, c)) for c in cols] + [pos.position_id])

    def get_position(self, position_id: str) -> Optional[Position]:
        row = self._conn.execute("SELECT * FROM positions WHERE position_id=?", (position_id,)).fetchone()
        return self._row_to_position(row) if row else None

    def list_positions(self, statuses: Optional[Sequence[PositionStatus]] = None,
                       cycle_id: Optional[str] = None) -> list[Position]:
        sql, params = "SELECT * FROM positions WHERE 1=1", []
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params += [s.value for s in statuses]
        if cycle_id:
            sql += " AND cycle_id=?"
            params.append(cycle_id)
        sql += " ORDER BY created_ts, rowid"
        return [self._row_to_position(r) for r in self._conn.execute(sql, params)]

    def has_active_position(self, cycle_id: str, strike: float, option_type: str) -> bool:
        marks = ",".join("?" * len(ACTIVE_STATUSES))
        row = self._conn.execute(
            f"SELECT 1 FROM positions WHERE cycle_id=? AND atm_strike=? AND option_type=? AND status IN ({marks}) LIMIT 1",
            [cycle_id, strike, option_type] + [s.value for s in ACTIVE_STATUSES]).fetchone()
        return row is not None

    def next_sequence(self, cycle_id: str, strike: float, option_type: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM positions WHERE cycle_id=? AND atm_strike=? AND option_type=?",
            (cycle_id, strike, option_type)).fetchone()
        return int(row[0]) + 1

    # ---- cycles / evaluations / snapshots ---------------------------------
    def upsert_cycle(self, cycle_id: str, near: date, far: date, started_at: datetime) -> bool:
        """Insert cycle if new. Returns True when it was newly created."""
        with self._conn:
            cur = self._conn.execute("INSERT OR IGNORE INTO cycles VALUES (?,?,?,?)",
                                     (cycle_id, near.isoformat(), far.isoformat(), started_at.isoformat()))
        return cur.rowcount == 1

    def insert_evaluation(self, rec: EvaluationRecord) -> None:
        cols = [f.name for f in fields(EvaluationRecord)]
        sql = f"INSERT INTO evaluations ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        with self._conn:
            self._conn.execute(sql, [_ser(getattr(rec, c)) for c in cols])

    def list_evaluations(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM evaluations ORDER BY id")]

    def insert_snapshot(self, ts: datetime, underlying: Optional[float], vix: Optional[float],
                        status: str, cycle_id: Optional[str], near: Optional[date], far: Optional[date],
                        atm: Optional[float], note: str = "") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO market_snapshots (ts,underlying,india_vix,underlying_status,cycle_id,near_expiry,far_expiry,atm_strike,note)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ts.isoformat(), underlying, vix, status, cycle_id, _ser(near), _ser(far), atm, note))

    def list_snapshots(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM market_snapshots ORDER BY id")]
