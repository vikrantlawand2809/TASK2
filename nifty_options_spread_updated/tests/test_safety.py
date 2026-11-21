"""Shadow-trading safety: nothing in the code base can place/modify/cancel real orders."""
import ast
import logging
import re
from pathlib import Path

import pytest
import requests

import config
from broker.broker_client import OrderPlacementForbiddenError, ReadOnlyHttpClient
from broker.market_data import MarketDataProvider
from broker.mock_data_provider import MockMarketDataProvider
from broker.upstox_provider import UpstoxMarketDataProvider
from logging_setup import SecretRedactingFilter

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [p for p in ROOT.rglob("*.py") if "tests" not in p.parts and ".venv" not in p.parts]


class _Session:
    def __init__(self):
        self.headers, self.calls = {}, []

    def get(self, *a, **k):
        self.calls.append(("GET", a, k))
        raise AssertionError("network must not be reached in this test")


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_http_client_refuses_non_get(method):
    s = _Session()
    c = ReadOnlyHttpClient("https://api.example", "tok", session=s)
    with pytest.raises(OrderPlacementForbiddenError):
        c.request(method, "/v2/option/chain")
    assert s.calls == []                       # refused BEFORE any network call


@pytest.mark.parametrize("path", ["/v2/order/place", "/v2/order/modify", "/v2/order/cancel", "/v2/portfolio/long-term-holdings",
                                  "/v2/market-quote/../order/place", "/v2/user/get-funds-and-margin", "/v3/order/place"])
def test_http_client_refuses_order_and_non_allowlisted_paths(path):
    s = _Session()
    c = ReadOnlyHttpClient("https://api.example", "tok", session=s)
    with pytest.raises(OrderPlacementForbiddenError):
        c.get(path)
    assert s.calls == []


def test_providers_expose_no_order_capability():
    for cls in (MarketDataProvider, MockMarketDataProvider, UpstoxMarketDataProvider, ReadOnlyHttpClient):
        bad = [n for n in dir(cls) if re.search(r"order|buy|sell|trade|execute", n, re.I) and not n.startswith("assert_read")]
        assert bad == [], f"{cls.__name__} exposes {bad}"


def test_source_contains_no_order_placement_code():
    pattern = re.compile(r"(place|modify|cancel)_?orders?|/order|order_?api|OrderApi|kite\.place|transaction_type", re.I)
    offenders = []
    for path in SOURCES:
        if path.name == "broker_client.py":      # holds the *deny-list* words by design
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert offenders == []


def test_simulated_trading_packages_never_import_broker():
    for pkg in ("positions", "database"):
        for path in (ROOT / pkg).glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                assert not any(n.split(".")[0] in ("broker", "upstox_client", "kiteconnect") for n in names), path


def test_demo_run_makes_no_network_or_write_http_calls(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("HTTP call attempted during shadow demo")
    for name in ("request", "get", "post", "put", "delete", "patch"):
        monkeypatch.setattr(requests.Session, name, boom)
    from demo import run_demo
    paths = run_demo(config.load_config(), tmp_path / "d.db", tmp_path / "out")
    assert paths["shadow_summary.csv"].exists()


def test_shadow_mode_flag_is_true():
    assert config.SHADOW_MODE_ONLY is True


def test_credentials_never_in_repr_or_logs(caplog):
    creds = config.BrokerCredentials("KEY123456", "SECRET123456", "TOKEN-abcdef123456")
    assert "TOKEN" not in repr(creds) and "SECRET" not in repr(creds)
    f = SecretRedactingFilter(creds.secret_values())
    rec = logging.LogRecord("x", logging.INFO, "", 0, "auth Bearer TOKEN-abcdef123456 and access_token=TOKEN-abcdef123456 SECRET123456", None, None)
    f.filter(rec)
    assert "TOKEN-abcdef" not in rec.msg and "SECRET123456" not in rec.msg


def test_env_example_has_no_real_values_and_env_is_gitignored():
    for line in (ROOT / ".env.example").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            assert line.split("=", 1)[1].strip() == ""
    assert ".env" in (ROOT / ".gitignore").read_text().splitlines()
