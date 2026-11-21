"""ShadowEngine: one polling tick = read market data -> monitor -> evaluate entries.

This is the ONLY place where market data (broker package) meets simulated
trading (positions package). It never places orders: the provider interface it
receives has no such capability.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from broker.market_data import FeedStatus, MarketDataError, MarketDataProvider, OptionChain, Quote
from config import AppConfig, parse_hhmm
from database.db_manager import DatabaseManager
from positions.exit_manager import CYCLE_CLOSURE, MANUAL, ExitManager
from positions.models import EvaluationRecord, NewPositionSpec, Position
from positions.pnl_calculator import CostModel
from positions.position_manager import DuplicatePositionError, PositionManager
from strategy.atm_selector import ATMSelectionError, select_atm_from_chain
from strategy.contract_selector import ContractSelectionError, ContractSelector
from strategy.entry_conditions import EntryConditions, EntryOutcome, PremiumBand
from strategy.expiry_manager import ExpiryError, ExpiryManager, ExpiryPair
from strategy.premium_calculator import BUY, SELL, PremiumCalculator, QuoteSelector
from strategy.quote_validation import QuoteCheck, QuoteValidator

log = logging.getLogger("nifty_spread.engine")


@dataclass
class TickResult:
    ts: datetime
    cycle_id: Optional[str] = None
    new_cycle: bool = False
    spot: Optional[float] = None
    atm: Optional[float] = None
    skipped_reason: Optional[str] = None
    evaluations: list[EvaluationRecord] = field(default_factory=list)
    opened: list[Position] = field(default_factory=list)
    closed: list[Position] = field(default_factory=list)


class ManualCloseError(RuntimeError):
    pass


class ShadowEngine:
    def __init__(self, provider: MarketDataProvider, config: AppConfig, db: DatabaseManager):
        self.provider, self.config, self.db = provider, config, db
        self.expiry_manager = ExpiryManager(config.cycle)
        self.contract_selector = ContractSelector()
        self.validator = QuoteValidator(config.quotes)
        self.selector = QuoteSelector(config.quotes.method)
        self.calculator = PremiumCalculator(self.selector)
        self.entry_conditions = EntryConditions(config.entry)
        self.exit_manager = ExitManager(config.exits)
        self.positions = PositionManager(db, CostModel(config.costs))
        self._chain_cache: dict[date, Optional[OptionChain]] = {}

    # ---- helpers ---------------------------------------------------------------
    def _chain(self, expiry: date) -> Optional[OptionChain]:
        if expiry not in self._chain_cache:
            try:
                self._chain_cache[expiry] = self.provider.get_option_chain(expiry)
                log.info("Option chain fetched for %s (%d strikes)", expiry, len(self._chain_cache[expiry].rows))
            except MarketDataError as exc:
                log.warning("Option chain unavailable for %s: %s", expiry, exc)
                self._chain_cache[expiry] = None
        return self._chain_cache[expiry]

    def _in_session(self, now: datetime) -> bool:
        s = self.config.session
        if not s.enforce_market_hours:
            return True
        return now.weekday() < 5 and parse_hhmm(s.open_time) <= now.time() <= parse_hhmm(s.close_time)

    def _position_quotes(self, pos: Position, now: datetime) -> Optional[tuple[Quote, Quote]]:
        """Validated (near_quote, far_quote) for an open position, else None."""
        near_chain, far_chain = self._chain(pos.near_expiry), self._chain(pos.far_expiry)
        if near_chain is None or far_chain is None:
            log.warning("%s: chain unavailable - mark not updated", pos.position_id)
            return None
        quotes, problems = [], []
        for chain, label in ((near_chain, "near"), (far_chain, "far")):
            row = chain.row_at(pos.atm_strike)
            oq = row.get(pos.option_type) if row else None
            q = oq.quote if oq else None
            check = self.validator.check(q, now, f"{label} {pos.atm_strike:g}{pos.option_type}")
            if not check.ok:
                problems.extend(check.reasons)
            quotes.append(q)
        if problems:
            log.warning("%s: DATA INVALID - %s - mark not updated, no exit evaluated", pos.position_id, "; ".join(problems))
            return None
        return quotes[0], quotes[1]

    # ---- main tick ---------------------------------------------------------------
    def run_once(self) -> TickResult:
        now = self.provider.now()
        result = TickResult(ts=now)
        self._chain_cache = {}
        try:
            self._tick(now, result)
        except (MarketDataError, ExpiryError, ATMSelectionError) as exc:
            result.skipped_reason = f"{type(exc).__name__}: {exc}"
            log.error("Tick skipped: %s", result.skipped_reason)
        return result

    def _tick(self, now: datetime, result: TickResult) -> None:
        if not self._in_session(now):
            result.skipped_reason = "OUTSIDE_MARKET_HOURS"
            log.info("Tick %s skipped: outside market hours", now.isoformat())
            return
        idx = self.provider.get_underlying_quote()
        try:
            vix = self.provider.get_india_vix().value
        except MarketDataError as exc:
            vix = None
            log.warning("India VIX unavailable: %s", exc)
        idx_check = self.validator.check_index(idx, now)
        log.info("Market data: %s=%s (%s) VIX=%s", idx.symbol, idx.value, idx_check.status.value, vix)
        if not idx_check.ok:
            self.db.insert_snapshot(now, idx.value, vix, idx_check.status.value, None, None, None, None,
                                    "; ".join(idx_check.reasons))
            result.skipped_reason = "UNDERLYING_DATA_INVALID: " + "; ".join(idx_check.reasons)
            log.warning("Data validation failure: %s", result.skipped_reason)
            return
        result.spot = idx.value

        pair = self.expiry_manager.resolve(self.provider.get_expiries(), now)
        result.cycle_id = pair.cycle_id
        result.new_cycle = self.db.upsert_cycle(pair.cycle_id, pair.near, pair.far, now)
        log.info("Expiries: NEAR=%s FAR=%s cycle=%s%s", pair.near, pair.far, pair.cycle_id,
                 " (NEW CYCLE)" if result.new_cycle else "")

        self._monitor_positions(now, pair, result)

        near_chain, far_chain = self._chain(pair.near), self._chain(pair.far)
        if near_chain is None or far_chain is None:
            result.skipped_reason = "OPTION_CHAIN_UNAVAILABLE"
            self.db.insert_snapshot(now, idx.value, vix, idx_check.status.value, pair.cycle_id, pair.near, pair.far, None,
                                    "option chain unavailable")
            return
        atm = select_atm_from_chain(near_chain, idx.value, self.config.atm_tie_break)
        result.atm = atm
        self.db.insert_snapshot(now, idx.value, vix, idx_check.status.value, pair.cycle_id, pair.near, pair.far, atm)
        log.info("ATM strike %g selected from near-expiry chain (spot %.2f)", atm, idx.value)
        for opt in ("CE", "PE"):
            rec, pos = self._evaluate(now, pair, near_chain, far_chain, atm, opt)
            result.evaluations.append(rec)
            if pos:
                result.opened.append(pos)

    # ---- entries -------------------------------------------------------------------
    def _record(self, now, pair, atm, opt, outcome, reason, **kw) -> EvaluationRecord:
        rec = EvaluationRecord(ts=now, cycle_id=pair.cycle_id, atm_strike=atm, option_type=opt,
                               outcome=outcome.value, reason=reason, quote_method=self.config.quotes.method,
                               band=kw.pop("band", None), **kw)
        self.db.insert_evaluation(rec)
        log.info("Evaluation %g %s -> %s | %s", atm, opt, outcome.value, reason)
        return rec

    def _evaluate(self, now: datetime, pair: ExpiryPair, near_chain: OptionChain, far_chain: OptionChain,
                  atm: float, opt: str) -> tuple[EvaluationRecord, Optional[Position]]:
        try:
            legs = self.contract_selector.select(near_chain, far_chain, atm, opt)
        except ContractSelectionError as exc:
            return self._record(now, pair, atm, opt, EntryOutcome.REJECTED_CONTRACT_MISSING, str(exc)), None
        nc, fc = legs.near.contract, legs.far.contract
        base = dict(near_symbol=nc.trading_symbol, far_symbol=fc.trading_symbol)

        lot = nc.lot_size
        if not lot or lot <= 0 or (fc.lot_size and fc.lot_size != lot):
            return self._record(now, pair, atm, opt, EntryOutcome.REJECTED_DATA_INVALID,
                                f"Lot size missing/mismatched (near={nc.lot_size}, far={fc.lot_size})", **base), None

        checks: list[tuple[str, QuoteCheck]] = [
            ("near", self.validator.check(legs.near.quote, now, f"near {atm:g}{opt}")),
            ("far", self.validator.check(legs.far.quote, now, f"far {atm:g}{opt}"))]
        statuses = {label: c.status.value for label, c in checks}
        base.update(near_status=statuses["near"], far_status=statuses["far"])
        bad = [c for _, c in checks if not c.ok]
        if bad:
            kinds = {c.status for c in bad}
            outcome = (EntryOutcome.REJECTED_DATA_MISSING if FeedStatus.MISSING in kinds else
                       EntryOutcome.REJECTED_DATA_INVALID if FeedStatus.INVALID in kinds else
                       EntryOutcome.REJECTED_DATA_UNVERIFIABLE if FeedStatus.UNVERIFIABLE in kinds else
                       EntryOutcome.REJECTED_DATA_STALE)
            reason = "; ".join(r for c in bad for r in c.reasons)
            log.warning("Data validation failure %g %s: %s", atm, opt, reason)
            return self._record(now, pair, atm, opt, outcome, reason, **base), None

        prem = self.calculator.entry_difference(legs.far.quote, legs.near.quote)
        base.update(near_price=prem.near_price, far_price=prem.far_price, premium_diff=prem.difference)
        assessment = self.entry_conditions.assess(prem.difference)
        if not assessment.eligible:
            outcome = (EntryOutcome.REJECTED_NEGATIVE_DIFFERENCE if assessment.band == PremiumBand.INVALID
                       else EntryOutcome.REJECTED_ABOVE_RANGE)
            return self._record(now, pair, atm, opt, outcome, assessment.reason, band=assessment.band.value, **base), None

        if self.positions.has_active_position(pair.cycle_id, atm, opt):
            return self._record(now, pair, atm, opt, EntryOutcome.REJECTED_DUPLICATE,
                                f"Box {atm:g} {opt} already open; no duplicate created", band=assessment.band.value, **base), None

        spec = NewPositionSpec(
            cycle_id=pair.cycle_id, atm_strike=atm, option_type=opt, near_expiry=pair.near, far_expiry=pair.far,
            near_symbol=nc.trading_symbol, far_symbol=fc.trading_symbol,
            near_instrument_key=nc.instrument_key, far_instrument_key=fc.instrument_key,
            lot_size=lot, lots=self.config.sizing.lots, quote_method=prem.method,
            near_entry_premium=prem.near_price, far_entry_premium=prem.far_price, initial_diff=prem.difference)
        try:
            pos = self.positions.open_new(spec, now)
        except DuplicatePositionError as exc:   # race / DB guard
            return self._record(now, pair, atm, opt, EntryOutcome.REJECTED_DUPLICATE, str(exc),
                                band=assessment.band.value, **base), None
        return self._record(now, pair, atm, opt, EntryOutcome.ENTERED, assessment.reason,
                            band=assessment.band.value, position_id=pos.position_id, **base), pos

    # ---- monitoring / exits ----------------------------------------------------
    def _exit_prices(self, near_q: Quote, far_q: Quote) -> tuple[float, float]:
        """(near_exit, far_exit): buy back near, sell far."""
        return self.selector.price(near_q, BUY), self.selector.price(far_q, SELL)

    def _monitor_positions(self, now: datetime, pair: ExpiryPair, result: TickResult) -> None:
        open_positions = self.positions.list_open()
        # Opt-in cycle-level closure (rule not yet confirmed by Pravrtti -> disabled by default)
        due = self.exit_manager.positions_due_for_cycle_closure(open_positions, pair.cycle_id)
        if due:
            def lookup(p: Position) -> Optional[tuple[float, float]]:
                q = self._position_quotes(p, now)
                return self._exit_prices(*q) if q else None
            for cid in sorted({p.cycle_id for p in due}):
                closed, _ = self.positions.close_cycle(cid, now, lookup, CYCLE_CLOSURE)
                result.closed.extend(closed)
            open_positions = self.positions.list_open()
        for pos in open_positions:
            quotes = self._position_quotes(pos, now)
            if quotes is None:
                continue
            near_q, far_q = quotes
            near_exit, far_exit = self._exit_prices(near_q, far_q)
            diff = self.calculator.entry_difference(far_q, near_q).difference
            pos = self.positions.update_mark(pos.position_id, diff, now, near_exit, far_exit)
            log.info("Monitor %s: diff %.2f -> %.2f, unrealised gross %.2f", pos.position_id,
                     pos.initial_diff, diff, pos.unrealized_gross_pnl)
            reason = self.exit_manager.check(pos, pos.unrealized_gross_pnl, now)
            if reason:
                result.closed.append(self.positions.close(pos.position_id, now, near_exit, far_exit, reason))

    def manual_close(self, position_id: str, reason: str = MANUAL) -> Position:
        """Simulated manual closure of ONE position at current exit-side quotes."""
        now = self.provider.now()
        self._chain_cache = {}
        pos = self.positions.get(position_id)
        quotes = self._position_quotes(pos, now)
        if quotes is None:
            raise ManualCloseError(f"No valid quotes to close {position_id}")
        near_exit, far_exit = self._exit_prices(*quotes)
        return self.positions.close(position_id, now, near_exit, far_exit, reason)
