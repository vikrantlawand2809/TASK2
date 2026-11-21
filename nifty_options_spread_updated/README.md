# NIFTY Multi-Expiry Options Spread — Shadow Trading (Pravrtti Element, Task 2)

Python application that **reads** market data, identifies NIFTY near/far-expiry ATM calendar spreads
(BUY far / SELL near), and **simulates** entries, monitoring, exits and P&L with independent
per-ATM-box positions stored in SQLite.

> ⚠️ **SHADOW MODE ONLY.** The code has no order-placement capability: the broker client is GET-only with a
> market-data path allow-list, the simulated-trading packages never import the broker package, and tests
> enforce both. Do not request order permissions. Brief is confidential — keep this repo private.

> The project has been implemented with a modular market-data provider architecture. Mock market data is used
> for development, testing and demonstration. An Upstox integration adapter is included, but live API
> connectivity has not been verified because authorized credentials were not available. No real trading
> orders are placed.

## Layout
```
main.py                CLI: demo | run | report | close
config.py              ALL thresholds (entry band, quote rules, costs, exits, cycle roll, sizing)
shadow_engine.py       one tick: market data -> monitor positions -> evaluate entries
broker/                market_data.py (models + read-only interface), mock_data_provider.py,
                       broker_client.py (GET-only guard), upstox_provider.py
strategy/              expiry_manager, atm_selector, contract_selector, quote_validation,
                       premium_calculator, entry_conditions
positions/             models, position_manager, pnl_calculator, exit_manager
database/              db_manager.py  (tradebook.db is created at runtime)
reports/               tradebook_export.py, shadow_summary.py
tests/                 95 pytest tests (no credentials needed)   |  tests/test_results.txt = recorded run
sample_data/           synthetic option-chain snapshot (JSON/CSV)
sample_output/         demo tradebook, open/closed, rejected, summary CSVs + demo log
docs/TECHNICAL_NOTE.md assumptions, limitations, unresolved questions for Pravrtti
```

## Setup
```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.10+
pip install -r requirements.txt
cp .env.example .env                                   # fill only when Pravrtti supplies credentials
```

## Demonstration (no credentials, no network)
```bash
python -m pytest -v
python main.py demo                  # scripted 7-step scenario on mock data -> sample_output/
```
The test suite is expanded by freshness, P&L, position-lifecycle and provider-validation cases.

Market freshness:
- `LIVE`, `DELAYED`, `STALE`, `MISSING`, `UNVERIFIABLE`, and `INVALID` are distinct data states.
- Fetch-time timestamps are never presented as exchange quote timestamps.
- Upstox option-chain quotes without exchange timestamps are marked `UNVERIFIABLE` and rejected by default.
- Delayed quotes remain disabled unless explicitly allowed in configuration.

Demo summary terminology:
- `premium_eligible_evaluations` = valid premium-eligible evaluations, including entered or duplicate boxes.
- Entries taken, duplicate-box rejections, above-range rejections and stale/unverifiable rejections are reported separately.

The demo shows, in order: above-85 rejection (no forced entry) → in-range entry → ATM change creates a
new box without closing the old one → below-60 eligible entry → duplicate blocked → gap straight to a
far ATM (no skipped boxes) → stale-quote rejection → manual close of **one** position → closed box re-opened.
Inspect `sample_output/simulated_tradebook.csv`, `rejected_entries.csv`, `shadow_summary.csv` and `shadow_run_demo.log`.

## Live shadow run (after approval)
1. Obtain **data-only** credentials through Pravrtti's approved process; put them in `.env` (never in code/Git):
   `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_ACCESS_TOKEN`.
2. `python main.py run --once` — supervised single tick; check `logs/shadow_run.log` and the CSVs.
3. `python main.py run --interval 30` — continuous polling; Ctrl-C writes reports to `output/`.
4. `python main.py close <POSITION_ID>` — manual *simulated* closure of one position.
5. `python main.py report` — regenerate CSVs from `database/tradebook.db`.

Upstox endpoints used (all GET): `/v2/market-quote/ltp`, `/v2/option/contract`, `/v2/option/chain`.
**The adapter has not been run against the live API** (no credentials at build time) — see Technical Note §3.
Another broker = implement `broker.market_data.MarketDataProvider` and register it in `build_provider`.

## Configuration
Defaults live in `config.py`; override with `--config my.json` (see `config.json.example`; unknown keys are rejected).
Profit target, stop-loss and cycle closure are **off** until Pravrtti confirms them.

## Outputs
`simulated_tradebook.csv` (position level) · `simulated_fills.csv` (leg level) · `open_positions.csv` ·
`closed_positions.csv` · `rejected_entries.csv` (with reasons) · `entry_evaluations.csv` · `market_snapshots.csv` ·
`shadow_summary.csv`.

See **docs/TECHNICAL_NOTE.md** for assumptions, known limitations and the questions to raise with Pravrtti.
