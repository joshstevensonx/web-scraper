"""Live terminal dashboard for ``webscraper run`` (section 27)."""

from __future__ import annotations

import shutil
import sys
import threading
import time
from typing import List, Optional

from .metrics import JobMetrics

_COLORS = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return "\033[%sm%s\033[0m" % (code, text) if _COLORS else text


def _bar(done: int, total: int, width: int = 28) -> str:
    total = max(1, total)
    filled = int(width * min(1.0, done / total))
    return "█" * filled + "░" * (width - filled)


def _fmt_secs(s: float) -> str:
    s = int(s)
    return "%dm%02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s


class ConsoleDashboard:
    def __init__(self, title: str, quiet: bool = False):
        self.title = title
        self.quiet = quiet
        self.tty = sys.stdout.isatty()
        self._lines = 0
        self._last = 0.0
        self._lock = threading.Lock()
        self._metrics: Optional[JobMetrics] = None
        self._log: List[str] = []

    def event(self, level: str, message: str) -> None:
        if self.quiet and level not in ("warning", "error"):
            return
        prefix = {"warning": _c("33", "!"), "error": _c("31", "✗")}.get(level, _c("36", "•"))
        line = "%s %s" % (prefix, message)
        with self._lock:
            self._clear()
            print(line, flush=True)
            self._draw()

    def progress(self, m: JobMetrics) -> None:
        self._metrics = m
        now = time.monotonic()
        if self.quiet:
            return
        if not self.tty:
            if now - self._last > 10:
                self._last = now
                print("[%s] %d/%d valid records, %d pages, %s" % (m.state, m.records_valid, m.target_maximum,
                                                                 m.pages_fetched, _fmt_secs(m.elapsed)), flush=True)
            return
        if now - self._last < 0.3:
            return
        self._last = now
        with self._lock:
            self._clear()
            self._draw()

    def _clear(self) -> None:
        if self.tty and self._lines:
            sys.stdout.write("\033[%dA\033[J" % self._lines)
            self._lines = 0

    def _draw(self) -> None:
        m = self._metrics
        if m is None or not self.tty or self.quiet:
            return
        width = min(shutil.get_terminal_size((80, 20)).columns, 100)
        goal = m.target_maximum
        lines = [
            _c("1", self.title[: width - 2]),
            "Status   %s  %s" % (_c("32", m.state.replace("_", " ").title()), _c("2", m.phase[: width - 30])),
            "Records  %s %d / %d  (min %d)" % (_bar(m.records_valid, goal), m.records_valid, goal, m.target_minimum),
            "Pages    %d fetched · %d queued · %.1f%% ok · budget %d" % (m.pages_fetched, m.urls_queued,
                                                                        m.success_rate, m.page_budget),
            "Quality  avg %.0f · %d duplicates · %d rejected · %d sources" % (m.average_quality, m.duplicates_removed,
                                                                             m.records_rejected, m.sources_discovered),
            _c("2", "Source   %s · %s elapsed" % (m.current_source[:40] or "-", _fmt_secs(m.elapsed))),
        ]
        sys.stdout.write("\n".join(l[: width + 20] for l in lines) + "\n")
        sys.stdout.flush()
        self._lines = len(lines)

    def finish(self) -> None:
        with self._lock:
            self._clear()
