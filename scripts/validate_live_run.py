#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from derivebench.validation import validate_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--ticks", required=True, type=Path)
    parser.add_argument("--min-duration", type=float, default=300.0)
    parser.add_argument("--min-ticks", type=int, default=60)
    parser.add_argument("--max-staleness-seconds", type=float, default=300.0)
    parser.add_argument("--max-age-seconds", type=float, default=300.0)
    parser.add_argument("--max-timeout-rate", type=float, default=0.05)
    parser.add_argument("--min-liquidity-ok-rate", type=float, default=0.95)
    parser.add_argument("--audit-tolerance", type=float, default=1e-6)
    parser.add_argument("--allow-replay", action="store_true")
    args = parser.parse_args(argv)
    summary = validate_run(
        report_path=args.report,
        ticks_path=args.ticks,
        min_duration=args.min_duration,
        min_ticks=args.min_ticks,
        allow_replay=args.allow_replay,
        max_staleness_seconds=args.max_staleness_seconds,
        max_age_seconds=args.max_age_seconds,
        max_timeout_rate=args.max_timeout_rate,
        min_liquidity_ok_rate=args.min_liquidity_ok_rate,
        audit_tolerance=args.audit_tolerance,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not summary["ok"]:
        for failure in summary["failures"]:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
