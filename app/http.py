from __future__ import annotations

import gzip
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LOG = logging.getLogger(__name__)


@dataclass
class Response:
    url: str
    status: int
    content_type: str
    body: bytes

    @property
    def text(self) -> str:
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                return self.body.decode(encoding)
            except UnicodeDecodeError:
                pass
        return self.body.decode("utf-8", errors="replace")


class HttpClient:
    def __init__(self, timeout: int = 15, retries: int = 1, max_bytes: int = 3_000_000):
        self.timeout = timeout
        self.retries = retries
        self.max_bytes = max_bytes
        self.headers: Dict[str, str] = {
            "User-Agent": "IndiaPolicyTracker/0.1 (+local research; respectful fetcher)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html;q=0.9, */*;q=0.5",
            "Accept-Encoding": "gzip",
        }

    def get(self, url: str) -> Response:
        if not url.startswith(("https://", "http://")):
            raise ValueError("Only HTTP(S) sources are supported")
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                request = Request(url, headers=self.headers)
                with urlopen(request, timeout=self.timeout) as raw:
                    body = raw.read(self.max_bytes + 1)
                    if len(body) > self.max_bytes:
                        raise ValueError(f"Response exceeded {self.max_bytes} bytes")
                    if raw.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                    return Response(
                        url=raw.geturl(),
                        status=getattr(raw, "status", 200),
                        content_type=raw.headers.get("Content-Type", ""),
                        body=body,
                    )
            except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.3 * (attempt + 1))
        assert last_error is not None
        raise last_error

