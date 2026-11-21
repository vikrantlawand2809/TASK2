import pytest

from broker.mock_data_provider import make_default_mock
from strategy.atm_selector import ATMSelectionError, select_atm, select_atm_from_chain
from strategy.contract_selector import ContractSelectionError, ContractSelector
from tests.helpers import FAR, NEAR


def test_atm_is_closest_available_strike():
    assert select_atm([25450, 25500, 25550], 25512) == 25500
    assert select_atm([25450, 25500, 25550], 25538) == 25550


def test_tie_break_configurable():
    assert select_atm([25500, 25550], 25525, "lower") == 25500
    assert select_atm([25500, 25550], 25525, "higher") == 25550


def test_gap_selects_far_strike_directly_without_manufacturing_levels():
    strikes = [25500, 25700]                      # no 25550/25600/25650 in the chain
    assert select_atm(strikes, 25690) == 25700    # result is always an existing strike


def test_invalid_inputs():
    with pytest.raises(ATMSelectionError):
        select_atm([], 25500)
    with pytest.raises(ATMSelectionError):
        select_atm([25500], None)


def test_atm_taken_from_near_expiry_chain():
    p = make_default_mock()
    near_chain = p.get_option_chain(NEAR)
    assert select_atm_from_chain(near_chain, 25512) == 25500


def test_same_strike_used_for_both_expiries_and_long_is_far():
    p = make_default_mock()
    legs = ContractSelector().select(p.get_option_chain(NEAR), p.get_option_chain(FAR), 25500, "CE")
    assert legs.near.contract.strike == legs.far.contract.strike == 25500
    assert legs.near.contract.option_type == legs.far.contract.option_type == "CE"
    assert legs.near.contract.expiry == NEAR and legs.far.contract.expiry == FAR   # short=near, long=far


def test_far_strike_missing_is_rejected_not_substituted():
    p = make_default_mock()
    p.remove_strike(FAR, 25500)
    with pytest.raises(ContractSelectionError) as exc:
        ContractSelector().select(p.get_option_chain(NEAR), p.get_option_chain(FAR), 25500, "CE")
    assert exc.value.code == "FAR_STRIKE_MISSING"
