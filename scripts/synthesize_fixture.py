#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from derivebench.derive import load_replay_ticks, tick_to_dict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="tests/fixtures/derive_replay.json", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    ticks = [tick_to_dict(tick) for tick in load_replay_ticks(args.input)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"metadata": {"source": "synthetic_from_fixture"}, "ticks": ticks}, indent=2))
    print(f"wrote {len(ticks)} ticks to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
