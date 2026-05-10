"""Scoring metrics for replay and live runs."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from derivebench.model import RunSegment

TICKS_PER_YEAR_AT_1HZ = 365.0 * 24.0 * 3600.0


def tick_returns(equity_curve: Sequence[float]) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(equity_curve, equity_curve[1:]):
        out.append(0.0 if prev == 0.0 or not math.isfinite(prev) else (cur - prev) / prev)
    return out


def sharpe_ratio(returns: Sequence[float], ticks_per_year: float = TICKS_PER_YEAR_AT_1HZ) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(max(var, 0.0))
    return 0.0 if std == 0.0 else (mean / std) * math.sqrt(ticks_per_year)


def max_drawdown(equity_curve: Sequence[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def segment_hit_rate(segments: Sequence[RunSegment]) -> float:
    if not segments:
        return 0.0
    return sum(1 for segment in segments if segment.pnl_total > 0.0) / len(segments)


def summarize(
    *,
    starting_capital: float,
    final_equity: float,
    equity_curve: Sequence[float],
    segments: Sequence[RunSegment],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    returns = tick_returns(equity_curve)
    pnl = final_equity - starting_capital
    pnl_pct = pnl / starting_capital if starting_capital else 0.0
    raw_sharpe = sharpe_ratio(returns)
    sharpe = max(raw_sharpe, 0.0)
    mdd = max_drawdown(equity_curve)
    primary_score = pnl * sharpe * max(0.0, 1.0 - mdd)
    ticks = sum(segment.n_ticks for segment in segments)
    timeouts = sum(segment.n_timeouts for segment in segments)
    metrics: dict[str, Any] = {
        "starting_capital": starting_capital,
        "final_equity": final_equity,
        "pnl_total": pnl,
        "pnl_pct": pnl_pct,
        "sharpe": sharpe,
        "raw_sharpe": raw_sharpe,
        "max_drawdown": mdd,
        "primary_score": primary_score,
        "segment_hit_rate": segment_hit_rate(segments),
        "hit_rate": segment_hit_rate(segments),
        "timeout_rate": (timeouts / ticks) if ticks else 0.0,
        "n_segments": len(segments),
        "n_ticks": ticks,
        "n_trades": sum(segment.n_trades for segment in segments),
    }
    metrics.update(extras or {})
    return metrics

