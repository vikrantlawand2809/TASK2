"""Upstox v2 market-data adapter (READ-ONLY).

Endpoints used (all GET):
  /v2/market-quote/ltp         index levels (NIFTY 50, India VIX)
  /v2/option/contract          expiries, lot size, trading symbols
  /v2/option/chain             strike-wise CE/PE market_data (ltp, bid_price, ask_price, ...)

STATUS: parsing is unit-tested against payload shapes taken from Upstox's public
documentation/community examples. It has NOT been run against the live API
(no approved credentials were available at build time). Verify on first
authorised run. Known limitation: option-chain `market_data` has no per-quote
exchange timestamp, so the adapter stamps FETCH time and marks it
timestamp_source='fetch_time' - true quote freshness is unverifiable.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable, Optional

from broker.broker_client import ReadOnlyHttpClient
from broker.market_data import (ChainRow, IndexQuote, MarketDataError, MarketDataProvider,
                                OptionChain, OptionContract, OptionQuote, Quote)
from config import IST

log = logging.getLogger("nifty_spread.upstox")


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    num = _num(value)
    return None if num is None else int(num)


def _exchange_timestamp(md: dict[str, Any], fetched_at: datetime) -> tuple[Optional[datetime], str, Optional[str]]:
    """Return only timestamps explicitly supplied by the provider payload.

    The Upstox option-chain shape does not consistently provide a quote timestamp.
    A local fetch time is intentionally never converted into exchange freshness.
    """
    for key in ("exchange_timestamp", "timestamp", "last_trade_time", "updated_at"):
        value = md.get(key)
        if value is None:
            continue
        try:
            text = str(value).strip()
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=IST)
            else:
                parsed = parsed.astimezone(IST)
            feed = md.get("feed") or md.get("data_source")
            return parsed, "exchange", feed if isinstance(feed, str) else None
        except (TypeError, ValueError, OverflowError):
            continue
    return None, "fetch_time", md.get("feed") if isinstance(md.get("feed"), str) else None


class UpstoxMarketDataProvider(MarketDataProvider):
    name = "upstox"

    def __init__(self, client: ReadOnlyHttpClient, underlying: str, underlying_key: str,
                 vix_key: str, clock: Optional[Callable[[], datetime]] = None):
        self._client = client
        self._underlying = underlying
        self._underlying_key = underlying_key
        self._vix_key = vix_key
        self._clock = clock or (lambda: datetime.now(IST))
        self._contract_meta: dict[date, dict[str, dict]] = {}

    def now(self) -> datetime:
        return self._clock()

    # ---- helpers -----------------------------------------------------------
    @staticmethod
    def _data(payload: dict) -> Any:
        if not isinstance(payload, dict) or payload.get("status") != "success" or "data" not in payload:
            raise MarketDataError("Unexpected Upstox response structure")
        return payload["data"]

    def _index_quote(self, key: str, symbol: str) -> IndexQuote:
        data = self._data(self._client.get("/v2/market-quote/ltp", {"instrument_key": key}))
        if not isinstance(data, dict) or not data:
            raise MarketDataError(f"No LTP data returned for {symbol}")
        entry = next(iter(data.values()))
        if not isinstance(entry, dict):
            raise MarketDataError(f"Invalid LTP payload returned for {symbol}")
        ts, source, _ = _exchange_timestamp(entry, self.now())
        if ts is None:
            ts = self.now()
        return IndexQuote(symbol, _num(entry.get("last_price")), ts, source)

    # ---- MarketDataProvider ----------------------------------------------------
    def get_underlying_quote(self) -> IndexQuote:
        return self._index_quote(self._underlying_key, self._underlying)

    def get_india_vix(self) -> IndexQuote:
        return self._index_quote(self._vix_key, "INDIA VIX")

    def get_expiries(self) -> list[date]:
        data = self._data(self._client.get("/v2/option/contract", {"instrument_key": self._underlying_key}))
        expiries = set()
        for item in data or []:
            try:
                expiries.add(date.fromisoformat(str(item["expiry"])[:10]))
            except (KeyError, ValueError):
                continue
        if not expiries:
            raise MarketDataError("No option expiries returned")
        return sorted(expiries)

    def _meta_for(self, expiry: date) -> dict[str, dict]:
        """instrument_key -> contract metadata (lot size, trading symbol)."""
        if expiry not in self._contract_meta:
            data = self._data(self._client.get("/v2/option/contract", {
                "instrument_key": self._underlying_key, "expiry_date": expiry.isoformat()}))
            self._contract_meta[expiry] = {c["instrument_key"]: c for c in data or [] if "instrument_key" in c}
        return self._contract_meta[expiry]

    def get_option_chain(self, expiry: date) -> OptionChain:
        fetched_at = self.now()
        rows_raw = self._data(self._client.get("/v2/option/chain", {
            "instrument_key": self._underlying_key, "expiry_date": expiry.isoformat()}))
        if not isinstance(rows_raw, list) or not rows_raw:
            raise MarketDataError(f"Empty option chain for {expiry}")
        meta = self._meta_for(expiry)
        spot = None
        rows = []
        for raw in rows_raw:
            if not isinstance(raw, dict):
                raise MarketDataError(f"Malformed option-chain row for {expiry}")
            strike = _num(raw.get("strike_price"))
            if strike is None:
                continue
            spot = _num(raw.get("underlying_spot_price")) or spot
            legs = {}
            for opt, field_name in (("CE", "call_options"), ("PE", "put_options")):
                leg = raw.get(field_name) or {}
                key = leg.get("instrument_key")
                if not key:
                    legs[opt] = None
                    continue
                m = meta.get(key, {})
                contract = OptionContract(
                    instrument_key=key,
                    trading_symbol=m.get("trading_symbol", f"{self._underlying} {int(strike)} {opt} {expiry:%d %b %y}"),
                    underlying=self._underlying, expiry=expiry, strike=strike, option_type=opt,
                    lot_size=_int(m.get("lot_size")))
                md = leg.get("market_data") or {}
                quote = None
                if any(md.get(k) is not None for k in ("bid_price", "ask_price", "ltp")):
                    quote_ts, quote_source, quote_feed = _exchange_timestamp(md, fetched_at)
                    if quote_ts is None:
                        quote_ts = fetched_at
                    quote = Quote(bid=_num(md.get("bid_price")), ask=_num(md.get("ask_price")),
                                  ltp=_num(md.get("ltp")), bid_qty=_int(md.get("bid_qty")),
                                  ask_qty=_int(md.get("ask_qty")), timestamp=quote_ts,
                                  timestamp_source=quote_source, feed=quote_feed)
                legs[opt] = OptionQuote(contract, quote)
            rows.append(ChainRow(strike, legs["CE"], legs["PE"]))
        return OptionChain(self._underlying, expiry, spot, fetched_at, tuple(sorted(rows, key=lambda r: r.strike)))
