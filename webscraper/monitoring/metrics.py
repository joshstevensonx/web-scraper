"""Job metrics shown on the dashboard (section 27)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class JobMetrics:
    state: str = "CREATED"
    phase: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    target_minimum: int = 0
    target_maximum: int = 0
    queries_run: int = 0
    search_results: int = 0
    sources_discovered: int = 0
    sources_active: int = 0
    sources_blocked: int = 0
    urls_discovered: int = 0
    urls_queued: int = 0
    pages_fetched: int = 0
    pages_ok: int = 0
    pages_failed: int = 0
    pages_rendered: int = 0
    robots_blocked: int = 0
    rate_limited: int = 0
    bytes_downloaded: int = 0
    records_extracted: int = 0
    records_total: int = 0
    records_valid: int = 0
    records_rejected: int = 0
    duplicates_removed: int = 0
    final_unique: int = 0
    average_quality: float = 0.0
    current_source: str = ""
    page_budget: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return round(100.0 * self.pages_ok / self.pages_fetched, 1) if self.pages_fetched else 0.0

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            del self.warnings[:-20]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["success_rate"] = self.success_rate
        d["elapsed"] = self.elapsed
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobMetrics":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in known})
