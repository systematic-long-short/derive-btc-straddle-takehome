#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.run_candidate import main as run_candidate_main


def main(argv: list[str] | None = None) -> int:
    default_submission = REPO_ROOT / "model_submissions" / "benchmark_straddle" / "model_submission.py"
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["replay", "live"], default="live")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--starting-capital", type=float, default=1000.0)
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    parser.add_argument("--fee-rate", type=float, default=0.0008)
    args = parser.parse_args(argv)
    forwarded = [
        "--submission", str(default_submission),
        "--mode", args.mode,
        "--output", str(args.output),
        "--duration", str(args.duration),
        "--starting-capital", str(args.starting_capital),
        "--slippage-bps", str(args.slippage_bps),
        "--fee-rate", str(args.fee_rate),
    ]
    if args.data is not None:
        forwarded.extend(["--data", str(args.data)])
    return run_candidate_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
