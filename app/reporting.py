from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Sequence

from .models import Event


def _link(label: str, url: str) -> str:
    safe_label = label.replace("[", "").replace("]", "")
    safe_url = url.replace(" ", "%20")
    return f"[{safe_label}]({safe_url})"


def render_report(now: datetime, events: Sequence[Event], watchlist: Iterable, coverage_days: int = 5) -> str:
    lines = [
        "# India Policy & Regulatory Intelligence Update",
        "",
        f"Date: {now.strftime('%-d %B %Y')}",
        "Coverage: Previous 24 hours; important missed developments from the preceding few days",
        "",
    ]
    for event in events:
        prefix = "Update: " if event.is_update else ""
        lines.extend([
            f"## [{event.area.upper()} · {event.signal_type.upper()}] — {prefix}{event.canonical_title}", "",
            "**What happened:**", "", event.description, "",
            "**Why it matters:**", "", event.why_it_matters, "",
            f"**Type:** {event.signal_type}", "", f"**Status:** {event.status}", "",
        ])
        if event.effective_date:
            lines.extend([f"**Effective date:** {event.effective_date}", ""])
        if event.deadline:
            lines.extend([f"**Deadline:** {event.deadline}", ""])
        if event.primary_source_url:
            authority = min((source.get("authority_level", 3) for source in event.sources), default=3)
            label = "Primary source" if authority == 1 else "Official source"
            lines.extend([f"**Source:** {_link(label, event.primary_source_url)}", ""])
        elif event.secondary_source_urls:
            lines.extend([f"**Source:** {_link('Strongest available secondary source; primary confirmation unavailable', event.secondary_source_urls[0])}", ""])
        if event.primary_source_url and event.secondary_source_urls:
            lines.extend([f"**Context:** {_link('News/context source', event.secondary_source_urls[0])}", ""])
    lines.extend(["## What to Watch", ""])
    count = 0
    for row in watchlist:
        title = row["canonical_title"] if not isinstance(row, Event) else row.canonical_title
        status = row["status"] if not isinstance(row, Event) else row.status
        deadline = row["deadline"] if not isinstance(row, Event) else row.deadline
        if isinstance(row, Event):
            source = row.primary_source_url or (row.secondary_source_urls[0] if row.secondary_source_urls else None)
        else:
            source = row["primary_source_url"]
            if not source:
                secondary = json.loads(row["secondary_source_urls"] or "[]")
                source = secondary[0] if secondary else None
        next_step = {
            "Consultation": "Watch for the consultation deadline and the regulator's final response.",
            "Draft": "Watch for changes in the final text, notification and commencement date.",
            "Bill introduced": "Watch for committee scrutiny, amendments and passage in Parliament.",
            "Cabinet approved": "Watch for the formal text, legislative step or Gazette notification.",
            "Announcement": "Watch for an authoritative document that defines scope and legal status.",
        }.get(status, "Watch for the next formal legal or implementation step.")
        if deadline:
            next_step = f"The recorded deadline is {deadline}. {next_step}"
        heading = _link(title, source) if source else title
        lines.extend([f"### {heading}", "", next_step, ""])
        count += 1
        if count == 4:
            break
    if count == 0:
        lines.extend(["No unresolved, sufficiently evidenced item is currently on the watchlist.", ""])
    if not events:
        lines.insert(5, "No sufficiently verified, meaningful new development was found in this run. Source failures, if any, are recorded in the local database and log.\n")
    return "\n".join(lines).rstrip() + "\n"


def save_report(reports_dir: Path, now: datetime, content: str) -> Path:
    path = reports_dir / f"{now.date().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
