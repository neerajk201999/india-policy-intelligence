#!/usr/bin/env python3
"""Export SQLite intelligence state as a safe, static JSON publication."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
AREAS = [
    "Deregulation & Ease of Doing Business",
    "Digital Economy & AI",
    "Financial & Banking",
    "Macroeconomy, Trade & Public Finance",
    "Competition",
    "Education",
    "Land, Housing & Governance Reform",
    "Corporate Governance / ESG",
]


def rows(connection: sqlite3.Connection, query: str, values=()):
    return [dict(row) for row in connection.execute(query, values).fetchall()]


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:100] or "policy-update"


def event_payload(connection: sqlite3.Connection, event: dict) -> dict:
    event_id = event["id"]
    sources = rows(connection, "SELECT url,source_name AS name,source_type AS type,authority_level AS authorityLevel FROM event_sources WHERE event_id=? ORDER BY authority_level,url", (event_id,))
    authority = min((source["authorityLevel"] for source in sources), default=3)
    institution = sources[0]["name"] if sources else None
    evidence_level = "Primary" if authority == 1 else ("Official" if authority == 2 else "Reported")
    next_step = None
    effective_date_text = None
    relative_effective = re.search(r"come into force\s+(?:one hundred )?eighty days from the date of (?:their|its) publication in the Official Gazette", event["description"], re.I)
    if relative_effective:
        effective_date_text = "180 days after Official Gazette publication"
    if event["watch_status"] == "open":
        next_step = {
            "Draft": "Final notification",
            "Consultation": "Consultation closes or final instrument",
            "Bill introduced": "Next legislative stage",
            "Cabinet approved": "Formal text or Gazette notification",
            "Announcement": "Authoritative document",
        }.get(event["status"], "Next formal policy step")
    return {
        "id": event_id,
        "slug": slugify(event["canonical_title"]),
        "title": event["canonical_title"],
        "area": event["area"],
        "signalType": event.get("signal_type") or "Institutional",
        "subtopic": None,
        "eventType": event["status"],
        "whatHappened": event["description"],
        "whyItMatters": event["why_it_matters"],
        "status": event["status"],
        "publicationDate": event["publication_date"],
        "effectiveDate": event["effective_date"],
        "effectiveDateText": effective_date_text,
        "deadline": event["deadline"],
        "affectedEntities": json.loads(event["affected_entities"] or "[]"),
        "primarySourceUrl": event["primary_source_url"],
        "secondarySourceUrls": json.loads(event["secondary_source_urls"] or "[]"),
        "sourceDocumentTitle": event["source_document_title"],
        "primarySourceTitle": event["source_document_title"] if event["primary_source_url"] else None,
        "institution": institution,
        "sourceIdentifier": event["source_identifier"],
        "isUpdate": bool(event["is_update"]),
        "watchStatus": event["watch_status"],
        "isOpen": event["watch_status"] == "open",
        "isVerified": authority == 1,
        "evidenceLevel": evidence_level,
        "nextStep": next_step,
        "firstSeen": event["first_seen"],
        "lastUpdated": event["last_seen"],
        "previousEventId": event["previous_event_id"],
        "evidence": f"{evidence_level} evidence",
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
            record = event_payload(connection, dict(row))
            record["briefingKind"] = "new" if str(record["firstSeen"]).startswith(report_date) else "recent-context"
            event_records.append(record)
    tracker_records = [event_payload(connection, item) for item in rows(connection, "SELECT * FROM events ORDER BY publication_date DESC,last_seen DESC,id DESC")]
    # The Watchlist is a complete action queue, intentionally distinct from the
    # complete Tracker and the curated daily Briefing.
    watch_records = [event_payload(connection, item) for item in rows(connection, "SELECT * FROM events WHERE watch_status='open' ORDER BY deadline IS NULL,deadline,publication_date DESC")]
    source_records = rows(connection, "SELECT name,url,source_type AS sourceType,authority_level AS authorityLevel,topic,default_signal_type AS defaultSignalType,last_checked AS lastChecked,last_success AS lastSuccess,failure_count AS failureCount,last_item_count AS lastItemCount,last_error AS lastError FROM sources ORDER BY authority_level,name")
    for source in source_records:
        if source["failureCount"]:
            source["health"] = "degraded"
        elif source["lastSuccess"] and source["lastItemCount"] > 0:
            source["health"] = "healthy"
        elif source["lastSuccess"]:
            source["health"] = "reachable"
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
    health_alerts = [
        {"name": source["name"], "failureCount": source["failureCount"], "lastError": source["lastError"]}
        for source in source_records if source["failureCount"] >= 3
    ]
    payload = {
        "schemaVersion": 1,
        "meta": {
            "title": "India Policy & Regulatory Intelligence",
            "reportDate": report_date,
            "generatedAt": generated_at,
            "timezone": "Asia/Kolkata",
            "coverage": "Newly verified developments, with clearly labelled five-day context when the daily diff is sparse",
            "refreshSchedule": "Daily at 08:00 IST",
        },
        "summary": {
            "developments": len(event_records),
            "newDevelopments": sum(event["briefingKind"] == "new" for event in event_records),
            "recentContext": sum(event["briefingKind"] == "recent-context" for event in event_records),
            "primaryVerified": sum(event["evidenceLevel"] == "Primary" for event in event_records),
            "watching": len(watch_records),
            "healthySources": healthy,
            "totalSources": len(source_records),
        },
        "areas": [{"name": area, "count": count} for area, count in area_counts.items()],
        "events": event_records,
        "tracker": tracker_records,
        "watchlist": watch_records,
        "sources": source_records,
        "healthAlerts": health_alerts,
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
