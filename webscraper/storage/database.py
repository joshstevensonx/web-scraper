"""SQLite storage (section 31): jobs, raw pages, records, provenance and job events."""

from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from ..config import database_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT,
    entity TEXT,
    config TEXT,
    state TEXT,
    created_at TEXT,
    updated_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    metrics TEXT,
    outputs TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS raw_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    source_id TEXT,
    url TEXT,
    final_url TEXT,
    fetched_at TEXT,
    status_code INTEGER,
    content_type TEXT,
    raw_content BLOB,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_pages_job ON raw_pages(job_id);
CREATE TABLE IF NOT EXISTS records (
    job_id TEXT,
    record_id TEXT,
    entity_type TEXT,
    normalized_data TEXT,
    quality_score REAL,
    quality_components TEXT,
    valid INTEGER,
    reasons TEXT,
    validation TEXT,
    sources TEXT,
    raw_data TEXT,
    position INTEGER,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (job_id, record_id)
);
CREATE TABLE IF NOT EXISTS record_provenance (
    job_id TEXT,
    record_id TEXT,
    field_name TEXT,
    value TEXT,
    source_url TEXT,
    extraction_method TEXT,
    confidence REAL,
    extracted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_prov_record ON record_provenance(job_id, record_id);
CREATE TABLE IF NOT EXISTS job_sources (
    job_id TEXT,
    source_index INTEGER,
    url TEXT,
    name TEXT,
    type TEXT,
    origin TEXT,
    score REAL,
    pages INTEGER,
    records INTEGER,
    status TEXT,
    PRIMARY KEY (job_id, source_index)
);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    ts TEXT,
    level TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: Optional[str] = None):
        self.path = path or database_path()
        self._lock = threading.Lock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _write(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(sql, tuple(params))
                conn.commit()
            finally:
                conn.close()

    def _many(self, sql: str, rows: List[Iterable[Any]]) -> None:
        if not rows:
            return
        with self._lock:
            conn = self.connect()
            try:
                conn.executemany(sql, [tuple(r) for r in rows])
                conn.commit()
            finally:
                conn.close()

    def _query(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        conn = self.connect()
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()

    # --- jobs -------------------------------------------------------------------------------
    def create_job(self, job_id: str, name: str, entity: str, config: Dict[str, Any]) -> None:
        ts = now_iso()
        self._write("INSERT OR REPLACE INTO jobs (id, name, entity, config, state, created_at, updated_at, metrics, "
                    "outputs) VALUES (?,?,?,?,?,?,?,?,?)",
                    (job_id, name, entity, json.dumps(config), "CREATED", ts, ts, "{}", "[]"))

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = []
        values = []
        for k, v in fields.items():
            if k in ("metrics", "outputs", "config") and not isinstance(v, str):
                v = json.dumps(v)
            cols.append("%s = ?" % k)
            values.append(v)
        cols.append("updated_at = ?")
        values.append(now_iso())
        values.append(job_id)
        self._write("UPDATE jobs SET %s WHERE id = ?" % ", ".join(cols), values)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        rows = self._query("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return self._job_row(rows[0]) if rows else None

    def list_jobs(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._job_row(r) for r in rows]

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            conn = self.connect()
            try:
                for table in ("raw_pages", "records", "record_provenance", "job_sources", "job_events"):
                    conn.execute("DELETE FROM %s WHERE job_id = ?" % table, (job_id,))
                conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                conn.commit()
            finally:
                conn.close()

    def mark_interrupted(self) -> int:
        """Jobs left in a running state by a previous process can never finish."""
        rows = self._query("SELECT id FROM jobs WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED',"
                           "'PARTIALLY_COMPLETED','SOURCE_BLOCKED','INTERRUPTED','CREATED')")
        for r in rows:
            self.update_job(r["id"], state="INTERRUPTED", error="The app stopped while this job was running")
        return len(rows)

    @staticmethod
    def _job_row(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for k in ("config", "metrics", "outputs"):
            try:
                d[k] = json.loads(d.get(k) or ("[]" if k == "outputs" else "{}"))
            except ValueError:
                d[k] = {} if k != "outputs" else []
        return d

    # --- events -----------------------------------------------------------------------------
    def add_event(self, job_id: str, level: str, message: str) -> None:
        self._write("INSERT INTO job_events (job_id, ts, level, message) VALUES (?,?,?,?)",
                    (job_id, now_iso(), level, message[:2000]))

    def events(self, job_id: str, limit: int = 200, after_id: int = 0) -> List[Dict[str, Any]]:
        rows = self._query("SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id DESC LIMIT ?",
                           (job_id, after_id, limit))
        return [dict(r) for r in reversed(rows)]

    # --- raw pages / sources ----------------------------------------------------------------
    def add_raw_pages(self, rows: List[Dict[str, Any]]) -> None:
        self._many(
            "INSERT INTO raw_pages (job_id, source_id, url, final_url, fetched_at, status_code, content_type, "
            "raw_content, metadata) VALUES (?,?,?,?,?,?,?,?,?)",
            [(r["job_id"], r.get("source_id"), r["url"], r.get("final_url"), r.get("fetched_at"), r.get("status_code"),
              r.get("content_type"), zlib.compress(r["raw_content"].encode("utf-8")) if r.get("raw_content") else None,
              json.dumps(r.get("metadata") or {})) for r in rows])

    def raw_page_count(self, job_id: str) -> int:
        return self._query("SELECT COUNT(*) AS n FROM raw_pages WHERE job_id = ?", (job_id,))[0]["n"]

    def save_sources(self, job_id: str, sources: List[Dict[str, Any]]) -> None:
        self._write("DELETE FROM job_sources WHERE job_id = ?", (job_id,))
        self._many("INSERT INTO job_sources (job_id, source_index, url, name, type, origin, score, pages, records, "
                   "status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [(job_id, s["index"], s["url"], s.get("name"), s.get("type"), s.get("origin"), s.get("score"),
                     s.get("pages", 0), s.get("records", 0), s.get("status", "")) for s in sources])

    def sources(self, job_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM job_sources WHERE job_id = ? ORDER BY source_index",
                                             (job_id,))]

    # --- records ----------------------------------------------------------------------------
    def save_records(self, job_id: str, entity: str, records: List[Dict[str, Any]]) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.execute("DELETE FROM records WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM record_provenance WHERE job_id = ?", (job_id,))
                conn.executemany(
                    "INSERT INTO records (job_id, record_id, entity_type, normalized_data, quality_score, "
                    "quality_components, valid, reasons, validation, sources, raw_data, position, created_at, "
                    "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(job_id, r["record_id"], entity, json.dumps(r["data"], default=str), r["quality"],
                      json.dumps(r.get("quality_components") or {}), 1 if r["valid"] else 0,
                      json.dumps(r.get("reasons") or []), json.dumps(r.get("validation") or []),
                      json.dumps(r.get("sources") or []), json.dumps(r.get("raw") or [], default=str), i,
                      r.get("created_at"), r.get("updated_at")) for i, r in enumerate(records)])
                prov_rows = []
                for r in records:
                    for fname, prov in (r.get("provenance") or {}).items():
                        for p in (prov if isinstance(prov, list) else [prov]):
                            prov_rows.append((job_id, r["record_id"], fname, json.dumps(p.get("value"), default=str),
                                              p.get("source"), p.get("method"), p.get("confidence"),
                                              p.get("extracted_at")))
                conn.executemany("INSERT INTO record_provenance (job_id, record_id, field_name, value, source_url, "
                                 "extraction_method, confidence, extracted_at) VALUES (?,?,?,?,?,?,?,?)", prov_rows)
                conn.commit()
            finally:
                conn.close()

    def records(self, job_id: str, valid_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM records WHERE job_id = ?" + (" AND valid = 1" if valid_only else "") + " ORDER BY position"
        out = []
        for r in self._query(sql, (job_id,)):
            d = dict(r)
            for k in ("normalized_data", "quality_components", "reasons", "validation", "sources", "raw_data"):
                try:
                    d[k] = json.loads(d.get(k) or "null")
                except ValueError:
                    d[k] = None
            d["valid"] = bool(d["valid"])
            out.append(d)
        return out

    def provenance(self, job_id: str, record_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if record_id:
            rows = self._query("SELECT * FROM record_provenance WHERE job_id = ? AND record_id = ?", (job_id, record_id))
        else:
            rows = self._query("SELECT * FROM record_provenance WHERE job_id = ?", (job_id,))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["value"] = json.loads(d["value"])
            except (ValueError, TypeError):
                pass
            out.append(d)
        return out
