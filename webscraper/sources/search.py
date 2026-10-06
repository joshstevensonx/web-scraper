"""Search providers used for automatic source discovery.

* ``duckduckgo`` – DuckDuckGo's HTML endpoint, no API key (default; rate-limited politely)
* ``brave``      – Brave Search API, set ``BRAVE_API_KEY``
* ``searxng``    – a SearXNG instance with JSON output enabled, set ``SEARXNG_URL``
"""

from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from ..crawling.fetcher import Fetcher
from ..util.text import clean_whitespace
from ..util.urls import normalize_url


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    rank: int
    query: str


class SearchError(RuntimeError):
    pass


class SearchProvider:
    name = "base"
    delay = 1.0

    def __init__(self, fetcher: Fetcher, region: Optional[str] = None):
        self.fetcher = fetcher
        self.region = region
        self.blocked = False

    async def search(self, query: str, limit: int) -> List[SearchResult]:  # pragma: no cover - interface
        raise NotImplementedError


class DuckDuckGoProvider(SearchProvider):
    name = "duckduckgo"
    delay = 2.5
    endpoint = "https://html.duckduckgo.com/html/"

    def _region(self) -> str:
        if not self.region:
            return "wt-wt"
        code = self.region.lower()
        return {"gb": "uk-en", "us": "us-en", "za": "za-en", "au": "au-en", "ca": "ca-en", "in": "in-en",
                "nz": "nz-en", "ie": "ie-en", "sg": "sg-en"}.get(code, "%s-en" % code)

    async def search(self, query: str, limit: int) -> List[SearchResult]:
        res = await self.fetcher.fetch(self.endpoint, method="POST", data={"q": query, "kl": self._region()},
                                       headers={"Referer": "https://html.duckduckgo.com/"}, retries=1)
        if res.status == 202 or (res.ok and "anomaly" in res.text[:5000].lower()):
            self.blocked = True
            raise SearchError("DuckDuckGo is rate limiting requests (try again later or configure BRAVE_API_KEY)")
        if not res.ok:
            raise SearchError("DuckDuckGo search failed: %s" % (res.error or "HTTP %d" % res.status))
        soup = BeautifulSoup(res.text, "lxml")
        out: List[SearchResult] = []
        for block in soup.select(".result"):
            classes = " ".join(block.get("class", []))
            if "result--ad" in classes:
                continue
            a = block.select_one("a.result__a")
            if a is None or not a.get("href"):
                continue
            href = a["href"]
            if href.startswith("//"):
                href = "https:" + href
            parsed = urlsplit(href)
            if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
                target = parse_qs(parsed.query).get("uddg", [""])[0]
                href = unquote(target)
            url = normalize_url(href)
            if not url or "duckduckgo.com" in url:
                continue
            snippet = block.select_one(".result__snippet")
            out.append(SearchResult(url=url, title=clean_whitespace(a.get_text(" ")),
                                    snippet=clean_whitespace(snippet.get_text(" ")) if snippet else "",
                                    rank=len(out) + 1, query=query))
            if len(out) >= limit:
                break
        return out


class BraveProvider(SearchProvider):
    name = "brave"
    delay = 1.1
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, fetcher: Fetcher, region: Optional[str] = None, api_key: Optional[str] = None):
        super().__init__(fetcher, region)
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")

    async def search(self, query: str, limit: int) -> List[SearchResult]:
        if not self.api_key:
            raise SearchError("BRAVE_API_KEY is not set")
        params = {"q": query, "count": str(min(20, max(1, limit)))}
        if self.region:
            params["country"] = self.region.lower()
        res = await self.fetcher.fetch(self.endpoint, params=params, retries=1,
                                       headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"})
        if not res.ok:
            raise SearchError("Brave search failed: %s" % (res.error or "HTTP %d" % res.status))
        import json

        data = json.loads(res.text or "{}")
        out = []
        for i, item in enumerate((data.get("web") or {}).get("results") or []):
            url = normalize_url(item.get("url", ""))
            if url:
                out.append(SearchResult(url, clean_whitespace(item.get("title", "")),
                                        clean_whitespace(BeautifulSoup(item.get("description", ""), "lxml").get_text()),
                                        i + 1, query))
        return out[:limit]


class SearxngProvider(SearchProvider):
    name = "searxng"
    delay = 1.0

    def __init__(self, fetcher: Fetcher, region: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(fetcher, region)
        self.base_url = (base_url or os.environ.get("SEARXNG_URL", "")).rstrip("/")

    async def search(self, query: str, limit: int) -> List[SearchResult]:
        if not self.base_url:
            raise SearchError("SEARXNG_URL is not set")
        import json

        res = await self.fetcher.fetch(self.base_url + "/search", params={"q": query, "format": "json"}, retries=1)
        if not res.ok:
            raise SearchError("SearXNG search failed: %s" % (res.error or "HTTP %d" % res.status))
        try:
            data = json.loads(res.text)
        except ValueError:
            raise SearchError("SearXNG did not return JSON (enable the json format in settings.yml)")
        out = []
        for i, item in enumerate(data.get("results") or []):
            url = normalize_url(item.get("url", ""))
            if url:
                out.append(SearchResult(url, clean_whitespace(item.get("title", "")),
                                        clean_whitespace(item.get("content", "")), i + 1, query))
        return out[:limit]


def make_provider(name: str, fetcher: Fetcher, region: Optional[str]) -> Optional[SearchProvider]:
    name = (name or "auto").lower()
    if name == "none":
        return None
    if name == "auto":
        if os.environ.get("BRAVE_API_KEY"):
            name = "brave"
        elif os.environ.get("SEARXNG_URL"):
            name = "searxng"
        else:
            name = "duckduckgo"
    if name == "brave":
        return BraveProvider(fetcher, region)
    if name == "searxng":
        return SearxngProvider(fetcher, region)
    return DuckDuckGoProvider(fetcher, region)


async def run_queries(provider: SearchProvider, queries: List[str], per_query: int,
                      on_error=None, should_stop=None) -> List[SearchResult]:
    results: List[SearchResult] = []
    for i, q in enumerate(queries):
        if should_stop and should_stop():
            break
        if provider.blocked:
            break
        try:
            results.extend(await provider.search(q, per_query))
        except SearchError as exc:
            if on_error:
                on_error(q, str(exc))
            if provider.blocked:
                break
        if i + 1 < len(queries):
            await asyncio.sleep(provider.delay + random.uniform(0, provider.delay / 2))
    return results
