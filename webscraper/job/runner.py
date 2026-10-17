"""Job runner: executes the full pipeline for one scraping job (sections 1, 26, 34).

INPUT → target → source discovery → URL discovery → crawl/fetch → extraction → normalization →
validation → deduplication → record limit / stop condition → output.

The runner is asynchronous (``httpx`` + ``asyncio``); :meth:`JobRunner.run_sync` runs it in the
calling thread with its own event loop, which is how the CLI and the web UI use it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from .. import config as app_config
from ..crawling.browser import BrowserRenderer, playwright_available
from ..crawling.fetcher import DEFAULT_USER_AGENT, FetchResult, Fetcher
from ..crawling.frontier import CrawlTask, Frontier
from ..crawling.rate_limiter import RateLimiter
from ..crawling.robots import RobotsCache
from ..discovery.sitemap import parse_feed, parse_sitemap
from ..discovery.url_classifier import classify_url, is_pagination_link, looks_like_list_title
from ..extraction.field_extractor import ROLE_JSONLD_PATHS, CandidateRecord, ExtractContext, PageExtractor
from ..extraction.page import Page
from ..extraction.patterns import is_entity_candidate_domain
from ..extraction.structured_data import get_path, scalar
from ..monitoring.metrics import JobMetrics
from ..output.writers import export
from ..processing.normalization import Normalizer
from ..processing.records import RecordBuilder, RecordStore
from ..sources.discovery import rank_sources
from ..sources.search import make_provider, run_queries
from ..storage.database import Database, now_iso
from ..target.query_generator import generate_queries
from ..util import module_available
from ..util.countries import country_code
from ..util.text import clean_whitespace, fold_words
from ..util.urls import host_of, normalize_url, origin, registered_domain
from .models import JobConfig, SourceProfile
from .state import JobState

log = logging.getLogger("webscraper")

# Pages on a company's own site worth visiting to complete its record.
ABOUT_PAGE = re.compile(r"/(?:about(?:-us)?|company|who-?we-?are|our-?story|team|impressum|imprint)/?$")


class JobControl:
    """Thread-safe pause / resume / cancel flags shared with the UI or CLI."""

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._pause = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()


@dataclass
class JobOutcome:
    job_id: str
    state: JobState
    records: List[Dict[str, Any]]
    outputs: List[str]
    metrics: JobMetrics
    error: Optional[str] = None
    sources: List[Dict[str, Any]] = field(default_factory=list)


class JobRunner:
    def __init__(self, job: JobConfig, db: Optional[Database] = None, control: Optional[JobControl] = None,
                 on_event: Optional[Callable[[str, str], None]] = None,
                 on_progress: Optional[Callable[[JobMetrics], None]] = None,
                 output_dir: Optional[str] = None, raw_config: Optional[Dict[str, Any]] = None,
                 transport: Any = None):
        self.job = job
        self.db = db
        self.control = control or JobControl()
        self.on_event = on_event
        self.on_progress = on_progress
        self.output_dir = output_dir or job.output.directory or app_config.output_dir()
        self.raw_config = raw_config or job.to_dict()
        self.transport = transport  # custom httpx transport (used by the test-suite)
        self.metrics = JobMetrics(target_minimum=job.records.minimum, target_maximum=job.records.maximum,
                                  page_budget=job.page_budget)
        self.state = JobState.CREATED
        self.sources: List[SourceProfile] = []
        self.source_stats: Dict[int, Dict[str, int]] = {}
        self.records_out: List[Dict[str, Any]] = []
        self.outputs: List[str] = []
        self.mode = job.record_mode
        self._last_progress = 0.0
        self._last_db_metrics = 0.0
        self._raw_buffer: List[Dict[str, Any]] = []
        self._host_failures: Dict[str, int] = {}
        self._blocked_hosts: set = set()
        self._enrich_counts: Dict[str, int] = {}
        self._crawl_delay_checked: set = set()
        self._target_reached = False
        self._start = time.monotonic()
        self._queries: List[str] = []
        self._query_cursor = 0
        self._sitemaps_tried: set = set()
        self._renderer_warned = False
        self._resume_state = JobState.SCRAPING
        self._provider = None
        loc = job.targeting.location
        self.region = country_code(loc.country[0]) if loc.country else None
        self.normalizer = Normalizer(self.region)
        self.store = RecordStore(job, self.normalizer)
        self.extractor = PageExtractor(job)
        self.logger = self._make_logger()

    # ------------------------------------------------------------------------------------------
    # Events, state and progress
    # ------------------------------------------------------------------------------------------
    def _make_logger(self) -> logging.Logger:
        logger = logging.getLogger("webscraper.job.%s" % self.job.id)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            try:
                path = os.path.join(app_config.logs_dir(), "%s.log" % self.job.id)
                handler = logging.FileHandler(path, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
                logger.addHandler(handler)
            except OSError:
                pass
        return logger

    def event(self, message: str, level: str = "info") -> None:
        getattr(self.logger, level if level in ("info", "warning", "error", "debug") else "info")(message)
        if level == "warning":
            self.metrics.warn(message)
        if self.db is not None and level != "debug":
            try:
                self.db.add_event(self.job.id, level, message)
            except Exception:
                pass
        if self.on_event is not None and level != "debug":
            self.on_event(level, message)

    def set_state(self, state: JobState, phase: str = "") -> None:
        self.state = state
        self.metrics.state = state.value
        self.metrics.phase = phase or state.label
        if self.db is not None:
            self.db.update_job(self.job.id, state=state.value, metrics=self.metrics.to_dict())
        self.event("State: %s%s" % (state.value, (" – " + phase) if phase else ""), "debug")
        self.progress(force=True)

    def progress(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_progress < 0.5:
            return
        self._last_progress = now
        counts = self.store.counts()
        m = self.metrics
        m.records_extracted = counts["records_extracted"]
        m.records_total = counts["records_total"]
        m.records_valid = counts["records_valid"]
        m.records_rejected = counts["records_rejected"]
        m.duplicates_removed = counts["duplicates_removed"]
        m.average_quality = counts["average_quality"]
        if hasattr(self, "frontier"):
            m.urls_discovered = self.frontier.discovered
            m.urls_queued = self.frontier.pending()
            if self.state == JobState.SCRAPING:
                m.phase = "Crawling · %d queued · %d in progress" % (m.urls_queued, self.frontier.in_flight)
        if hasattr(self, "rate"):
            m.rate_limited = self.rate.rate_limited_events
        if hasattr(self, "robots"):
            m.robots_blocked = self.robots.blocked_urls
        if self.on_progress is not None:
            self.on_progress(m)
        if self.db is not None and (force or now - self._last_db_metrics > 2.0):
            self._last_db_metrics = now
            try:
                self.db.update_job(self.job.id, metrics=m.to_dict())
            except Exception:
                pass

    # ------------------------------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------------------------------
    def run_sync(self) -> JobOutcome:
        return asyncio.run(self.run())

    async def run(self) -> JobOutcome:
        error: Optional[str] = None
        self.metrics.started_at = time.time()
        if self.db is not None:
            if self.db.get_job(self.job.id) is None:
                self.db.create_job(self.job.id, self.job.name, self.job.entity, self.raw_config)
            self.db.update_job(self.job.id, started_at=now_iso(), error=None)
        self.event("Job started: %s" % self.job.name)
        try:
            await self._pipeline()
        except asyncio.CancelledError:
            self.control.cancel()
            error = "cancelled"
        except Exception as exc:  # unexpected failure: keep what we have
            error = "%s: %s" % (type(exc).__name__, exc)
            self.event("Job failed: %s\n%s" % (error, traceback.format_exc()), "error")
            try:
                self._finalize_records()
                self._save_records()
            except Exception:
                pass
            self.set_state(JobState.FAILED, error)
        finally:
            await self._close()
        self.metrics.finished_at = time.time()
        self.progress(force=True)
        if self.db is not None:
            self.db.update_job(self.job.id, finished_at=now_iso(), metrics=self.metrics.to_dict(),
                               outputs=self.outputs, error=error)
        return JobOutcome(self.job.id, self.state, self.records_out, self.outputs, self.metrics, error,
                          self._source_summary())

    async def _close(self) -> None:
        self._flush_raw(force=True)
        for name in ("renderer", "fetcher"):
            obj = getattr(self, name, None)
            if obj is not None:
                try:
                    await obj.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------------------------------
    async def _pipeline(self) -> None:
        job = self.job
        self.set_state(JobState.VALIDATING)
        self._check_environment()

        self.rate = RateLimiter(job.crawl.delay)
        self.fetcher = Fetcher(job.crawl.user_agent or DEFAULT_USER_AGENT, job.crawl.timeout, job.crawl.retries,
                               job.crawl.max_response_bytes, max_connections=job.crawl.concurrency * 2,
                               on_rate_limited=self._on_rate_limited, transport=self.transport)
        self.robots = RobotsCache(self.fetcher, job.crawl.respect_robots)
        self.frontier = Frontier(self.rate, job.crawl.max_pages_per_domain)
        self.renderer: Optional[BrowserRenderer] = None
        if job.crawl.render_js != "never" and playwright_available():
            self.renderer = BrowserRenderer(self.fetcher.user_agent, job.crawl.timeout)

        self.set_state(JobState.DISCOVERING_SOURCES)
        for src in job.sources.urls:
            self._add_source(src)
        if job.sources.mode in ("auto", "hybrid"):
            self._queries = generate_queries(job)
            await self._discover_sources()
        self.metrics.sources_discovered = len(self.sources)
        if not self.sources:
            self.event("No sources found. Add source URLs or adjust the target description.", "warning")

        self.set_state(JobState.DISCOVERING_URLS)
        for i in range(len(self.sources)):
            self._seed_source(i)

        rounds = 0
        while True:
            self.set_state(JobState.SCRAPING, "Crawling %d queued URL(s)" % self.frontier.pending())
            await self._crawl()
            rounds += 1
            if self.control.cancelled or self._target_reached or self._budget_exhausted() or self._timed_out():
                break
            if self.store.valid_count() >= job.records.minimum and job.records.stop_at_minimum:
                break
            if self.store.valid_count() >= job.records.maximum or rounds >= 4:
                break
            added = await self._expand()
            if not added:
                break

        self.set_state(JobState.NORMALIZING)
        self.store.evaluate_all()
        self.set_state(JobState.VALIDATING_RECORDS)
        self.set_state(JobState.DEDUPLICATING)
        merged = self.store.fuzzy_dedupe()
        if merged:
            self.event("Fuzzy de-duplication merged %d record(s)" % merged)
        self.set_state(JobState.CHECKING_TARGET_COUNT)
        self._report_rejections()
        self._finalize_records()
        valid = sum(1 for r in self.records_out if r["valid"])
        self.metrics.final_unique = valid
        self._save_records()

        self.set_state(JobState.EXPORTING)
        if self.records_out or job.output.include_rejected:
            try:
                self.outputs = export(job, self.records_out, self.output_dir, summary=self._summary())
                for p in self.outputs:
                    self.event("Saved %s" % p)
            except Exception as exc:
                self.event("Export failed: %s" % exc, "error")
                raise
        else:
            self.event("No valid records – nothing to export", "warning")

        if self.control.cancelled:
            final = JobState.CANCELLED
        elif valid == 0 and self.sources and self.metrics.sources_blocked >= len(self.sources):
            final = JobState.SOURCE_BLOCKED
        elif valid >= job.records.minimum:
            final = JobState.COMPLETED
        else:
            final = JobState.PARTIALLY_COMPLETED
            self.event("Collected %d of the %d records requested (minimum). Sources, page budget or targeting "
                       "limited the result." % (valid, job.records.minimum), "warning")
        self.set_state(final)
        self.event("Finished: %d valid record(s), %d page(s) fetched in %.0fs" %
                   (valid, self.metrics.pages_fetched, self.metrics.elapsed))

    def _report_rejections(self) -> None:
        """Summarize why records were rejected so a mis-specified target is easy to spot."""
        counts: Dict[str, int] = {}
        total = 0
        for b in self.store.all_builders():
            if b.result is None or b.result.valid:
                continue
            total += 1
            for reason in b.result.reasons[:2]:
                counts[reason] = counts.get(reason, 0) + 1
        if not total:
            return
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        level = "warning" if total > self.store.valid_count() else "info"
        self.event("Rejected %d record(s). Most common reasons: %s" %
                   (total, "; ".join("%s (%d)" % (r, n) for r, n in top)), level)

    def _check_environment(self) -> None:
        fmts = self.job.output.all_formats()
        if "parquet" in fmts and not module_available("pyarrow"):
            raise RuntimeError("Parquet output needs pyarrow (pip install pyarrow)")
        if self.job.crawl.render_js == "always" and not playwright_available():
            self.event("render_js is 'always' but Playwright is not installed; falling back to HTTP fetching. "
                       "Install with: pip install playwright && python -m playwright install chromium", "warning")

    # ------------------------------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------------------------------
    def _add_source(self, profile: SourceProfile) -> int:
        url = normalize_url(profile.url) or profile.url
        for i, s in enumerate(self.sources):
            if s.url == url:
                return i
        profile.url = url
        self.sources.append(profile)
        idx = len(self.sources) - 1
        self.source_stats[idx] = {"pages": 0, "records": 0, "failed": 0}
        return idx

    async def _discover_sources(self) -> int:
        job = self.job
        if self._provider is None:
            self._provider = make_provider(job.sources.search_provider, self.fetcher, self.region)
        provider = self._provider
        if provider is not None and provider.blocked:
            return 0
        if provider is None or not job.sources.discover_search_engines:
            if job.sources.mode == "auto" and not self.sources:
                self.event("Automatic discovery is disabled and no sources were provided.", "warning")
            return 0
        batch = self._queries[self._query_cursor:self._query_cursor + job.sources.max_queries]
        self._query_cursor += len(batch)
        if not batch:
            return 0
        self.metrics.phase = "Searching (%s)" % provider.name
        self.event("Searching %s with %d quer%s" % (provider.name, len(batch), "y" if len(batch) == 1 else "ies"))

        def on_error(q: str, msg: str) -> None:
            self.event("Search failed for %r: %s" % (q, msg), "warning")

        results = await run_queries(provider, batch, job.sources.results_per_query, on_error,
                                    should_stop=lambda: self.control.cancelled)
        self.metrics.queries_run += len(batch)
        self.metrics.search_results += len(results)
        ranked = rank_sources(job, results)
        before = len(self.sources)
        for s in ranked:
            self._add_source(s.profile)
        added = len(self.sources) - before
        dirs = sum(1 for s in ranked if s.kind == "directory")
        self.event("Discovered %d source(s) from %d search result(s): %d directories/lists, %d websites" %
                   (added, len(results), dirs, len(ranked) - dirs))
        self.progress(force=True)
        return added

    def _seed_source(self, idx: int) -> None:
        src = self.sources[idx]
        url = src.url
        stype = src.type
        if stype == "auto" and re.search(r"\.xml(\.gz)?$|sitemap", url, re.I):
            stype = "sitemap"
        kind = {"sitemap": "sitemap", "feed": "feed", "api": "api", "directory": "listing", "listing": "listing",
                "detail": "detail"}.get(stype, "seed")
        group = None
        if stype == "website":
            kind = "entity_home" if self.mode == "site" else "seed"
            group = "domain:" + registered_domain(url) if self.mode == "site" else None
        boost = (src.score - 0.5) + (5 - src.priority) * 0.1
        task = self.frontier.make_task(url, kind, 0, idx, group, None, boost)
        if src.rate_limit:
            self.rate.set_delay(host_of(url), src.rate_limit)
        self.frontier.add(task, force=True)

    async def _expand(self) -> int:
        """Below the minimum after the queue drained: run more queries / read sitemaps."""
        added = 0
        if self.job.sources.mode in ("auto", "hybrid") and self._query_cursor < len(self._queries):
            self.set_state(JobState.DISCOVERING_SOURCES, "More sources needed")
            before = len(self.sources)
            await self._discover_sources()
            for i in range(before, len(self.sources)):
                self._seed_source(i)
                added += 1
        if self.job.sources.use_sitemaps:
            for i, src in enumerate(self.sources):
                host = host_of(src.url)
                if src.origin != "user" or src.type in ("website", "api", "feed", "sitemap") or \
                        host in self._sitemaps_tried:
                    continue
                if self.mode == "site" and src.type not in ("directory", "listing", "auto"):
                    continue
                self._sitemaps_tried.add(host)
                maps = []
                try:
                    maps = await self.robots.sitemaps(src.url)
                except Exception:
                    maps = []
                if not maps:
                    maps = [origin(src.url) + "sitemap.xml"]
                for sm in maps[:5]:
                    if self.frontier.add(self.frontier.make_task(sm, "sitemap", 1, i)):
                        added += 1
            if added:
                self.event("Reading sitemaps to find more pages")
        if added:
            self.set_state(JobState.DISCOVERING_URLS)
        return added

    def _source_summary(self) -> List[Dict[str, Any]]:
        out = []
        for i, s in enumerate(self.sources):
            st = self.source_stats.get(i, {})
            status = "blocked" if host_of(s.url) in self._blocked_hosts else ("ok" if st.get("pages") else "pending")
            out.append({"index": i, "url": s.url, "name": s.name or registered_domain(s.url), "type": s.type,
                        "origin": s.origin, "score": s.score, "pages": st.get("pages", 0),
                        "records": st.get("records", 0), "status": status})
        return out

    # ------------------------------------------------------------------------------------------
    # Crawl loop
    # ------------------------------------------------------------------------------------------
    def _budget_exhausted(self) -> bool:
        return self.metrics.pages_fetched >= self.job.page_budget

    def _timed_out(self) -> bool:
        limit = self.job.crawl.max_runtime_minutes
        return bool(limit and (time.monotonic() - self._start) > limit * 60)

    def _goal(self) -> int:
        r = self.job.records
        return r.minimum if r.stop_at_minimum else r.maximum

    def _check_target(self) -> None:
        if self._target_reached:
            return
        if self.store.valid_count() >= self._goal():
            # Make sure near-duplicates are not inflating the count before stopping.
            self.store.fuzzy_dedupe()
            if self.store.valid_count() >= self._goal():
                self._target_reached = True
                self.event("Record target reached (%d valid unique records)" % self.store.valid_count())
                if self.mode == "site":
                    # Let already-queued contact/about pages of found records finish enriching them.
                    dropped = self.frontier.retain(self._is_enrichment_task)
                    self.event("Finishing %d enrichment page(s); skipped %d queued URL(s)" %
                               (self.frontier.pending(), dropped), "debug")
                else:
                    self.frontier.close()

    def _is_enrichment_task(self, task: CrawlTask) -> bool:
        if task.kind not in ("entity_contact", "entity_home") or not task.group_key:
            return False
        b = self.store.find(task.group_key)
        return b is not None and b.result is not None and b.result.valid

    async def _crawl(self) -> None:
        self.frontier.closed = False
        if self._target_reached:
            return
        workers = [asyncio.ensure_future(self._worker()) for _ in range(self.job.crawl.concurrency)]
        try:
            await asyncio.gather(*workers)
        finally:
            for w in workers:
                if not w.done():
                    w.cancel()
        self._flush_raw(force=True)
        self.progress(force=True)

    async def _worker(self) -> None:
        while True:
            if self.control.cancelled or self._budget_exhausted() or self._timed_out():
                if self._budget_exhausted() and not self.control.cancelled:
                    self.metrics.warn("Page budget of %d pages reached" % self.job.page_budget)
                self.frontier.close()
                return
            if self.control.paused:
                if self.state != JobState.PAUSED:
                    self._resume_state = self.state
                    self.set_state(JobState.PAUSED)
                    self.event("Paused")
                await asyncio.sleep(0.4)
                continue
            if self.state == JobState.PAUSED:
                self.set_state(self._resume_state)
                self.event("Resumed")
            task = await self.frontier.get()
            if task is None:
                return
            if self._target_reached and self.mode == "site" and not self._is_enrichment_task(task):
                self.frontier.done(task)
                continue
            try:
                await self._process(task)
            except Exception as exc:
                self.event("Error processing %s: %s" % (task.url, exc), "warning")
                self.logger.debug(traceback.format_exc())
            finally:
                self.frontier.done(task)
            self._check_target()
            self.progress()

    def _on_rate_limited(self, url: str, wait: float) -> None:
        self.rate.penalize(host_of(url), wait)
        self.event("Rate limited by %s – backing off %.0fs" % (host_of(url), wait), "warning")

    async def _fetch(self, task: CrawlTask, render: bool = False) -> FetchResult:
        if render and self.renderer is not None:
            res = await self.renderer.render(task.url)
            if res.ok:
                self.metrics.pages_rendered += 1
                return res
            if not self._renderer_warned:
                self._renderer_warned = True
                self.event("Browser rendering failed (%s); using plain HTTP" % res.error, "warning")
        return await self.fetcher.fetch(task.url)

    async def _process(self, task: CrawlTask) -> None:
        host = host_of(task.url)
        if host in self._blocked_hosts:
            return
        src = self.sources[task.source_index] if task.source_index < len(self.sources) else SourceProfile(url=task.url)
        if not await self.robots.allowed(task.url):
            self.event("robots.txt disallows %s" % task.url, "debug")
            return
        if host not in self._crawl_delay_checked:
            self._crawl_delay_checked.add(host)
            delay = await self.robots.crawl_delay(task.url)
            if delay:
                self.rate.set_delay(host, delay)
        self.metrics.current_source = src.name or registered_domain(src.url)

        if task.kind == "api":
            await self._process_api(task, src)
            return
        render_mode = src.render_js or self.job.crawl.render_js
        res = await self._fetch(task, render=render_mode == "always")
        self.metrics.pages_fetched += 1
        stats = self.source_stats.setdefault(task.source_index, {"pages": 0, "records": 0, "failed": 0})
        stats["pages"] += 1
        self._record_raw(task, res)
        if not res.ok:
            self.metrics.pages_failed += 1
            stats["failed"] += 1
            self._handle_failure(task, res)
            return
        self._host_failures[host] = 0
        self.metrics.pages_ok += 1
        self.metrics.bytes_downloaded += len(res.content) or len(res.text)
        final_url = normalize_url(res.final_url or task.url) or task.url
        if final_url != task.url:
            self.frontier.mark_seen(final_url)
        if final_url.startswith("https://"):
            self.normalizer.https_hosts.add(host_of(final_url))

        head = res.text[:2000]
        if task.kind == "sitemap" or (task.kind == "seed" and ("<urlset" in head or "<sitemapindex" in head)):
            self._process_sitemap(task, res)
            return
        if task.kind == "feed" or ("xml" in res.content_type and re.search(r"<(rss|feed)\b", head)):
            self._process_feed(task, res)
            return
        if not res.is_html:
            return
        page = await asyncio.to_thread(Page, final_url, res.text, res.status, res.content_type)
        needs_js = render_mode == "auto" and not res.rendered and self._needs_browser(page, src)
        if needs_js and self.renderer is not None:
            rendered = await self._fetch(task, render=True)
            if rendered.ok and rendered.rendered:
                page = await asyncio.to_thread(Page, final_url, rendered.text, rendered.status, "text/html")
        elif needs_js and not self._renderer_warned:
            self._renderer_warned = True
            self.event("Some pages need JavaScript; install Playwright for better results "
                       "(pip install playwright && python -m playwright install chromium)", "warning")
        await self._handle_page(task, page, src)

    @staticmethod
    def _needs_browser(page: Page, src: SourceProfile) -> bool:
        """An app shell, or a page where the user's record selector only appears after scripts run."""
        if page.looks_js_rendered():
            return True
        if src.record_selector and page.script_count:
            try:
                return page.soup.select_one(src.record_selector) is None
            except Exception:
                return False
        return False

    def _handle_failure(self, task: CrawlTask, res: FetchResult) -> None:
        host = host_of(task.url)
        if res.status in (401, 403, 429, 451) or (res.status == 503 and "cloudflare" in res.headers.get("server", "")):
            self._host_failures[host] = self._host_failures.get(host, 0) + 1
            if self._host_failures[host] >= 4 and host not in self._blocked_hosts:
                self._blocked_hosts.add(host)
                dropped = self.frontier.drop_host(host)
                self.metrics.sources_blocked += 1
                self.event("Source blocked: %s refused %d requests in a row (HTTP %d); skipping %d queued URL(s)" %
                           (host, self._host_failures[host], res.status, dropped), "warning")
        if task.kind in ("seed", "listing", "entity_home") and task.depth == 0:
            self.event("Could not fetch %s: %s" % (task.url, res.error or "HTTP %d" % res.status), "warning")

    # ------------------------------------------------------------------------------------------
    # Page handling
    # ------------------------------------------------------------------------------------------
    def _seed_kind(self, page: Page, src: SourceProfile, items: int, listing_score: float) -> str:
        """Decide what an untyped seed URL is: a directory/listing or an entity's own website.

        A bare homepage is treated as the entity's own website (companies link to partners and
        sponsors all the time); deeper URLs are directories when the page says so.
        """
        url_kind = classify_url(page.url)
        list_title = looks_like_list_title(page.title)
        if items >= 2 or src.record_selector or (listing_score >= 0.5 and list_title):
            return "listing"
        if url_kind == "home":
            return "entity_home"
        if url_kind in ("listing", "search", "pagination"):
            return "listing"
        detail_links = sum(1 for l in page.links if not l.external and not l.in_boilerplate and
                           classify_url(l.url) == "detail")
        if detail_links >= 8 and (list_title or listing_score < 0.3):
            return "listing"
        return "entity_home"

    def _detail_match(self, src: SourceProfile, url: str) -> bool:
        return bool(src.detail_link_pattern and re.search(src.detail_link_pattern, url))

    async def _handle_page(self, task: CrawlTask, page: Page, src: SourceProfile) -> None:
        job = self.job
        kind = task.kind
        url_kind = classify_url(page.url)
        prelim = await asyncio.to_thread(self._prelim, page)
        entity_items, listing_score = prelim

        if self.mode == "site":
            if kind == "seed":
                kind = self._seed_kind(page, src, entity_items, listing_score)
                if kind == "entity_home":
                    task.group_key = task.group_key or "domain:" + page.domain
                if src.type == "auto":  # remember what this source turned out to be
                    src.type = "website" if kind == "entity_home" else "directory"
            if kind == "entity_home" and listing_score >= 0.6 and looks_like_list_title(page.title) and \
                    task.group_key and task.depth == 0:
                kind = "listing"  # a "Top 20 companies" article, not a company website
                task.group_key = None
            if kind in ("entity_home", "entity_contact"):
                ctx = ExtractContext(role="entity", source=src, is_home=kind == "entity_home")
                want: Any = True
            else:
                ctx = ExtractContext(role="directory", source=src)
                want = kind == "detail" or url_kind == "detail" or self._detail_match(src, page.url)
        else:
            ctx = ExtractContext(role="page", source=src)
            if src.record_selector and page.soup.select_one(src.record_selector) is not None:
                want = False  # the repeating containers are the records
            elif job.entity == "generic" or kind == "detail" or src.type == "detail" or \
                    self._detail_match(src, page.url):
                want = True
            elif url_kind == "detail" and not src.detail_link_pattern and not src.detail_link_selector:
                want = True
            elif url_kind in ("other", "article") or (task.depth == 0 and src.type == "auto" and url_kind == "detail"):
                want = "auto"  # a record only if the user's selectors match on this page
            else:
                want = False  # home, listing, pagination and search pages are not records themselves

        extraction = await asyncio.to_thread(self.extractor.extract, page, ctx, want)
        stats = self.source_stats.setdefault(task.source_index, {"pages": 0, "records": 0, "failed": 0})
        meta_desc = ""
        desc_tag = page.soup.find("meta", attrs={"name": "description"})
        if desc_tag is not None:
            meta_desc = clean_whitespace(desc_tag.get("content") or "")
        for cand in extraction.candidates:
            group = self._group_key(cand, task, page, ctx)
            if cand.kind == "page":
                match_text = page.title + " " + page.text[:60_000]
                context = page.title + " " + meta_desc
            else:
                match_text = cand.text + " " + page.title
                context = cand.text[:300]
            independent = kind != "entity_contact" and not (task.data or {}).get("enrich")
            builder = self.store.add(cand, group, match_text, context, src.score, independent)
            stats["records"] += 1
            self._maybe_enrich(builder, cand, task, ctx)

        self._enqueue_links(task, kind, page, src, ctx, extraction.listing_score, extraction.external_entity_links)

    def _prelim(self, page: Page):
        items = self.extractor.matching_entities(self.extractor.structured_items(page))
        ext = set()
        for l in page.links:
            if l.external and not l.in_boilerplate and is_entity_candidate_domain(l.url):
                ext.add(registered_domain(l.url))
        return len(items), min(1.0, len(ext) / 10.0)

    def _group_key(self, cand: CandidateRecord, task: CrawlTask, page: Page, ctx: ExtractContext) -> Optional[str]:
        if self.mode == "site":
            if ctx.role == "entity":
                return task.group_key or "domain:" + page.domain
            website = self._best_website(cand)
            if website:
                return "domain:" + registered_domain(website)
            return "url:" + (cand.source_url or page.url)
        if cand.kind == "page":
            return "url:" + (cand.source_url or page.url)
        if cand.group_hint or (cand.source_url and cand.source_url != page.url):
            return "url:" + (cand.group_hint or cand.source_url)
        return self._content_key(cand)

    @staticmethod
    def _content_key(cand: CandidateRecord) -> str:
        """Fingerprint of a listing item without its own URL (identical items merge)."""
        parts = []
        for name in sorted(cand.fields):
            best = max(cand.fields[name], key=lambda f: f.confidence)
            parts.append("%s=%s" % (name, fold_words(str(best.value))[:200]))
        return "content:" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]

    def _best_website(self, cand: CandidateRecord) -> Optional[str]:
        for spec in self.job.fields:
            if spec.role != "website":
                continue
            for fv in sorted(cand.fields.get(spec.name, []), key=lambda f: -f.confidence):
                v = str(fv.value)
                u = normalize_url(v if "://" in v else "http://" + v)
                if u and is_entity_candidate_domain(u) and fv.confidence >= 0.7:
                    return u
        return None

    def _maybe_enrich(self, builder: RecordBuilder, cand: CandidateRecord, task: CrawlTask, ctx: ExtractContext) -> None:
        """Directory record with a website: visit the company site to fill missing fields."""
        if self.mode != "site" or ctx.role == "entity" or not self.job.crawl.enrich_from_website:
            return
        if builder.enrich_requested:
            return
        website = self._best_website(cand)
        if not website:
            return
        key = "domain:" + registered_domain(website)
        builder.enrich_requested = True
        if key not in builder.keys:
            builder.keys.add(key)
            self.store.key_index[key] = builder.id
        home = origin(website)
        t = self.frontier.make_task(home, "entity_home", task.depth + 1, task.source_index, key, task.url,
                                    data={"enrich": True})
        self.frontier.add(t)

    def _enqueue_links(self, task: CrawlTask, kind: str, page: Page, src: SourceProfile, ctx: ExtractContext,
                       listing_score: float, external_links) -> None:
        job = self.job
        max_depth = src.max_depth if src.max_depth is not None else job.crawl.max_depth
        src_host = host_of(src.url)
        src_domain = registered_domain(src.url)

        if ctx.role == "entity":
            if kind != "entity_home" or not task.group_key:
                return
            budget = job.crawl.enrich_pages - self._enrich_counts.get(task.group_key, 0)
            if budget <= 0:
                return
            picked = []
            for link in page.links:
                if link.external or registered_domain(link.url) != page.domain:
                    continue
                k = classify_url(link.url)
                path = urlsplit(link.url).path.lower()
                if k == "contact" or (k == "about" and ABOUT_PAGE.search(path)):
                    if link.url not in picked:
                        picked.append(link.url)
            picked.sort(key=lambda u: 0 if classify_url(u) == "contact" else 1)
            for u in picked[:budget]:
                if self.frontier.add(self.frontier.make_task(u, "entity_contact", task.depth + 1, task.source_index,
                                                             task.group_key, page.url)):
                    self._enrich_counts[task.group_key] = self._enrich_counts.get(task.group_key, 0) + 1
            return

        page_cap = src.max_pages
        stats = self.source_stats.get(task.source_index, {})
        if page_cap and stats.get("pages", 0) >= page_cap:
            return

        detail_urls = set()
        if src.detail_link_selector:
            try:
                for a in page.soup.select(src.detail_link_selector):
                    href = a.get("href") if a.name == "a" else (a.find("a", href=True) or {}).get("href")
                    u = normalize_url(href, page.url) if href else None
                    if u:
                        detail_urls.add(u)
            except Exception as exc:
                self.event("Invalid detail_link_selector: %s" % exc, "warning")
        pagination_urls = set()
        if src.pagination_selector:
            try:
                for a in page.soup.select(src.pagination_selector):
                    href = a.get("href") if a.name == "a" else (a.find("a", href=True) or {}).get("href")
                    u = normalize_url(href, page.url) if href else None
                    if u:
                        pagination_urls.add(u)
            except Exception as exc:
                self.event("Invalid pagination_selector: %s" % exc, "warning")

        # External company websites listed on a directory / list article.
        if self.mode == "site" and job.sources.discover_company_websites and task.depth < max_depth and (
                listing_score >= 0.3 or kind == "listing"):
            for link in external_links:
                dom = registered_domain(link.url)
                if dom in job.sources.exclude_domains:
                    continue
                self.frontier.add(self.frontier.make_task(origin(link.url), "entity_home", task.depth + 1,
                                                          task.source_index, "domain:" + dom, page.url))

        for link in page.links:
            u = link.url
            if link.external:
                continue
            same = host_of(u) == src_host or registered_domain(u) == src_domain
            if not same:
                continue
            if src.exclude_pattern and re.search(src.exclude_pattern, u):
                continue
            is_detail = u in detail_urls or self._detail_match(src, u)
            is_page = u in pagination_urls or is_pagination_link(link.text, link.rel, link.classes, link.title)
            cls = classify_url(u)
            if cls == "skip" and not (is_detail or is_page):
                continue
            if src.follow_pattern and not (is_detail or is_page) and not re.search(src.follow_pattern, u):
                continue
            if link.in_boilerplate and not (is_detail or is_page) and cls != "pagination":
                continue
            if is_page or cls == "pagination":
                t_kind, depth = "pagination", task.depth
            elif is_detail or (cls == "detail" and not (src.detail_link_pattern or src.detail_link_selector)):
                t_kind, depth = "detail", task.depth + 1
            elif cls in ("listing", "search") and task.depth + 1 <= max_depth:
                t_kind, depth = "listing", task.depth + 1
            elif cls in ("other", "home") and task.depth + 1 < max_depth:
                t_kind, depth = "other", task.depth + 1
            elif cls == "article" and job.entity == "generic" and task.depth + 1 <= max_depth:
                t_kind, depth = "detail", task.depth + 1
            else:
                continue
            if depth > max_depth and t_kind != "pagination":
                continue
            self.frontier.add(self.frontier.make_task(u, t_kind, depth, task.source_index, None, page.url))

    # ------------------------------------------------------------------------------------------
    # Sitemaps, feeds, APIs
    # ------------------------------------------------------------------------------------------
    def _process_sitemap(self, task: CrawlTask, res: FetchResult) -> None:
        src = self.sources[task.source_index]
        pages, children = parse_sitemap(res.content, res.text, task.url)
        for c in children[:25]:
            self.frontier.add(self.frontier.make_task(c, "sitemap", task.depth, task.source_index))
        limit = max(0, self.job.page_budget - self.metrics.pages_fetched)
        added = 0
        for u in pages:
            if added >= limit:
                break
            if src.exclude_pattern and re.search(src.exclude_pattern, u):
                continue
            if src.follow_pattern and not re.search(src.follow_pattern, u):
                continue
            cls = classify_url(u)
            if src.detail_link_pattern:
                if not re.search(src.detail_link_pattern, u):
                    continue
            elif cls not in ("detail", "other") and not (self.mode == "site" and cls in ("home",)):
                continue
            if self.frontier.add(self.frontier.make_task(u, "detail", task.depth + 1, task.source_index)):
                added += 1
        self.event("Sitemap %s: %d URL(s), %d queued" % (task.url, len(pages), added))

    def _process_feed(self, task: CrawlTask, res: FetchResult) -> None:
        items = parse_feed(res.content, res.text, task.url)
        for u, _title in items:
            self.frontier.add(self.frontier.make_task(u, "detail", task.depth + 1, task.source_index))
        self.event("Feed %s: %d item(s)" % (task.url, len(items)))

    async def _process_api(self, task: CrawlTask, src: SourceProfile) -> None:
        res = await self.fetcher.fetch(task.url, headers=src.headers or None)
        self.metrics.pages_fetched += 1
        stats = self.source_stats.setdefault(task.source_index, {"pages": 0, "records": 0, "failed": 0})
        stats["pages"] += 1
        if not res.ok:
            self.metrics.pages_failed += 1
            self._handle_failure(task, res)
            return
        self.metrics.pages_ok += 1
        try:
            data = json.loads(res.text)
        except ValueError:
            self.event("API source %s did not return JSON" % task.url, "warning")
            return
        items = get_path(data, src.records_path) if src.records_path else data
        if isinstance(items, dict):
            items = next((v for v in items.values() if isinstance(v, list)), [items])
        if not isinstance(items, list):
            items = []
        for i, item in enumerate(items):
            cand = CandidateRecord(source_url=task.url, kind="api", structured=True)
            for spec in self.job.fields:
                path = src.field_map.get(spec.name)
                paths = [path] if path else [spec.name] + ROLE_JSONLD_PATHS.get(spec.role, [])
                for p in paths:
                    v = get_path(item, p) if isinstance(item, dict) else None
                    if v not in (None, "", [], {}):
                        cand.add(spec.name, v if spec.type in ("array", "object", "json") else scalar(v),
                                 "api:" + p, 0.95)
                        break
            if self.job.output.include_raw_data:
                cand.raw = {"api": item}
            if not cand.filled():
                continue
            ident = None
            for key in ("id", "uuid", "slug", "url"):
                if isinstance(item, dict) and item.get(key):
                    ident = "api:%s:%s" % (host_of(task.url), item[key])
                    break
            group = ident or ("api:%s#%d" % (task.url, i))
            text = json.dumps(item, default=str)[:20000] if isinstance(item, dict) else str(item)
            self.store.add(cand, group, text, "", src.score)
            stats["records"] += 1
        # Pagination for API sources: ?page=N
        if src.page_param and items:
            m = re.search(r"([?&])%s=(\d+)" % re.escape(src.page_param), task.url)
            current = int(m.group(2)) if m else 1
            if current < src.max_api_pages:
                if m:
                    nxt = task.url[:m.start()] + "%s%s=%d" % (m.group(1), src.page_param, current + 1) + task.url[m.end():]
                else:
                    nxt = task.url + ("&" if "?" in task.url else "?") + "%s=%d" % (src.page_param, current + 1)
                self.frontier.add(self.frontier.make_task(nxt, "api", task.depth, task.source_index))

    # ------------------------------------------------------------------------------------------
    # Raw page storage
    # ------------------------------------------------------------------------------------------
    def _record_raw(self, task: CrawlTask, res: FetchResult) -> None:
        if self.db is None:
            return
        self._raw_buffer.append({
            "job_id": self.job.id, "source_id": str(task.source_index), "url": task.url,
            "final_url": res.final_url, "fetched_at": now_iso(), "status_code": res.status,
            "content_type": res.content_type,
            "raw_content": res.text if self.job.crawl.keep_raw_pages and res.ok else None,
            "metadata": {"kind": task.kind, "depth": task.depth, "error": res.error, "rendered": res.rendered,
                         "elapsed": round(res.elapsed, 3), "referrer": task.referrer},
        })
        self._flush_raw()

    def _flush_raw(self, force: bool = False) -> None:
        if self.db is None or not self._raw_buffer:
            return
        if force or len(self._raw_buffer) >= 25:
            rows, self._raw_buffer = self._raw_buffer, []
            try:
                self.db.add_raw_pages(rows)
            except Exception as exc:
                self.event("Could not store raw pages: %s" % exc, "debug")

    # ------------------------------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------------------------------
    def _finalize_records(self) -> None:
        builders = self.store.final(include_rejected=self.job.output.include_rejected)
        out = []
        for b in builders:
            r = b.result
            out.append({
                "record_id": b.id, "data": r.values, "quality": r.quality, "quality_components": r.quality_components,
                "valid": r.valid, "reasons": r.reasons, "validation": r.validation, "sources": b.sources,
                "provenance": r.provenance, "raw": b.raw, "created_at": b.created_at, "updated_at": b.updated_at,
            })
        self.records_out = out

    def _save_records(self) -> None:
        if self.db is None:
            return
        self.db.save_records(self.job.id, self.job.entity, self.records_out)
        self.db.save_sources(self.job.id, self._source_summary())

    def _summary(self) -> Dict[str, Any]:
        s = self.job.summary()
        m = self.metrics
        s.update({
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "pages_fetched": m.pages_fetched, "sources_used": len(self.sources),
            "records_valid": sum(1 for r in self.records_out if r["valid"]),
            "duplicates_removed": self.store.duplicates_merged,
            "average_quality": round(sum(r["quality"] for r in self.records_out if r["valid"]) /
                                     max(1, sum(1 for r in self.records_out if r["valid"])), 1),
            "fields": ", ".join(f.name for f in self.job.fields),
        })
        return s
