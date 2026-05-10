from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from derivebench.runner import load_submission
from derivebench.submission_scan import scan_file
from derivebench.validation import accounting_audit, validate_run

from tests.conftest import FIXTURES, ROOT


def test_scanner_accepts_safe_and_rejects_unsafe() -> None:
    assert scan_file(FIXTURES / "safe_submission.py").verdict == "accept"
    report = scan_file(FIXTURES / "unsafe_submission.py")
    assert report.verdict == "reject"
    assert any(f.rule == "blocked_import" for f in report.findings)


def test_loader_requires_model_submission_filename(tmp_path: Path) -> None:
    bad = tmp_path / "candidate.py"
    bad.write_text((FIXTURES / "safe_submission.py").read_text())
    try:
        load_submission(bad)
    except ValueError as exc:
        assert "model_submission.py" in str(exc)
    else:
        raise AssertionError("expected load_submission to reject nonstandard filename")


def test_cli_scanner_and_replay_smoke(tmp_path: Path) -> None:
    scan = subprocess.run(
        [sys.executable, "scripts/scan_submission.py", "model_submissions/benchmark_straddle/model_submission.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "verdict: ACCEPT" in scan.stdout
    out = tmp_path / "run"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_baseline.py",
            "--mode", "replay",
            "--data", "tests/fixtures/derive_replay.json",
            "--output", str(out),
            "--duration", "30",
        ],
        cwd=ROOT,
        check=True,
    )
    summary = validate_run(report_path=out / "report.json", ticks_path=out / "ticks.parquet", min_duration=1.0, min_ticks=1, allow_replay=True)
    assert summary["ok"], summary
    audit = accounting_audit(report_path=out / "report.json", ticks_path=out / "ticks.parquet")
    assert audit["ok"], audit


def _write_report(path: Path, report: dict) -> Path:
    path.write_text(json.dumps(report))
    return path


def test_validator_failure_modes(tmp_path: Path) -> None:
    report = {
        "metadata": {"mode": "replay", "duration_seconds": 0.0},
        "model": {"metrics": {}},
        "feed_health": {"tick_count": 0},
        "validation": {"liquidated": False},
    }
    report_path = tmp_path / "report.json"
    ticks_path = tmp_path / "ticks.parquet"
    report_path.write_text(json.dumps(report))
    import pandas as pd

    pd.DataFrame({"x": [1]}).to_parquet(ticks_path, index=False)
    summary = validate_run(report_path=report_path, ticks_path=ticks_path, min_duration=1.0, min_ticks=2, allow_replay=False)
    assert not summary["ok"]
    assert len(summary["failures"]) >= 4


def test_validator_rejects_report_parquet_final_accounting_mismatch(tmp_path: Path) -> None:
    out = tmp_path / "run"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_baseline.py",
            "--mode", "replay",
            "--data", "tests/fixtures/derive_replay.json",
            "--output", str(out),
            "--duration", "30",
        ],
        cwd=ROOT,
        check=True,
    )
    import pandas as pd

    report_path = out / "report.json"
    ticks_path = out / "ticks.parquet"
    report = json.loads(report_path.read_text())
    report["benchmark"]["metrics"]["timeout_rate"] = 0.50
    report_path.write_text(json.dumps(report))
    ticks = pd.read_parquet(ticks_path)
    last_idx = ticks.index[-1]
    ticks.loc[last_idx, "model_position_contracts"] = 0.25
    ticks.loc[last_idx, "benchmark_equity"] = ticks.loc[last_idx, "benchmark_equity"] + 1.0
    ticks.to_parquet(ticks_path, index=False)

    summary = validate_run(
        report_path=report_path,
        ticks_path=ticks_path,
        min_duration=1.0,
        min_ticks=1,
        allow_replay=True,
    )
    assert not summary["ok"]
    assert any("model final_position_contracts mismatch" in failure for failure in summary["failures"])
    assert any("model has unresolved final accounting state" in failure for failure in summary["failures"])
    assert any("benchmark final_equity mismatch" in failure for failure in summary["failures"])
    assert any("benchmark timeout_rate exceeds" in failure for failure in summary["failures"])


def test_validator_rejects_invalid_side_and_non_otm_replay(tmp_path: Path) -> None:
    out = tmp_path / "run"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_baseline.py",
            "--mode", "replay",
            "--data", "tests/fixtures/derive_replay.json",
            "--output", str(out),
            "--duration", "30",
        ],
        cwd=ROOT,
        check=True,
    )
    import pandas as pd

    ticks_path = out / "ticks.parquet"
    ticks = pd.read_parquet(ticks_path)
    ticks.loc[0, "model_signal"] = "CALL_ONLY"
    ticks.loc[0, "call_strike"] = ticks.loc[0, "btc_spot"] - 1.0
    ticks.loc[0, "call_name"] = "BTC-CALL-BAD"
    ticks.loc[0, "package_ask"] = ticks.loc[0, "package_bid"] - 0.01
    ticks.loc[0, "package_mid"] = 0.0
    ticks.to_parquet(ticks_path, index=False)
    summary = validate_run(
        report_path=out / "report.json",
        ticks_path=ticks_path,
        min_duration=1.0,
        min_ticks=1,
        allow_replay=True,
    )
    assert not summary["ok"]
    assert any("model_signal contains an invalid side" in failure for failure in summary["failures"])
    assert any("call strike is not OTM" in failure for failure in summary["failures"])
    assert any("call_name does not match BTC-YYYYMMDD-strike-C" in failure for failure in summary["failures"])
    assert any("package ask below bid" in failure for failure in summary["failures"])
    assert any("package_mid must be positive and finite" in failure for failure in summary["failures"])


def test_validator_enforces_live_source_freshness_and_liquidity(tmp_path: Path) -> None:
    out = tmp_path / "run"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_baseline.py",
            "--mode", "replay",
            "--data", "tests/fixtures/derive_replay.json",
            "--output", str(out),
            "--duration", "30",
        ],
        cwd=ROOT,
        check=True,
    )
    import pandas as pd

    report_path = out / "report.json"
    ticks_path = out / "ticks.parquet"
    report = json.loads(report_path.read_text())
    report["metadata"]["mode"] = "live"
    report_path.write_text(json.dumps(report))
    ticks = pd.read_parquet(ticks_path)
    ticks.loc[0, "liquidity_ok"] = False
    ticks.to_parquet(ticks_path, index=False)

    summary = validate_run(
        report_path=report_path,
        ticks_path=ticks_path,
        min_duration=1.0,
        min_ticks=1,
        allow_replay=False,
        max_age_seconds=1.0,
        min_liquidity_ok_rate=1.0,
        now=float(ticks["ts"].iloc[-1]) + 5.0,
    )
    assert not summary["ok"]
    assert any("live validation requires derive_rest feed source" in failure for failure in summary["failures"])
    assert any("latest ts age exceeds" in failure for failure in summary["failures"])
    assert any("latest exchange_ts age exceeds" in failure for failure in summary["failures"])
    assert any("liquidity_ok rate below" in failure for failure in summary["failures"])


def test_validator_rejects_inconsistent_feed_health_poll_counters(tmp_path: Path) -> None:
    out = tmp_path / "run"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_baseline.py",
            "--mode",
            "replay",
            "--data",
            "tests/fixtures/derive_replay.json",
            "--output",
            str(out),
            "--duration",
            "30",
        ],
        cwd=ROOT,
        check=True,
    )

    report_path = out / "report.json"
    ticks_path = out / "ticks.parquet"
    base_report = json.loads(report_path.read_text())
    base_summary = validate_run(
        report_path=report_path,
        ticks_path=ticks_path,
        min_duration=1.0,
        min_ticks=1,
        allow_replay=True,
    )
    assert base_summary["ok"], base_summary
    tick_count = base_summary["tick_count"]

    def with_poll_health(**overrides: object) -> dict:
        report = json.loads(json.dumps(base_report))
        report["feed_health"].update(
            {
                "poll_attempts": tick_count,
                "poll_success_count": tick_count,
                "poll_error_count": 0,
                "consecutive_poll_failures": 0,
                "max_consecutive_poll_failures": 0,
                "poll_errors": [],
            }
        )
        report["feed_health"].update(overrides)
        return report

    cases = [
        (
            "attempts_sum_mismatch",
            with_poll_health(poll_attempts=tick_count + 2, poll_error_count=1),
            "feed_health poll_attempts does not equal successes plus errors",
        ),
        (
            "success_count_tick_mismatch",
            with_poll_health(poll_attempts=tick_count + 1, poll_success_count=tick_count + 1),
            "feed_health poll_success_count does not match parquet rows",
        ),
        (
            "consecutive_failures_exceed_max",
            with_poll_health(
                poll_attempts=tick_count + 2,
                poll_error_count=2,
                consecutive_poll_failures=2,
                max_consecutive_poll_failures=1,
            ),
            "feed_health consecutive_poll_failures exceeds max_consecutive_poll_failures",
        ),
        (
            "poll_errors_not_list",
            with_poll_health(poll_errors={"type": "RuntimeError", "message": "boom"}),
            "feed_health poll_errors must be a list when present",
        ),
    ]

    for name, report, expected_failure in cases:
        summary = validate_run(
            report_path=_write_report(tmp_path / f"{name}.json", report),
            ticks_path=ticks_path,
            min_duration=1.0,
            min_ticks=1,
            allow_replay=True,
        )
        assert not summary["ok"], name
        assert any(expected_failure in failure for failure in summary["failures"]), summary


def test_validator_allows_populated_feed_health_poll_errors_when_counters_match(tmp_path: Path) -> None:
    out = tmp_path / "run"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_baseline.py",
            "--mode",
            "replay",
            "--data",
            "tests/fixtures/derive_replay.json",
            "--output",
            str(out),
            "--duration",
            "30",
        ],
        cwd=ROOT,
        check=True,
    )

    report_path = out / "report.json"
    ticks_path = out / "ticks.parquet"
    report = json.loads(report_path.read_text())
    base_summary = validate_run(
        report_path=report_path,
        ticks_path=ticks_path,
        min_duration=1.0,
        min_ticks=1,
        allow_replay=True,
    )
    assert base_summary["ok"], base_summary
    tick_count = base_summary["tick_count"]
    report["feed_health"].update(
        {
            "poll_attempts": tick_count + 1,
            "poll_success_count": tick_count,
            "poll_error_count": 1,
            "consecutive_poll_failures": 0,
            "max_consecutive_poll_failures": 1,
            "poll_errors": [{"type": "RuntimeError", "message": "transient spot failure"}],
        }
    )

    summary = validate_run(
        report_path=_write_report(tmp_path / "report_with_poll_errors.json", report),
        ticks_path=ticks_path,
        min_duration=1.0,
        min_ticks=1,
        allow_replay=True,
    )
    assert summary["ok"], summary
