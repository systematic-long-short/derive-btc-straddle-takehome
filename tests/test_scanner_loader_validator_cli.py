from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from derivebench.runner import load_submission
from derivebench.submission_scan import scan_file
from derivebench.validation import validate_run

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

