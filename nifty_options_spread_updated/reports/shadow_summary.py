"""Shadow-run summary (brief s.9): entries considered / taken / rejected + P&L."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

from database.db_manager import DatabaseManager
from positions.models import PositionStatus


def build_summary(db: DatabaseManager) -> list[dict]:
    evals = db.list_evaluations()
    outcomes = Counter(e["outcome"] for e in evals)
    entered = outcomes.get("ENTERED", 0)
    duplicates = outcomes.get("REJECTED_DUPLICATE", 0)
    above_range = outcomes.get("REJECTED_ABOVE_RANGE", 0)
    stale = outcomes.get("REJECTED_DATA_STALE", 0)
    unverifiable = outcomes.get("REJECTED_DATA_UNVERIFIABLE", 0)
    missing = outcomes.get("REJECTED_DATA_MISSING", 0)
    invalid = outcomes.get("REJECTED_DATA_INVALID", 0)
    contract_missing = outcomes.get("REJECTED_CONTRACT_MISSING", 0)
    negative = outcomes.get("REJECTED_NEGATIVE_DIFFERENCE", 0)
    premium_eligible = sum(v for k, v in outcomes.items()
                           if k not in {"REJECTED_DATA_MISSING", "REJECTED_DATA_STALE",
                                        "REJECTED_DATA_UNVERIFIABLE", "REJECTED_DATA_INVALID",
                                        "REJECTED_CONTRACT_MISSING", "REJECTED_NEGATIVE_DIFFERENCE",
                                        "REJECTED_ABOVE_RANGE"})
    premium_eligible = entered + duplicates
    rejected = len(evals) - entered
    positions = db.list_positions()
    open_pos = [p for p in positions if p.status == PositionStatus.OPEN]
    closed = [p for p in positions if p.status == PositionStatus.CLOSED]
    gross = round(sum(p.gross_pnl or 0 for p in closed), 2)
    costs = round(sum(p.est_costs or 0 for p in closed), 2)
    net = round(sum(p.net_pnl or 0 for p in closed), 2)
    unreal_g = round(sum(p.unrealized_gross_pnl or 0 for p in open_pos), 2)
    unreal_n = round(sum(p.unrealized_net_pnl or 0 for p in open_pos), 2)
    rows = [
        ("total_opportunities_evaluated", len(evals), "Every ATM x CE/PE evaluation, incl. repeats on later polls"),
        ("premium_eligible_evaluations", premium_eligible,
         "Valid quotes within 60-85 or below 60, including entered and duplicate-box evaluations"),
        ("simulated_entries_taken", entered, ""),
        ("duplicate_box_rejections", duplicates, "Active position already exists for the same cycle/strike/type"),
        ("above_range_rejections", above_range, "Valid premium difference above 85"),
        ("stale_data_rejections", stale, "One or both quote timestamps are stale"),
        ("unverifiable_data_rejections", unverifiable, "Fetch-time or otherwise unverifiable quote freshness"),
        ("missing_data_rejections", missing, ""),
        ("invalid_data_rejections", invalid, ""),
        ("contract_missing_rejections", contract_missing, ""),
        ("negative_difference_rejections", negative, ""),
        ("rejected_entries", rejected, "All non-entered evaluations; categories reconcile to this total"),
        ("open_positions", len(open_pos), ""),
        ("closed_positions", len(closed), ""),
        ("total_simulated_gross_pnl_realised", gross, "INR, closed positions only"),
        ("total_estimated_costs_realised", costs, "INR, ESTIMATED (placeholder rates)"),
        ("total_simulated_net_pnl_realised", net, "INR, closed positions only"),
        ("unrealised_gross_pnl_open", unreal_g, "INR, mark-to-exit prices; not a result"),
        ("unrealised_net_pnl_open", unreal_n, "INR, mark-to-exit prices; not a result"),
    ]
    rows += [(f"outcome_{k}", v, "Evaluation outcome count") for k, v in sorted(outcomes.items())]
    return [{"metric": m, "value": v, "note": n} for m, v, n in rows]


def write_summary(db: DatabaseManager, path: Path) -> Path:
    pd.DataFrame(build_summary(db), dtype=object).to_csv(path, index=False)
    return path
