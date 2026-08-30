from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT
from .pipeline import Pipeline


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Generate the daily India policy and regulatory intelligence brief.")
    result.add_argument("--offline", action="store_true", help="Use local history only; do not make network requests.")
    result.add_argument("--lookback-days", type=int, default=5, choices=range(1, 6), metavar="1-5")
    result.add_argument("--max-items", type=int, default=12)
    result.add_argument("--date", help="Override run time for deterministic testing (ISO-8601; interpreted in Asia/Kolkata).")
    result.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    result.add_argument("--verbose", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    now = None
    if args.date:
        now = datetime.fromisoformat(args.date)
        tz = ZoneInfo("Asia/Kolkata")
        now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    pipeline = Pipeline(root=args.root, now=now, lookback_days=args.lookback_days, offline=args.offline, max_items=max(1, args.max_items))
    result = pipeline.run()
    print(f"Report: {result.report_path}")
    print(f"Discovered: {result.discovered}; included: {result.included}; source errors: {len(result.errors)}")
    for source in pipeline.db.source_alerts():
        print(f"::warning title=Source health alert::{source['name']} has failed {source['failure_count']} consecutive runs: {source['last_error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
