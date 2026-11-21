from broker.market_data import Quote
from positions.pnl_calculator import CostModel  # noqa: F401  (import sanity)
from strategy.premium_calculator import PremiumCalculator, QuoteSelector, premium_difference
from tests.helpers import FAR, NEAR, set_diff, make_env


def test_formula_matches_brief_example():
    assert premium_difference(210, 130) == 80


def test_ce_and_pe_differences_are_independent():
    e = make_env()
    set_diff(e.provider, 25500, "CE", 70)
    set_diff(e.provider, 25500, "PE", 55)
    res = e.engine.run_once()
    diffs = {r.option_type: r.premium_diff for r in res.evaluations}
    assert diffs == {"CE": 70.0, "PE": 55.0}


def test_executable_method_far_ask_minus_near_bid():
    calc = PremiumCalculator(QuoteSelector("executable"))
    r = calc.entry_difference(far_quote=Quote(bid=205, ask=210), near_quote=Quote(bid=130, ask=134))
    assert (r.far_price, r.near_price, r.difference) == (210, 130, 80)


def test_mid_and_ltp_methods_use_same_rule_for_both_legs():
    far, near = Quote(bid=200, ask=210, ltp=207), Quote(bid=130, ask=134, ltp=131)
    assert PremiumCalculator(QuoteSelector("mid")).entry_difference(far, near).difference == 205 - 132
    assert PremiumCalculator(QuoteSelector("ltp")).entry_difference(far, near).difference == 207 - 131
