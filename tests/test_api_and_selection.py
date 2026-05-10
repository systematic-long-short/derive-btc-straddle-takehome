from __future__ import annotations

import json

import pytest

from derivebench import FLAT, MarketInfo, Model, RunResult, RunSegment, Side, Signal, Tick
from derivebench.derive import parse_btc_spot, parse_instruments, parse_tickers, select_otm_package, tick_from_package

from tests.conftest import FIXTURES


def fixture() -> dict:
    return json.loads((FIXTURES / "derive_public_responses.json").read_text())


def test_public_candidate_api_exports_expected_shapes() -> None:
    assert Side.LONG_STRADDLE.value == "LONG_STRADDLE"
    assert Side.SHORT_STRADDLE.value == "SHORT_STRADDLE"
    assert Side.FLAT.value == "FLAT"
    assert FLAT.side is Side.FLAT
    assert MarketInfo and Tick and Signal and RunResult and RunSegment and Model


def test_parse_recorded_public_derive_responses() -> None:
    data = fixture()
    instruments = parse_instruments(data["instruments"])
    tickers = parse_tickers(data["tickers"])
    spot = parse_btc_spot(data["currencies"])
    assert spot == 80000.0
    assert instruments[0].name == "BTC-20260515-81000-C"
    assert instruments[0].option_type == "C"
    assert tickers["BTC-20260515-79000-P"].bid == 800.0
    assert tickers["BTC-20260515-81000-C"].implied_vol == 0.58


def test_parse_currencies_accepts_live_list_shape() -> None:
    response = {"result": [{"currency": "BTC", "spot_price": "80748.5"}, {"currency": "ETH", "spot_price": "2500"}]}
    assert parse_btc_spot(response) == 80748.5


def test_otm_package_selection_uses_nearest_liquid_same_expiry() -> None:
    data = fixture()
    package = select_otm_package(parse_instruments(data["instruments"]), parse_tickers(data["tickers"]), 80000.0)
    assert package.call.name == "BTC-20260515-81000-C"
    assert package.put.name == "BTC-20260515-79000-P"
    tick = tick_from_package(package, spot=80000.0, ts=1778397000.0)
    assert tick.package_id == "20260515:BTC-20260515-81000-C|BTC-20260515-79000-P"
    assert tick.package_bid == 1650.0
    assert tick.package_ask == 1750.0
    assert tick.call_strike > tick.btc_spot > tick.put_strike
    assert tick.liquidity_ok


def test_selection_rejects_when_spread_filter_excludes_all_liquid_packages() -> None:
    data = fixture()
    tickers = parse_tickers(data["tickers"])
    with pytest.raises(ValueError, match="no liquid BTC OTM call/put package"):
        select_otm_package(parse_instruments(data["instruments"]), tickers, 80000.0, max_spread_pct=0.001)
