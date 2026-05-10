from __future__ import annotations

from pathlib import Path

import pytest

import scripts.run_official_evaluation as official


def test_official_wrapper_passes_computed_timeout_to_subprocess_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], *, check: bool, timeout: float) -> None:
        calls.append({"command": command, "check": check, "timeout": timeout})

    submission = tmp_path / "model_submission.py"
    submission.write_text("")
    monkeypatch.setattr(official.subprocess, "run", fake_run)

    rc = official.main(
        [
            "--submission",
            str(submission),
            "--output",
            str(tmp_path / "out"),
            "--duration",
            "12.5",
            "--skip-build",
        ]
    )

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["check"] is True
    assert calls[0]["timeout"] == pytest.approx(312.5)


def test_official_wrapper_print_only_does_not_execute_docker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("print-only must not call subprocess.run")

    submission = tmp_path / "model_submission.py"
    submission.write_text("")
    monkeypatch.setattr(official.subprocess, "run", fail_run)

    rc = official.main(
        [
            "--submission",
            str(submission),
            "--output",
            str(tmp_path / "out"),
            "--duration",
            "12.5",
            "--timeout",
            "42",
            "--skip-build",
            "--print-only",
        ]
    )

    assert rc == 0
