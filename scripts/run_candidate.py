#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from derivebench.runner import RunConfig, check_official_environment, load_submission, run_live, run_replay


def benchmark_path(repo_root: Path) -> Path:
    return repo_root / "model_submissions" / "benchmark_straddle" / "model_submission.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", required=True, type=Path)
    parser.add_argument("--class", dest="class_name", default="ModelSubmission")
    parser.add_argument("--mode", choices=["replay", "live"], default="live")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--starting-capital", type=float, default=1000.0)
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    parser.add_argument("--fee-rate", type=float, default=0.0008)
    parser.add_argument("--latency-budget-ms", type=float, default=500.0)
    parser.add_argument("--official", action="store_true")
    parser.add_argument("--require-container", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    bench_path = benchmark_path(repo_root)
    config = RunConfig(
        output_dir=args.output,
        duration=args.duration,
        starting_capital=args.starting_capital,
        slippage_bps=args.slippage_bps,
        fee_rate=args.fee_rate,
        latency_budget_ms=args.latency_budget_ms,
        mode=args.mode,
        official=args.official,
        require_container=args.require_container,
    )
    check_official_environment(config, args.output)
    candidate = load_submission(args.submission, class_name=args.class_name)
    benchmark = load_submission(bench_path)
    if args.mode == "replay":
        if args.data is None:
            print("ERROR: --data is required in replay mode", file=sys.stderr)
            return 2
        result = run_replay(candidate=candidate, benchmark=benchmark, data=args.data, config=config, candidate_path=args.submission, benchmark_path=bench_path)
    else:
        result = run_live(candidate=candidate, benchmark=benchmark, config=config, candidate_path=args.submission, benchmark_path=bench_path)
    summary = f"primary_score={result.metrics['primary_score']:.6f} pnl_total={result.pnl_total:.6f} ticks={result.tick_count}"
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "run.log").write_text(
        "\n".join(
            [
                "derivebench run complete",
                f"mode={args.mode}",
                f"duration_requested={args.duration}",
                summary,
                f"report={args.output / 'report.json'}",
                f"ticks={args.output / 'ticks.parquet'}",
                "",
            ]
        )
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
