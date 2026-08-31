from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, quote_plus, urlsplit

from .database import Database
from .http import HttpClient
from .models import RawItem
from .parsing import page_links, parse_data_gov_resource, parse_feed, parse_rbi_notifications, parse_wordpress_posts


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
        enabled = [source for source in sources if source.get("enabled", True)]
        # Public-sector hosts vary wildly in latency. Independent bounded workers keep
        # one slow portal from serially blocking the other fifty sources.
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(enabled)))) as executor:
            futures = {executor.submit(self._collect_source, source, topics): source for source in enabled}
            for future in as_completed(futures):
                source = futures[future]
                source_items, source_errors, fetched_any = future.result()
                self.errors.extend(source_errors)
                self.db.source_result(
                    source["name"], self.now.isoformat(), fetched_any,
                    None if fetched_any else (source_errors[-1] if source_errors else "Source returned no response"),
                    len(source_items),
                )
                output.extend(source_items)
        return self._deduplicate(output)

    def _collect_source(self, source: dict, topics: Dict):
        source_items: List[RawItem] = []
        source_errors: List[str] = []
        fetched_any = False
        try:
            urls = self._source_urls(source, topics)
        except Exception as exc:
            safe_error = re.sub(r"([?&]api-key=)[^&\s]+", r"\1REDACTED", str(exc), flags=re.I)
            message = f"{source['name']}: {type(exc).__name__}: {safe_error[:220]}"
            LOG.warning(message)
            return source_items, [message], fetched_any
        for url in urls:
            try:
                response = self.client.get(url)
                source_items.extend(self._parse(source, response.body, response.text, response.url, topics))
                fetched_any = True
            except Exception as exc:  # a failed source must never stop the run
                safe_error = re.sub(r"([?&]api-key=)[^&\s]+", r"\1REDACTED", str(exc), flags=re.I)
                message = f"{source['name']}: {type(exc).__name__}: {safe_error[:220]}"
                LOG.warning(message)
                source_errors.append(message)
        return source_items, source_errors, fetched_any

    def _source_urls(self, source: dict, topics: Dict) -> List[str]:
        if source.get("type") == "data_gov":
            api_key = os.environ.get("DATA_GOV_IN_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("DATA_GOV_IN_API_KEY is not configured")
            return [
                f"https://api.data.gov.in/resource/{resource['id']}?api-key={api_key}&format=json&offset=0&limit=10"
                for resource in source.get("resources", [])
            ]
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

    def _parse(self, source: dict, body: bytes, text: str, final_url: str, topics: Dict) -> List[RawItem]:
        kind = source.get("type", "page")
        entries = []
        if kind in ("rss", "atom", "search_feed"):
            entries = parse_feed(body)
        elif kind == "wordpress":
            entries = parse_wordpress_posts(body)
        elif kind == "rbi_notifications":
            entries = parse_rbi_notifications(text, final_url)
        elif kind == "data_gov":
            entries = parse_data_gov_resource(body, source)
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
            identifier = entry.get("identifier") or self._identifier_from_url(str(entry["url"]), source)
            result.append(RawItem(
                source_name=source["name"], source_type=source.get("category", "primary"),
                authority_level=int(source.get("authority_level", 1)), url=str(entry["url"]),
                title=str(entry["title"]), published_at=published, summary=str(entry.get("summary", "")),
                source_identifier=identifier, default_signal_type=source.get("default_signal_type"),
                default_area=source.get("topic") if source.get("topic") in topics["areas"] else None,
            ))
        return result

    @staticmethod
    def _identifier_from_url(url: str, source: dict):
        query = parse_qs(urlsplit(url).query)
        for key, values in query.items():
            if key.casefold() in {"id", "prid", "notificationid", "circularid", "documentid"} and values:
                return f"{source.get('short_name', source['name'])}/{key.upper()}/{values[0]}"
        return None

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
