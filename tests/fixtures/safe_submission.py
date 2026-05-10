from derivebench import FLAT, Model, Side, Signal, Tick


class ModelSubmission(Model):
    def on_tick(self, tick: Tick) -> Signal | None:
        if tick.package_spread_pct < 0.20:
            return Signal(Side.LONG_STRADDLE, size=0.5, confidence=0.5)
        return FLAT

