#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from derivebench.derive import DeriveRESTFeed, tick_to_dict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args(argv)
    feed = DeriveRESTFeed()
    deadline = time.time() + args.duration
    ticks = []
    while time.time() < deadline:
        ticks.append(tick_to_dict(feed.poll()))
        time.sleep(args.interval)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"metadata": {"source": "derive_public_rest"}, "ticks": ticks}, indent=2))
    print(f"wrote {len(ticks)} ticks to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
