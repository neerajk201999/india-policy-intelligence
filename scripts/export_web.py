#!/usr/bin/env python3
"""Export SQLite intelligence state as a safe, static JSON publication."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
AREAS = [
    "Deregulation & Ease of Doing Business",
    "Digital Economy & Technology",
    "Financial & Banking",
    "Competition",
    "Education",
    "Land, Housing & Governance Reform",
    "Corporate Governance / ESG",
]


def rows(connection: sqlite3.Connection, query: str, values=()):
    return [dict(row) for row in connection.execute(query, values).fetchall()]


def event_payload(connection: sqlite3.Connection, event: dict) -> dict:
    event_id = event["id"]
    sources = rows(connection, "SELECT url,source_name AS name,source_type AS type,authority_level AS authorityLevel FROM event_sources WHERE event_id=? ORDER BY authority_level,url", (event_id,))
    return {
        "id": event_id,
        "title": event["canonical_title"],
        "area": event["area"],
        "whatHappened": event["description"],
        "whyItMatters": event["why_it_matters"],
        "status": event["status"],
        "publicationDate": event["publication_date"],
        "effectiveDate": event["effective_date"],
        "deadline": event["deadline"],
        "affectedEntities": json.loads(event["affected_entities"] or "[]"),
        "primarySourceUrl": event["primary_source_url"],
        "secondarySourceUrls": json.loads(event["secondary_source_urls"] or "[]"),
        "sourceDocumentTitle": event["source_document_title"],
        "sourceIdentifier": event["source_identifier"],
        "isUpdate": bool(event["is_update"]),
        "watchStatus": event["watch_status"],
        "evidence": "Primary verified" if sources and min(source["authorityLevel"] for source in sources) == 1 else ("Official release" if event["primary_source_url"] else "Secondary only"),
        "sources": sources,
    }


def export(db_path: Path, output: Path) -> dict:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    report = connection.execute("SELECT * FROM daily_reports ORDER BY report_date DESC LIMIT 1").fetchone()
    if report:
        event_ids = [int(value) for value in json.loads(report["event_ids"])]
        report_date = report["report_date"]
        generated_at = report["generated_at"]
    else:
        event_ids, report_date = [], datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        generated_at = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
    event_records = []
    for event_id in event_ids:
        row = connection.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if row:
            event_records.append(event_payload(connection, dict(row)))
    watch_records = [event_payload(connection, item) for item in rows(connection, "SELECT * FROM events WHERE watch_status='open' ORDER BY deadline IS NULL,deadline,publication_date DESC LIMIT 4")]
    source_records = rows(connection, "SELECT name,url,source_type AS sourceType,authority_level AS authorityLevel,topic,last_checked AS lastChecked,last_success AS lastSuccess,failure_count AS failureCount,last_error AS lastError FROM sources ORDER BY authority_level,name")
    for source in source_records:
        if source["lastSuccess"]:
            source["health"] = "healthy"
        elif source["failureCount"]:
            source["health"] = "degraded"
        else:
            source["health"] = "pending"
    run = connection.execute("SELECT * FROM runs WHERE discovered>0 ORDER BY id DESC LIMIT 1").fetchone()
    run_payload = dict(run) if run else None
    if run_payload:
        run_payload["errors"] = json.loads(run_payload["errors"] or "[]")
    archives = rows(connection, "SELECT report_date AS date,generated_at AS generatedAt,event_ids AS eventIds,report_path AS reportPath FROM daily_reports ORDER BY report_date DESC LIMIT 90")
    for archive in archives:
        archive["eventCount"] = len(json.loads(archive.pop("eventIds")))
        archive.pop("reportPath", None)
    area_counts = {area: 0 for area in AREAS}
    for event in event_records:
        area_counts[event["area"]] = area_counts.get(event["area"], 0) + 1
    healthy = sum(source["health"] == "healthy" for source in source_records)
    payload = {
        "schemaVersion": 1,
        "meta": {
            "title": "India Policy & Regulatory Intelligence",
            "reportDate": report_date,
            "generatedAt": generated_at,
            "timezone": "Asia/Kolkata",
            "coverage": "Previous 24 hours; limited 3–5 day backfill for material updates",
            "refreshSchedule": "Daily at 08:00 IST",
        },
        "summary": {
            "developments": len(event_records),
            "primaryVerified": sum(event["evidence"] == "Primary verified" for event in event_records),
            "watching": len(watch_records),
            "healthySources": healthy,
            "totalSources": len(source_records),
        },
        "areas": [{"name": area, "count": count} for area, count in area_counts.items()],
        "events": event_records,
        "watchlist": watch_records,
        "sources": source_records,
        "latestRun": run_payload,
        "archive": archives,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    connection.close()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "intelligence.db")
    parser.add_argument("--output", type=Path, default=ROOT / "web" / "data" / "latest.json")
    args = parser.parse_args()
    payload = export(args.db, args.output)
    print(f"Exported {len(payload['events'])} developments and {len(payload['sources'])} sources to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
