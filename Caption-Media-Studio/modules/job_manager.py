"""Single-worker persistent job queue with restart recovery."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

import config

LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobContext:
    def __init__(self, manager: "JobManager", job_id: str):
        self.manager = manager
        self.job_id = job_id

    def progress(self, percent: int, message: str) -> None:
        self.manager.update(self.job_id, progress=max(0, min(100, int(percent))), message=message)


class JobManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cms-job")
        self._futures: dict[str, Future] = {}
        self._initialize()
        self._recover_interrupted()

    def _connect(self):
        connection = sqlite3.connect(config.DATABASE_PATH, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                    progress INTEGER NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, result_json TEXT, error TEXT
                )"""
            )

    def _recover_interrupted(self):
        with self._connect() as db:
            rows = db.execute("SELECT id, state FROM jobs WHERE state IN ('queued','running')").fetchall()
            for row in rows:
                db.execute(
                    "UPDATE jobs SET state='failed', message=?, error=?, updated_at=? WHERE id=?",
                    ("Interrupted by server restart", "The server restarted before this job finished.",
                     utc_now(), row["id"]),
                )
        for row in rows:
            for path in config.TEMP_DIR.glob(f"{row['id']}*"):
                if path.is_file():
                    path.unlink(missing_ok=True)

    def submit(self, kind: str, function: Callable, *args, **kwargs) -> str:
        with self._lock:
            with self._connect() as db:
                queued = db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0]
            if queued >= config.MAX_QUEUED_JOBS:
                raise RuntimeError("The processing queue is full. Wait for a job to finish and try again.")
            job_id = uuid.uuid4().hex
            now = utc_now()
            with self._connect() as db:
                db.execute(
                    "INSERT INTO jobs (id,kind,state,progress,message,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (job_id, kind, "queued", 0, "Waiting for the worker", now, now),
                )
            future = self._executor.submit(self._run, job_id, function, args, kwargs)
            self._futures[job_id] = future
            return job_id

    def _run(self, job_id: str, function: Callable, args: tuple, kwargs: dict):
        self.update(job_id, state="running", message="Processing", progress=1)
        started = time.monotonic()
        try:
            context = JobContext(self, job_id)
            result = function(context, *args, **kwargs)
            if time.monotonic() - started > config.JOB_TIMEOUT_SECONDS:
                raise TimeoutError("The job exceeded the configured processing time limit.")
            with self._connect() as db:
                db.execute(
                    "UPDATE jobs SET state='completed',progress=100,message=?,result_json=?,updated_at=? WHERE id=?",
                    ("Completed", json.dumps(result, ensure_ascii=False), utc_now(), job_id),
                )
        except Exception as exc:
            LOGGER.exception("Background job %s failed", job_id)
            self.update(job_id, state="failed", message="Processing failed", error=str(exc))
        finally:
            with self._lock:
                self._futures.pop(job_id, None)

    def update(self, job_id: str, **values):
        allowed = {"state", "progress", "message", "error"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = utc_now()
        columns = ", ".join(f"{key}=?" for key in updates)
        with self._connect() as db:
            db.execute(
                f"UPDATE jobs SET {columns} WHERE id=?",
                (*updates.values(), job_id),
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        if len(job_id) != 32 or any(ch not in "0123456789abcdef" for ch in job_id):
            return None
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json")) if result.get("result_json") else None
        return result

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
            items.append(item)
        return items

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
            if future is None or not future.cancel():
                return False
            self._futures.pop(job_id, None)
            self.update(job_id, state="cancelled", message="Cancelled before processing")
            return True

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
