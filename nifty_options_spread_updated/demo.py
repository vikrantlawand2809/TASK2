"""Scripted mock-data demonstration (no credentials, no network)."""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from broker.mock_data_provider import DEMO_STEPS, make_default_mock
from config import IST, AppConfig
from database.db_manager import DatabaseManager
from reports.tradebook_export import export_reports
from shadow_engine import ShadowEngine

log = logging.getLogger("nifty_spread.demo")


def run_demo(config: AppConfig, db_path: Path, reports_dir: Path) -> dict:
    if str(db_path) != ":memory:" and Path(db_path).exists():
        Path(db_path).unlink()          # demo always starts from a clean DB
    db = DatabaseManager(db_path)
    provider = make_default_mock()
    engine = ShadowEngine(provider, config, db)
    near, far = date(2026, 11, 24), date(2026, 12, 1)
    log.info("=== DEMO START (mock data, shadow mode, no orders possible) ===")
    for i, step in enumerate(DEMO_STEPS, 1):
        provider.set_time(datetime(*step["at"], tzinfo=IST))
        provider.set_spot(step["spot"])
        provider.populate_synthetic({(near, "CE"): step["tv_near"], (near, "PE"): step["tv_near"],
                                     (far, "CE"): step["tv_far_ce"], (far, "PE"): step["tv_far_pe"]})
        if "stale" in step:
            which, opt, strike = step["stale"]
            provider.make_stale(far if which == "far" else near, strike, opt, 300)
        log.info("--- Step %d: %s", i, step["label"])
        if "manual_close" in step:
            opt, strike = step["manual_close"]
            target = next((p for p in engine.positions.list_open() if p.atm_strike == strike and p.option_type == opt), None)
            if target:
                engine.manual_close(target.position_id)
        res = engine.run_once()
        print(f"[{i}] {provider.now():%d-%b %H:%M} spot={res.spot} ATM={res.atm} cycle={res.cycle_id} | "
              + ", ".join(f"{e.atm_strike:g}{e.option_type}:{e.outcome}" + (f"({e.premium_diff:.1f})" if e.premium_diff is not None else "")
                          for e in res.evaluations))
    paths = export_reports(db, reports_dir)
    log.info("=== DEMO END: reports written to %s ===", reports_dir)
    db.close()
    return paths
