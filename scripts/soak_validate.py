#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from derivebench.derive import DeriveRESTFeed, tick_to_dict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=1)
    args = parser.parse_args(argv)
    feed = DeriveRESTFeed()
    out = [tick_to_dict(feed.poll()) for _ in range(args.ticks)]
    print(json.dumps({"ok": True, "ticks": out}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
