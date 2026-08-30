from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Event, RawItem
from .parsing import clean_text, parse_date


ACTION_TERMS = (
    "notification", "notifies", "notified", "circular", "regulation", "regulations", "rules", "rule",
    "draft", "consultation", "order", "judgment", "judgement", "approves", "approved", "amendment",
    "amends", "directions", "guidelines", "framework", "penalty", "enforcement", "bill", "act",
    "deadline", "effective", "implementation", "launches", "issues", "releases", "mandates", "prohibits",
)
EXCLUDE_TERMS = (
    "photo gallery", "courtesy call", "paid respects", "celebrates", "celebration", "inaugurates conference",
    "attends event", "addresses gathering", "meets delegation", "birthday", "condolences", "workshop held",
    "weekly statistical supplement", "exchange rate", "vacancy", "recruitment", "tender notice",
    "debunks fake", "fake letter", "fake notification", "fact check",
)
RELIABLE_SECONDARY = (
    "reuters", "press trust of india", " pti", "bloomberg", "financial times", "mint", "livemint",
    "economic times", "business standard", "indian express", "the hindu", "news on air", "akashvani",
)
STATUS_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Court judgment", ("judgment", "judgement", "court holds", "supreme court", "high court")),
    ("Enforcement action", ("penalty", "enforcement action", "cease and desist", "show cause", "adjudication order")),
    ("Draft", ("draft rules", "draft regulation", "draft guidelines", "draft amendment")),
    ("Draft", ("draft directions", "exposure draft", "draft framework")),
    ("Consultation", ("consultation paper", "invites comments", "public comments", "consultation")),
    ("Bill passed", ("bill passed", "passes bill")),
    ("Bill introduced", ("bill introduced", "introduces bill")),
    ("Presidential assent", ("presidential assent", "received assent")),
    ("Cabinet approved", ("cabinet approved", "cabinet approves")),
    ("Regulation issued", ("regulations, 20", "regulations 20", "regulation issued")),
    ("Circular issued", ("circular", "master direction")),
    ("Notified", ("gazette notification", "notified", "notifies", "notification")),
    ("Order issued", ("order", "directions")),
    ("In force", ("comes into force", "effective from", "in force")),
    ("Announcement", ("announces", "announcement", "launches", "unveils")),
]
ENTITY_TERMS = {
    "banks": ("bank", "banking"), "NBFCs": ("nbfc", "non-banking financial"),
    "listed companies": ("listed compan", "listed entit", "lodr"), "startups": ("startup",),
    "employers": ("employer", "labour", "labor"), "schools": ("school", "rte"),
    "universities": ("university", "universities", "ugc"), "e-commerce platforms": ("e-commerce", "ecommerce"),
    "social-media intermediaries": ("social media", "intermediary"), "technology companies": ("technology compan", "digital platform", "big tech"),
    "data fiduciaries": ("data fiduciary", "personal data"), "FinTechs": ("fintech", "digital lending"),
    "developers": ("developer", "building regulation", "real estate"), "property owners": ("property owner", "land record"),
    "investors": ("investor", "securities market"), "consumers": ("consumer",), "insurers": ("insurer", "insurance"),
    "pension funds": ("pension fund",), "NPS subscribers": ("nps subscriber", "subscribers"),
}


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def normalize_title(title: str) -> str:
    title = clean_text(title)
    title = re.sub(r"\s+Pension Fund\s+Ref:\s*.*$", "", title, flags=re.I)
    title = re.sub(r"\s*[|–—-]\s*(press release|pib|rbi|sebi|cci|government of india)\s*$", "", title, flags=re.I)
    return title.strip(" -–—")


def title_tokens(title: str) -> set:
    return {word for word in re.findall(r"[a-z0-9]+", title.casefold()) if len(word) > 2 and word not in {"the", "and", "for", "with", "from", "india"}}


def similar_title(left: str, right: str) -> bool:
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.72


def classify(text: str, topics: Dict) -> Optional[str]:
    lower = text.casefold()
    priority = (
        ("Deregulation & Ease of Doing Business", ("legal metrology", "jan vishwas", "fssai", "bureau of indian standards")),
        ("Digital Economy & Technology", ("digital personal data protection", "dpdp", "intermediary guidelines", "artificial intelligence", "deepfake")),
        ("Competition", ("competition commission of india", "competition act", "abuse of dominance", "cartel")),
    )
    for area, phrases in priority:
        if any(phrase in lower for phrase in phrases):
            return area
    best: Optional[Tuple[str, int]] = None
    for area, config in topics["areas"].items():
        matches = sum(1 for term in config["keywords"] if term.casefold() in lower)
        if matches and (best is None or matches > best[1]):
            best = (area, matches)
    return best[0] if best else None


def is_meaningful(text: str, area: Optional[str]) -> bool:
    lower = text.casefold()
    if not area or any(term in lower for term in EXCLUDE_TERMS):
        return False
    return any(term in lower for term in ACTION_TERMS)


def extract_status(text: str) -> str:
    lower = text.casefold()
    if re.search(r"\b(?:passage of|passes|passed)\b.{0,50}\bbill\b|\bbill\b.{0,50}\b(?:passes|passed)\b", lower):
        return "Bill passed"
    if re.search(r"\b(?:introduces?|introduced)\b.{0,50}\bbill\b|\bbill\b.{0,50}\bintroduced\b", lower):
        return "Bill introduced"
    for status, phrases in STATUS_RULES:
        if any(phrase in lower for phrase in phrases):
            return status
    return "Announcement"


IDENTIFIER = re.compile(r"\b(?:circular|notification|order|bill|press release)\s*(?:no\.?\s*)?([A-Z0-9][A-Z0-9./()_-]{3,})", re.I)
DATE_CONTEXT = re.compile(r"\b(effective|with effect from|deadline|comments? (?:by|until)|commencement)\s+(?:on\s+|from\s+|is\s+)?([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4})", re.I)


def extract_identifier(text: str, fallback: Optional[str]) -> Optional[str]:
    if fallback:
        return clean_text(fallback)
    match = IDENTIFIER.search(text)
    if match and any(character.isdigit() for character in match.group(0)):
        return clean_text(match.group(0)).upper()
    return None


def extract_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    effective = deadline = None
    for match in DATE_CONTEXT.finditer(text):
        date = parse_date(match.group(2))
        if not date:
            continue
        value = date.date().isoformat()
        if "deadline" in match.group(1).casefold() or "comment" in match.group(1).casefold():
            deadline = value
        else:
            effective = value
    return effective, deadline


def affected_entities(text: str) -> List[str]:
    lower = text.casefold()
    return [entity for entity, terms in ENTITY_TERMS.items() if any(term in lower for term in terms)][:6]


def has_sufficient_evidence(item: RawItem, detail: str) -> bool:
    """Secondary discovery needs substantive text and a trusted publisher.

    Search-result headlines alone cannot support a legal-status brief.
    """
    if item.authority_level <= 2:
        return True
    publisher_ok = any(name in item.title.casefold() for name in RELIABLE_SECONDARY)
    substantive = len(clean_text(detail).split()) >= 70
    return publisher_ok and substantive


def _sentences(text: str) -> List[str]:
    return [clean_text(part) for part in re.split(r"(?<=[.!?])\s+", clean_text(text)) if len(clean_text(part)) > 20]


def build_description(item: RawItem, detail: str, status: str) -> str:
    body = clean_text(detail or item.summary)
    circular_at = body.upper().find("CIRCULAR ")
    if 0 < circular_at < 500:
        body = body[circular_at:]
    operative_at = body.casefold().find("in exercise of the powers")
    if 0 < operative_at < 650:
        body = body[operative_at:]
    body = re.sub(
        r"Classification Framework\s*1\.?\s*Classification of Investment Schemes\s*1\.1",
        "The framework states that",
        body,
        flags=re.I,
    )
    sentences = _sentences(body)
    selected: List[str] = []
    for sentence in sentences:
        lower = sentence.casefold()
        if any(term in lower for term in ACTION_TERMS) or not selected:
            if sentence not in selected:
                selected.append(sentence)
        if len(" ".join(selected).split()) >= 120:
            break
    description = " ".join(selected) or f"{item.source_name} published {normalize_title(item.title)}."
    words = description.split()
    if len(words) > 155:
        description = " ".join(words[:155]).rstrip(",;:") + "."
    if len(words) < 35:
        description += f" The source records this as a {status.casefold()} and is the authoritative basis for this entry. The pipeline did not infer requirements beyond the published material."
    return description


def build_why(area: str, entities: List[str], status: str, topics: Dict) -> str:
    audience = ", ".join(entities) if entities else "regulated entities and their legal, policy and compliance teams"
    implication = topics["areas"][area]["implication"]
    pending = " Because this is not yet final, implementation obligations should not be treated as operative." if status in ("Draft", "Consultation", "Bill introduced", "Announcement") else " Teams should check scope, commencement and transition provisions against the source document."
    return f"This matters to {audience} because {implication}.{pending} Implementation owners should map the instrument against existing policies, products and controls."


def event_from_item(item: RawItem, detail: str, area: str, topics: Dict, now: datetime) -> Event:
    combined = clean_text(f"{item.title}. {item.summary} {detail}")
    status = extract_status(combined)
    entities = affected_entities(combined)
    identifier = extract_identifier(combined, item.source_identifier)
    effective, deadline = extract_dates(combined)
    title = normalize_title(item.title)
    canonical = normalize_url(item.url)
    fingerprint = "|".join((identifier or canonical, title.casefold(), status, effective or "", deadline or ""))
    content_hash = hashlib.sha256(fingerprint.encode()).hexdigest()
    primary = item.url if item.authority_level <= 2 else None
    secondary = [] if primary else [item.url]
    watch = "open" if status in ("Draft", "Consultation", "Bill introduced", "Announcement", "Cabinet approved") or (deadline and deadline >= now.date().isoformat()) else None
    stamp = now.isoformat()
    return Event(
        canonical_title=title, area=area, description=build_description(item, detail, status),
        why_it_matters=build_why(area, entities, status, topics), status=status,
        publication_date=item.published_at.date().isoformat() if item.published_at else None,
        effective_date=effective, deadline=deadline, affected_entities=entities,
        primary_source_url=primary, secondary_source_urls=secondary,
        source_document_title=title, source_identifier=identifier, content_hash=content_hash,
        first_seen=stamp, last_seen=stamp, watch_status=watch,
        sources=[{"url": item.url, "name": item.source_name, "type": item.source_type, "authority_level": item.authority_level}],
    )
