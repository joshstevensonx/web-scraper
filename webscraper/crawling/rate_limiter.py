"""Per-host politeness: minimum delay between requests, with adaptive back-off."""

from __future__ import annotations

import time
from typing import Dict


class RateLimiter:
    def __init__(self, default_delay: float = 1.0):
        self.default_delay = max(0.0, default_delay)
        self._delay: Dict[str, float] = {}
        self._last: Dict[str, float] = {}
        self._penalty_until: Dict[str, float] = {}
        self.rate_limited_events = 0

    def delay_for(self, host: str) -> float:
        return self._delay.get(host, self.default_delay)

    def set_delay(self, host: str, delay: float) -> None:
        """Raise the delay for a host (robots.txt crawl-delay or source profile); never lowers it."""
        self._delay[host] = max(self.delay_for(host), min(float(delay), 60.0))

    def next_allowed(self, host: str) -> float:
        last = self._last.get(host)
        t = 0.0 if last is None else last + self.delay_for(host)
        return max(t, self._penalty_until.get(host, 0.0))

    def mark(self, host: str) -> None:
        self._last[host] = time.monotonic()

    def penalize(self, host: str, seconds: float) -> None:
        """Called on HTTP 429/503: slow this host down and pause it briefly."""
        self.rate_limited_events += 1
        self._delay[host] = min(max(self.delay_for(host) * 2, 2.0), 30.0)
        self._penalty_until[host] = time.monotonic() + max(1.0, seconds)
