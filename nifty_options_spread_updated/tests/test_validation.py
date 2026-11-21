from datetime import timedelta

from broker.market_data import FeedStatus, IndexQuote, Quote
from config import QuoteConfig
from strategy.quote_validation import QuoteValidator
from tests.helpers import T0


def q(age=0, bid=100.0, ask=101.0, **kw):
    return Quote(bid=bid, ask=ask, ltp=100.5, timestamp=T0 - timedelta(seconds=age), **kw)


def v(**kw):
    return QuoteValidator(QuoteConfig(**kw))


def test_live():
    c = v().check(q(2), T0, "x")
    assert c.ok and c.status == FeedStatus.LIVE


def test_delayed_rejected_by_default_accepted_when_allowed():
    assert not v().check(q(60), T0, "x").ok
    assert v().check(q(60), T0, "x").status == FeedStatus.DELAYED
    assert v(accept_delayed=True).check(q(60), T0, "x").ok


def test_provider_declared_delayed_feed():
    assert v().check(q(1, feed="delayed"), T0, "x").status == FeedStatus.DELAYED


def test_stale():
    c = v().check(q(500), T0, "x")
    assert not c.ok and c.status == FeedStatus.STALE


def test_missing_quote_and_fields():
    assert v().check(None, T0, "x").status == FeedStatus.MISSING
    assert v().check(Quote(bid=None, ask=None, ltp=5, timestamp=T0), T0, "x").status == FeedStatus.MISSING
    assert v().check(q(bid=0.0), T0, "x").status == FeedStatus.MISSING
    assert v().check(Quote(bid=1, ask=2, timestamp=None), T0, "x").status == FeedStatus.MISSING


def test_crossed_market_invalid():
    c = v().check(q(bid=105, ask=100), T0, "x")
    assert not c.ok and c.status == FeedStatus.INVALID


def test_future_timestamp_invalid():
    assert v().check(q(-60), T0, "x").status == FeedStatus.INVALID


def test_ltp_method_only_needs_ltp():
    assert v(method="ltp").check(Quote(ltp=10, timestamp=T0), T0, "x").ok


def test_unverifiable_freshness_rejected_by_default():
    fq = Quote(bid=1, ask=2, timestamp=T0, timestamp_source="fetch_time")
    check = v().check(fq, T0, "x")
    assert not check.ok
    assert check.status == FeedStatus.UNVERIFIABLE
    assert not v(reject_unverifiable_freshness=False).check(fq, T0, "x").ok


def test_index_checks():
    assert v().check_index(IndexQuote("NIFTY", 25500, T0), T0).ok
    assert v().check_index(IndexQuote("NIFTY", None, T0), T0).status == FeedStatus.MISSING
    assert v().check_index(IndexQuote("NIFTY", 25500, T0 - timedelta(seconds=999)), T0).status == FeedStatus.STALE
