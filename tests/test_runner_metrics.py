from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest

from derivebench import FLAT, Model, Side, Signal, Tick
from derivebench.derive import load_replay_ticks
from derivebench.metrics import summarize
from derivebench.runner import RunConfig, load_submission, run_on_ticks, run_replay

from tests.conftest import FIXTURES, ROOT


class LongModel(Model):
    def on_tick(self, tick: Tick) -> Signal | None:
        return Signal(Side.LONG_STRADDLE, size=0.5)


class FlatModel(Model):
    def on_tick(self, tick: Tick) -> Signal | None:
        return FLAT


class SlowModel(Model):
    def on_tick(self, tick: Tick) -> Signal | None:
        time.sleep(0.01)
        return Signal(Side.LONG_STRADDLE, size=0.5)


class HangingModel(Model):
    def on_tick(self, tick: Tick) -> Signal | None:
        while True:
            pass


def test_next_tick_execution_records_position_after_second_tick(tmp_path: Path) -> None:
    ticks = load_replay_ticks(FIXTURES / "derive_replay.json")
    config = RunConfig(output_dir=tmp_path, latency_budget_ms=500.0)
    _, rows = run_on_ticks(model=LongModel(), benchmark=FlatModel(), ticks=ticks, config=config)
    assert rows[0]["model_position_contracts"] == 0.0
    assert rows[1]["model_position_contracts"] > 0.0


def test_primary_score_formula_uses_positive_sharpe_and_drawdown_penalty() -> None:
    metrics = summarize(starting_capital=100.0, final_equity=120.0, equity_curve=[100.0, 110.0, 105.0, 120.0], segments=())
    expected = metrics["pnl_total"] * max(metrics["raw_sharpe"], 0.0) * (1.0 - metrics["max_drawdown"])
    assert metrics["primary_score"] == pytest.approx(expected)


def test_timeout_handling_drops_slow_signal(tmp_path: Path) -> None:
    ticks = load_replay_ticks(FIXTURES / "derive_replay.json")
    config = RunConfig(output_dir=tmp_path, latency_budget_ms=1.0)
    result, rows = run_on_ticks(model=SlowModel(), benchmark=FlatModel(), ticks=ticks[:2], config=config)
    assert result.metrics["timeout_rate"] > 0.0
    assert rows[0]["model_signal"] == "INVALID"
    assert rows[1]["model_position_contracts"] == 0.0


def test_infinite_on_tick_is_marked_timed_out_without_hanging(tmp_path: Path) -> None:
    ticks = load_replay_ticks(FIXTURES / "derive_replay.json")
    config = RunConfig(output_dir=tmp_path, latency_budget_ms=50.0)
    result, rows = run_on_ticks(model=HangingModel(), benchmark=FlatModel(), ticks=ticks[:1], config=config)
    assert rows[0]["model_timed_out"] is True
    assert rows[0]["model_signal"] == "INVALID"
    assert result.metrics["timeout_rate"] == pytest.approx(1.0)


def test_load_submission_rejects_slow_import_with_timeout(tmp_path: Path) -> None:
    candidate_path = tmp_path / "model_submission.py"
    candidate_path.write_text(
        "\n".join(
            [
                "import time",
                "from derivebench import FLAT, Model, Signal, Tick",
                "time.sleep(1.0)",
                "",
                "class ModelSubmission(Model):",
                "    def on_tick(self, tick: Tick) -> Signal | None:",
                "        return FLAT",
                "",
            ]
        )
    )
    with pytest.raises(TimeoutError, match="load submission .* timed out"):
        load_submission(candidate_path, timeout_seconds=0.05)


def test_replay_writes_report_and_parquet_schema(tmp_path: Path) -> None:
    candidate_path = tmp_path / "model_submission.py"
    candidate_path.write_text((FIXTURES / "safe_submission.py").read_text())
    benchmark_path = ROOT / "model_submissions" / "benchmark_straddle" / "model_submission.py"
    candidate = load_submission(candidate_path)
    benchmark = load_submission(benchmark_path)
    config = RunConfig(output_dir=tmp_path, duration=30.0, mode="replay")
    result = run_replay(candidate=candidate, benchmark=benchmark, data=FIXTURES / "derive_replay.json", config=config, candidate_path=candidate_path, benchmark_path=benchmark_path)
    report = json.loads((tmp_path / "report.json").read_text())
    ticks = pd.read_parquet(tmp_path / "ticks.parquet")
    assert result.tick_count == 4
    assert set(["metadata", "model", "benchmark", "feed_health", "validation", "paths"]).issubset(report)
    assert "package_id" in ticks.columns
    assert report["metadata"]["starting_capital"] == 1000.0
    assert report["metadata"]["short_margin_fraction"] == 1.5
    assert report["model"]["metrics"]["n_ticks"] == 4
    for participant in ("model", "benchmark"):
        metrics = report[participant]["metrics"]
        assert metrics["final_equity"] == pytest.approx(ticks[f"{participant}_equity"].iloc[-1])
        assert metrics["final_position_contracts"] == pytest.approx(ticks[f"{participant}_position_contracts"].iloc[-1])
        assert ticks[f"{participant}_position_contracts"].iloc[-1] == pytest.approx(0.0)
