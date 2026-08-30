from __future__ import annotations

import re
from typing import List
from urllib.parse import urlsplit

from .models import Event


ALLOWED_STATUS = {
    "Proposed", "Announcement", "Consultation", "Draft", "Cabinet approved",
    "Bill introduced", "Bill passed", "Presidential assent", "Notified",
    "Regulation issued", "Circular issued", "Order issued", "In force",
    "Enforcement action", "Court judgment", "Stayed", "Withdrawn", "Repealed", "Pending",
}


def publication_issues(event: Event) -> List[str]:
    """Return hard editorial-gate failures for an automatically extracted event."""
    issues: List[str] = []
    if not event.publication_date:
        issues.append("missing publication date")
    if event.status not in ALLOWED_STATUS:
        issues.append("unrecognised legal status")
    if not event.primary_source_url and not event.secondary_source_urls:
        issues.append("missing source URL")
    for url in ([event.primary_source_url] if event.primary_source_url else event.secondary_source_urls[:1]):
        if url:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                issues.append("invalid source URL")
    what_words = len(event.description.split())
    why_words = len(event.why_it_matters.split())
    if what_words < 90:
        issues.append("insufficient factual detail")
    if what_words > 300:
        issues.append("factual summary too long")
    if why_words < 35:
        issues.append("insufficient practical analysis")
    ignored = {"the", "and", "for", "with", "from", "under", "india", "government", "rules", "order", "issued"}
    title_tokens = {word for word in re.findall(r"[a-z0-9]+", event.canonical_title.casefold()) if len(word) > 3 and word not in ignored}
    description_tokens = set(re.findall(r"[a-z0-9]+", event.description.casefold()))
    if title_tokens and len(title_tokens & description_tokens) < min(2, len(title_tokens)):
        issues.append("evidence text is unrelated to the event title")
    if "citizen's charter" in event.description.casefold() or "client's charter" in event.description.casefold():
        issues.append("site-wide boilerplate mistaken for evidence")
    if not event.primary_source_url:
        issues.append("primary source unavailable")
    if event.effective_date and event.publication_date and event.effective_date < event.publication_date:
        issues.append("effective date predates publication date; manual check required")
    return issues


def is_publishable(event: Event) -> bool:
    # A primary-source miss is transparently allowed only when every other gate passes.
    return all(issue == "primary source unavailable" for issue in publication_issues(event))
