from __future__ import annotations

from pathlib import Path

import pytest

import derivebench.runner as runner
import scripts.run_candidate as candidate_cli
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


def test_run_candidate_wires_require_container_to_run_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[tuple[bool, bool, Path]] = []

    class FakeResult:
        metrics = {"primary_score": 1.0}
        pnl_total = 0.0
        tick_count = 1

    def fake_check(config: runner.RunConfig, output_dir: Path) -> None:
        seen.append((config.require_container, config.official, output_dir))

    monkeypatch.setattr(candidate_cli, "check_official_environment", fake_check)
    monkeypatch.setattr(candidate_cli, "load_submission", lambda *args, **kwargs: object())
    monkeypatch.setattr(candidate_cli, "run_replay", lambda **kwargs: FakeResult())

    submission = tmp_path / "model_submission.py"
    data = tmp_path / "replay.json"
    output = tmp_path / "out"
    submission.write_text("")
    data.write_text("[]")

    rc = candidate_cli.main(
        [
            "--submission",
            str(submission),
            "--mode",
            "replay",
            "--data",
            str(data),
            "--output",
            str(output),
            "--official",
            "--require-container",
        ]
    )

    assert rc == 0
    assert seen == [(True, True, output)]


def test_require_container_rejects_without_container_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    monkeypatch.setattr(runner, "is_running_in_container", lambda: False)

    config = runner.RunConfig(output_dir=output, require_container=True)

    with pytest.raises(RuntimeError, match="--require-container"):
        runner.check_official_environment(config, output)


def test_require_container_allows_container_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    monkeypatch.setattr(runner, "is_running_in_container", lambda: True)

    config = runner.RunConfig(output_dir=output, require_container=True)

    runner.check_official_environment(config, output)
