from __future__ import annotations

import math
import statistics

from derivebench import FLAT, MarketInfo, Model, Side, Signal, Tick


class ModelSubmission(Model):
    def on_start(self, market_info: MarketInfo) -> None:
        self.max_long = 0.65
        self.max_short = 0.35
        self.min_window = 4

    def on_tick(self, tick: Tick) -> Signal | None:
        if not tick.liquidity_ok or tick.package_spread_pct > 0.22:
            return FLAT
        if tick.time_to_expiry < 6 * 3600:
            return FLAT
        spot_window = list(tick.btc_recent)[-30:] + [tick.btc_spot]
        package_window = list(tick.package_mid_recent)[-30:] + [tick.package_mid]
        if len(spot_window) < self.min_window or len(package_window) < self.min_window:
            return FLAT
        realized_move = abs(spot_window[-1] - spot_window[0]) / max(spot_window[0], 1.0)
        returns = [
            (b - a) / max(a, 1.0)
            for a, b in zip(spot_window, spot_window[1:])
            if a > 0.0
        ]
        realized_vol = statistics.pstdev(returns) * math.sqrt(86_400.0) if len(returns) > 1 else 0.0
        package_ratio = tick.package_mid / max(tick.btc_spot, 1.0)
        mark_edge = (tick.package_mark - tick.package_mid) / max(tick.package_mid, 1.0)
        trend = (package_window[-1] - package_window[0]) / max(package_window[0], 1.0)
        if realized_vol + realized_move > package_ratio * 1.20 and mark_edge > -0.04 and trend >= -0.15:
            size = min(self.max_long, 0.20 + 8.0 * max(0.0, realized_vol - package_ratio))
            return Signal(Side.LONG_STRADDLE, size=size, confidence=0.65)
        if package_ratio > max(0.015, realized_vol * 1.80) and mark_edge < 0.08 and abs(trend) < 0.20:
            return Signal(Side.SHORT_STRADDLE, size=self.max_short, confidence=0.60)
        return FLAT

