"""NIFTY multi-expiry calendar-spread SHADOW-TRADING application.

  python main.py demo                 # scripted run on mock data (no credentials)
  python main.py run [--once]         # live shadow run on approved Upstox data (read-only)
  python main.py report               # regenerate CSV reports from the SQLite DB
  python main.py close POSITION_ID    # manual SIMULATED closure of one position

No command can place, modify or cancel a real order.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from broker.broker_client import MissingCredentialsError, build_provider
from config import BrokerCredentials, load_config, resolve_path
from database.db_manager import DatabaseManager
from logging_setup import setup_logging
from reports.tradebook_export import export_reports
from shadow_engine import ShadowEngine


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="optional JSON config override (see config.json.example)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_demo = sub.add_parser("demo")
    p_demo.add_argument("--db", default="database/demo_tradebook.db")
    p_demo.add_argument("--reports-dir", default="sample_output")
    p_run = sub.add_parser("run")
    p_run.add_argument("--once", action="store_true")
    p_run.add_argument("--interval", type=int)
    p_rep = sub.add_parser("report")
    p_rep.add_argument("--db")
    p_rep.add_argument("--reports-dir")
    p_close = sub.add_parser("close")
    p_close.add_argument("position_id")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    creds = BrokerCredentials.from_env()
    setup_logging(resolve_path(config.paths.logs_dir), secrets=creds.secret_values())
    log = logging.getLogger("nifty_spread.main")
    log.info("Application startup | command=%s | SHADOW MODE (read-only, no orders)", args.cmd)

    try:
        if args.cmd == "demo":
            from demo import run_demo
            paths = run_demo(config, resolve_path(args.db), resolve_path(args.reports_dir))
            print("\nReports:", *[str(p) for p in paths.values()], sep="\n  ")
            return 0

        db = DatabaseManager(resolve_path(getattr(args, "db", None) or config.paths.db_path))
        out_dir = resolve_path(getattr(args, "reports_dir", None) or config.paths.reports_dir)

        if args.cmd == "report":
            print(*[str(p) for p in export_reports(db, out_dir).values()], sep="\n")
            return 0

        try:
            provider = build_provider(config, creds)
        except MissingCredentialsError as exc:
            log.error("%s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        engine = ShadowEngine(provider, config, db)
        try:
            provider.get_expiries()
            log.info("API connection status: OK (%s)", provider.name)
        except Exception as exc:  # noqa: BLE001
            log.error("API connection status: FAILED (%s)", type(exc).__name__)
            return 2

        if args.cmd == "close":
            pos = engine.manual_close(args.position_id)
            print(f"Closed {pos.position_id}: gross={pos.gross_pnl} net={pos.net_pnl}")
            return 0

        interval = args.interval or config.poll_interval_seconds
        try:
            while True:
                try:
                    engine.run_once()
                except Exception:  # noqa: BLE001 - keep the shadow loop alive
                    log.exception("Unexpected error in tick")
                if args.once:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            log.info("Interrupted by user")
        export_reports(db, out_dir)
        log.info("Application shutdown | reports in %s", out_dir)
        return 0
    except Exception:  # noqa: BLE001
        log.exception("Fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
