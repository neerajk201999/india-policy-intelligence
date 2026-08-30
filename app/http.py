from __future__ import annotations

import gzip
import logging
import ssl
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
            # Several public Indian-government sites reject non-browser user agents even
            # though their published pages and feeds are openly accessible. This profile
            # only requests the same public document representation as a normal reader.
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,application/atom+xml;q=0.8,*/*;q=0.7",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        # Keep certificate validation on. certifi closes gaps in minimal CI images whose
        # system certificate store does not include every public-government issuer.
        try:
            import certifi  # type: ignore
            self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self.ssl_context = ssl.create_default_context()

    def get(self, url: str) -> Response:
        if not url.startswith(("https://", "http://")):
            raise ValueError("Only HTTP(S) sources are supported")
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                request = Request(url, headers=self.headers)
                with urlopen(request, timeout=self.timeout, context=self.ssl_context) as raw:
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
