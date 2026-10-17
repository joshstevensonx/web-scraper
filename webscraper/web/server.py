"""Local web app: job wizard, live dashboard and results browser (sections 27-30).

Runs on http://127.0.0.1:8765 using only the standard library HTTP server. Requests must come
from the app's own origin (Host/Origin checks + a custom header on writes), so other websites
open in your browser cannot drive the scraper.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

from .. import __version__
from .. import config as app_config
from ..job.manager import JobManager
from ..job.models import DATA_TYPES, OUTPUT_FORMATS, JobConfigError, parse_job
from ..target.entities import ENTITIES
from ..target.query_generator import generate_queries
from ..util import module_available

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_BODY = 2_000_000


def meta() -> Dict[str, Any]:
    provider = "brave" if os.environ.get("BRAVE_API_KEY") else ("searxng" if os.environ.get("SEARXNG_URL")
                                                                else "duckduckgo")
    browser = module_available("playwright")
    parquet = module_available("pyarrow")
    return {
        "version": __version__,
        "entities": [{"key": e.key, "label": e.label, "mode": e.record_mode, "fields": e.default_fields}
                     for e in ENTITIES.values()],
        "data_types": sorted(DATA_TYPES),
        "output_formats": [f for f in OUTPUT_FORMATS if f != "ndjson"],
        "search_provider": provider,
        "browser_rendering": browser,
        "parquet": parquet,
        "output_dir": app_config.output_dir(),
        "platform": sys.platform,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "WebScraper/" + __version__
    manager: JobManager = None  # type: ignore[assignment]
    allowed_hosts: Tuple[str, ...] = ()

    # --- plumbing ---------------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # keep the terminal quiet
        if os.environ.get("WEBSCRAPER_DEBUG"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str, **extra: Any) -> None:
        payload = {"error": message}
        payload.update(extra)
        self._json(payload, status)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in self.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return urlsplit(origin).netloc.lower() in self.allowed_hosts

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    # --- routing ----------------------------------------------------------------------------
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._error(403, "Forbidden host")
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/meta":
                return self._json(meta())
            if path == "/api/jobs":
                return self._json({"jobs": self.manager.list()})
            m = re.fullmatch(r"/api/jobs/([\w-]+)(?:/(\w+))?(?:/([\w-]+))?", path)
            if m:
                return self._job_get(m.group(1), m.group(2), m.group(3), query)
            return self._error(404, "Not found")
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            return self._error(500, "%s: %s" % (type(exc).__name__, exc))

    def do_POST(self) -> None:
        if not self._host_ok() or not self._origin_ok() or self.headers.get("X-Requested-With") != "webscraper":
            return self._error(403, "Forbidden")
        path = urlsplit(self.path).path
        try:
            body = self._body()
        except ValueError as exc:
            return self._error(400, str(exc))
        try:
            if path == "/api/jobs/validate":
                return self._validate(body)
            if path == "/api/jobs":
                job = self.manager.create(body)
                if body.get("_start", True):
                    self.manager.start(job.id)
                return self._json({"id": job.id, "summary": job.summary()}, 201)
            m = re.fullmatch(r"/api/jobs/([\w-]+)/(start|pause|resume|cancel|reveal|clone)", path)
            if m:
                return self._job_post(m.group(1), m.group(2), body)
            return self._error(404, "Not found")
        except JobConfigError as exc:
            return self._error(400, "Invalid job", errors=exc.errors)
        except KeyError as exc:
            return self._error(404, "Not found: %s" % exc)
        except RuntimeError as exc:
            return self._error(409, str(exc))
        except Exception as exc:  # pragma: no cover
            return self._error(500, "%s: %s" % (type(exc).__name__, exc))

    def do_DELETE(self) -> None:
        if not self._host_ok() or not self._origin_ok() or self.headers.get("X-Requested-With") != "webscraper":
            return self._error(403, "Forbidden")
        m = re.fullmatch(r"/api/jobs/([\w-]+)", urlsplit(self.path).path)
        if not m:
            return self._error(404, "Not found")
        try:
            self.manager.delete(m.group(1))
        except RuntimeError as exc:
            return self._error(409, str(exc))
        return self._json({"deleted": m.group(1)})

    # --- handlers ---------------------------------------------------------------------------
    def _static(self, name: str) -> None:
        safe = os.path.normpath(name).lstrip("/\\")
        if safe.startswith("..") or os.path.isabs(safe):
            return self._error(404, "Not found")
        full = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(full):
            return self._error(404, "Not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            data = fh.read()
        headers = {}
        if name == "index.html":
            headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                                  "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self._send(200, data, ctype, headers)

    def _validate(self, body: Dict[str, Any]) -> None:
        try:
            job = parse_job(body)
        except JobConfigError as exc:
            return self._json({"ok": False, "errors": exc.errors})
        return self._json({"ok": True, "summary": job.summary(), "mode": job.record_mode,
                           "page_budget": job.page_budget, "queries": generate_queries(job)[:8]})

    def _job_get(self, job_id: str, sub: Optional[str], rid: Optional[str], query: Dict[str, Any]) -> None:
        mgr = self.manager
        if sub is None:
            st = mgr.status(job_id)
            return self._json(st) if st else self._error(404, "Job not found")
        if sub == "events":
            after = int((query.get("after") or ["0"])[0] or 0)
            return self._json({"events": mgr.db.events(job_id, 300, after)})
        if sub == "sources":
            return self._json({"sources": mgr.db.sources(job_id)})
        if sub == "config":
            row = mgr.db.get_job(job_id)
            if row is None:
                return self._error(404, "Job not found")
            data = json.dumps(row["config"], indent=2).encode("utf-8")
            name = re.sub(r"[^\w.-]", "-", row["name"])[:60] or job_id
            return self._send(200, data, "application/json", {
                "Content-Disposition": "attachment; filename=\"%s.json\"" % name})
        if sub == "records":
            include_rejected = (query.get("include_rejected") or ["1"])[0] == "1"
            records = mgr.records(job_id, include_rejected=include_rejected)
            if rid:
                rec = next((r for r in records if r["record_id"] == rid), None)
                return self._json(rec) if rec else self._error(404, "Record not found")
            slim = [{"record_id": r["record_id"], "data": r["data"], "quality": r["quality"], "valid": r["valid"],
                     "reasons": r["reasons"], "sources": r["sources"]} for r in records]
            return self._json({"records": slim})
        if sub == "export":
            fmt = (query.get("format") or ["csv"])[0]
            if fmt not in OUTPUT_FORMATS:
                return self._error(400, "Unsupported format")
            include_rejected = (query.get("include_rejected") or ["0"])[0] == "1"
            try:
                path = mgr.export(job_id, fmt, include_rejected=include_rejected)
            except KeyError:
                return self._error(404, "Job not found")
            except Exception as exc:
                return self._error(400, str(exc))
            with open(path, "rb") as fh:
                data = fh.read()
            fname = os.path.basename(path)
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            return self._send(200, data, ctype, {
                "Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
                    fname.encode("ascii", "replace").decode(), quote(fname)),
                "X-Saved-Path": quote(path)})
        return self._error(404, "Not found")

    def _job_post(self, job_id: str, action: str, body: Dict[str, Any]) -> None:
        mgr = self.manager
        if action == "start":
            mgr.start(job_id)
            return self._json({"started": job_id})
        if action in ("pause", "resume", "cancel"):
            mgr.control(job_id, action)
            return self._json({"ok": True, "action": action})
        if action == "clone":
            row = mgr.db.get_job(job_id)
            if row is None:
                return self._error(404, "Job not found")
            cfg = dict(row["config"])
            cfg.pop("id", None)
            return self._json({"config": cfg})
        if action == "reveal":
            row = mgr.db.get_job(job_id)
            target = (row or {}).get("outputs") or []
            path = target[0] if target else app_config.output_dir()
            if sys.platform == "darwin":
                args = ["open", "-R", path] if os.path.isfile(path) else ["open", path]
                subprocess.run(args, check=False)
                return self._json({"ok": True, "path": path})
            return self._json({"ok": False, "path": path})
        return self._error(404, "Not found")


def make_server(host: str = "127.0.0.1", port: int = 8765, manager: Optional[JobManager] = None) -> ThreadingHTTPServer:
    mgr = manager or JobManager()
    names = {host.lower(), "127.0.0.1", "localhost"}

    class BoundHandler(Handler):
        pass

    BoundHandler.manager = mgr
    httpd = ThreadingHTTPServer((host, port), BoundHandler)
    actual_port = httpd.server_address[1]
    BoundHandler.allowed_hosts = tuple("%s:%d" % (n, actual_port) for n in names) + (
        tuple(names) if actual_port == 80 else ())
    httpd.daemon_threads = True
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    try:
        httpd = make_server(host, port)
    except OSError as exc:
        if getattr(exc, "errno", None) in (48, 98) or "in use" in str(exc).lower():
            url = "http://%s:%d/" % ("127.0.0.1" if host in ("0.0.0.0", "") else host, port)
            print("Port %d is already in use — Web Scraper may already be running at %s" % (port, url))
            if open_browser:
                webbrowser.open(url)
            return
        raise
    url = "http://%s:%d/" % ("127.0.0.1" if host in ("0.0.0.0", "") else host, httpd.server_address[1])
    print("Web Scraper %s is running at %s" % (__version__, url))
    print("Results are saved in %s" % app_config.output_dir())
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        httpd.server_close()
        mgr = getattr(httpd.RequestHandlerClass, "manager", None)
        if mgr is not None:
            mgr.shutdown()
