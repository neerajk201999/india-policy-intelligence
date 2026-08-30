from __future__ import annotations

import html
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
    for fmt in ("%d %B %Y", "%d %b %Y", "%d %b, %Y %z", "%d %b, %Y", "%B %d, %Y", "%d-%m-%Y", "%d/%m/%Y"):
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
