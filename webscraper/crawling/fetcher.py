"""HTTP fetching layer (section 14) with retries, size limits and polite headers."""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import httpx

from .. import __version__

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 (compatible; WebScraper/%s; +https://github.com/joshstevensonx/web-scraper)"
    % __version__
)
ROBOTS_TOKEN = "WebScraper"

ACCEPTED_TYPES = ("text/html", "application/xhtml", "text/xml", "application/xml", "application/rss",
                  "application/atom", "application/json", "application/ld+json", "text/plain", "application/x-gzip",
                  "application/gzip", "application/octet-stream")


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int = 0
    content_type: str = ""
    text: str = ""
    content: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed: float = 0.0
    rendered: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    @property
    def is_html(self) -> bool:
        ct = self.content_type.lower()
        return "html" in ct or (not ct and self.text.lstrip()[:200].lower().startswith(("<!doctype html", "<html")))


def _sniff_encoding(raw: bytes, header_encoding: Optional[str]) -> str:
    if header_encoding:
        return header_encoding
    head = raw[:4096].decode("ascii", errors="ignore")
    m = re.search(r"""<meta[^>]+charset=["']?([\w-]+)""", head, re.I)
    if m:
        return m.group(1)
    m = re.search(r"""<\?xml[^>]+encoding=["']([\w-]+)""", head, re.I)
    if m:
        return m.group(1)
    return "utf-8"


class Fetcher:
    def __init__(self, user_agent: str = "", timeout: float = 20.0, retries: int = 2, max_bytes: int = 5_000_000,
                 max_connections: int = 20, on_rate_limited: Optional[Callable[[str, float], None]] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.retries = retries
        self.max_bytes = max_bytes
        self.on_rate_limited = on_rate_limited
        self.client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0)),
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.5",
                "Accept-Language": "en;q=0.9,*;q=0.5",
            },
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections),
            max_redirects=8,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch(self, url: str, method: str = "GET", data: Optional[Dict[str, str]] = None,
                    headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, str]] = None,
                    retries: Optional[int] = None) -> FetchResult:
        attempts = (self.retries if retries is None else retries) + 1
        last = FetchResult(url=url, error="not fetched")
        for attempt in range(attempts):
            last = await self._fetch_once(url, method, data, headers, params)
            if last.error is None and last.status not in (429, 500, 502, 503, 504, 520, 522, 524):
                return last
            if attempt + 1 >= attempts:
                break
            wait = min(30.0, (2 ** attempt) + random.uniform(0, 0.5))
            if last.status in (429, 503):
                retry_after = last.headers.get("retry-after", "")
                if retry_after.isdigit():
                    wait = min(60.0, max(wait, float(retry_after)))
                if self.on_rate_limited:
                    self.on_rate_limited(url, wait)
            await asyncio.sleep(wait)
        return last

    async def _fetch_once(self, url: str, method: str, data, headers, params) -> FetchResult:
        start = time.monotonic()
        result = FetchResult(url=url)
        try:
            async with self.client.stream(method, url, data=data, headers=headers, params=params) as resp:
                result.status = resp.status_code
                result.final_url = str(resp.url)
                result.headers = {k.lower(): v for k, v in resp.headers.items()}
                result.content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if result.content_type and not result.content_type.startswith(ACCEPTED_TYPES):
                    result.error = "unsupported content type %s" % result.content_type
                    return result
                chunks = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= self.max_bytes:
                        result.truncated = True
                        break
                raw = b"".join(chunks)
                result.content = raw
                enc = _sniff_encoding(raw, resp.charset_encoding)
                try:
                    result.text = raw.decode(enc, errors="replace")
                except LookupError:
                    result.text = raw.decode("utf-8", errors="replace")
        except httpx.TimeoutException:
            result.error = "timeout"
        except httpx.TooManyRedirects:
            result.error = "too many redirects"
        except httpx.HTTPError as exc:
            result.error = "%s: %s" % (type(exc).__name__, str(exc)[:200] or "connection failed")
        except Exception as exc:  # pragma: no cover - defensive (e.g. invalid URL characters)
            result.error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        finally:
            result.elapsed = time.monotonic() - start
        return result
