from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

from .classifier import classify, event_from_item, has_sufficient_evidence, is_meaningful, normalize_url, similar_title
from .collector import Collector
from .config import ROOT, sources_config, topics_config
from .database import Database
from .http import HttpClient
from .models import Event
from .quality import is_publishable
from .parsing import article_text, page_links, parse_date
from .reporting import render_report, save_report


LOG = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class RunResult:
    report_path: Path
    discovered: int
    included: int
    errors: List[str]
    events: List[Event]


class Pipeline:
    def __init__(self, root: Path = ROOT, now: Optional[datetime] = None, lookback_days: int = 5, offline: bool = False, max_items: int = 30, client: Optional[HttpClient] = None, source_names: Optional[Sequence[str]] = None):
        self.root = root
        self.now = (now or datetime.now(IST)).astimezone(IST)
        self.lookback_days = min(max(lookback_days, 1), 5)
        self.primary_since = self.now - timedelta(hours=24)
        # Sources usually publish a date without a time. Use an IST calendar boundary so
        # a five-day backfill never drops material posted early on the fifth day.
        self.backfill_since = datetime.combine(self.now.date() - timedelta(days=self.lookback_days), time.min, tzinfo=IST)
        self.offline = offline
        self.max_items = max_items
        self.client = client or HttpClient()
        self.source_names = set(source_names or [])
        self.db = Database(root / "data" / "intelligence.db")
        self.sources = sources_config(root / "config" / "sources.yaml")
        self.topics = topics_config(root / "config" / "topics.yaml")

    def run(self) -> RunResult:
        self.db.initialize()
        self.db.close_interrupted_runs(self.now.isoformat())
        source_registry = self.sources["sources"]
        self.db.sync_sources(source_registry)
        source_list = [source for source in source_registry if not self.source_names or source["name"] in self.source_names]
        run_id = self.db.start_run(self.now.isoformat())
        collector = Collector(self.db, self.client, self.now, self.backfill_since, self.offline)
        included: List[Event] = []
        try:
            raw_items = collector.collect(source_list, self.topics)
            candidates = self._prepare_candidates(raw_items)
            for event in candidates:
                saved = self._store_if_new(event)
                if saved:
                    included.append(saved)
            # The tracker is the complete verified record.  The edition limit is
            # applied only after every publishable candidate has been persisted.
            # Stopping here would silently discard lower-ranked developments from
            # history and make Tracker a second, incomplete version of Briefing.
            prior_today = self.db.report_events(self.now.date().isoformat())
            known_ids = {event.id for event in prior_today}
            daily_events = prior_today + [event for event in included if event.id not in known_ids]
            daily_events = self._deduplicate_candidates(daily_events)
            daily_events.sort(key=self._candidate_priority, reverse=True)
            report_events = self._select_daily_edition(daily_events)
            self.db.reconcile_watchlist(self.now.date().isoformat())
            watchlist = self.db.open_watchlist()
            report = render_report(self.now, report_events, watchlist, self.lookback_days)
            report_path = save_report(self.root / "reports" / "daily", self.now, report)
            self.db.save_report(self.now.date().isoformat(), self.now.isoformat(), [e.id for e in report_events if e.id], str(report_path))
            status = "partial" if collector.errors else "success"
            self.db.finish_run(run_id, datetime.now(IST).isoformat(), status, len(raw_items), len(included), collector.errors)
            return RunResult(report_path, len(raw_items), len(included), collector.errors, included)
        except Exception as exc:
            errors = collector.errors + [f"pipeline: {type(exc).__name__}: {exc}"]
            self.db.finish_run(run_id, datetime.now(IST).isoformat(), "failed", 0, 0, errors)
            raise

    def _select_daily_edition(self, daily_events: List[Event]) -> List[Event]:
        """Supplement a sparse true diff with accurately labelled recent context."""
        selected = list(daily_events[: self.max_items])
        minimum_edition = min(self.max_items, 8)
        if len(selected) >= minimum_edition:
            return selected
        selected_ids = {event.id for event in selected if event.id}
        for row in self.db.recent_verified_context(self.backfill_since.date().isoformat()):
            if row["id"] in selected_ids:
                continue
            candidate = self.db.event_from_row(row)
            if any(self._same_macro_data_release(candidate, existing) for existing in selected):
                continue
            selected.append(candidate)
            selected_ids.add(row["id"])
            if len(selected) >= minimum_edition:
                break
        return selected[: self.max_items]

    def _prepare_candidates(self, items) -> List[Event]:
        result = []
        for item in items:
            # The headline must independently identify a policy, regulatory or data
            # development. Official publisher pages contain broad political and social
            # coverage; allowing incidental terms in a long article body to establish
            # relevance creates convincing but incorrect entries.
            area = classify(item.title, self.topics) or item.default_area
            if not is_meaningful(item.title, area):
                continue
            combined = f"{item.title} {item.summary}"
            detail = item.summary
            if not self.offline and (len(detail.split()) < 70 or item.published_at is None):
                try:
                    response = self.client.get(item.url)
                    detail = self._pdf_text(response.body) if response.body.startswith(b"%PDF-") else article_text(response.text)
                    if item.authority_level <= 2 and "html" in response.content_type.casefold():
                        pdf_url = self._first_pdf_url(response.text, response.url, item.title)
                        if pdf_url:
                            pdf_response = self.client.get(pdf_url)
                            pdf_text = self._pdf_text(pdf_response.body)
                            if pdf_text:
                                detail = pdf_text
                                item.url = pdf_url
                except Exception as exc:
                    LOG.info("Detail fetch failed for %s: %s", item.url, exc)
            full_text = f"{combined} {detail}"
            area = classify(full_text, self.topics) or area
            if not is_meaningful(full_text, area):
                continue
            # Never present an undated index link as current. Try to find a date in its detail.
            if item.published_at is None:
                item.published_at = self._date_from_detail(detail)
            if item.published_at is None or item.published_at < self.backfill_since or item.published_at > self.now + timedelta(hours=6):
                continue
            if not has_sufficient_evidence(item, detail):
                continue
            event = event_from_item(item, detail, area, self.topics, self.now)
            if is_publishable(event):
                result.append(event)
        result.sort(key=self._candidate_priority, reverse=True)
        return self._deduplicate_candidates(result)

    def _candidate_priority(self, event: Event):
        """Deterministic editorial ranking; this is selection priority, not impact."""
        authority = {1: 30, 2: 20, 3: 10}.get(event.sources[0]["authority_level"], 0)
        signal = {"Regulation": 18, "Legislative": 17, "Consultation": 15, "Data": 13, "Programme": 10, "Institutional": 8}.get(event.signal_type, 0)
        published = datetime.fromisoformat(event.publication_date).date() if event.publication_date else self.now.date()
        recency = max(0, 25 - (self.now.date() - published).days * 5)
        evidence = (5 if event.source_identifier else 0) + (4 if len(event.description.split()) >= 100 else 0)
        actionability = 6 if event.deadline or event.effective_date else 0
        return authority + signal + recency + evidence + actionability, event.publication_date or "", event.canonical_title

    @staticmethod
    def _deduplicate_candidates(events: List[Event]) -> List[Event]:
        """Collapse coverage into underlying events before touching history."""
        selected: List[Event] = []
        for event in events:
            duplicate_index = None
            for index, existing in enumerate(selected):
                # Similar titles are not identity: one regulator commonly publishes
                # several near-identical directions on the same day.  Merge only a
                # shared publisher document ID or exactly the same canonical URL.
                same_identifier = bool(event.source_identifier and existing.source_identifier and event.source_identifier == existing.source_identifier)
                same_story = normalize_url(event.primary_source_url or "") == normalize_url(existing.primary_source_url or "")
                same_macro_release = Pipeline._same_macro_data_release(event, existing)
                if same_identifier or same_story or same_macro_release:
                    duplicate_index = index
                    break
            if duplicate_index is None:
                selected.append(event)
                continue
            existing = selected[duplicate_index]
            event_quality = (3 - event.sources[0]["authority_level"], len(event.description))
            existing_quality = (3 - existing.sources[0]["authority_level"], len(existing.description))
            preferred, other = (event, existing) if event_quality > existing_quality else (existing, event)
            for url in ([other.primary_source_url] if other.primary_source_url else other.secondary_source_urls):
                if url and url != preferred.primary_source_url and url not in preferred.secondary_source_urls:
                    preferred.secondary_source_urls.append(url)
            preferred.sources.extend(s for s in other.sources if s not in preferred.sources)
            selected[duplicate_index] = preferred
        return selected

    @staticmethod
    def _same_macro_data_release(left: Event, right: Event) -> bool:
        """Collapse two headlines reporting the same dated macro indicator/value."""
        import re
        if left.signal_type != "Data" or right.signal_type != "Data" or left.publication_date != right.publication_date:
            return False
        indicators = {
            "gdp": ("gross domestic product", "gdp", "economic growth"),
            "iip": ("industrial output", "index of industrial production", "iip"),
            "cpi": ("consumer price index", "cpi", "retail inflation"),
            "wpi": ("wholesale price index", "wpi", "wholesale inflation"),
        }
        def signature(event: Event):
            text = f"{event.canonical_title} {event.description}".casefold()
            indicator = next((name for name, terms in indicators.items() if any(term in text for term in terms)), None)
            title_values = set(re.findall(r"\b\d+(?:\.\d+)?\s*%", event.canonical_title))
            return indicator, title_values
        left_indicator, left_values = signature(left)
        right_indicator, right_values = signature(right)
        return bool(left_indicator and left_indicator == right_indicator and left_values & right_values)

    @staticmethod
    def _date_from_detail(detail: str):
        import re
        match = re.search(r"\b(?:published|dated|date|issued)\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4})", detail, re.I)
        return parse_date(match.group(1)) if match else None

    @staticmethod
    def _first_pdf_url(html: str, base_url: str, document_title: str) -> Optional[str]:
        import re
        ignored = {"the", "and", "for", "with", "from", "under", "india", "govt", "government", "rules", "order"}
        title_tokens = {token for token in re.findall(r"[a-z0-9]+", document_title.casefold()) if len(token) > 3 and token not in ignored}
        best = None
        best_overlap = 0
        for label, url in page_links(html, base_url):
            if ".pdf" not in url.casefold():
                continue
            candidate_tokens = set(re.findall(r"[a-z0-9]+", f"{label} {url}".casefold()))
            overlap = len(title_tokens & candidate_tokens)
            if overlap > best_overlap:
                best, best_overlap = url, overlap
        if best_overlap >= 2:
            return best
        return None

    @staticmethod
    def _pdf_text(data: bytes) -> str:
        """Use pypdf when the host provides it; otherwise degrade to page metadata."""
        if not data.startswith(b"%PDF-"):
            return ""
        try:
            from io import BytesIO
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(BytesIO(data))
            text = " ".join((page.extract_text() or "") for page in reader.pages[:20])
            return " ".join(text.split())[:25_000]
        except Exception as exc:
            LOG.info("Optional PDF text extraction unavailable: %s", exc)
            return ""

    def _store_if_new(self, event: Event) -> Optional[Event]:
        exact = self.db.find_hash(event.content_hash)
        if exact:
            self.db.touch_event(exact["id"], self.now.isoformat())
            self.db.add_event_sources(exact["id"], event.sources)
            return None
        equivalent = self.db.find_equivalent(event)
        if equivalent:
            self.db.touch_event(equivalent["id"], self.now.isoformat())
            self.db.add_event_sources(equivalent["id"], event.sources)
            return None
        previous = self.db.find_identifier(event.source_identifier) if event.source_identifier else None
        if previous is None and not event.source_identifier:
            for row in self.db.recent_events(300):
                if row["area"] == event.area and similar_title(row["canonical_title"], event.canonical_title):
                    previous = row
                    break
        if previous:
            # Two publication routes can describe the same instrument with different
            # source identifiers. Do not turn corroboration into a fictional update.
            unchanged_state = (
                previous["status"] == event.status
                and previous["publication_date"] == event.publication_date
                and previous["effective_date"] == event.effective_date
                and previous["deadline"] == event.deadline
            )
            if unchanged_state:
                self.db.touch_event(previous["id"], self.now.isoformat())
                self.db.add_event_sources(previous["id"], event.sources)
                return None
            # Same event with materially changed fingerprint/status/date is an update.
            event.previous_event_id = previous["id"]
            event.is_update = True
        event.id = self.db.insert_event(event)
        return event
