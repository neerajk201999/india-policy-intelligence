from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin
from xml.etree import ElementTree


SPACE = re.compile(r"\s+")
TAG = re.compile(r"<[^>]+>")


def clean_text(value: Optional[str]) -> str:
    value = html.unescape(TAG.sub(" ", value or ""))
    return SPACE.sub(" ", value).strip()


def parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = clean_text(value)
    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = value.replace("Z", "+00:00")
    for candidate in (normalized, normalized[:19], normalized[:10]):
        try:
            result = datetime.fromisoformat(candidate)
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            return result
        except ValueError:
            continue
    for fmt in ("%d %B %Y", "%d %b %Y", "%d %b, %Y %z", "%d %b, %Y", "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(node: ElementTree.Element, names: Iterable[str]) -> str:
    wanted = set(names)
    for child in node.iter():
        if _local_name(child.tag) in wanted and child.text:
            return child.text
    return ""


def parse_feed(data: bytes) -> List[Dict[str, object]]:
    root = ElementTree.fromstring(data)
    items: List[Dict[str, object]] = []
    for node in root.iter():
        if _local_name(node.tag) not in ("item", "entry"):
            continue
        title = clean_text(_first_text(node, ("title",)))
        link = _first_text(node, ("link",))
        if not link:
            for child in node.iter():
                if _local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        summary = clean_text(_first_text(node, ("description", "summary", "content")))
        date = parse_date(_first_text(node, ("pubdate", "published", "updated", "date")))
        guid = clean_text(_first_text(node, ("guid", "id"))) or None
        if title and link:
            items.append({"title": title, "url": link.strip(), "summary": summary, "published_at": date, "identifier": guid})
    return items


def parse_wordpress_posts(data: bytes) -> List[Dict[str, object]]:
    """Parse the public WordPress REST response used by several official publishers."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    items: List[Dict[str, object]] = []
    for post in payload:
        if not isinstance(post, dict):
            continue
        title_data = post.get("title") or {}
        content_data = post.get("content") or {}
        title = clean_text(title_data.get("rendered") if isinstance(title_data, dict) else str(title_data))
        detail = clean_text(content_data.get("rendered") if isinstance(content_data, dict) else "")
        link = str(post.get("link") or "").strip()
        published = parse_date(str(post.get("date_gmt") or post.get("date") or ""))
        identifier = str(post.get("id") or "").strip() or None
        if title and link:
            items.append({"title": title, "url": link, "summary": detail, "published_at": published, "identifier": identifier})
    return items


def parse_data_gov_resource(data: bytes, source: Dict[str, object]) -> List[Dict[str, object]]:
    """Turn one authenticated data.gov.in resource response into a safe public item.

    The authenticated API URL is deliberately never returned.  Citations use the
    publisher's public resource page configured beside the resource UUID.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return []
    resource_id = str(payload.get("index_name") or "").removeprefix("resource_")
    resources = source.get("resources") if isinstance(source, dict) else None
    resource = next(
        (item for item in resources or [] if isinstance(item, dict) and item.get("id") == resource_id),
        None,
    )
    if not resource:
        return []
    title = clean_text(str(payload.get("title") or ""))
    published = parse_date(str(payload.get("updated_date") or payload.get("created_date") or ""))
    public_url = str(resource.get("public_url") or "").strip()
    if not title or not public_url or not published:
        return []
    organizations = payload.get("org") if isinstance(payload.get("org"), list) else []
    sectors = payload.get("sector") if isinstance(payload.get("sector"), list) else []
    publisher = ", ".join(clean_text(str(value)) for value in organizations[:3] if value) or "the responsible Government of India authority"
    sector_text = ", ".join(dict.fromkeys(clean_text(str(value)) for value in sectors if value)) or "public statistics"
    total = payload.get("total")
    record_text = f" The API currently exposes {int(total):,} records." if isinstance(total, int) else ""
    summary = (
        f"Open Government Data Platform India updated the dataset “{title}” on {published.date().isoformat()}. "
        f"The resource is published by {publisher} and classified under {sector_text}.{record_text} "
        "This entry records the official dataset refresh and its publisher metadata; it does not infer a change in the underlying index values. "
        "Users should inspect the linked public resource, its field definitions, revisions and individual records before drawing a trend conclusion or changing an operating assumption."
    )
    return [{
        "title": title,
        "url": public_url,
        "summary": summary,
        "published_at": published,
        "identifier": f"data.gov.in/{resource_id}",
    }]


def parse_rbi_notifications(text: str, base_url: str) -> List[Dict[str, object]]:
    """Read RBI's dated notification table, preserving the linked official PDF."""
    from urllib.parse import urljoin

    headers = list(re.finditer(r"<b>\s*([A-Z][a-z]{2}\s+\d{1,2},\s+20\d{2})\s*</b>", text, re.I))
    items: List[Dict[str, object]] = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        published = parse_date(header.group(1))
        section = text[header.end():end]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", section, re.I | re.S):
            match = re.search(r"<a[^>]+href\s*=\s*['\"]?([^'\"\s>]*NotificationUser\.aspx\?Id=([^&'\"\s>]+)[^'\"\s>]*)[^>]*>(.*?)</a>", row, re.I | re.S)
            if not match:
                continue
            title = clean_text(match.group(3))
            pdf = re.search(r"href\s*=\s*['\"]([^'\"]+\.PDF)['\"]", row, re.I)
            url = urljoin(base_url, pdf.group(1) if pdf else match.group(1))
            if title and published:
                items.append({"title": title, "url": url, "summary": "", "published_at": published, "identifier": f"RBI/{match.group(2)}"})
    return items


class LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            values = dict(attrs)
            self._href = values.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            title = clean_text(" ".join(self._text))
            url = urljoin(self.base_url, self._href)
            if title and url.startswith(("http://", "https://")):
                self.links.append((title, url))
            self._href = None
            self._text = []


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() in ("script", "style", "nav", "footer", "form", "svg"):
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style", "nav", "footer", "form", "svg") and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            text = clean_text(data)
            if len(text) > 30:
                self.parts.append(text)


class ArticleParser(HTMLParser):
    """Extract a semantic article body without site navigation or related-story rails."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if self.depth:
            self.depth += 1
        elif tag.lower() == "article" or "entry-content" in classes or "post-content" in classes:
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            value = clean_text(data)
            if value:
                self.parts.append(value)


def page_links(text: str, base_url: str) -> List[Tuple[str, str]]:
    parser = LinkParser(base_url)
    parser.feed(text)
    seen = set()
    result = []
    for title, url in parser.links:
        key = (title.casefold(), url.split("#", 1)[0])
        if key not in seen:
            seen.add(key)
            result.append((title, key[1]))
    return result


def page_text(text: str, limit: int = 5000) -> str:
    parser = TextParser()
    parser.feed(text)
    return clean_text(" ".join(parser.parts))[:limit]


def article_text(text: str, limit: int = 25_000) -> str:
    parser = ArticleParser()
    parser.feed(text)
    extracted = clean_text(" ".join(parser.parts))
    return extracted[:limit] if len(extracted.split()) >= 35 else page_text(text, limit)
