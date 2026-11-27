"""
jobs.py — work that takes minutes, queued in the shared database so any
server can start it and any worker can run it (0.65.0; the PCB Importer
cloud move, the owner's "all as recommended", CCT_Business D-36, Q-C6).

    from cct_common.jobs import Jobs, run_until_empty
    jobs = Jobs(DATABASE_URL, launcher=launcher_from_env())
    job_id = jobs.submit("pcbimporter", "import", {"upload": "uploads/u1.zip"}, account_id=acct)
    jobs.get(job_id)            # the page polls: state, progress, result or error

    # the worker: a Cloud Run Job's run, or a thread locally
    HANDLERS = {"import": lambda job: {"board": do_import(job.params, progress=job.progress)}}
    run_until_empty(jobs, "pcbimporter", HANDLERS)

    python -m cct_common.jobs run --app pcbimporter --handlers pcbimporter.jobs:HANDLERS

job_queue.py stays what it is (each process's own jobs and Pulley's
single-user sessions); this is the one that works across servers.

- A job is a row: app, kind, the account, its params (JSON, at most
  MAX_JSON bytes: files go in cct_common.storage, the params name them),
  its state (queued, running, done, failed, cancelled), attempts, progress,
  and its result (JSON, MAX_JSON) or error (one line, ERROR_CHARS). Its id
  is random, so a page can poll it, but get() for an account checks the
  account too.
- Claiming is one write (the database's writes are serialised: SQLite's
  BEGIN IMMEDIATE, Postgres's advisory lock), so a job goes to one worker.
  The oldest due job goes first.
- A running job holds a lease (LEASE_S) that its progress() calls renew.
  A worker that dies (an instance stopped, a timeout) loses the lease, and
  the job is claimed again, up to its max_attempts; after that it fails
  ("stopped N times"). A worker whose lease was taken, or whose job was
  cancelled, learns it at its next progress() (Cancelled is raised in the
  handler) and its finish() changes nothing.
- A handler that raises is retried after BACKOFF_S, until max_attempts;
  Permanent (a bad upload, a file we can't read) fails it at once. A kind
  with no handler fails at once.
- Launch: after a submit commits, the launcher starts a worker. "thread"
  (local and tests) wakes this process's worker thread; a CloudRunJob
  launcher runs a Cloud Run Job execution (the Run Admin API, as the
  service's own account: it needs the run.jobs.run permission on that job,
  the owner's Cloud), which drains the queue and exits. Every submit
  launches; an execution that finds nothing exits at once. A launch that
  fails leaves the job queued for the next launch or the scheduled run.
- Kept: finished jobs for KEEP_S (7 days), then purge() removes them.
  Closing an account removes its jobs (0.66.0, the shared store).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

from . import forksafe
from .sqlite_db import SqliteDB

LEASE_S = 120
BACKOFF_S = (30, 120, 600)
MAX_ATTEMPTS = 3
MAX_JSON = 64 * 1024
ERROR_CHARS = 500
KEEP_S = 7 * 86400
STATES = ("queued", "running", "done", "failed", "cancelled")
ENDED = ("done", "failed", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    app           TEXT NOT NULL,
    kind          TEXT NOT NULL,
    account_id    TEXT,
    params        TEXT NOT NULL,
    state         TEXT NOT NULL,
    created_at    REAL NOT NULL,
    next_at       REAL NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL,
    lease_until   REAL,
    worker        TEXT,
    started_at    REAL,
    finished_at   REAL,
    progress      REAL,
    result        TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs (app, state, next_at);
CREATE INDEX IF NOT EXISTS jobs_account ON jobs (account_id, created_at);
"""


class JobError(ValueError):
    """A job the queue refuses (too large, an unknown state)."""


class Permanent(Exception):
    """Raised by a handler: fail the job now, no retry (the message is shown)."""


class Cancelled(Exception):
    """Raised in a handler by progress(): the job was cancelled, or another worker has it."""


def _dumps(value, what: str) -> str:
    text = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if len(text.encode()) > MAX_JSON:
        raise JobError(f"a job's {what} is at most {MAX_JSON} bytes of JSON; put files in cct_common.storage")
    return text


def _one_line(e) -> str:
    text = " ".join(str(e).split()) or type(e).__name__
    return text[:ERROR_CHARS]


def worker_name() -> str:
    """Who holds a lease: the host (or Cloud Run's execution) and the process."""
    where = os.environ.get("CLOUD_RUN_EXECUTION") or os.environ.get("K_REVISION") or socket.gethostname()
    return f"{where}:{os.getpid()}:{secrets.token_hex(3)}"


class Jobs(SqliteDB):
    def __init__(self, path: str, *, clock: Callable[[], float] = time.time, launcher=None):
        super().__init__(path, SCHEMA)
        self.clock = clock
        self.launcher = launcher

    # ── the page's side ──
    def submit(self, app: str, kind: str, params: dict, *, account_id: Optional[str] = None,
               max_attempts: int = MAX_ATTEMPTS, delay: float = 0) -> str:
        if not app or not kind:
            raise JobError("a job needs its app and kind")
        if max_attempts < 1:
            raise JobError("max_attempts is at least 1")
        text, now, job_id = _dumps(params or {}, "params"), self.clock(), secrets.token_urlsafe(12)
        with self._write() as db:
            db.execute("INSERT INTO jobs (id, app, kind, account_id, params, state, created_at, next_at, "
                       "max_attempts) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                       (job_id, app, kind, account_id, text, now, now + max(0.0, delay), int(max_attempts)))
        if self.launcher is not None:          # after the commit: the worker must find the row
            try:
                self.launcher(app)
            except Exception as e:             # the job stays queued for the next launch
                import logging
                logging.getLogger(__name__).error("jobs: launching a worker for %s failed: %s", app, _one_line(e))
        return job_id

    @staticmethod
    def _row(r) -> dict:
        d = dict(zip(r.keys(), tuple(r)))
        d["params"] = json.loads(d["params"])
        d["result"] = json.loads(d["result"]) if d["result"] is not None else None
        return d

    def get(self, job_id: str, *, account_id: Optional[str] = None) -> Optional[dict]:
        """The job, or None; with account_id, None unless it's that account's."""
        with self._read() as db:
            r = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None or (account_id is not None and r["account_id"] != account_id):
            return None
        return self._row(r)

    def for_account(self, account_id: str, *, app: Optional[str] = None, limit: int = 50) -> list[dict]:
        sql, args = "SELECT * FROM jobs WHERE account_id = ?", [account_id]
        if app:
            sql, args = sql + " AND app = ?", args + [app]
        with self._read() as db:
            rows = db.execute(sql + " ORDER BY created_at DESC, id LIMIT ?", (*args, int(limit))).fetchall()
        return [self._row(r) for r in rows]

    def cancel(self, job_id: str, *, account_id: Optional[str] = None) -> bool:
        """A queued or running job stops (a running one at its next progress())."""
        now = self.clock()
        with self._write() as db:
            r = db.execute("SELECT account_id, state FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if r is None or r["state"] in ENDED or (account_id is not None and r["account_id"] != account_id):
                return False
            db.execute("UPDATE jobs SET state = 'cancelled', finished_at = ?, lease_until = NULL WHERE id = ?",
                       (now, job_id))
        return True

    # ── the worker's side ──
    def claim(self, app: str, *, worker: str, kinds: Optional[list] = None, lease_s: float = LEASE_S) -> Optional[dict]:
        """The oldest due job of the app (of these kinds), now this worker's; or None.
        A running job whose lease ran out is due again, or failed once it has
        used its attempts."""
        now = self.clock()
        kinds_sql = (" AND kind IN (" + ",".join("?" * len(kinds)) + ")") if kinds else ""
        with self._write() as db:
            for r in db.execute("SELECT id, attempts, max_attempts FROM jobs WHERE app = ? AND state = 'running' "
                                "AND lease_until < ? AND attempts >= max_attempts" + kinds_sql,
                                (app, now, *(kinds or ()))).fetchall():
                db.execute("UPDATE jobs SET state = 'failed', finished_at = ?, lease_until = NULL, error = ? "
                           "WHERE id = ?", (now, f"stopped {r['attempts']} times before it finished", r["id"]))
            r = db.execute("SELECT id FROM jobs WHERE app = ? AND ((state = 'queued' AND next_at <= ?) OR "
                           "(state = 'running' AND lease_until < ?))" + kinds_sql +
                           " ORDER BY next_at, created_at, id LIMIT 1", (app, now, now, *(kinds or ()))).fetchone()
            if r is None:
                return None
            db.execute("UPDATE jobs SET state = 'running', attempts = attempts + 1, worker = ?, lease_until = ?, "
                       "started_at = ?, progress = NULL WHERE id = ?", (worker, now + lease_s, now, r["id"]))
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (r["id"],)).fetchone()
        return self._row(job)

    def _mine(self, db, job_id: str, worker: str) -> bool:
        r = db.execute("SELECT state, worker FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return r is not None and r["state"] == "running" and r["worker"] == worker

    def heartbeat(self, job_id: str, worker: str, *, progress: Optional[float] = None,
                  lease_s: float = LEASE_S) -> bool:
        """Renew the lease (and say how far it got); False if the job isn't this worker's any more."""
        now = self.clock()
        with self._write() as db:
            if not self._mine(db, job_id, worker):
                return False
            if progress is None:
                db.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", (now + lease_s, job_id))
            else:
                db.execute("UPDATE jobs SET lease_until = ?, progress = ? WHERE id = ?",
                           (now + lease_s, min(1.0, max(0.0, float(progress))), job_id))
        return True

    def finish(self, job_id: str, worker: str, result=None) -> bool:
        text, now = _dumps(result, "result"), self.clock()
        with self._write() as db:
            if not self._mine(db, job_id, worker):
                return False
            db.execute("UPDATE jobs SET state = 'done', finished_at = ?, lease_until = NULL, progress = 1, "
                       "result = ?, error = NULL WHERE id = ?", (now, text, job_id))
        return True

    def fail(self, job_id: str, worker: str, error, *, retry: bool = True) -> Optional[str]:
        """Back to queued after BACKOFF_S while attempts are left (retry), else
        failed; the state it's in now, or None if it wasn't this worker's."""
        now = self.clock()
        with self._write() as db:
            if not self._mine(db, job_id, worker):
                return None
            r = db.execute("SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if retry and r["attempts"] < r["max_attempts"]:
                wait = BACKOFF_S[min(r["attempts"], len(BACKOFF_S)) - 1]
                db.execute("UPDATE jobs SET state = 'queued', next_at = ?, lease_until = NULL, worker = NULL, "
                           "error = ? WHERE id = ?", (now + wait, _one_line(error), job_id))
                return "queued"
            db.execute("UPDATE jobs SET state = 'failed', finished_at = ?, lease_until = NULL, error = ? "
                       "WHERE id = ?", (now, _one_line(error), job_id))
        return "failed"

    # ── upkeep ──
    def counts(self, app: str) -> dict:
        with self._read() as db:
            rows = db.execute("SELECT state, COUNT(*) AS n FROM jobs WHERE app = ? GROUP BY state", (app,)).fetchall()
        return {**{s: 0 for s in STATES}, **{r["state"]: int(r["n"]) for r in rows}}

    def purge(self, *, keep_s: float = KEEP_S) -> int:
        """Remove jobs that ended more than keep_s ago; how many."""
        cut = self.clock() - keep_s
        with self._write() as db:
            n = db.execute("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('done', 'failed', 'cancelled') "
                           "AND finished_at < ?", (cut,)).fetchone()["n"]
            db.execute("DELETE FROM jobs WHERE state IN ('done', 'failed', 'cancelled') AND finished_at < ?", (cut,))
        return int(n)


# ── running jobs ──────────────────────────────────────────────────────────

class Job:
    """What a handler gets: the job's id, kind, account and params, and progress()."""

    def __init__(self, jobs: Jobs, row: dict, worker: str, lease_s: float):
        self._jobs, self._worker, self._lease_s = jobs, worker, lease_s
        self.id, self.kind, self.account_id = row["id"], row["kind"], row["account_id"]
        self.params, self.attempt = row["params"], row["attempts"]

    def progress(self, fraction: Optional[float] = None) -> None:
        """Renew the lease (call it at least every LEASE_S); Cancelled if the job is no longer ours."""
        if not self._jobs.heartbeat(self.id, self._worker, progress=fraction, lease_s=self._lease_s):
            raise Cancelled(self.id)


def run_one(jobs: Jobs, app: str, handlers: dict, *, worker: Optional[str] = None,
            lease_s: float = LEASE_S) -> Optional[str]:
    """Claim one due job and run it: its id, or None when nothing is due."""
    worker = worker or worker_name()
    row = jobs.claim(app, worker=worker, lease_s=lease_s)
    if row is None:
        return None
    handler = handlers.get(row["kind"])
    if handler is None:
        jobs.fail(row["id"], worker, f"no handler for {row['kind']!r}", retry=False)
        return row["id"]
    try:
        result = handler(Job(jobs, row, worker, lease_s))
    except Cancelled:
        return row["id"]                      # cancelled, or another worker has it: theirs to end
    except Permanent as e:
        jobs.fail(row["id"], worker, e, retry=False)
        return row["id"]
    except Exception as e:
        jobs.fail(row["id"], worker, e)
        return row["id"]
    try:
        jobs.finish(row["id"], worker, result)
    except JobError as e:                     # a result too large to keep
        jobs.fail(row["id"], worker, e, retry=False)
    return row["id"]


def run_until_empty(jobs: Jobs, app: str, handlers: dict, *, max_jobs: Optional[int] = None,
                    max_seconds: Optional[float] = None, worker: Optional[str] = None) -> int:
    """Run due jobs until none is left (or max_jobs, or max_seconds have gone): how many ran."""
    worker = worker or worker_name()
    start, n = time.monotonic(), 0
    while max_jobs is None or n < max_jobs:
        if max_seconds is not None and time.monotonic() - start >= max_seconds:
            break
        if run_one(jobs, app, handlers, worker=worker) is None:
            break
        n += 1
    return n


# ── launchers: what starts a worker after a submit ───────────────────────

class ThreadWorker:
    """This process's worker thread (local runs, the local install, tests):
    a launch wakes it; it runs due jobs until none is left, then waits
    (poll_s, for jobs with a delay or a retry's back-off)."""

    def __init__(self, jobs: Jobs, app: str, handlers: dict, *, poll_s: float = 5.0):
        self.jobs, self.app, self.handlers, self.poll_s = jobs, app, handlers, poll_s
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        forksafe.after_fork_in_child(self._after_fork)

    def _after_fork(self):
        self._wake, self._stop, self._thread = threading.Event(), threading.Event(), None

    def __call__(self, app: str) -> None:
        if app == self.app:
            self.start()
            self._wake.set()

    def start(self) -> "ThreadWorker":
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name=f"cct-jobs-{self.app}", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self):
        import logging
        worker = worker_name()
        while not self._stop.is_set():
            try:
                run_until_empty(self.jobs, self.app, self.handlers, worker=worker)
            except Exception as e:                 # the database away: try again after the wait
                logging.getLogger(__name__).error("jobs: the %s worker: %s", self.app, _one_line(e))
            self._wake.wait(self.poll_s)
            self._wake.clear()


METADATA = "http://metadata.google.internal/computeMetadata/v1"
RUN_API = "https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run"


def _urlopen(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


class CloudRunJob:
    """Starts an execution of a Cloud Run Job (which runs
    `python -m cct_common.jobs run ...`) as this service's own account."""

    def __init__(self, project: str, region: str, job: str, *, opener: Callable = _urlopen):
        for what, v in (("project", project), ("region", region), ("job", job)):
            if not v or not all(c.isalnum() or c in "-_" for c in v):
                raise JobError(f"not a Cloud Run {what}: {v!r}")
        self.project, self.region, self.job, self._opener = project, region, job, opener

    def __call__(self, app: str) -> None:
        req = urllib.request.Request(f"{METADATA}/instance/service-accounts/default/token",
                                     headers={"Metadata-Flavor": "Google"})
        with self._opener(req, 10) as r:
            token = json.loads(r.read())["access_token"]
        url = RUN_API.format(project=urllib.parse.quote(self.project), region=urllib.parse.quote(self.region),
                             job=urllib.parse.quote(self.job))
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with self._opener(req, 30) as r:
            r.read()


def launcher_from(spec: Optional[str], *, jobs: Optional[Jobs] = None, app: str = "", handlers: Optional[dict] = None):
    """CCT_JOBS_LAUNCH's value: "cloud-run-job:<project>/<region>/<job>",
    "thread" (needs jobs, app and handlers), or "off"/empty (none: jobs wait
    for a scheduled run)."""
    if not spec or spec == "off":
        return None
    if spec == "thread":
        if jobs is None or not app or handlers is None:
            raise JobError("a thread launcher needs the jobs, the app and its handlers")
        return ThreadWorker(jobs, app, handlers)
    if spec.startswith("cloud-run-job:"):
        parts = spec[len("cloud-run-job:"):].split("/")
        if len(parts) != 3:
            raise JobError(f"cloud-run-job:<project>/<region>/<job>, not {spec!r}")
        return CloudRunJob(*parts)
    raise JobError(f"CCT_JOBS_LAUNCH is cloud-run-job:..., thread or off, not {spec!r}")


# ── the Cloud Run Job's command ───────────────────────────────────────────

def _handlers(spec: str) -> dict:
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise SystemExit(f"--handlers is module:attribute, not {spec!r}")
    handlers = getattr(importlib.import_module(module), attr)
    if not isinstance(handlers, dict):
        raise SystemExit(f"{spec} is not a dict of kind -> handler")
    return handlers


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cct_common.jobs", description="Run an app's queued jobs.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run due jobs until none is left, then exit (a Cloud Run Job's run)")
    run.add_argument("--app", required=True)
    run.add_argument("--handlers", required=True, help="module:attribute, a dict of kind -> handler")
    run.add_argument("--database", help="default: DATABASE_URL")
    run.add_argument("--max-jobs", type=int)
    run.add_argument("--max-seconds", type=float)
    purge = sub.add_parser("purge", help=f"remove jobs that ended over {KEEP_S // 86400} days ago")
    purge.add_argument("--database", help="default: DATABASE_URL")
    a = ap.parse_args(argv)
    database = a.database or os.environ.get("DATABASE_URL")
    if not database:
        ap.error("no database: pass --database or set DATABASE_URL")
    jobs = Jobs(database)
    if a.cmd == "purge":
        print(f"jobs purge: {jobs.purge()} removed", flush=True)
        return 0
    handlers = _handlers(a.handlers)
    from .server_use import job_use
    with job_use(database, f"{a.app}-jobs", "run"):
        n = run_until_empty(jobs, a.app, handlers, max_jobs=a.max_jobs, max_seconds=a.max_seconds)
    print(f"jobs run {a.app}: {n} ran; now {jobs.counts(a.app)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
