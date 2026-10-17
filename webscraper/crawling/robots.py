"""robots.txt handling (RFC 9309 semantics for unavailable/unreachable files)."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .fetcher import ROBOTS_TOKEN, Fetcher


class _Rules:
    def __init__(self, parser: Optional[RobotFileParser], allow_all: bool = False, deny_all: bool = False):
        self.parser = parser
        self.allow_all = allow_all
        self.deny_all = deny_all

    def can_fetch(self, agent: str, url: str) -> bool:
        if self.allow_all:
            return True
        if self.deny_all or self.parser is None:
            return False
        return self.parser.can_fetch(agent, url)

    def crawl_delay(self, agent: str) -> Optional[float]:
        if self.parser is None:
            return None
        try:
            d = self.parser.crawl_delay(agent)
        except Exception:
            d = None
        return float(d) if d is not None else None

    def sitemaps(self) -> List[str]:
        if self.parser is None:
            return []
        try:
            return list(self.parser.site_maps() or [])
        except Exception:
            return []


class RobotsCache:
    def __init__(self, fetcher: Fetcher, enabled: bool = True, agent: str = ROBOTS_TOKEN):
        self.fetcher = fetcher
        self.enabled = enabled
        self.agent = agent
        self._rules: Dict[str, _Rules] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self.blocked_urls = 0

    @staticmethod
    def _root(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))

    async def rules(self, url: str) -> _Rules:
        root = self._root(url)
        if root in self._rules:
            return self._rules[root]
        lock = self._locks.setdefault(root, asyncio.Lock())
        async with lock:
            if root in self._rules:
                return self._rules[root]
            res = await self.fetcher.fetch(root + "/robots.txt", retries=1)
            if res.status == 0:
                rules = _Rules(None, deny_all=True)  # unreachable: assume complete disallow
            elif 400 <= res.status < 500:
                rules = _Rules(None, allow_all=True)  # unavailable: no restrictions
            elif res.status >= 500:
                rules = _Rules(None, deny_all=True)
            elif res.ok:
                parser = RobotFileParser()
                parser.parse(res.text.splitlines())
                rules = _Rules(parser)
            else:
                rules = _Rules(None, allow_all=True)
            self._rules[root] = rules
            return rules

    async def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        rules = await self.rules(url)
        ok = rules.can_fetch(self.agent, url)
        if not ok:
            self.blocked_urls += 1
        return ok

    async def crawl_delay(self, url: str) -> Optional[float]:
        if not self.enabled:
            return None
        return (await self.rules(url)).crawl_delay(self.agent)

    async def sitemaps(self, url: str) -> List[str]:
        return (await self.rules(url)).sitemaps()
