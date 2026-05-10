#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from derivebench.submission_scan import as_json, scan_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?")
    parser.add_argument("--file", dest="file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.file or args.path or "")
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2
    report = scan_file(path)
    print(as_json(report) if args.json else report.format_text())
    return 0 if report.verdict == "accept" else 1


if __name__ == "__main__":
    raise SystemExit(main())
