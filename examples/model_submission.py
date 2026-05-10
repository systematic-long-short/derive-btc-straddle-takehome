"""Candidate template for the Derive BTC OTM straddle take-home."""

from __future__ import annotations

from derivebench import FLAT, MarketInfo, Model, RunResult, Side, Signal, Tick


class ModelSubmission(Model):
    def on_start(self, market_info: MarketInfo) -> None:
        self.max_size = 0.25

    def on_tick(self, tick: Tick) -> Signal | None:
        if not tick.liquidity_ok or tick.package_spread_pct > 0.25:
            return FLAT
        if len(tick.btc_recent) < 2:
            return FLAT
        move = (tick.btc_spot - tick.btc_recent[0]) / tick.btc_recent[0]
        if abs(move) > 0.01 and tick.package_mid < tick.package_mark * 1.02:
            return Signal(Side.LONG_STRADDLE, size=self.max_size, confidence=0.55)
        return FLAT

    def on_finish(self, result: RunResult) -> None:
        self.last_primary_score = result.metrics.get("primary_score", 0.0)

