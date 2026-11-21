"""Pairs the near-expiry (SHORT) and far-expiry (LONG) contract for a strike/type."""
from __future__ import annotations

from dataclasses import dataclass

from broker.market_data import OptionChain, OptionQuote


class ContractSelectionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SpreadLegs:
    strike: float
    option_type: str
    near: OptionQuote     # SELL leg
    far: OptionQuote      # BUY leg


class ContractSelector:
    def select(self, near_chain: OptionChain, far_chain: OptionChain, strike: float,
               option_type: str) -> SpreadLegs:
        if option_type not in ("CE", "PE"):
            raise ContractSelectionError("BAD_TYPE", f"Invalid option type {option_type}")
        near_row = near_chain.row_at(strike)
        if near_row is None or near_row.get(option_type) is None:
            raise ContractSelectionError("NEAR_CONTRACT_MISSING", f"No near-expiry {strike:g} {option_type}")
        far_row = far_chain.row_at(strike)
        if far_row is None:
            # Never substitute another strike: same strike is mandatory.
            raise ContractSelectionError("FAR_STRIKE_MISSING",
                                         f"Strike {strike:g} absent from far-expiry chain {far_chain.expiry}")
        if far_row.get(option_type) is None:
            raise ContractSelectionError("FAR_CONTRACT_MISSING", f"No far-expiry {strike:g} {option_type}")
        near, far = near_row.get(option_type), far_row.get(option_type)
        nc, fc = near.contract, far.contract
        if not (nc.underlying == fc.underlying and nc.strike == fc.strike == strike
                and nc.option_type == fc.option_type == option_type and nc.expiry < fc.expiry):
            raise ContractSelectionError("LEG_MISMATCH", "Legs differ in underlying/strike/type or expiry order")
        return SpreadLegs(strike, option_type, near=near, far=far)
