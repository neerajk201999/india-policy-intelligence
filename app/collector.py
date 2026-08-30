from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from urllib.parse import quote_plus

from .database import Database
from .http import HttpClient
from .models import RawItem
from .parsing import page_links, parse_feed, parse_rbi_notifications, parse_wordpress_posts


LOG = logging.getLogger(__name__)


class Collector:
    def __init__(self, db: Database, client: HttpClient, now: datetime, since: datetime, offline: bool = False):
        self.db = db
        self.client = client
        self.now = now
        self.since = since
        self.offline = offline
        self.errors: List[str] = []

    def collect(self, sources: List[dict], topics: Dict) -> List[RawItem]:
        if self.offline:
            return []
        output: List[RawItem] = []
        for source in sources:
            if not source.get("enabled", True):
                continue
            urls = self._source_urls(source, topics)
            source_items: List[RawItem] = []
            source_errors: List[str] = []
            fetched_any = False
            for url in urls:
                try:
                    response = self.client.get(url)
                    fetched_any = True
                    source_items.extend(self._parse(source, response.body, response.text, response.url))
                except Exception as exc:  # a failed source must never stop the run
                    message = f"{source['name']}: {type(exc).__name__}: {str(exc)[:220]}"
                    LOG.warning(message)
                    self.errors.append(message)
                    source_errors.append(message)
            success = fetched_any
            self.db.source_result(source["name"], self.now.isoformat(), success, None if success else (source_errors[-1] if source_errors else "Source returned no response"))
            output.extend(source_items)
        return self._deduplicate(output)

    def _source_urls(self, source: dict, topics: Dict) -> List[str]:
        if source.get("type") == "wordpress":
            since = self.since.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            return [source["url"].replace("{since}", since)]
        if source.get("type") != "search_feed":
            return [source["url"]]
        urls = []
        max_queries = int(source.get("max_queries", 7))
        for _, topic in list(topics["areas"].items())[:max_queries]:
            terms = topic.get("search_terms", topic["keywords"][:4])
            query = " OR ".join(f'"{term}"' if " " in term else term for term in terms[:5])
            urls.append(source["url"].replace("{query}", quote_plus(query + " when:5d")))
        return urls

    def _parse(self, source: dict, body: bytes, text: str, final_url: str) -> List[RawItem]:
        kind = source.get("type", "page")
        entries = []
        if kind in ("rss", "atom", "search_feed"):
            entries = parse_feed(body)
        elif kind == "wordpress":
            entries = parse_wordpress_posts(body)
        elif kind == "rbi_notifications":
            entries = parse_rbi_notifications(text, final_url)
        elif kind == "page":
            entries = [
                {"title": title, "url": url, "summary": "", "published_at": None, "identifier": None}
                for title, url in page_links(text, final_url)
                if len(title) >= 22
            ][: int(source.get("max_links", 50))]
        result = []
        for entry in entries:
            published = entry["published_at"]
            if published is None and kind == "page":
                published = self._date_in_title(str(entry["title"]))
            # Undated index-page links are collected for topic filtering, then their detail page
            # may provide a date. They are never included in a report until dated.
            if published is not None and published < self.since:
                continue
            result.append(RawItem(
                source_name=source["name"], source_type=source.get("category", "primary"),
                authority_level=int(source.get("authority_level", 1)), url=str(entry["url"]),
                title=str(entry["title"]), published_at=published, summary=str(entry.get("summary", "")),
                source_identifier=entry.get("identifier"),
            ))
        return result

    @staticmethod
    def _date_in_title(title: str):
        import re
        from .parsing import parse_date
        match = re.search(r"\b(\d{1,2}\s+[A-Za-z]+\s+20\d{2}|[A-Za-z]+\s+\d{1,2},?\s+20\d{2}|\d{1,2}[-/]\d{1,2}[-/]20\d{2})\b", title)
        return parse_date(match.group(1)) if match else None

    @staticmethod
    def _deduplicate(items: List[RawItem]) -> List[RawItem]:
        seen = set()
        result = []
        for item in items:
            key = (item.url.split("#", 1)[0].rstrip("/"), item.title.casefold())
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
