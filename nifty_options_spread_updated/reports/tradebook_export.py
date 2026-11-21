"""CSV exports: tradebook, open, closed, rejected entries, fills, evaluations."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pandas as pd

from database.db_manager import DatabaseManager
from positions.models import Position, PositionStatus
from reports.shadow_summary import write_summary


def _position_row(p: Position) -> dict:
    row = asdict(p)
    row["status"] = p.status.value
    row["quantity"] = p.quantity
    return row


def _fills(positions: list[Position]) -> list[dict]:
    """Leg-level fills (what a broker tradebook would show) for entered positions."""
    out = []
    for p in positions:
        if p.entry_ts is None:
            continue
        base = dict(position_id=p.position_id, cycle_id=p.cycle_id, strike=p.atm_strike,
                    option_type=p.option_type, quantity=p.quantity, simulated=True)
        out += [dict(base, ts=p.entry_ts, leg="FAR", side="BUY", symbol=p.far_symbol, price=p.far_entry_premium, phase="ENTRY"),
                dict(base, ts=p.entry_ts, leg="NEAR", side="SELL", symbol=p.near_symbol, price=p.near_entry_premium, phase="ENTRY")]
        if p.status == PositionStatus.CLOSED:
            out += [dict(base, ts=p.exit_ts, leg="FAR", side="SELL", symbol=p.far_symbol, price=p.far_exit_premium, phase="EXIT"),
                    dict(base, ts=p.exit_ts, leg="NEAR", side="BUY", symbol=p.near_symbol, price=p.near_exit_premium, phase="EXIT")]
    return out


def export_reports(db: DatabaseManager, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    positions = db.list_positions()
    pos_df = pd.DataFrame([_position_row(p) for p in positions])
    evals = pd.DataFrame(db.list_evaluations())
    paths: dict[str, Path] = {}

    def save(name: str, df: pd.DataFrame) -> None:
        path = out_dir / name
        df.to_csv(path, index=False)
        paths[name] = path

    save("simulated_tradebook.csv", pos_df)
    status = pos_df["status"] if not pos_df.empty else pd.Series(dtype=str)
    save("open_positions.csv", pos_df[status == "OPEN"] if not pos_df.empty else pos_df)
    save("closed_positions.csv", pos_df[status == "CLOSED"] if not pos_df.empty else pos_df)
    save("simulated_fills.csv", pd.DataFrame(_fills(positions)))
    save("entry_evaluations.csv", evals)
    rejected = evals[evals["outcome"] != "ENTERED"] if not evals.empty else evals
    save("rejected_entries.csv", rejected)
    save("market_snapshots.csv", pd.DataFrame(db.list_snapshots()))
    paths["shadow_summary.csv"] = write_summary(db, out_dir / "shadow_summary.csv")
    return paths
