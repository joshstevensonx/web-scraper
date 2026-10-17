"""Job lifecycle states (section 26)."""

from __future__ import annotations

from enum import Enum


class JobState(str, Enum):
    CREATED = "CREATED"
    CONFIGURING = "CONFIGURING"
    VALIDATING = "VALIDATING"
    DISCOVERING_SOURCES = "DISCOVERING_SOURCES"
    DISCOVERING_URLS = "DISCOVERING_URLS"
    SCRAPING = "SCRAPING"
    EXTRACTING = "EXTRACTING"
    NORMALIZING = "NORMALIZING"
    VALIDATING_RECORDS = "VALIDATING_RECORDS"
    DEDUPLICATING = "DEDUPLICATING"
    CHECKING_TARGET_COUNT = "CHECKING_TARGET_COUNT"
    EXPORTING = "EXPORTING"
    COMPLETED = "COMPLETED"
    # failure and control states
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RATE_LIMITED = "RATE_LIMITED"
    SOURCE_BLOCKED = "SOURCE_BLOCKED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    INTERRUPTED = "INTERRUPTED"  # the app stopped while the job was running

    @property
    def is_final(self) -> bool:
        return self in FINAL_STATES

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


FINAL_STATES = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED, JobState.PARTIALLY_COMPLETED,
                JobState.SOURCE_BLOCKED, JobState.INTERRUPTED}
