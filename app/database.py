from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

from .models import Event


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    area TEXT NOT NULL,
    signal_type TEXT NOT NULL DEFAULT 'Institutional',
    description TEXT NOT NULL,
    why_it_matters TEXT NOT NULL,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    publication_date TEXT,
    effective_date TEXT,
    deadline TEXT,
    affected_entities TEXT NOT NULL DEFAULT '[]',
    primary_source_url TEXT,
    secondary_source_urls TEXT NOT NULL DEFAULT '[]',
    source_document_title TEXT NOT NULL,
    source_identifier TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    previous_event_id INTEGER REFERENCES events(id),
    is_update INTEGER NOT NULL DEFAULT 0,
    watch_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_identifier ON events(source_identifier);
CREATE INDEX IF NOT EXISTS idx_events_publication ON events(publication_date);
CREATE TABLE IF NOT EXISTS event_sources (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    authority_level INTEGER NOT NULL,
    PRIMARY KEY(event_id, url)
);
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    authority_level INTEGER NOT NULL,
    topic TEXT,
    default_signal_type TEXT,
    last_checked TEXT,
    last_success TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_item_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS daily_reports (
    report_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    event_ids TEXT NOT NULL,
    report_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    discovered INTEGER NOT NULL DEFAULT 0,
    included INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]'
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "signal_type" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN signal_type TEXT NOT NULL DEFAULT 'Institutional'")
            source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sources)").fetchall()}
            if "default_signal_type" not in source_columns:
                conn.execute("ALTER TABLE sources ADD COLUMN default_signal_type TEXT")
            if "last_item_count" not in source_columns:
                conn.execute("ALTER TABLE sources ADD COLUMN last_item_count INTEGER NOT NULL DEFAULT 0")

    def sync_sources(self, sources: Sequence[dict]) -> None:
        with self.connect() as conn:
            for source in sources:
                conn.execute(
                    """INSERT INTO sources(name,url,source_type,authority_level,topic,default_signal_type)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                    url=excluded.url, source_type=excluded.source_type,
                    authority_level=excluded.authority_level, topic=excluded.topic,
                    default_signal_type=excluded.default_signal_type""",
                    (source["name"], source["url"], source.get("type", "page"), source.get("authority_level", 1), source.get("topic"), source.get("default_signal_type")),
                )
            names = [source["name"] for source in sources]
            if names:
                placeholders = ",".join("?" for _ in names)
                conn.execute(f"DELETE FROM sources WHERE name NOT IN ({placeholders})", names)

    def source_result(self, name: str, checked_at: str, success: bool, error: Optional[str] = None, item_count: int = 0) -> None:
        with self.connect() as conn:
            if success:
                conn.execute("UPDATE sources SET last_checked=?, last_success=?, failure_count=0, last_item_count=?, last_error=NULL WHERE name=?", (checked_at, checked_at, item_count, name))
            else:
                conn.execute("UPDATE sources SET last_checked=?, failure_count=failure_count+1, last_item_count=0, last_error=? WHERE name=?", (checked_at, (error or "")[:500], name))

    def find_hash(self, content_hash: str):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM events WHERE content_hash=?", (content_hash,)).fetchone()

    def find_identifier(self, identifier: str):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM events WHERE source_identifier=? ORDER BY id DESC LIMIT 1", (identifier,)).fetchone()

    def find_equivalent(self, event: Event):
        """Find the same published instrument when alternate feeds use different IDs."""
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM events WHERE canonical_title=? AND status=? AND publication_date=?
                AND effective_date IS ? AND deadline IS ? ORDER BY id DESC LIMIT 1""",
                (event.canonical_title, event.status, event.publication_date, event.effective_date, event.deadline),
            ).fetchone()

    def recent_events(self, limit: int = 500):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def recent_verified_context(self, since_date: str):
        """Recent verified records used only as clearly labelled briefing context."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM events WHERE publication_date >= ? ORDER BY publication_date DESC,last_seen DESC,id DESC",
                (since_date,),
            ).fetchall()

    def event_from_row(self, row) -> Event:
        """Hydrate an event and its provenance from a stored SQLite row."""
        with self.connect() as conn:
            sources = [dict(item) for item in conn.execute(
                "SELECT url,source_name AS name,source_type AS type,authority_level FROM event_sources WHERE event_id=?",
                (row["id"],),
            ).fetchall()]
        return Event(
            id=row["id"], canonical_title=row["canonical_title"], area=row["area"], signal_type=row["signal_type"],
            description=row["description"], why_it_matters=row["why_it_matters"], status=row["status"],
            first_seen=row["first_seen"], last_seen=row["last_seen"], publication_date=row["publication_date"],
            effective_date=row["effective_date"], deadline=row["deadline"],
            affected_entities=json.loads(row["affected_entities"]), primary_source_url=row["primary_source_url"],
            secondary_source_urls=json.loads(row["secondary_source_urls"]), source_document_title=row["source_document_title"],
            source_identifier=row["source_identifier"], content_hash=row["content_hash"],
            previous_event_id=row["previous_event_id"], is_update=bool(row["is_update"]),
            watch_status=row["watch_status"], sources=sources,
        )

    def insert_event(self, event: Event) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO events(canonical_title,area,signal_type,description,why_it_matters,status,
                first_seen,last_seen,publication_date,effective_date,deadline,affected_entities,
                primary_source_url,secondary_source_urls,source_document_title,source_identifier,
                content_hash,previous_event_id,is_update,watch_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event.canonical_title, event.area, event.signal_type, event.description, event.why_it_matters, event.status,
                 event.first_seen, event.last_seen, event.publication_date, event.effective_date, event.deadline,
                 json.dumps(event.affected_entities), event.primary_source_url, json.dumps(event.secondary_source_urls),
                 event.source_document_title, event.source_identifier, event.content_hash, event.previous_event_id,
                 int(event.is_update), event.watch_status),
            )
            event_id = int(cursor.lastrowid)
            for source in event.sources:
                conn.execute("INSERT OR IGNORE INTO event_sources VALUES(?,?,?,?,?)", (event_id, source["url"], source["name"], source["type"], source["authority_level"]))
            if event.previous_event_id:
                # A later version replaces the prior open step in the action queue;
                # history remains linked in Tracker, but users should not be asked to
                # monitor both an old draft and its successor.
                conn.execute("UPDATE events SET watch_status=NULL WHERE id=?", (event.previous_event_id,))
            return event_id

    def touch_event(self, event_id: int, seen_at: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE events SET last_seen=? WHERE id=?", (seen_at, event_id))

    def add_event_sources(self, event_id: int, sources: Sequence[dict]) -> None:
        with self.connect() as conn:
            for source in sources:
                conn.execute(
                    "INSERT OR IGNORE INTO event_sources VALUES(?,?,?,?,?)",
                    (event_id, source["url"], source["name"], source["type"], source["authority_level"]),
                )

    def open_watchlist(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM events WHERE watch_status='open' ORDER BY publication_date DESC").fetchall()

    def reconcile_watchlist(self, today: str) -> None:
        """Remove stale or non-actionable legacy entries from the active queue."""
        with self.connect() as conn:
            conn.execute(
                """UPDATE events SET watch_status=NULL
                WHERE watch_status='open' AND deadline IS NOT NULL AND deadline < ?""",
                (today,),
            )
            conn.execute(
                """UPDATE events SET watch_status=NULL
                WHERE watch_status='open' AND deadline IS NULL
                AND status NOT IN ('Draft','Consultation','Bill introduced','Cabinet approved')"""
            )

    def source_alerts(self, threshold: int = 3):
        """Persistent failures become CI warnings instead of quiet degraded state."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT name,failure_count,last_error FROM sources WHERE failure_count>=? ORDER BY failure_count DESC,name",
                (threshold,),
            ).fetchall()

    def save_report(self, date: str, generated_at: str, event_ids: List[int], path: str) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO daily_reports VALUES(?,?,?,?)
                ON CONFLICT(report_date) DO UPDATE SET generated_at=excluded.generated_at,
                event_ids=excluded.event_ids,report_path=excluded.report_path""", (date, generated_at, json.dumps(event_ids), path))

    def report_events(self, date: str) -> List[Event]:
        with self.connect() as conn:
            report = conn.execute("SELECT event_ids FROM daily_reports WHERE report_date=?", (date,)).fetchone()
            if not report:
                return []
            ids = [int(value) for value in json.loads(report["event_ids"])]
            events: List[Event] = []
            for event_id in ids:
                row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
                if not row:
                    continue
                events.append(self.event_from_row(row))
            return events

    def start_run(self, started_at: str) -> int:
        with self.connect() as conn:
            return int(conn.execute("INSERT INTO runs(started_at,status) VALUES(?,'running')", (started_at,)).lastrowid)

    def close_interrupted_runs(self, finished_at: str) -> None:
        """Ensure a terminated process cannot leave source health in an ambiguous state."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, status='interrupted', errors=? WHERE status='running'",
                (finished_at, json.dumps(["Run did not complete before the next invocation."])),
            )

    def finish_run(self, run_id: int, finished_at: str, status: str, discovered: int, included: int, errors: List[str]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE runs SET finished_at=?,status=?,discovered=?,included=?,errors=? WHERE id=?", (finished_at, status, discovered, included, json.dumps(errors), run_id))
