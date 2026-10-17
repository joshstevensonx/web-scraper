"""Web app API and CLI smoke tests (real local HTTP server on a free port)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from webscraper import cli
from webscraper.job.manager import JobManager
from webscraper.storage.database import Database
from webscraper.web.server import make_server


@pytest.fixture
def server(tmp_path):
    mgr = JobManager(Database(str(tmp_path / "web.sqlite")))
    httpd = make_server("127.0.0.1", 0, mgr)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1], mgr
    httpd.shutdown()
    httpd.server_close()


def call(base, path, method="GET", body=None, headers=None):
    hdrs = {"X-Requested-With": "webscraper", "Content-Type": "application/json"}
    hdrs.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if resp.headers.get("content-type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def test_static_and_meta(server):
    base, _ = server
    status, html = call(base, "/")
    assert status == 200 and b"Web Scraper" in html
    status, js = call(base, "/static/app.js")
    assert status == 200 and b"viewWizard" in js
    assert call(base, "/static/../server.py")[0] == 404
    status, meta = call(base, "/api/meta")
    assert status == 200 and {"business", "product", "job"} <= {e["key"] for e in meta["entities"]}
    assert "xlsx" in meta["output_formats"]


def test_validate_create_and_records_flow(server):
    base, mgr = server
    status, res = call(base, "/api/jobs/validate", "POST", {"entity": "business", "fields": [{"name": "x", "type": "bad"}]})
    assert status == 200 and not res["ok"] and res["errors"]
    job = {"entity": "product", "sources": {"mode": "manual", "urls": ["https://shop.invalid/"]},
           "crawl": {"delay": 0, "retries": 0, "timeout": 2}, "records": {"minimum": 1, "maximum": 1},
           "_start": False}
    status, res = call(base, "/api/jobs/validate", "POST", job)
    assert res["ok"] and res["mode"] == "page"
    status, created = call(base, "/api/jobs", "POST", job)
    assert status == 201
    jid = created["id"]
    status, st = call(base, "/api/jobs/" + jid)
    assert st["state"] == "CREATED" and st["summary"]["entity"] == "Product"
    status, jobs = call(base, "/api/jobs")
    assert [j["id"] for j in jobs["jobs"]] == [jid]
    # Store a record directly and read it back through the API (no network needed).
    mgr.db.save_records(jid, "product", [{"record_id": "r1", "data": {"name": "Lamp", "price": 9.5}, "quality": 90,
                                          "valid": True, "sources": ["https://shop.invalid/lamp"],
                                          "provenance": {"name": {"value": "Lamp", "method": "json-ld",
                                                                  "confidence": 0.95, "source": "s"}}}])
    status, recs = call(base, "/api/jobs/%s/records" % jid)
    assert recs["records"][0]["data"]["name"] == "Lamp"
    status, rec = call(base, "/api/jobs/%s/records/r1" % jid)
    assert rec["provenance"]["name"]["method"] == "json-ld"
    status, data = call(base, "/api/jobs/%s/export?format=csv" % jid)
    assert status == 200 and b"Lamp" in data
    status, cfg = call(base, "/api/jobs/%s/clone" % jid, "POST", {})
    assert cfg["config"]["entity"] == "product" and "id" not in cfg["config"]
    assert call(base, "/api/jobs/" + jid, "DELETE")[0] == 200
    assert call(base, "/api/jobs/" + jid)[0] == 404


def test_job_runs_in_background_and_can_be_cancelled(server):
    base, _ = server
    job = {"entity": "product", "sources": {"mode": "manual", "urls": ["https://shop.invalid/"]},
           "crawl": {"delay": 0, "retries": 0, "timeout": 2, "respect_robots": False},
           "records": {"minimum": 1, "maximum": 1}}
    status, created = call(base, "/api/jobs", "POST", job)
    jid = created["id"]
    for _ in range(100):
        st = call(base, "/api/jobs/" + jid)[1]
        if st["final"] and not st["running"]:
            break
        time.sleep(0.1)
    assert st["state"] in ("PARTIALLY_COMPLETED", "SOURCE_BLOCKED")
    status, ev = call(base, "/api/jobs/%s/events" % jid)
    assert any("Could not fetch" in e["message"] for e in ev["events"])


def test_csrf_and_host_protection(server):
    base, _ = server
    assert call(base, "/api/jobs", "POST", {}, headers={"X-Requested-With": "nope"})[0] == 403
    assert call(base, "/api/jobs", "POST", {}, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(base, "/api/meta", headers={"Host": "evil.example:80"})[0] == 403


def test_cli_validate_example_and_jobs(capsys, tmp_path):
    assert cli.main(["validate", "examples/books-toscrape.json"]) == 0
    assert "is valid" in capsys.readouterr().out
    assert cli.main(["example", "product"]) == 0
    assert json.loads(capsys.readouterr().out)["entity"] == "product"
    bad = tmp_path / "bad.json"
    bad.write_text('{"records": {"minimum": 5, "maximum": 1}}')
    assert cli.main(["validate", str(bad)]) == 2
    assert cli.main(["jobs"]) == 0
    assert cli.main(["doctor"]) == 0
