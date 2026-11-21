"""Read-only HTTP client for broker REST APIs + provider factory.

SAFETY: this client can only issue GET requests to an allow-list of market-data
paths. Any other method, or any path outside the allow-list (and any path
containing 'order'), raises OrderPlacementForbiddenError before a network call.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from config import AppConfig, BrokerCredentials

log = logging.getLogger("nifty_spread.broker")

ALLOWED_PATH_PREFIXES = ("/v2/market-quote/", "/v2/option/")
FORBIDDEN_FRAGMENTS = ("order", "trade", "portfolio", "funds", "gtt")


class MissingCredentialsError(RuntimeError):
    """No approved access token configured."""


class OrderPlacementForbiddenError(PermissionError):
    """Raised on any attempt to use a non-read-only broker capability."""


class ReadOnlyHttpClient:
    def __init__(self, base_url: str, access_token: str, timeout: float = 10.0,
                 max_retries: int = 2, session: Optional[requests.Session] = None):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = session or requests.Session()
        # Token lives only in the session header; it is never logged.
        self._session.headers.update({"Accept": "application/json",
                                      "Authorization": f"Bearer {access_token}"})

    @staticmethod
    def assert_read_only(method: str, path: str) -> None:
        if method.upper() != "GET":
            raise OrderPlacementForbiddenError(f"Only GET is permitted (got {method}); shadow mode is read-only")
        lowered = path.lower()
        if any(f in lowered for f in FORBIDDEN_FRAGMENTS):
            raise OrderPlacementForbiddenError(f"Path '{path}' is not a market-data endpoint")
        if not path.startswith(ALLOWED_PATH_PREFIXES):
            raise OrderPlacementForbiddenError(f"Path '{path}' is not in the market-data allow-list")

    def request(self, method: str, path: str, params: Optional[dict[str, Any]] = None) -> dict:
        self.assert_read_only(method, path)
        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.get(self._base_url + path, params=params, timeout=self._timeout)
                log.debug("GET %s -> HTTP %s", path, resp.status_code)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                log.warning("GET %s failed (attempt %d): %s", path, attempt + 1, type(exc).__name__)
                if attempt < self._max_retries:
                    time.sleep(0.5 * (attempt + 1))
        from broker.market_data import MarketDataError
        raise MarketDataError(f"GET {path} failed: {type(last_error).__name__}")

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict:
        return self.request("GET", path, params)


def build_provider(config: AppConfig, credentials: Optional[BrokerCredentials] = None):
    """Create the live provider. Requires an approved access token."""
    from broker.upstox_provider import UpstoxMarketDataProvider
    credentials = credentials or BrokerCredentials.from_env()
    if not credentials.has_access_token:
        raise MissingCredentialsError("UPSTOX_ACCESS_TOKEN is not set (see .env.example). Run `python main.py demo` "
                                      "(mock data) until Pravrtti supplies approved, data-only credentials.")
    client = ReadOnlyHttpClient("https://api.upstox.com", credentials.access_token)
    log.info("Upstox read-only market-data client created (credentials not logged)")
    return UpstoxMarketDataProvider(client, config.underlying, config.underlying_key, config.vix_key)
