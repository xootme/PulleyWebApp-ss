"""
shared_jobs.py — finished download jobs, visible to every server.

cct_common.job_queue keeps jobs in this process's memory. On Cloud Run
several servers share the load, and the page's status poll
(/api/download-status/<id>) can land on a different server from the one
that ran the export — which would answer "Job not found". So when
DATABASE_URL is set, each job's final status is also written to the
database, and the status route falls back to it.

On Cloud Run the jobs run inside the request that starts them (QUEUE_DISABLED
— CPU is throttled once a response is sent, so a background thread would
crawl), which means the job is finished, and published, before its id is
even returned. Rows are kept an hour, like the result files they point to.

Job ids are 144 random bits (cct_common.job_queue): the status answer
carries the result link, so the id is what guards the file.
"""
from __future__ import annotations

import json
import time

from cct_common.sqlite_db import SqliteDB

SHARED_JOB_TTL_S = 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_jobs (
    job_id     TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS shared_jobs_updated ON shared_jobs (updated_at);
"""


class SharedJobs(SqliteDB):
    def __init__(self, path: str, clock=time.time):
        super().__init__(path, _SCHEMA)
        self.clock = clock

    def publish(self, job) -> None:
        """Record a job's current status (call once it has finished)."""
        if job is None:
            return
        data = json.dumps(job.to_dict())
        now = self.clock()
        with self._write() as db:
            db.execute("DELETE FROM shared_jobs WHERE job_id = ? OR updated_at < ?",
                       (job.id, now - SHARED_JOB_TTL_S))
            db.execute("INSERT INTO shared_jobs (job_id, data, updated_at) VALUES (?, ?, ?)",
                       (job.id, data, now))

    def get(self, job_id: str) -> dict | None:
        with self._read() as db:
            row = db.execute("SELECT data, updated_at FROM shared_jobs WHERE job_id = ?",
                             (job_id,)).fetchone()
        if row is None or self.clock() - row["updated_at"] > SHARED_JOB_TTL_S:
            return None
        return json.loads(row["data"])
