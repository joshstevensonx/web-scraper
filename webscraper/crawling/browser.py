"""Optional headless-browser rendering for JavaScript-heavy pages (Playwright + Chromium).

Install with ``pip install playwright && python -m playwright install chromium`` (the macOS
installer script offers to do this). If Playwright's own Chromium is not downloaded, an installed
Google Chrome or Microsoft Edge is used instead. ``WEBSCRAPER_CHROMIUM_PATH`` points at a specific
browser executable. When Playwright is missing the scraper falls back to plain HTTP fetching.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

from ..util import module_available
from .fetcher import FetchResult


def playwright_available() -> bool:
    return module_available("playwright")


class BrowserRenderer:
    def __init__(self, user_agent: str, timeout: float = 30.0, max_concurrency: int = 2):
        self.user_agent = user_agent
        self.timeout_ms = int(timeout * 1000)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._pw = None
        self._browser = None
        self._context = None
        self._start_lock = asyncio.Lock()
        self.failed: Optional[str] = None

    async def _ensure(self) -> bool:
        if self._context is not None:
            return True
        if self.failed:
            return False
        async with self._start_lock:
            if self._context is not None:
                return True
            try:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                self._browser = await self._launch()
                self._context = await self._browser.new_context(user_agent=self.user_agent,
                                                                java_script_enabled=True)
            except Exception as exc:  # browser not installed, sandbox restrictions, ...
                self.failed = "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:200])
                await self.close()
                return False
        return True

    async def _launch(self):
        attempts = []
        path = os.environ.get("WEBSCRAPER_CHROMIUM_PATH")
        if path:
            attempts.append({"executable_path": path})
        attempts += [{}, {"channel": "chrome"}, {"channel": "msedge"}]
        error: Optional[Exception] = None
        for kwargs in attempts:
            try:
                return await self._pw.chromium.launch(headless=True, **kwargs)
            except Exception as exc:
                error = exc
        raise error if error else RuntimeError("no browser available")

    async def render(self, url: str, scroll: bool = False) -> FetchResult:
        result = FetchResult(url=url, rendered=True)
        if not await self._ensure():
            result.error = "browser unavailable: %s" % self.failed
            return result
        start = time.monotonic()
        async with self._sem:
            page = await self._context.new_page()
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(10_000, self.timeout_ms))
                except Exception:
                    pass
                if scroll:  # infinite scroll: load a few more batches
                    for _ in range(5):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(800)
                result.text = await page.content()
                result.final_url = page.url
                result.status = resp.status if resp else 200
                result.content_type = "text/html"
            except Exception as exc:
                result.error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            finally:
                await page.close()
                result.elapsed = time.monotonic() - start
        return result

    async def close(self) -> None:
        for obj, meth in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    await getattr(obj, meth)()
                except Exception:
                    pass
        self._context = self._browser = self._pw = None
