"""Paper execution and accounting for the selected straddle package."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from derivebench.model import FLAT, RunSegment, Side, Signal, Tick


@dataclass(frozen=True, slots=True)
class Fill:
    ts: float
    side: str
    delta_contracts: float
    price: float
    notional: float
    fee: float
    slippage: float
    spread_paid: float


@dataclass(slots=True)
class AccountConfig:
    starting_capital: float = 1000.0
    max_position_fraction: float = 1.0
    short_margin_fraction: float = 1.5
    slippage_bps: float = 10.0
    fee_rate: float = 0.0008


@dataclass(slots=True)
class PaperAccount:
    config: AccountConfig = field(default_factory=AccountConfig)
    cash: float = field(init=False)
    position_contracts: float = 0.0
    trade_count: int = 0
    timeout_count: int = 0
    fees_paid: float = 0.0
    slippage_paid: float = 0.0
    spread_paid: float = 0.0
    rejected_signals: int = 0
    long_ticks: int = 0
    short_ticks: int = 0
    flat_ticks: int = 0
    equity_curve: list[float] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = float(self.config.starting_capital)

    @property
    def starting_capital(self) -> float:
        return self.config.starting_capital

    def mark_equity(self, tick: Tick) -> float:
        if self.position_contracts > 0.0:
            return self.cash + self.position_contracts * tick.package_bid
        if self.position_contracts < 0.0:
            return self.cash + self.position_contracts * tick.package_ask
        return self.cash

    def _target_contracts(self, signal: Signal, tick: Tick) -> float:
        size = min(max(float(signal.size), 0.0), self.config.max_position_fraction)
        if signal.side == Side.FLAT or size == 0.0:
            return 0.0
        notional = size * self.starting_capital
        if signal.side == Side.LONG_STRADDLE:
            return notional / tick.package_ask if tick.package_ask > 0.0 else 0.0
        if signal.side == Side.SHORT_STRADDLE:
            equity = max(self.mark_equity(tick), 0.0)
            max_short_notional = equity / max(self.config.short_margin_fraction, 0.1)
            bounded = min(notional, max_short_notional)
            return -(bounded / tick.package_ask) if tick.package_ask > 0.0 else 0.0
        return 0.0

    def execute(self, signal: Signal | None, tick: Tick) -> list[Fill]:
        signal = signal or FLAT
        if not self._quotes_executable(tick):
            self.rejected_signals += 1
            return []
        target = self._target_contracts(signal, tick)
        delta = target - self.position_contracts
        if abs(delta) < 1e-12:
            return []
        fills = [self._trade(delta, tick)]
        self.position_contracts = target
        return fills

    def liquidate(self, tick: Tick) -> list[Fill]:
        if abs(self.position_contracts) < 1e-12:
            return []
        return [self._trade(-self.position_contracts, tick, liquidation=True)]

    def observe(self, tick: Tick) -> float:
        if self.position_contracts > 0.0:
            self.long_ticks += 1
        elif self.position_contracts < 0.0:
            self.short_ticks += 1
        else:
            self.flat_ticks += 1
        equity = self.mark_equity(tick)
        self.equity_curve.append(equity)
        return equity

    def segment(self, package_id: str, started_ts: float, ended_ts: float) -> RunSegment:
        pnl = self.mark_equity_value - self.starting_capital
        if self.long_ticks >= self.short_ticks and self.long_ticks > 0:
            side = Side.LONG_STRADDLE.value
        elif self.short_ticks > 0:
            side = Side.SHORT_STRADDLE.value
        else:
            side = Side.FLAT.value
        return RunSegment(
            package_id=package_id,
            start_ts=started_ts,
            end_ts=ended_ts,
            side=side,
            pnl_total=pnl,
            n_trades=self.trade_count,
            n_ticks=len(self.equity_curve),
            n_timeouts=self.timeout_count,
        )

    @property
    def mark_equity_value(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.cash

    def summary_extras(self) -> dict[str, Any]:
        total = max(1, self.long_ticks + self.short_ticks + self.flat_ticks)
        return {
            "trade_count": self.trade_count,
            "fees_paid": self.fees_paid,
            "slippage_paid": self.slippage_paid,
            "spread_paid": self.spread_paid,
            "long_exposure_rate": self.long_ticks / total,
            "short_exposure_rate": self.short_ticks / total,
            "flat_rate": self.flat_ticks / total,
            "rejected_signals": self.rejected_signals,
            "final_position_contracts": self.position_contracts,
        }

    def _trade(self, delta: float, tick: Tick, *, liquidation: bool = False) -> Fill:
        abs_delta = abs(delta)
        if delta > 0.0:
            quote = tick.package_ask
            slip = quote * self.config.slippage_bps / 10_000.0
            executable = quote + slip
            notional = abs_delta * executable
            fee = notional * self.config.fee_rate
            self.cash -= notional + fee
            spread_paid = abs_delta * max(0.0, tick.package_ask - tick.package_mid)
            side = "BUY"
        else:
            quote = tick.package_bid
            slip = quote * self.config.slippage_bps / 10_000.0
            executable = max(0.0, quote - slip)
            notional = abs_delta * executable
            fee = notional * self.config.fee_rate
            self.cash += notional - fee
            spread_paid = abs_delta * max(0.0, tick.package_mid - tick.package_bid)
            side = "SELL"
        self.trade_count += 1
        self.fees_paid += fee
        self.slippage_paid += abs_delta * slip
        self.spread_paid += spread_paid
        if liquidation:
            self.position_contracts = 0.0
        fill = Fill(tick.ts, side, delta, executable, notional, fee, abs_delta * slip, spread_paid)
        self.fills.append(fill)
        return fill

    @staticmethod
    def _quotes_executable(tick: Tick) -> bool:
        values = [tick.call_bid, tick.call_ask, tick.put_bid, tick.put_ask, tick.package_bid, tick.package_ask]
        return all(math.isfinite(v) and v > 0.0 for v in values) and tick.package_ask >= tick.package_bid

