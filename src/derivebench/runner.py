"""Submission loading, replay/live execution, and report writing."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, MutableMapping, Sequence

import pandas as pd

from derivebench.derive import DeriveRESTFeed, Tick, load_replay_ticks, tick_to_dict
from derivebench.metrics import summarize
from derivebench.model import FLAT, MarketInfo, Model, RunResult, RunSegment, Side, Signal
from derivebench.sim import AccountConfig, PaperAccount
from derivebench.submission_scan import scan_file

PACKAGE_RULE = "same-expiry nearest active OTM BTC call above spot and nearest active OTM put below spot, with liquidity/spread filters"


@dataclass(frozen=True, slots=True)
class RunConfig:
    output_dir: Path
    duration: float = 30.0
    starting_capital: float = 1000.0
    max_position_fraction: float = 1.0
    short_margin_fraction: float = 1.5
    slippage_bps: float = 10.0
    fee_rate: float = 0.0008
    latency_budget_ms: float = 500.0
    mode: str = "replay"
    official: bool = False


def load_submission(path: Path | str, *, class_name: str = "ModelSubmission", config: dict[str, Any] | None = None) -> Model:
    p = Path(path)
    if p.name != "model_submission.py":
        raise ValueError("submission file must be named model_submission.py")
    report = scan_file(p)
    if report.verdict != "accept":
        raise ValueError(report.format_text())
    spec = importlib.util.spec_from_file_location(f"derivebench_candidate_{abs(hash(p))}", p)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not import submission: {p}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(f"{p} does not define {class_name}")
    if not isinstance(cls, type) or not issubclass(cls, Model):
        raise ValueError(f"{class_name} must subclass derivebench.Model")
    return cls(config=config or {})


def normalize_signal(signal: Signal | None) -> Signal | None:
    if signal is None:
        return FLAT
    if not isinstance(signal, Signal):
        return None
    try:
        side = Side(signal.side)
        size = float(signal.size)
        confidence = float(signal.confidence)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(size) or not math.isfinite(confidence):
        return None
    return Signal(side=side, size=min(max(size, 0.0), 1.0), confidence=min(max(confidence, 0.0), 1.0))


def safe_on_tick(model: Model, tick: Tick, *, latency_budget_ms: float) -> tuple[Signal | None, bool]:
    started = time.perf_counter()
    try:
        signal = normalize_signal(model.on_tick(tick))
    except Exception:
        return None, False
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms > latency_budget_ms:
        return None, True
    return signal, False


def _market_info(config: RunConfig) -> MarketInfo:
    return MarketInfo(
        package_rule=PACKAGE_RULE,
        base_currency="BTC",
        quote_currency="USDC",
        starting_capital=config.starting_capital,
        max_position_fraction=config.max_position_fraction,
        slippage_bps=config.slippage_bps,
        fee_rate=config.fee_rate,
        scratch_dir=config.output_dir / "scratch",
    )


def _account(config: RunConfig) -> PaperAccount:
    return PaperAccount(
        AccountConfig(
            starting_capital=config.starting_capital,
            max_position_fraction=config.max_position_fraction,
            short_margin_fraction=config.short_margin_fraction,
            slippage_bps=config.slippage_bps,
            fee_rate=config.fee_rate,
        )
    )


def run_on_ticks(
    *,
    model: Model,
    benchmark: Model,
    ticks: Sequence[Tick],
    config: RunConfig,
) -> tuple[RunResult, list[dict[str, Any]]]:
    if not ticks:
        raise ValueError("at least one tick is required")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "scratch").mkdir(parents=True, exist_ok=True)
    info = _market_info(config)
    model.on_start(info)
    benchmark.on_start(info)
    model_account = _account(config)
    bench_account = _account(config)
    model_pending: Signal | None = None
    bench_pending: Signal | None = None
    rows: list[dict[str, Any]] = []
    started_ts = ticks[0].ts
    for tick in ticks:
        if model_pending is not None:
            model_account.execute(model_pending, tick)
        if bench_pending is not None:
            bench_account.execute(bench_pending, tick)
        model_equity = model_account.observe(tick)
        bench_equity = bench_account.observe(tick)
        model_signal, model_timeout = safe_on_tick(model, tick, latency_budget_ms=config.latency_budget_ms)
        bench_signal, bench_timeout = safe_on_tick(benchmark, tick, latency_budget_ms=config.latency_budget_ms)
        if model_timeout:
            model_account.timeout_count += 1
        if bench_timeout:
            bench_account.timeout_count += 1
        model_pending = model_signal
        bench_pending = bench_signal
        row = tick_to_dict(tick)
        row.update(
            model_equity=model_equity,
            model_position_contracts=model_account.position_contracts,
            model_signal=(model_signal.side.value if model_signal else "INVALID"),
            model_signal_size=(model_signal.size if model_signal else 0.0),
            model_timed_out=model_timeout,
            benchmark_equity=bench_equity,
            benchmark_position_contracts=bench_account.position_contracts,
            benchmark_signal=(bench_signal.side.value if bench_signal else "INVALID"),
            benchmark_signal_size=(bench_signal.size if bench_signal else 0.0),
            benchmark_timed_out=bench_timeout,
        )
        rows.append(row)
    last = ticks[-1]
    model_account.liquidate(last)
    bench_account.liquidate(last)
    model_final = model_account.observe(last)
    bench_final = bench_account.observe(last)
    model_account.position_contracts = 0.0
    bench_account.position_contracts = 0.0
    rows[-1].update(
        model_equity=model_final,
        model_position_contracts=model_account.position_contracts,
        benchmark_equity=bench_final,
        benchmark_position_contracts=bench_account.position_contracts,
    )
    ended_ts = last.ts
    model_segment = RunSegment(last.package_id, started_ts, ended_ts, "MIXED", model_final - config.starting_capital, model_account.trade_count, len(ticks), model_account.timeout_count)
    bench_segment = RunSegment(last.package_id, started_ts, ended_ts, "MIXED", bench_final - config.starting_capital, bench_account.trade_count, len(ticks), bench_account.timeout_count)
    model_metrics = summarize(
        starting_capital=config.starting_capital,
        final_equity=model_final,
        equity_curve=model_account.equity_curve,
        segments=(model_segment,),
        extras=model_account.summary_extras(),
    )
    bench_metrics = summarize(
        starting_capital=config.starting_capital,
        final_equity=bench_final,
        equity_curve=bench_account.equity_curve,
        segments=(bench_segment,),
        extras=bench_account.summary_extras(),
    )
    result = RunResult(
        started_ts=started_ts,
        ended_ts=ended_ts,
        starting_capital=config.starting_capital,
        final_equity=model_final,
        pnl_total=model_final - config.starting_capital,
        pnl_pct=(model_final - config.starting_capital) / config.starting_capital,
        segments=(model_segment,),
        metrics=model_metrics,
        benchmark_metrics=bench_metrics,
        benchmark_final_equity=bench_final,
        benchmark_pnl_total=bench_final - config.starting_capital,
        tick_count=len(ticks),
    )
    model.on_finish(result)
    benchmark.on_finish(result)
    return result, rows


def feed_health(ticks: Sequence[Tick], *, mode: str, extra: MutableMapping[str, Any] | None = None) -> dict[str, Any]:
    packages = {tick.package_id for tick in ticks}
    sources = {tick.feed_source for tick in ticks}
    duration = ticks[-1].ts - ticks[0].ts if len(ticks) > 1 else 0.0
    health: dict[str, Any] = {
        "mode": mode,
        "tick_count": len(ticks),
        "duration_seconds": duration,
        "feed_sources": sorted(sources),
        "package_count": len(packages),
        "packages": sorted(packages),
        "liquidity_ok_rate": sum(1 for tick in ticks if tick.liquidity_ok) / len(ticks) if ticks else 0.0,
        "min_package_mid": min((tick.package_mid for tick in ticks), default=0.0),
        "max_package_spread_pct": max((tick.package_spread_pct for tick in ticks), default=0.0),
    }
    if extra:
        health.update(dict(extra))
    return health


def write_outputs(
    *,
    result: RunResult,
    rows: list[dict[str, Any]],
    config: RunConfig,
    candidate_path: Path,
    benchmark_path: Path,
    feed_health_extra: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    ticks_path = config.output_dir / "ticks.parquet"
    report_path = config.output_dir / "report.json"
    pd.DataFrame(rows).to_parquet(ticks_path, index=False)
    ticks = [Tick(**{field: row[field] for field in Tick.__dataclass_fields__}) for row in rows]
    report = {
        "metadata": {
            "package": "derivebench",
            "mode": config.mode,
            "official": config.official,
            "started_ts": result.started_ts,
            "ended_ts": result.ended_ts,
            "duration_seconds": result.ended_ts - result.started_ts,
            "package_rule": PACKAGE_RULE,
            "starting_capital": config.starting_capital,
            "max_position_fraction": config.max_position_fraction,
            "short_margin_fraction": config.short_margin_fraction,
            "slippage_bps": config.slippage_bps,
            "fee_rate": config.fee_rate,
            "latency_budget_ms": config.latency_budget_ms,
        },
        "model": {
            "submission": str(candidate_path),
            "metrics": result.metrics,
            "segments": [asdict(segment) for segment in result.segments],
        },
        "benchmark": {
            "submission": str(benchmark_path),
            "metrics": result.benchmark_metrics,
        },
        "feed_health": feed_health(ticks, mode=config.mode, extra=feed_health_extra),
        "validation": {
            "liquidated": (
                result.metrics.get("final_position_contracts", 1.0) == 0.0
                and result.benchmark_metrics.get("final_position_contracts", 1.0) == 0.0
            ),
            "accepted_sides": [side.value for side in Side],
            "public_data_only": True,
            "synthetic_fills": False,
        },
        "paths": {
            "report": str(report_path),
            "ticks": str(ticks_path),
        },
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def run_replay(
    *,
    candidate: Model,
    benchmark: Model,
    data: Path,
    config: RunConfig,
    candidate_path: Path,
    benchmark_path: Path,
) -> RunResult:
    ticks = load_replay_ticks(data, duration=config.duration)
    result, rows = run_on_ticks(model=candidate, benchmark=benchmark, ticks=ticks, config=config)
    write_outputs(result=result, rows=rows, config=config, candidate_path=candidate_path, benchmark_path=benchmark_path)
    return result


def _record_live_health(health: MutableMapping[str, Any] | None, stats: dict[str, Any]) -> None:
    if health is not None:
        health.update(stats)


def collect_live_ticks(
    *,
    duration: float,
    poll_interval: float = 1.0,
    max_consecutive_failures: int = 5,
    feed: DeriveRESTFeed | None = None,
    health: MutableMapping[str, Any] | None = None,
) -> list[Tick]:
    if max_consecutive_failures < 1:
        raise ValueError("max_consecutive_failures must be at least 1")
    feed = feed or DeriveRESTFeed()
    deadline = time.time() + duration
    ticks: list[Tick] = []
    stats: dict[str, Any] = {
        "poll_attempts": 0,
        "poll_success_count": 0,
        "poll_error_count": 0,
        "consecutive_poll_failures": 0,
        "max_consecutive_poll_failures": 0,
        "poll_error_types": {},
        "last_poll_error": None,
        "poll_errors": [],
    }
    while time.time() < deadline:
        stats["poll_attempts"] += 1
        try:
            tick = feed.poll()
        except Exception as exc:
            error_type = type(exc).__name__
            stats["poll_error_count"] += 1
            stats["consecutive_poll_failures"] += 1
            stats["max_consecutive_poll_failures"] = max(
                stats["max_consecutive_poll_failures"],
                stats["consecutive_poll_failures"],
            )
            stats["poll_error_types"][error_type] = stats["poll_error_types"].get(error_type, 0) + 1
            stats["last_poll_error"] = f"{error_type}: {exc}"
            stats["poll_errors"].append(
                {
                    "ts": time.time(),
                    "type": error_type,
                    "message": str(exc),
                }
            )
            stats["poll_errors"] = stats["poll_errors"][-10:]
            _record_live_health(health, stats)
            if stats["consecutive_poll_failures"] >= max_consecutive_failures:
                raise RuntimeError(
                    "Derive live polling failed "
                    f"{stats['consecutive_poll_failures']} consecutive times; "
                    f"last error: {stats['last_poll_error']}"
                ) from exc
            time.sleep(max(0.0, poll_interval))
            continue
        ticks.append(tick)
        stats["poll_success_count"] += 1
        stats["consecutive_poll_failures"] = 0
        _record_live_health(health, stats)
        time.sleep(max(0.0, poll_interval))
    _record_live_health(health, stats)
    if not ticks:
        raise RuntimeError(
            "Derive live polling collected no valid ticks "
            f"over {duration:.3f}s; poll_errors={stats['poll_error_count']} "
            f"last_error={stats['last_poll_error']}"
        )
    return ticks


def run_live(
    *,
    candidate: Model,
    benchmark: Model,
    config: RunConfig,
    candidate_path: Path,
    benchmark_path: Path,
) -> RunResult:
    live_health: dict[str, Any] = {}
    ticks = collect_live_ticks(duration=config.duration, health=live_health)
    result, rows = run_on_ticks(model=candidate, benchmark=benchmark, ticks=ticks, config=config)
    write_outputs(
        result=result,
        rows=rows,
        config=config,
        candidate_path=candidate_path,
        benchmark_path=benchmark_path,
        feed_health_extra=live_health,
    )
    return result


def check_official_environment(config: RunConfig, output_dir: Path) -> None:
    if not config.official:
        return
    if os.environ.get("DERIVEBENCH_OFFICIAL_EVALUATOR") != "1":
        raise RuntimeError("DERIVEBENCH_OFFICIAL_EVALUATOR=1 is required for --official")
    if not output_dir.exists():
        raise RuntimeError("official output directory must exist")
