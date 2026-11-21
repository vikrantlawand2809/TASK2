# NIFTY Multi-Expiry Options Spread — Shadow Trading

Task 2 submission for Pravrtti Element. This Python application reads market data, identifies NIFTY near/far-expiry ATM calendar spreads (buy far expiry, sell near expiry), and simulates entries, position monitoring, exits, and P&L.

> **Shadow mode only.** The application does not place real orders. Its broker client is read-only, and the tests check that simulated trading cannot place orders.

## Project contents

- [`nifty_options_spread_updated/`](./nifty_options_spread_updated/) — application source, tests, configuration examples, synthetic market data, and demo outputs.
- [`TECHNICAL_NOTE.md`](./TECHNICAL_NOTE.md) — assumptions, limitations, and open questions.
- [`shadow_summary.csv`](./shadow_summary.csv) and [`simulated_tradebook.csv`](./simulated_tradebook.csv) — sample report exports.

The project also includes a detailed [project README](./nifty_options_spread_updated/README.md).

## Requirements

- Python 3.10 or later
- Dependencies listed in `nifty_options_spread_updated/requirements.txt`

## Install and run the demo

From the repository root:

```powershell
cd nifty_options_spread_updated
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -v
python main.py demo
```

The demo uses mock market data and does not need credentials or network access. It writes example reports under `sample_output/`.

## Run commands

Run one supervised shadow-trading cycle:

```powershell
python main.py run --once
```

Run with a 30-second polling interval:

```powershell
python main.py run --interval 30
```

Other commands:

```powershell
python main.py report
python main.py close <POSITION_ID>
python main.py --help
```

`close` closes a simulated position only. Configuration defaults are in `config.py`; `config.json.example` shows the supported JSON overrides.

## Data and credentials

Mock data is used for development and demonstrations. An Upstox market-data adapter is included, but live API connectivity has not been verified. If authorized data-only credentials are later supplied, copy `.env.example` to `.env` and follow the approved credential process. Never commit `.env` or credentials.

The project README describes the assignment brief as confidential and requests that the repository remain private.

## Safety and limitations

The application has no order-placement capability. Profit targets, stop-losses, and cycle closure are disabled by default pending confirmation. Quote freshness is validated; unverifiable or stale quotes are rejected by default. See the [technical note](./TECHNICAL_NOTE.md) for details.
