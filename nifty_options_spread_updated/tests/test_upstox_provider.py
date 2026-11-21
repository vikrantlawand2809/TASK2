"""Adapter parsing tests against canned payloads shaped like Upstox's documented responses.
These prove parsing/validation logic only - NOT live-API compatibility."""
import logging
from datetime import date, datetime

import pytest
import requests

from broker.broker_client import ReadOnlyHttpClient
from broker.market_data import MarketDataError
from broker.upstox_provider import UpstoxMarketDataProvider
from config import IST

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=IST)
TOKEN = "SUPERSECRETTOKEN123"


class Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, status=200):
        self.headers, self.calls, self.status = {}, [], status

    def get(self, url, params=None, timeout=None):
        path = url.split("api.upstox.com")[1]
        self.calls.append((path, params))
        if self.status != 200:
            return Resp({}, self.status)
        if path == "/v2/market-quote/ltp":
            return Resp({"status": "success", "data": {"NSE_INDEX:Nifty 50": {"last_price": 25510.4, "instrument_token": "NSE_INDEX|Nifty 50"}}})
        if path == "/v2/option/contract":
            allc = []
            for exp in ("2026-11-24", "2026-12-01"):
                for strike in (25500.0, 25550.0):
                    for opt in ("CE", "PE"):
                        allc.append({"instrument_key": f"NSE_FO|{exp[-2:]}{int(strike)}{opt}", "trading_symbol": f"NIFTY {int(strike)} {opt} {exp}",
                                     "lot_size": 75, "expiry": exp, "strike_price": strike, "instrument_type": opt})
            if params and "expiry_date" in params:
                allc = [c for c in allc if c["expiry"] == params["expiry_date"]]
            return Resp({"status": "success", "data": allc})
        if path == "/v2/option/chain":
            exp = params["expiry_date"]
            rows = []
            for strike in (25500.0, 25550.0):
                def leg(opt, empty=False):
                    return {"instrument_key": f"NSE_FO|{exp[-2:]}{int(strike)}{opt}",
                            "market_data": {} if empty else {"ltp": 100.0, "bid_price": 99.5, "ask_price": 100.5, "bid_qty": 75, "ask_qty": 150}}
                rows.append({"expiry": exp, "strike_price": strike, "underlying_key": "NSE_INDEX|Nifty 50",
                             "underlying_spot_price": 25510.4, "call_options": leg("CE"), "put_options": leg("PE", empty=strike == 25550.0)})
            return Resp({"status": "success", "data": rows})
        return Resp({"status": "error"}, 404)


def provider(status=200):
    sess = FakeSession(status)
    client = ReadOnlyHttpClient("https://api.upstox.com", TOKEN, max_retries=0, session=sess)
    return UpstoxMarketDataProvider(client, "NIFTY", "NSE_INDEX|Nifty 50", "NSE_INDEX|India VIX", clock=lambda: NOW), sess


def test_index_expiries_and_chain_parsing():
    p, _ = provider()
    assert p.get_underlying_quote().value == 25510.4
    assert p.get_expiries() == [date(2026, 11, 24), date(2026, 12, 1)]
    chain = p.get_option_chain(date(2026, 11, 24))
    assert chain.strikes == [25500.0, 25550.0] and chain.underlying_spot == 25510.4
    ce = chain.row_at(25500.0).ce
    assert (ce.quote.bid, ce.quote.ask, ce.quote.ltp) == (99.5, 100.5, 100.0)
    assert ce.contract.lot_size == 75 and ce.contract.expiry == date(2026, 11, 24)
    assert ce.quote.timestamp == NOW and ce.quote.timestamp_source == "fetch_time"


def test_empty_market_data_becomes_missing_quote():
    p, _ = provider()
    pe = p.get_option_chain(date(2026, 11, 24)).row_at(25550.0).pe
    assert pe.quote is None


def test_only_get_requests_to_allowed_paths(caplog):
    p, sess = provider()
    with caplog.at_level(logging.DEBUG, logger="nifty_spread"):
        p.get_expiries(); p.get_option_chain(date(2026, 12, 1)); p.get_underlying_quote()
    assert {path for path, _ in sess.calls} <= {"/v2/market-quote/ltp", "/v2/option/contract", "/v2/option/chain"}
    assert TOKEN not in caplog.text
    assert sess.headers["Authorization"] == f"Bearer {TOKEN}"       # only ever in the header


def test_http_error_becomes_market_data_error_without_leaking_token(caplog):
    p, _ = provider(status=401)
    with caplog.at_level(logging.DEBUG, logger="nifty_spread"):
        with pytest.raises(MarketDataError) as exc:
            p.get_expiries()
    assert TOKEN not in str(exc.value) and TOKEN not in caplog.text


def test_malformed_payload_rejected():
    p, sess = provider()
    sess.get = lambda url, params=None, timeout=None: Resp({"unexpected": True})
    with pytest.raises(MarketDataError):
        p.get_expiries()


def test_adapter_fetch_time_quotes_are_rejected_by_engine():
    """Upstox-shaped data cannot become entries merely because it was fetched now."""
    from config import load_config
    from database.db_manager import DatabaseManager
    from shadow_engine import ShadowEngine
    p, _ = provider()
    p._clock = lambda: datetime(2026, 11, 17, 15, 5, tzinfo=IST)     # expiry day after roll -> near 24-Nov
    res = ShadowEngine(p, load_config(), DatabaseManager(":memory:")).run_once()
    assert res.atm is None
    assert res.skipped_reason.startswith("UNDERLYING_DATA_INVALID")
    assert res.evaluations == []
