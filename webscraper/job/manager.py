"""Job manager: creates, runs (in background threads), controls and exports jobs."""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

from .. import config as app_config
from ..monitoring.metrics import JobMetrics
from ..output.writers import export, record_from_row
from ..storage.database import Database
from .models import JobConfig, parse_job
from .runner import JobControl, JobRunner
from .state import JobState


class JobManager:
    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()
        self._lock = threading.Lock()
        self._running: Dict[str, Dict[str, Any]] = {}
        self.db.mark_interrupted()

    # ------------------------------------------------------------------------------------------
    def create(self, raw: Dict[str, Any]) -> JobConfig:
        job = parse_job(raw)
        raw = dict(raw)
        raw["id"] = job.id
        self.db.create_job(job.id, job.name, job.entity, raw)
        try:
            with open(os.path.join(app_config.jobs_dir(), "%s.json" % job.output.filename), "w",
                      encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2)
        except OSError:
            pass
        return job

    def load(self, job_id: str) -> JobConfig:
        row = self.db.get_job(job_id)
        if row is None:
            raise KeyError(job_id)
        raw = dict(row["config"])
        raw["id"] = job_id
        return parse_job(raw)

    def start(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._running:
                raise RuntimeError("Job is already running")
            job = self.load(job_id)
            row = self.db.get_job(job_id)
            control = JobControl()
            runner = JobRunner(job, db=self.db, control=control, raw_config=row["config"])
            entry: Dict[str, Any] = {"runner": runner, "control": control}

            def target() -> None:
                try:
                    runner.run_sync()
                finally:
                    with self._lock:
                        self._running.pop(job_id, None)

            thread = threading.Thread(target=target, name="job-%s" % job_id, daemon=True)
            entry["thread"] = thread
            self._running[job_id] = entry
            thread.start()

    def is_running(self, job_id: str) -> bool:
        return job_id in self._running

    def control(self, job_id: str, action: str) -> None:
        entry = self._running.get(job_id)
        if entry is None:
            raise KeyError("Job is not running")
        ctl: JobControl = entry["control"]
        if action == "pause":
            ctl.pause()
        elif action == "resume":
            ctl.resume()
        elif action == "cancel":
            ctl.cancel()
        else:
            raise ValueError("Unknown action %r" % action)

    def delete(self, job_id: str) -> None:
        if job_id in self._running:
            raise RuntimeError("Cancel the job before deleting it")
        self.db.delete_job(job_id)

    def shutdown(self) -> None:
        for entry in list(self._running.values()):
            entry["control"].cancel()
        for entry in list(self._running.values()):
            entry["thread"].join(timeout=15)

    # ------------------------------------------------------------------------------------------
    def status(self, job_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.get_job(job_id)
        if row is None:
            return None
        entry = self._running.get(job_id)
        if entry is not None:
            runner: JobRunner = entry["runner"]
            row["metrics"] = runner.metrics.to_dict()
            row["state"] = runner.state.value
            row["running"] = True
            row["paused"] = entry["control"].paused
        else:
            row["running"] = False
            row["paused"] = False
            metrics = JobMetrics.from_dict(row.get("metrics") or {})
            row["metrics"] = metrics.to_dict() if row.get("metrics") else {}
        try:
            job = parse_job(dict(row["config"], id=job_id))
            row["summary"] = job.summary()
            row["fields"] = [{"name": f.name, "label": f.label, "type": f.type, "required": f.required}
                             for f in job.fields]
        except Exception as exc:
            row["summary"] = {"error": str(exc)}
            row["fields"] = []
        row["final"] = JobState(row["state"]).is_final if row["state"] in JobState.__members__ else False
        return row

    def list(self) -> List[Dict[str, Any]]:
        out = []
        for row in self.db.list_jobs():
            entry = self._running.get(row["id"])
            if entry is not None:
                row["metrics"] = entry["runner"].metrics.to_dict()
                row["state"] = entry["runner"].state.value
                row["running"] = True
            else:
                row["running"] = False
            row.pop("config", None)
            out.append(row)
        return out

    def records(self, job_id: str, include_rejected: bool = True) -> List[Dict[str, Any]]:
        rows = self.db.records(job_id, valid_only=not include_rejected)
        prov = self.db.provenance(job_id)
        by_record: Dict[str, List[Dict[str, Any]]] = {}
        for p in prov:
            by_record.setdefault(p["record_id"], []).append(p)
        return [record_from_row(r, by_record.get(r["record_id"], [])) for r in rows]

    def export(self, job_id: str, fmt: str, directory: Optional[str] = None,
               include_rejected: bool = False) -> str:
        job = self.load(job_id)
        records = self.records(job_id, include_rejected=include_rejected)
        if include_rejected:
            job.output.include_rejected = True
        row = self.db.get_job(job_id) or {}
        summary = job.summary()
        summary["state"] = row.get("state")
        paths = export(job, records, directory or job.output.directory or app_config.output_dir(), [fmt],
                       summary=summary)
        return paths[0]
