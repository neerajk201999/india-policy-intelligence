from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class RawItem:
    source_name: str
    source_type: str
    authority_level: int
    url: str
    title: str
    published_at: Optional[datetime]
    summary: str = ""
    source_identifier: Optional[str] = None
    default_signal_type: Optional[str] = None
    default_area: Optional[str] = None


@dataclass
class Event:
    canonical_title: str
    area: str
    signal_type: str
    description: str
    why_it_matters: str
    status: str
    publication_date: Optional[str]
    effective_date: Optional[str]
    deadline: Optional[str]
    affected_entities: List[str]
    primary_source_url: Optional[str]
    secondary_source_urls: List[str]
    source_document_title: str
    source_identifier: Optional[str]
    content_hash: str
    first_seen: str
    last_seen: str
    previous_event_id: Optional[int] = None
    is_update: bool = False
    watch_status: Optional[str] = None
    id: Optional[int] = None
    sources: List[dict] = field(default_factory=list)
