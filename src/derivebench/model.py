"""Candidate-facing dataclasses and model base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence


class Side(str, Enum):
    LONG_STRADDLE = "LONG_STRADDLE"
    SHORT_STRADDLE = "SHORT_STRADDLE"
    FLAT = "FLAT"


@dataclass(frozen=True, slots=True)
class MarketInfo:
    package_rule: str
    base_currency: str
    quote_currency: str
    starting_capital: float
    max_position_fraction: float
    slippage_bps: float
    fee_rate: float
    scratch_dir: Path


@dataclass(frozen=True, slots=True)
class Tick:
    ts: float
    exchange_ts: float
    package_id: str
    feed_source: str
    expiry_ts: float
    expiry_date: str
    time_to_expiry: float
    btc_spot: float
    btc_index: float
    call_name: str
    put_name: str
    call_strike: float
    put_strike: float
    call_bid: float
    call_ask: float
    call_mid: float
    call_mark: float
    put_bid: float
    put_ask: float
    put_mid: float
    put_mark: float
    package_bid: float
    package_ask: float
    package_mid: float
    package_mark: float
    package_spread: float
    package_spread_pct: float
    call_bid_size: float
    call_ask_size: float
    put_bid_size: float
    put_ask_size: float
    min_leg_size: float
    liquidity_ok: bool
    btc_recent: Sequence[float] = field(default_factory=tuple)
    package_mid_recent: Sequence[float] = field(default_factory=tuple)
    selection_reason: str = ""


@dataclass(frozen=True, slots=True)
class Signal:
    side: Side
    size: float = 0.0
    confidence: float = 0.0


FLAT = Signal(side=Side.FLAT, size=0.0, confidence=0.0)


@dataclass(frozen=True, slots=True)
class RunSegment:
    package_id: str
    start_ts: float
    end_ts: float
    side: str
    pnl_total: float
    n_trades: int
    n_ticks: int
    n_timeouts: int


EventResult = RunSegment


@dataclass(frozen=True, slots=True)
class RunResult:
    started_ts: float
    ended_ts: float
    starting_capital: float
    final_equity: float
    pnl_total: float
    pnl_pct: float
    segments: Sequence[RunSegment] = field(default_factory=tuple)
    metrics: dict[str, Any] = field(default_factory=dict)
    benchmark_metrics: dict[str, Any] = field(default_factory=dict)
    benchmark_final_equity: float = 0.0
    benchmark_pnl_total: float = 0.0
    tick_count: int = 0

    @property
    def events(self) -> Sequence[RunSegment]:
        return self.segments


class Model(ABC):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})

    def on_start(self, market_info: MarketInfo) -> None:
        pass

    @abstractmethod
    def on_tick(self, tick: Tick) -> Signal | None:
        pass

    def on_finish(self, result: RunResult) -> None:
        pass

