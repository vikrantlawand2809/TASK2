"""Dump a SYNTHETIC mock option-chain snapshot (JSON + CSV) for documentation/demo."""
import csv, json, sys
from datetime import date, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from broker.mock_data_provider import make_default_mock
from config import IST

p = make_default_mock(datetime(2026, 11, 18, 9, 30, tzinfo=IST))
p.set_spot(25560)
near, far = date(2026, 11, 24), date(2026, 12, 1)
p.populate_synthetic({(near, "CE"): 100, (near, "PE"): 100, (far, "CE"): 170, (far, "PE"): 150})
rows = []
for exp in (near, far):
    for r in p.get_option_chain(exp).rows:
        if 25450 <= r.strike <= 25650:
            for oq in (r.ce, r.pe):
                q = oq.quote
                rows.append(dict(timestamp=q.timestamp.isoformat(), expiry=exp.isoformat(), strike=r.strike,
                                 type=oq.contract.option_type, symbol=oq.contract.trading_symbol, bid=q.bid, ask=q.ask,
                                 ltp=q.ltp, lot_size=oq.contract.lot_size, feed=q.feed))
out = Path(__file__).parent
(out / "sample_market_snapshot.json").write_text(json.dumps(dict(
    note="SYNTHETIC mock data - not real market prices", underlying="NIFTY", underlying_value=25560, india_vix=13.5,
    expiries=[e.isoformat() for e in p.get_expiries()], near=near.isoformat(), far=far.isoformat(), quotes=rows), indent=2))
with open(out / "sample_option_quotes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print(len(rows), "quotes written")
