"""Crawl scheduler (section 13): prioritized URL queue with per-host politeness.

Each host has its own priority heap. :meth:`Frontier.get` hands out the highest-priority task from
any host that is idle and whose rate-limit delay has elapsed, so one slow site never blocks the
others and no site receives concurrent requests.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..util.urls import host_of
from .rate_limiter import RateLimiter

# Lower number = fetched sooner.
PRIORITY = {
    "api": 0, "feed": 0, "sitemap": 0, "entity_contact": 1, "detail": 1, "entity_home": 2, "seed": 2,
    "pagination": 3, "listing": 4, "other": 6,
}


@dataclass(order=True)
class CrawlTask:
    priority: float
    seq: int
    url: str = field(compare=False)
    kind: str = field(compare=False, default="seed")
    depth: int = field(compare=False, default=0)
    source_index: int = field(compare=False, default=0)
    group_key: Optional[str] = field(compare=False, default=None)
    referrer: Optional[str] = field(compare=False, default=None)
    attempt: int = field(compare=False, default=0)
    data: Optional[dict] = field(compare=False, default=None)


class Frontier:
    def __init__(self, rate_limiter: RateLimiter, max_per_host: int = 0):
        self.rate = rate_limiter
        self.max_per_host = max_per_host
        self._queues: Dict[str, List[CrawlTask]] = {}
        self._seen: Set[str] = set()
        self._host_counts: Dict[str, int] = {}
        self._busy_hosts: Set[str] = set()
        self._in_flight = 0
        self._seq = itertools.count()
        self._changed = asyncio.Event()
        self.closed = False
        self.discovered = 0

    def make_task(self, url: str, kind: str, depth: int = 0, source_index: int = 0, group_key: Optional[str] = None,
                  referrer: Optional[str] = None, boost: float = 0.0, data: Optional[dict] = None) -> CrawlTask:
        prio = PRIORITY.get(kind, 5) + depth * 0.5 - boost
        return CrawlTask(prio, next(self._seq), url, kind, depth, source_index, group_key, referrer, 0, data)

    def seen(self, url: str) -> bool:
        return url in self._seen

    def mark_seen(self, url: str) -> None:
        self._seen.add(url)

    def add(self, task: CrawlTask, force: bool = False) -> bool:
        if self.closed:
            return False
        if task.url in self._seen and not force:
            return False
        host = host_of(task.url)
        if not host:
            return False
        if self.max_per_host and self._host_counts.get(host, 0) >= self.max_per_host and task.kind not in (
                "entity_home", "entity_contact", "api", "sitemap", "feed"):
            return False
        self._seen.add(task.url)
        self._host_counts[host] = self._host_counts.get(host, 0) + 1
        heapq.heappush(self._queues.setdefault(host, []), task)
        self.discovered += 1
        self._changed.set()
        return True

    def requeue(self, task: CrawlTask, delay_priority: float = 1.0) -> None:
        task.attempt += 1
        task.priority += delay_priority
        task.seq = next(self._seq)
        heapq.heappush(self._queues.setdefault(host_of(task.url), []), task)
        self._changed.set()

    def pending(self) -> int:
        return sum(len(q) for q in self._queues.values())

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def close(self) -> None:
        self.closed = True
        self._changed.set()

    def retain(self, keep) -> int:
        """Drop queued tasks for which ``keep(task)`` is false; returns how many were dropped."""
        dropped = 0
        for host in list(self._queues):
            kept = [t for t in self._queues[host] if keep(t)]
            dropped += len(self._queues[host]) - len(kept)
            if kept:
                heapq.heapify(kept)
                self._queues[host] = kept
            else:
                del self._queues[host]
        self._changed.set()
        return dropped

    def drop_host(self, host: str) -> int:
        q = self._queues.pop(host, [])
        return len(q)

    async def get(self) -> Optional[CrawlTask]:
        while True:
            if self.closed:
                return None
            now = time.monotonic()
            best: Optional[CrawlTask] = None
            best_host = ""
            earliest: Optional[float] = None
            for host, heap in self._queues.items():
                if not heap or host in self._busy_hosts:
                    continue
                t = self.rate.next_allowed(host)
                if t <= now:
                    if best is None or heap[0] < best:
                        best, best_host = heap[0], host
                elif earliest is None or t < earliest:
                    earliest = t
            if best is not None:
                heapq.heappop(self._queues[best_host])
                if not self._queues[best_host]:
                    del self._queues[best_host]
                self._busy_hosts.add(best_host)
                self._in_flight += 1
                self.rate.mark(best_host)
                return best
            if self._in_flight == 0 and not any(self._queues.values()):
                return None
            self._changed.clear()
            timeout = max(0.01, earliest - now) if earliest is not None else 1.0
            try:
                await asyncio.wait_for(self._changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    def done(self, task: CrawlTask) -> None:
        self._busy_hosts.discard(host_of(task.url))
        self._in_flight -= 1
        self._changed.set()
