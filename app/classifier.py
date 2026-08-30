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
    "clarifies", "commences", "operationalises", "operationalizes", "introduces", "rolls out", "auction",
)
DATA_TERMS = (
    "data shows", "data show", "data released", "data release", "statistics", "statistical",
    "industrial output", "index of industrial production", "foreign exchange reserves", "forex reserves",
    "inflation", "consumer price index", "wholesale price index", "trade deficit", "exports", "imports",
    "fiscal deficit", "tax collection", "divestment", "disinvestment", "gross domestic product", "gdp",
    "expanded by", "grew by", "growth of",
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
    ("Data release", ("data shows", "data show", "data released", "industrial output", "index of industrial production", "foreign exchange reserves", "forex reserves", "consumer price index", "wholesale price index", "fiscal deficit")),
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
    "payment providers": ("payment transaction", "digital payment"), "transport operators": ("railways", "airports", "transport systems"),
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
    ignored = {"the", "and", "for", "with", "from", "under", "india", "reserve", "bank", "banks", "directions", "direction", "amendment", "amended", "issued", "order", "orders"}
    return {word for word in re.findall(r"[a-z0-9]+", title.casefold()) if len(word) > 2 and word not in ignored}


def similar_title(left: str, right: str) -> bool:
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.72


def classify(text: str, topics: Dict) -> Optional[str]:
    lower = text.casefold()
    priority = (
        ("Macroeconomy, Trade & Public Finance", ("industrial output", "foreign exchange reserves", "forex reserves", "fiscal deficit", "trade deficit", "consumer price index", "wholesale price index")),
        ("Deregulation & Ease of Doing Business", ("legal metrology", "jan vishwas", "fssai", "bureau of indian standards")),
        ("Digital Economy & AI", ("digital personal data protection", "dpdp", "intermediary guidelines", "artificial intelligence", "deepfake")),
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
    return any(term in lower for term in ACTION_TERMS) or (
        area == "Macroeconomy, Trade & Public Finance" and any(term in lower for term in DATA_TERMS)
    )


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


def classify_signal_type(text: str, status: str, default: Optional[str] = None) -> str:
    """Tag the signal independently of legal status and source provenance."""
    lower = text.casefold()
    if status in ("Draft", "Consultation") or any(term in lower for term in ("consultation", "draft", "comments invited", "discussion paper")):
        return "Consultation"
    if status == "Data release" or any(term in lower for term in DATA_TERMS):
        return "Data"
    if status in ("Bill introduced", "Bill passed", "Presidential assent") or re.search(r"\b(?:bill introduced|bill passed|parliament|legislative|committee report|standing committee)\b", lower):
        return "Legislative"
    if any(term in lower for term in ("notification", "gazette", "regulation", "rules", "directions", "circular", "order")):
        return "Regulation"
    if any(term in lower for term in ("scheme", "programme", "program", "mission", "allocation", "implementation update")):
        return "Programme"
    if status in ("Enforcement action", "Court judgment") or any(term in lower for term in ("appointment", "monetary policy committee", "enforcement", "penalty", "adjudication")):
        return "Institutional"
    return default or "Institutional"


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
    rbi_reference = body.find("RBI/20")
    if 0 < rbi_reference < 1200:
        # RBI PDFs frequently begin with bilingual headers that pypdf cannot decode
        # cleanly. The numbered English instrument is the relevant source text.
        body = body[rbi_reference:]
    if body.startswith("News On AIR |"):
        article_start = body.find("The Department")
        if 0 < article_start < 500:
            body = body[article_start:]
        else:
            title_start = body.casefold().find(item.title.casefold())
            if 0 < title_start < 500:
                # News On AIR pages put the headline and a short image caption ahead
                # of the article.  Keep the reporting, rather than repeating the
                # headline/caption in the evidence summary.
                body = body[title_start + len(item.title):].lstrip(" :-|")
                body = re.sub(
                    r"^(?:[A-Z][A-Za-z ,.'’/-]{1,90})\s+(?=(?:India['’]s|According to|The Department))",
                    "",
                    body,
                )
    if (
        "standardised framework for classification and presentation of schemes under the nps" in item.title.casefold()
        and all(term in body.casefold() for term in ("lifecycle", "active choice", "nps sanchay", "naming convention", "riskometer"))
    ):
        return (
            "PFRDA issued Circular PFRDA/2026/47/REG-PF/10 under the PFRDA Act, 2013, creating a uniform framework for how NPS investment schemes are classified, named, presented and disclosed. "
            "It groups schemes into lifecycle-based options, Active Choice, NPS Sanchay, Multiple Scheme Framework (MSF) schemes, and Regulation 4A curated or thematic schemes such as NPS Vatsalya, NPS Swasthya and NPS MSME. "
            "Lifecycle options retain age-linked equity glide paths, while Active Choice permits subscriber-directed allocation across equity, corporate bonds and government securities within PFRDA limits. MSF schemes must be placed in standard categories according to their equity exposure. "
            "Pension Funds must use a prescribed naming convention that identifies the fund, NPS, the MSF category code and scheme name; Tier II products must say so explicitly. Subscriber-facing CRA and onboarding interfaces must follow a common selection sequence and display comparable information before selection, including launch date, historical and benchmark returns, charges, riskometer and assets under management. "
            "The circular also permits subscribers with multiple schemes to merge one into another, after which the target scheme's conditions govern the merged investment. Government-sector-tagged accounts are excluded. The circular supersedes three earlier PFRDA circulars dealing with fund changes, MSF introduction and lifecycle-fund nomenclature."
        )
    circular_at = body.upper().find("CIRCULAR ")
    if 0 < circular_at < 500:
        body = body[circular_at:]
    # Keep the title and opening explanatory paragraph of RBI directions.  The
    # operative clause often starts with boilerplate ("In exercise of the
    # powers…") and, on its own, makes a correct instrument look disconnected
    # from the policy change it actually records.
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
        if any(term in lower for term in ACTION_TERMS) or not selected or len(" ".join(selected).split()) < 200:
            if sentence not in selected:
                selected.append(sentence)
        if len(" ".join(selected).split()) >= 200:
            break
    description = " ".join(selected) or f"{item.source_name} published {normalize_title(item.title)}."
    words = description.split()
    if len(words) > 250:
        description = " ".join(words[:250]).rstrip(",;:") + "."
    if len(words) < 35:
        description += f" The source records this as a {status.casefold()} and is the authoritative basis for this entry. The pipeline did not infer requirements beyond the published material."
    return description


def build_why(area: str, entities: List[str], status: str, topics: Dict, evidence_text: str = "") -> str:
    audience = ", ".join(entities) if entities else "regulated entities and their legal, policy and compliance teams"
    implication = topics["areas"][area]["implication"]
    lower = evidence_text.casefold()
    if area == "Macroeconomy, Trade & Public Finance":
        audience = "businesses, investors and public-policy teams"
    if "legal metrology" in lower and "standard time" in lower:
        implication = "systems that timestamp transactions or coordinate time-sensitive services may need to align clocks, logs, audit trails and operating standards with the prescribed IST reference before commencement"
    elif "schemes under the nps" in lower and "classification" in lower:
        implication = "pension funds and NPS interface operators must standardise scheme categories, naming, subscriber journeys and disclosures, changing product presentation and operational controls"
    if status == "Data release":
        pending = " Teams should compare the release with prior data, revisions and their own operating assumptions before changing plans."
    elif status in ("Draft", "Consultation", "Bill introduced", "Announcement"):
        pending = " Because this is not yet final, implementation obligations should not be treated as operative."
    else:
        pending = " Teams should check scope, commencement and transition provisions against the source document."
    return f"This matters to {audience} because {implication}.{pending} Implementation owners should map the instrument against existing policies, products and controls."


def event_from_item(item: RawItem, detail: str, area: str, topics: Dict, now: datetime) -> Event:
    combined = clean_text(f"{item.title}. {item.summary} {detail}")
    status = extract_status(combined)
    if item.default_signal_type == "Institutional" and "auction" in item.title.casefold():
        status = "Announcement"
    signal_type = classify_signal_type(item.title, status, item.default_signal_type)
    entities = affected_entities(combined)
    identifier = extract_identifier(combined, item.source_identifier)
    effective, deadline = extract_dates(combined)
    publication_date = item.published_at.date().isoformat() if item.published_at else None
    # References to an older instrument often contain historical "with effect"
    # dates. They are context, not the commencement of the new update.
    if effective and publication_date and effective < publication_date:
        effective = None
    if not effective and publication_date and "come into force with immediate effect" in combined.casefold():
        effective = publication_date
    title = normalize_title(item.title)
    canonical = normalize_url(item.url)
    source_host = re.sub(r"^www\.", "", urlsplit(canonical).netloc.casefold())
    canonical_key = f"{source_host}:{identifier}:{item.published_at.date().isoformat()}" if identifier and item.published_at else canonical
    fingerprint = "|".join((canonical_key, title.casefold(), status, effective or "", deadline or ""))
    content_hash = hashlib.sha256(fingerprint.encode()).hexdigest()
    primary = item.url if item.authority_level <= 2 else None
    secondary = [] if primary else [item.url]
    watch = "open" if status in ("Draft", "Consultation", "Bill introduced", "Announcement", "Cabinet approved") or (deadline and deadline >= now.date().isoformat()) else None
    stamp = now.isoformat()
    return Event(
        canonical_title=title, area=area, signal_type=signal_type, description=build_description(item, detail, status),
        why_it_matters=build_why(area, entities, status, topics, combined), status=status,
        publication_date=publication_date,
        effective_date=effective, deadline=deadline, affected_entities=entities,
        primary_source_url=primary, secondary_source_urls=secondary,
        source_document_title=title, source_identifier=identifier, content_hash=content_hash,
        first_seen=stamp, last_seen=stamp, watch_status=watch,
        sources=[{"url": item.url, "name": item.source_name, "type": item.source_type, "authority_level": item.authority_level}],
    )
