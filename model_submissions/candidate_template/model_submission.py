from __future__ import annotations

from derivebench import FLAT, Model, Side, Signal, Tick


class ModelSubmission(Model):
    def on_tick(self, tick: Tick) -> Signal | None:
        if not tick.liquidity_ok:
            return FLAT
        if tick.package_mid < tick.package_mark * 0.97:
            return Signal(side=Side.LONG_STRADDLE, size=0.25, confidence=0.5)
        if tick.package_mid > tick.package_mark * 1.08:
            return Signal(side=Side.SHORT_STRADDLE, size=0.20, confidence=0.5)
        return FLAT

