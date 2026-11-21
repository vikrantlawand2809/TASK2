import csv

from database.db_manager import DatabaseManager
from positions.models import PositionStatus
from reports.tradebook_export import export_reports
from tests.helpers import NEXT_DAY, make_env, set_diff


def test_sqlite_persistence_roundtrip_across_connections(tmp_path):
    path = tmp_path / "t.db"
    e = make_env()
    e.db.close()
    e.db = DatabaseManager(path)
    from shadow_engine import ShadowEngine
    engine = ShadowEngine(e.provider, e.cfg, e.db)
    set_diff(e.provider, 25500, "CE", 70)
    engine.run_once()
    pos = engine.positions.list_open()[0]
    e.db.close()

    db2 = DatabaseManager(path)                              # fresh connection = restart
    again = db2.get_position(pos.position_id)
    assert again.status == PositionStatus.OPEN
    assert (again.atm_strike, again.option_type, again.initial_diff) == (25500.0, "CE", pos.initial_diff)
    assert again.entry_ts == pos.entry_ts and again.near_expiry == pos.near_expiry
    assert len(db2.list_evaluations()) == 2 and len(db2.list_snapshots()) == 1


def test_csv_reports_and_summary_counts(tmp_path):
    e = make_env()
    set_diff(e.provider, 25500, "CE", 70)
    set_diff(e.provider, 25500, "PE", 95)
    e.engine.run_once()
    e.provider.set_time(NEXT_DAY)
    set_diff(e.provider, 25500, "CE", 71)
    e.engine.run_once()                                      # duplicate for CE
    e.engine.manual_close(e.engine.positions.list_open()[0].position_id)
    paths = export_reports(e.db, tmp_path)
    for name in ("simulated_tradebook.csv", "open_positions.csv", "closed_positions.csv", "rejected_entries.csv",
                 "shadow_summary.csv", "simulated_fills.csv", "entry_evaluations.csv"):
        assert paths[name].exists()
    summary = {r["metric"]: r["value"] for r in csv.DictReader(open(paths["shadow_summary.csv"]))}
    assert summary["simulated_entries_taken"] == "1"
    assert summary["premium_eligible_evaluations"] == "2"
    assert summary["closed_positions"] == "1" and summary["open_positions"] == "0"
    assert int(summary["total_opportunities_evaluated"]) == 4
    assert int(summary["rejected_entries"]) == 3
    rejection_categories = (
        int(summary["duplicate_box_rejections"])
        + int(summary["above_range_rejections"])
        + int(summary["stale_data_rejections"])
        + int(summary["unverifiable_data_rejections"])
        + int(summary["missing_data_rejections"])
        + int(summary["invalid_data_rejections"])
        + int(summary["contract_missing_rejections"])
        + int(summary["negative_difference_rejections"])
    )
    assert rejection_categories == int(summary["rejected_entries"])
    rej = list(csv.DictReader(open(paths["rejected_entries.csv"])))
    assert all(r["reason"] for r in rej)
    fills = list(csv.DictReader(open(paths["simulated_fills.csv"])))
    assert {(f["leg"], f["side"], f["phase"]) for f in fills} == {
        ("FAR", "BUY", "ENTRY"), ("NEAR", "SELL", "ENTRY"), ("FAR", "SELL", "EXIT"), ("NEAR", "BUY", "EXIT")}
