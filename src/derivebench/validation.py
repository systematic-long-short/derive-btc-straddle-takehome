"""Report and parquet validation for live/replay runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def validate_run(
    *,
    report_path: Path,
    ticks_path: Path,
    min_duration: float,
    min_ticks: int,
    allow_replay: bool = False,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text())
    ticks = pd.read_parquet(ticks_path)
    failures: list[str] = []
    metadata = report.get("metadata", {})
    model_metrics = (report.get("model") or {}).get("metrics") or {}
    feed = report.get("feed_health") or {}
    duration = float(metadata.get("duration_seconds") or 0.0)
    if duration < min_duration:
        failures.append(f"duration {duration:.3f}s below minimum {min_duration:.3f}s")
    if len(ticks) < min_ticks:
        failures.append(f"tick count {len(ticks)} below minimum {min_ticks}")
    if metadata.get("mode") == "replay" and not allow_replay:
        failures.append("replay report requires --allow-replay")
    for col in ("call_name", "put_name", "expiry_date", "package_bid", "package_ask", "package_mid", "model_equity"):
        if col not in ticks.columns:
            failures.append(f"ticks missing column {col}")
    if "package_ask" in ticks and "package_bid" in ticks and bool((ticks["package_ask"] < ticks["package_bid"]).any()):
        failures.append("package ask below bid")
    if "call_name" in ticks and not bool(ticks["call_name"].astype(str).str.contains("-C").all()):
        failures.append("call_name does not look like Derive call instrument")
    if "put_name" in ticks and not bool(ticks["put_name"].astype(str).str.contains("-P").all()):
        failures.append("put_name does not look like Derive put instrument")
    for key in ("pnl_total", "sharpe", "max_drawdown", "primary_score", "timeout_rate"):
        if key not in model_metrics:
            failures.append(f"model metrics missing {key}")
    if not report.get("validation", {}).get("liquidated", False):
        failures.append("report says final exposure was not liquidated")
    if int(feed.get("tick_count") or 0) != len(ticks):
        failures.append("feed_health tick_count does not match parquet rows")
    return {
        "ok": not failures,
        "failures": failures,
        "duration_seconds": duration,
        "tick_count": len(ticks),
        "primary_score": model_metrics.get("primary_score"),
    }

