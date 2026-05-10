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
    parser.add_argument("--allow-replay", action="store_true")
    args = parser.parse_args(argv)
    summary = validate_run(report_path=args.report, ticks_path=args.ticks, min_duration=args.min_duration, min_ticks=args.min_ticks, allow_replay=args.allow_replay)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not summary["ok"]:
        for failure in summary["failures"]:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
