#!/usr/bin/env python3
"""Make repeated free scheduler attempts idempotent and publication-window aware."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
IST = ZoneInfo("Asia/Kolkata")


def should_refresh(event_name: str, now: datetime, db_path: Path) -> bool:
    if event_name != "schedule":
        return True
    local_now = now.astimezone(IST)
    # Start early enough for the 5–8 minute source pass to be ready by 08:00.
    if local_now.time() < time(7, 0):
        return False
    if not db_path.exists():
        return True
    with sqlite3.connect(str(db_path)) as connection:
        row = connection.execute(
            "SELECT 1 FROM daily_reports WHERE report_date=? LIMIT 1",
            (local_now.date().isoformat(),),
        ).fetchone()
    return row is None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", default="schedule")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "intelligence.db")
    args = parser.parse_args()
    print("true" if should_refresh(args.event, datetime.now(IST), args.db) else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
