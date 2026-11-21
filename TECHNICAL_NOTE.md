# Technical Note — NIFTY Multi-Expiry Spread (Shadow Mode)

*Source of truth: Pravrtti brief PE-QR-01-CAND. Confidential — do not publish or share outside the assessment.*

## 1. What is confirmed vs. assumed

| Topic | Status | Implementation |
|---|---|---|
| Far = BUY, near = SELL, same strike/type | **Confirmed** | `ContractSelector` enforces same underlying/strike/type and `near.expiry < far.expiry` |
| Premium diff = far − near | **Confirmed** | `premium_calculator.premium_difference` |
| 60–85 eligible; <60 eligible; >85 no entry, keep monitoring | **Confirmed** | `EntryConditions` (bounds inclusive, set in `config.py`) |
| ATM = closest strike in the **near-expiry chain**; same strike in far | **Confirmed** | `atm_selector.select_atm`; never invents strikes |
| No forced entry at 3 PM; keep monitoring later sessions | **Confirmed** | engine re-evaluates every tick within the cycle |
| One position per ATM box; no duplicates; re-open after close; boxes independent | **Confirmed** | `PositionManager` + DB partial unique index |
| Shadow only, no orders | **Confirmed** | read-only HTTP guard, no order code anywhere, tested |
| Quote method: far BUY @ ask, near SELL @ bid | **Assumption** | `quotes.method = executable` (also `mid`, `ltp`); same method for both legs |
| Quote freshness | **Required safety rule** | `UNVERIFIABLE` fetch-time timestamps are rejected by default; stale/missing/invalid legs never open positions |
| Box = strike **and** option type (CE/PE tracked separately) | **Assumption** | duplicate key = (cycle, strike, type) |
| Negative difference = data anomaly, not "below 60" | **Assumption** | `entry.reject_negative_difference` |
| Cycle roll: on expiry day from 15:00 the next expiry becomes "near" | **Assumption** | `cycle.roll_time`, `ExpiryManager` |
| Profit target / stop-loss / cycle closure | **NOT specified** | optional rules, **all disabled by default** |
| Cost rates, 1 lot, quote-age thresholds (15 s live / 120 s stale) | **Placeholder** | `config.py` |

## 2. Questions for the designated Pravrtti contact

1. **ATM example conflict.** The brief shows NIFTY 25,530 → ATM 25,500, but on a 50-point strike grid 25,530 is closer to 25,550. The code follows the written rule (closest strike in the near-expiry chain). Which is intended, or is the chain's strike spacing different?
2. **Cycle start.** At ~3 PM on which expiry day does a cycle begin, and which contract is "near" at that moment (the expiring one or the next)? Are entries allowed on the near contract's own expiry day before 3 PM?
3. **Box definition.** One strike holding both a CE and a PE spread, or one box per strike *per option type*? Implemented the latter.
4. **Quote method.** Is far-at-ask / near-at-bid the intended entry price, and far-at-bid / near-at-ask for exits? Is LTP or mid ever acceptable?
5. **"Below 60 … subject to agreed calculation rules".** What are those rules? Is there a floor (e.g. zero/negative difference)?
6. **Exits.** Profit target and stop-loss values and units (INR P&L vs premium-difference points)? Cycle closure: at what time and prices, and how is the expiring near leg settled (3 PM market quote vs exchange settlement)? May a box re-open in the same tick it was just closed?
7. **Cost model.** Brokerage plan and charge rates for "estimated costs".
8. **Data.** Which provider/endpoints are approved (Upstox implemented)? Is delayed data acceptable? Required polling frequency? Do monthly expiries count in "next available expiry"?
9. **Sizing.** Lots per position (1 assumed). Lot size is read from contract data, never hardcoded.
10. **Open positions with no data at cycle end** — leave open and flag, or close as `DATA_UNAVAILABLE`?

## 3. Known limitations

* **Not validated against the live broker API.** The Upstox adapter parses payload shapes from Upstox's public documentation and community examples and is unit-tested with canned responses only. The first authorised run must be supervised. Endpoints: `/v2/market-quote/ltp`, `/v2/option/contract`, `/v2/option/chain`.
* **Freshness is unverifiable on Upstox option-chain data**: `market_data` carries no per-quote timestamp, so the adapter stamps *fetch time* (`timestamp_source=fetch_time`). A stale illiquid quote can therefore look live. Mitigations: bid/ask > 0 and non-crossed checks; optional `quotes.reject_unverifiable_freshness`.
* Polling (not streaming); no exchange-holiday calendar (weekday + 09:15–15:30 check only); no slippage/latency model; simulated fills assume full quantity at top of book.
* Cost rates are placeholders — net P&L is indicative only.
* After a cycle rolls, old-cycle positions can only be marked while their chains remain available from the API; otherwise they stay `OPEN` with a stale `current_diff_ts` (logged).
* PENDING / REJECTED / DATA_UNAVAILABLE states are implemented and tested in the state machine, but the engine records rejected *entry attempts* in the `evaluations` table (with reasons) rather than as position rows, to avoid thousands of rows from repeated polling.
* Only Upstox is implemented; Zerodha/Dhan would be another `MarketDataProvider`.
* Passing mock-data tests proves logic, **not profitability and not live-readiness**.

## 4. Design notes

* **Separation of concerns.** `broker/` can only read. `positions/` and `database/` never import `broker/` (AST-tested). Only `shadow_engine.py` joins them.
* **Quote validation** classifies LIVE / DELAYED / STALE / MISSING / INVALID. Any unusable leg means no position, with the reason recorded.
* **Duplicate prevention** is enforced twice: `PositionManager.create_pending` and a SQLite partial unique index on `(cycle_id, atm_strike, option_type) WHERE status IN ('PENDING','OPEN')`.
* **Position IDs**: `CYC-<near expiry>-<strike>-<CE|PE>-<seq>`; re-opening a closed box yields `-02`, `-03`, …
* **Summary definitions** (`shadow_summary.csv`): *evaluated* = every ATM×type check including repeats on later polls; *eligible* = valid data + acceptable band (entered or blocked as duplicate); *entries taken* = new positions; *rejected* = all non-entered evaluations.
* **P&L**: long far = (exit − entry)×qty; short near = (entry − exit)×qty; costs = four orders (2 entry, 2 exit). Realised and unrealised P&L are reported separately.

## 5. SQLite schema
`cycles`, `positions` (all lifecycle fields), `evaluations` (every entry check + reason), `market_snapshots` (timestamped NIFTY / VIX / ATM / data status per tick).
