"""Report, parquet, and accounting validation for live/replay runs."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd

from derivebench.derive import tick_from_dict
from derivebench.metrics import summarize
from derivebench.model import RunSegment, Side, Signal
from derivebench.sim import AccountConfig, PaperAccount


ACCEPTED_SIDES = {side.value for side in Side}
DERIVE_CALL_RE = re.compile(r"^BTC-\d{8}-\d+(?:\.\d+)?-C$")
DERIVE_PUT_RE = re.compile(r"^BTC-\d{8}-\d+(?:\.\d+)?-P$")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _compare_numeric(name: str, left: Any, right: Any, failures: list[str], *, tolerance: float) -> None:
    lhs = _finite(left)
    rhs = _finite(right)
    if lhs is None or rhs is None:
        failures.append(f"{name} is not finite")
    elif abs(lhs - rhs) > tolerance:
        failures.append(f"{name} mismatch: report={lhs:.12g} parquet={rhs:.12g}")


def _signal_from_row(row: pd.Series, prefix: str) -> Signal | None:
    side_raw = str(row.get(f"{prefix}_signal", "INVALID"))
    if side_raw not in ACCEPTED_SIDES:
        return None
    return Signal(Side(side_raw), size=_f(row.get(f"{prefix}_signal_size")), confidence=0.0)


def _account_config(report: dict[str, Any]) -> AccountConfig:
    metadata = report.get("metadata") or {}
    metrics = ((report.get("model") or {}).get("metrics") or {})
    return AccountConfig(
        starting_capital=_f(metadata.get("starting_capital"), _f(metrics.get("starting_capital"), 1000.0)),
        max_position_fraction=_f(metadata.get("max_position_fraction"), 1.0),
        short_margin_fraction=_f(metadata.get("short_margin_fraction"), 1.5),
        slippage_bps=_f(metadata.get("slippage_bps"), 10.0),
        fee_rate=_f(metadata.get("fee_rate"), 0.0008),
    )


def recompute_accounting(report: dict[str, Any], ticks: pd.DataFrame, *, prefix: str) -> dict[str, Any]:
    if ticks.empty:
        raise ValueError("cannot audit empty tick table")
    config = _account_config(report)
    account = PaperAccount(config)
    pending: Signal | None = None
    first_tick = tick_from_dict(ticks.iloc[0].to_dict())
    last_tick = first_tick
    for _, row in ticks.iterrows():
        tick = tick_from_dict(row.to_dict())
        last_tick = tick
        if pending is not None:
            account.execute(pending, tick)
        account.observe(tick)
        if bool(row.get(f"{prefix}_timed_out", False)):
            account.timeout_count += 1
        pending = _signal_from_row(row, prefix)
    account.liquidate(last_tick)
    final_equity = account.observe(last_tick)
    account.position_contracts = 0.0
    segment = RunSegment(
        last_tick.package_id,
        first_tick.ts,
        last_tick.ts,
        "MIXED",
        final_equity - config.starting_capital,
        account.trade_count,
        len(ticks),
        account.timeout_count,
    )
    metrics = summarize(
        starting_capital=config.starting_capital,
        final_equity=final_equity,
        equity_curve=account.equity_curve,
        segments=(segment,),
        extras=account.summary_extras(),
    )
    return {
        "prefix": prefix,
        "starting_capital": config.starting_capital,
        "final_equity": final_equity,
        "pnl_total": final_equity - config.starting_capital,
        "metrics": metrics,
    }


def accounting_audit(
    *,
    report_path: Path,
    ticks_path: Path,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text())
    ticks = pd.read_parquet(ticks_path)
    failures: list[str] = []
    audited: dict[str, Any] = {}
    for prefix, section_name in (("model", "model"), ("benchmark", "benchmark")):
        expected_metrics = ((report.get(section_name) or {}).get("metrics") or {})
        if not expected_metrics:
            failures.append(f"{section_name} metrics missing from report")
            continue
        try:
            actual = recompute_accounting(report, ticks, prefix=prefix)
        except Exception as exc:
            failures.append(f"{section_name} accounting recompute failed: {exc}")
            continue
        audited[prefix] = {
            "final_equity": actual["final_equity"],
            "pnl_total": actual["pnl_total"],
            "primary_score": actual["metrics"].get("primary_score"),
            "trade_count": actual["metrics"].get("trade_count"),
        }
        for key in ("final_equity", "pnl_total", "primary_score", "max_drawdown", "timeout_rate", "trade_count"):
            expected = _f(expected_metrics.get(key))
            got = _f(actual["metrics"].get(key))
            if abs(expected - got) > tolerance:
                failures.append(f"{section_name} {key} mismatch: report={expected:.12g} recomputed={got:.12g}")
    return {"ok": not failures, "failures": failures, "audited": audited}


def validate_run(
    *,
    report_path: Path,
    ticks_path: Path,
    min_duration: float,
    min_ticks: int,
    allow_replay: bool = False,
    max_staleness_seconds: float = 300.0,
    max_age_seconds: float | None = None,
    max_timeout_rate: float = 0.05,
    min_liquidity_ok_rate: float = 0.95,
    audit_tolerance: float = 1e-6,
    now: float | None = None,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text())
    ticks = pd.read_parquet(ticks_path)
    failures: list[str] = []
    metadata = report.get("metadata", {})
    model_metrics = (report.get("model") or {}).get("metrics") or {}
    benchmark_metrics = (report.get("benchmark") or {}).get("metrics") or {}
    feed = report.get("feed_health") or {}
    duration = float(metadata.get("duration_seconds") or 0.0)
    freshness_limit = max_staleness_seconds if max_age_seconds is None else max_age_seconds
    if duration < min_duration:
        failures.append(f"duration {duration:.3f}s below minimum {min_duration:.3f}s")
    if len(ticks) < min_ticks:
        failures.append(f"tick count {len(ticks)} below minimum {min_ticks}")
    if metadata.get("mode") == "replay" and not allow_replay:
        failures.append("replay report requires --allow-replay")
    if metadata.get("mode") not in {"live", "replay"}:
        failures.append("metadata mode must be live or replay")
    if metadata.get("mode") == "live" and "feed_source" in ticks and not bool((ticks["feed_source"] == "derive_rest").all()):
        failures.append("live rows must come from derive_rest")
    for col in (
        "ts",
        "exchange_ts",
        "feed_source",
        "call_name",
        "put_name",
        "expiry_date",
        "btc_spot",
        "btc_index",
        "call_strike",
        "put_strike",
        "call_bid",
        "call_ask",
        "call_mid",
        "call_mark",
        "put_bid",
        "put_ask",
        "put_mid",
        "put_mark",
        "package_bid",
        "package_ask",
        "package_mid",
        "package_mark",
        "liquidity_ok",
        "model_equity",
        "model_position_contracts",
        "benchmark_equity",
        "benchmark_position_contracts",
        "model_signal",
        "benchmark_signal",
    ):
        if col not in ticks.columns:
            failures.append(f"ticks missing column {col}")
    if {"ts", "exchange_ts"}.issubset(ticks.columns) and metadata.get("mode") == "live":
        stale = (ticks["ts"].astype(float) - ticks["exchange_ts"].astype(float)).abs()
        if bool((stale > freshness_limit).any()):
            failures.append(f"live feed staleness exceeds {freshness_limit:.1f}s")
    numeric_positive = [
        "btc_spot",
        "btc_index",
        "call_bid",
        "call_ask",
        "call_mid",
        "call_mark",
        "put_bid",
        "put_ask",
        "put_mid",
        "put_mark",
        "package_bid",
        "package_ask",
        "package_mid",
        "package_mark",
    ]
    for col in numeric_positive:
        if col in ticks:
            values = pd.to_numeric(ticks[col], errors="coerce")
            invalid = values.isna() | ~values.map(math.isfinite) | (values <= 0.0)
            if bool(invalid.any()):
                failures.append(f"{col} must be positive and finite on every row")
    if "package_ask" in ticks and "package_bid" in ticks and bool((ticks["package_ask"] < ticks["package_bid"]).any()):
        failures.append("package ask below bid")
    if {"call_ask", "call_bid", "put_ask", "put_bid"}.issubset(ticks.columns):
        if bool((ticks["call_ask"] < ticks["call_bid"]).any()) or bool((ticks["put_ask"] < ticks["put_bid"]).any()):
            failures.append("leg ask below bid")
    if {"call_name", "put_name"}.issubset(ticks.columns):
        if not bool(ticks["call_name"].astype(str).str.startswith("BTC-").all()):
            failures.append("call_name is not a BTC Derive option")
        if not bool(ticks["put_name"].astype(str).str.startswith("BTC-").all()):
            failures.append("put_name is not a BTC Derive option")
    if "call_name" in ticks and not bool(ticks["call_name"].astype(str).map(lambda value: bool(DERIVE_CALL_RE.fullmatch(value))).all()):
        failures.append("call_name does not match BTC-YYYYMMDD-strike-C")
    if "put_name" in ticks and not bool(ticks["put_name"].astype(str).map(lambda value: bool(DERIVE_PUT_RE.fullmatch(value))).all()):
        failures.append("put_name does not match BTC-YYYYMMDD-strike-P")
    if {"call_strike", "put_strike", "btc_spot"}.issubset(ticks.columns):
        if bool((ticks["call_strike"].astype(float) <= ticks["btc_spot"].astype(float)).any()):
            failures.append("call strike is not OTM above BTC spot")
        if bool((ticks["put_strike"].astype(float) >= ticks["btc_spot"].astype(float)).any()):
            failures.append("put strike is not OTM below BTC spot")
    if {"call_bid", "put_bid", "package_bid", "call_ask", "put_ask", "package_ask"}.issubset(ticks.columns):
        if bool(((ticks["call_bid"] + ticks["put_bid"] - ticks["package_bid"]).abs() > 1e-9).any()):
            failures.append("package_bid does not equal call_bid + put_bid")
        if bool(((ticks["call_ask"] + ticks["put_ask"] - ticks["package_ask"]).abs() > 1e-9).any()):
            failures.append("package_ask does not equal call_ask + put_ask")
    for col in ("model_signal", "benchmark_signal"):
        if col in ticks and not bool(ticks[col].astype(str).isin(ACCEPTED_SIDES).all()):
            failures.append(f"{col} contains an invalid side")
    for participant, metrics in (("model", model_metrics), ("benchmark", benchmark_metrics)):
        for key in ("pnl_total", "sharpe", "max_drawdown", "primary_score", "timeout_rate", "final_equity", "final_position_contracts"):
            if key not in metrics:
                failures.append(f"{participant} metrics missing {key}")
        equity_col = f"{participant}_equity"
        position_col = f"{participant}_position_contracts"
        if equity_col in ticks and "final_equity" in metrics and len(ticks) > 0:
            _compare_numeric(
                f"{participant} final_equity",
                metrics.get("final_equity"),
                ticks.iloc[-1][equity_col],
                failures,
                tolerance=audit_tolerance,
            )
        if position_col in ticks and "final_position_contracts" in metrics and len(ticks) > 0:
            _compare_numeric(
                f"{participant} final_position_contracts",
                metrics.get("final_position_contracts"),
                ticks.iloc[-1][position_col],
                failures,
                tolerance=audit_tolerance,
            )
        timeout_rate = _finite(metrics.get("timeout_rate"))
        if timeout_rate is None:
            failures.append(f"{participant} timeout_rate is not finite")
        elif timeout_rate > max_timeout_rate:
            failures.append(f"{participant} timeout_rate exceeds {max_timeout_rate:.3f}")
        report_position = _finite(metrics.get("final_position_contracts"))
        row_position = _finite(ticks.iloc[-1][position_col]) if position_col in ticks and len(ticks) > 0 else None
        if report_position is None or row_position is None:
            failures.append(f"{participant} final position is not finite")
        elif abs(report_position) > audit_tolerance or abs(row_position) > audit_tolerance:
            failures.append(f"{participant} has unresolved final accounting state")
    if not report.get("validation", {}).get("liquidated", False):
        failures.append("report says final exposure was not liquidated")
    if int(feed.get("tick_count") or 0) != len(ticks):
        failures.append("feed_health tick_count does not match parquet rows")
    for participant, metrics in (("model", model_metrics), ("benchmark", benchmark_metrics)):
        if int(metrics.get("n_ticks") or 0) != len(ticks):
            failures.append(f"{participant} metrics n_ticks does not match parquet rows")
    if {"ts"}.issubset(ticks.columns) and len(ticks) > 0:
        parquet_duration = float(ticks.iloc[-1]["ts"] - ticks.iloc[0]["ts"])
        if abs(duration - parquet_duration) > 1e-6:
            failures.append("metadata duration_seconds does not match parquet timestamps")
        if "duration_seconds" in feed and abs(float(feed.get("duration_seconds") or 0.0) - parquet_duration) > 1e-6:
            failures.append("feed_health duration_seconds does not match parquet timestamps")
    if not allow_replay:
        if metadata.get("mode") != "live":
            failures.append("non-replay validation requires live metadata mode")
        if len(ticks) == 0:
            failures.append("live validation requires nonzero tick count")
        if duration <= 0.0:
            failures.append("live validation requires nonzero duration")
        if "feed_source" in ticks:
            sources = {str(source) for source in ticks["feed_source"].dropna().unique()}
            if sources != {"derive_rest"}:
                failures.append(f"live validation requires derive_rest feed source, got {sorted(sources)}")
        if len(ticks) > 0:
            current_time = time.time() if now is None else now
            for col in ("ts", "exchange_ts"):
                if col not in ticks:
                    continue
                latest = _finite(ticks.iloc[-1][col])
                if latest is None:
                    failures.append(f"latest {col} is not finite")
                    continue
                age = current_time - latest
                if age < 0.0 or age > freshness_limit:
                    failures.append(f"latest {col} age exceeds {freshness_limit:.1f}s")
        if "liquidity_ok" in ticks:
            liquidity_ok_rate = float(ticks["liquidity_ok"].astype(bool).mean()) if len(ticks) else 0.0
            if liquidity_ok_rate < min_liquidity_ok_rate:
                failures.append(f"liquidity_ok rate below {min_liquidity_ok_rate:.3f}")
            if "liquidity_ok_rate" in feed and abs(float(feed.get("liquidity_ok_rate") or 0.0) - liquidity_ok_rate) > 1e-9:
                failures.append("feed_health liquidity_ok_rate does not match parquet")
    audit = accounting_audit(report_path=report_path, ticks_path=ticks_path, tolerance=audit_tolerance)
    failures.extend(f"accounting audit: {failure}" for failure in audit["failures"])
    return {
        "ok": not failures,
        "failures": failures,
        "duration_seconds": duration,
        "tick_count": len(ticks),
        "primary_score": model_metrics.get("primary_score"),
        "accounting_audit": audit,
    }
