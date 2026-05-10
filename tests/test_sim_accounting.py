from __future__ import annotations

import pytest

from derivebench import Side, Signal
from derivebench.derive import load_replay_ticks
from derivebench.sim import AccountConfig, PaperAccount

from tests.conftest import FIXTURES


def ticks():
    return load_replay_ticks(FIXTURES / "derive_replay.json")


def test_long_straddle_buys_at_asks_plus_costs_and_liquidates_at_bids() -> None:
    t0, t1, *_ = ticks()
    account = PaperAccount(AccountConfig(starting_capital=1000.0, slippage_bps=10.0, fee_rate=0.001))
    fills = account.execute(Signal(Side.LONG_STRADDLE, size=0.5), t0)
    assert fills[0].side == "BUY"
    expected_cost_per_contract = t0.package_ask * 1.001 * 1.001
    assert account.position_contracts == pytest.approx(500.0 / expected_cost_per_contract)
    assert account.cash == pytest.approx(500.0)
    before = account.mark_equity(t1)
    account.liquidate(t1)
    assert account.position_contracts == 0.0
    assert account.cash == pytest.approx(account.mark_equity(t1))
    assert before != account.cash


def test_short_straddle_sells_at_bids_and_marks_liability_at_asks() -> None:
    t0, t1, *_ = ticks()
    account = PaperAccount(AccountConfig(starting_capital=1000.0, slippage_bps=0.0, fee_rate=0.0, short_margin_fraction=2.0))
    account.execute(Signal(Side.SHORT_STRADDLE, size=1.0), t0)
    assert account.position_contracts < 0.0
    assert account.cash > 1000.0
    assert account.mark_equity(t1) == pytest.approx(account.cash + account.position_contracts * t1.package_ask)


def test_flat_closes_existing_exposure() -> None:
    t0, t1, *_ = ticks()
    account = PaperAccount()
    account.execute(Signal(Side.LONG_STRADDLE, size=0.25), t0)
    assert account.position_contracts > 0
    account.execute(Signal(Side.FLAT), t1)
    assert abs(account.position_contracts) < 1e-12
    assert account.trade_count == 2
