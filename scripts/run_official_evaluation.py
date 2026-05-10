#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path

DEFAULT_IMAGE = "derivebench-eval:latest"


def docker_build_cmd(*, image: str, repo_root: Path) -> list[str]:
    return ["docker", "build", "-t", image, str(repo_root)]


def docker_run_cmd(*, image: str, submission: Path, output_dir: Path, duration: float, memory: str, cpus: str, pids_limit: int) -> list[str]:
    uid = os.getuid()
    gid = os.getgid()
    inner = [
        "scripts/run_candidate.py",
        "--submission", "/submission/model_submission.py",
        "--mode", "live",
        "--duration", str(duration),
        "--output", "/output",
        "--official",
        "--require-container",
    ]
    return [
        "docker", "run", "--rm",
        "--network", "bridge",
        "--user", f"{uid}:{gid}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(pids_limit),
        "--memory", memory,
        "--cpus", cpus,
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m",
        "-e", "DERIVEBENCH_OFFICIAL_EVALUATOR=1",
        "-e", "PYTHONHASHSEED=0",
        "--mount", f"type=bind,source={submission},target=/submission/model_submission.py,readonly",
        "--mount", f"type=bind,source={output_dir},target=/output",
        "--entrypoint", "python",
        image,
        *inner,
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--duration", type=float, default=3600.0)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--memory", default="4g")
    parser.add_argument("--cpus", default="2")
    parser.add_argument("--pids-limit", type=int, default=256)
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    commands = []
    if not args.skip_build:
        commands.append(docker_build_cmd(image=args.image, repo_root=repo_root))
    commands.append(docker_run_cmd(image=args.image, submission=args.submission.resolve(), output_dir=args.output.resolve(), duration=args.duration, memory=args.memory, cpus=args.cpus, pids_limit=args.pids_limit))
    if args.print_only:
        for command in commands:
            print(" ".join(shlex.quote(part) for part in command))
        return 0
    for command in commands:
        subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

