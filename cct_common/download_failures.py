"""
download_failures.py — every download that failed, kept for the admin page.

A download can fail in three places, and each leaves a row here:

  server  a download route answered with an error (400/500 …): the route,
          the error text, how long it took (an after_request hook);
  job     a background download (a STEP job, the Download window's zip)
          finished with an error: the app calls record_job_failure() from
          its finish_job;
  page    the browser saw it fail where the server never could say so: the
          server process killed at its time limit, the instance gone (out of
          memory), the network dropped. The page POSTs it to
          /api/download-failure (cct_download.js cctReportDownloadFailure).

Each row has when, which app / version / Cloud Run revision and which server
process (its boot id), the route, the error, the time taken, and the design
the download was for — so a failure can be reopened and tried again. No
account, email or token: who it was isn't needed to reproduce it.

Refusals are not failures and aren't kept: sign in (401), pay (402), not
allowed (403), the trial limit (429), not enough tokens. A page report or a
job's row for a job already recorded is dropped (same job id), so a job
failure is one row. A zip that fails on one of its files (the bundle calls
each file's route in-process) is that file's row: its route, part and
error, with the zip job's id; the job's own row, the page's report and, for
a zip made at once, the bundle route's answer are dropped (0.30.0: Worm and
Gear saw two rows a zip). Rows older than RETENTION_S are deleted as new
ones arrive.

A row the server kept says so on its answer (KEPT_HEADER), so the page
doesn't report the same failure again (0.31.1: a zip made at once was the
file's row and the page's). Our own test traffic leaves no row: the
X-CCT-Test header, a test run's user agent (server_use.is_test_request),
and the zips and jobs it starts (a release's smoke test asks for an expired
file on purpose). The design's own copy that a file embeds (`design`, the
whole design as JSON) isn't kept: the other params are the design, and it
took a zip's row past MAX_PARAMS_CHARS.

    store = DownloadFailures(os.environ["DATABASE_URL"])
    register_download_failure_log(app, store, app_name="pulleys", app_version=V)
    register_admin(app, accounts, ..., download_failures=store)
"""
from __future__ import annotations

import contextvars
import json
import os
import secrets
import threading
import time
from typing import Optional

from .server_use import is_test_request
from .sqlite_db import SqliteDB

RETENTION_S = 90 * 24 * 3600
MAX_PARAMS_CHARS = 20000
MAX_ERROR_CHARS = 2000
# The routes whose failures are downloads. A status poll's "Job not found"
# isn't one by itself (the page reports what it led to).
PREFIXES = ("/download/", "/api/download/", "/api/v1/files", "/api/v1/export")
SKIP_PREFIXES = ("/api/download-status/",)
REFUSAL_STATUSES = frozenset({401, 402, 403, 429})
REFUSAL_ERRORS = ("Not enough tokens", "reached its daily limit", "Sign in to download")
# Never kept: they say who, not what.
PRIVATE_KEYS = frozenset({"_fpt", "session_id", "design_id", "token", "access_token", "email"})
# Never kept: the whole design again, as JSON, for the file to embed (0.31.1).
BULKY_KEYS = frozenset({"design"})
# On a download route's error answer when its row was kept: the page needn't
# report it (cct_download.js).
KEPT_HEADER = "X-CCT-Download-Failure"
# Page reports per server process per minute: enough for a bad hour, not a flood.
PAGE_REPORTS_PER_MIN = 30
# The WSGI environ key that marks a zip's own call to a file route
# (download_bundle.build_zip): {"job_id": …}; the failure log records a failed
# call as the zip's one row and sets "recorded" (0.30.0). Set in-process
# only: a request from outside can't carry it.
BUNDLE_ENVIRON = "cct.bundle"
# Set while a test request's zip is built (download_bundle), in its thread too.
_TESTING = contextvars.ContextVar("cct_download_testing", default=False)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS download_failures (
    failure_id   TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    app          TEXT,
    app_version  TEXT,
    revision     TEXT,
    instance     TEXT,
    source       TEXT NOT NULL,
    route        TEXT,
    part         TEXT,
    fmt          TEXT,
    status       INTEGER,
    error        TEXT,
    duration_ms  INTEGER,
    job_id       TEXT,
    params       TEXT
);
CREATE INDEX IF NOT EXISTS download_failures_created ON download_failures (created_at);
CREATE INDEX IF NOT EXISTS download_failures_job ON download_failures (job_id);
"""


def _clean_params(params) -> dict:
    """The params less who it was and the design's embedded copy; a zip's
    files' params too."""
    if not isinstance(params, dict):
        return {}
    out = {str(k): v for k, v in params.items() if str(k) not in PRIVATE_KEYS | BULKY_KEYS}
    if isinstance(out.get("files"), list):
        out["files"] = [dict(f, params=_clean_params(f["params"]))
                        if isinstance(f, dict) and isinstance(f.get("params"), dict) else f for f in out["files"]]
    return out


def is_testing() -> bool:
    """This request, or the zip or job it started, is our own test traffic."""
    if _TESTING.get():
        return True
    try:
        from flask import has_request_context, request
    except ImportError:
        return False
    return has_request_context() and is_test_request(request.headers, request.user_agent.string,
                                                     request.query_string.decode("latin-1"))


def is_refusal(status: Optional[int], error: Optional[str]) -> bool:
    """A refusal (sign in, pay, a limit) — the download worked as meant."""
    if status in REFUSAL_STATUSES:
        return True
    return any(e in (error or "") for e in REFUSAL_ERRORS)


class DownloadFailures(SqliteDB):
    def __init__(self, path: str, clock=time.time):
        super().__init__(path, _SCHEMA)
        self.clock = clock

    def add(self, *, source: str, app: str = "", app_version: str = "", route: str = "",
            part: str = "", fmt: str = "", status: Optional[int] = None, error: str = "",
            duration_ms: Optional[int] = None, job_id: str = "", params=None,
            revision: Optional[str] = None, instance: Optional[str] = None) -> Optional[str]:
        """Keep one failure; returns its id, or None when it isn't kept (a
        refusal, or a page report or job row of a job already recorded)."""
        if is_refusal(status, error):
            return None
        now = self.clock()
        fid = secrets.token_hex(8)
        p = json.dumps(_clean_params(params), default=str)
        if len(p) > MAX_PARAMS_CHARS:
            p = json.dumps({"_truncated": p[:MAX_PARAMS_CHARS]})
        with self._write() as db:
            if source in ("page", "job") and job_id and db.execute(
                    "SELECT 1 FROM download_failures WHERE job_id = ?", (job_id,)).fetchone():
                return None
            db.execute("DELETE FROM download_failures WHERE created_at < ?", (now - RETENTION_S,))
            db.execute(
                "INSERT INTO download_failures (failure_id, created_at, app, app_version, revision, "
                "instance, source, route, part, fmt, status, error, duration_ms, job_id, params) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fid, now, app, app_version,
                 os.environ.get("K_REVISION", "") if revision is None else revision,
                 _instance_id() if instance is None else instance,
                 source, route[:300], part[:60], fmt[:20], status, (error or "")[:MAX_ERROR_CHARS],
                 duration_ms, job_id or None, p))
        return fid

    def list(self, limit: int = 500) -> list[dict]:
        """Newest first, for the admin dashboard (cct_common.admin)."""
        with self._read() as db:
            rows = db.execute("SELECT * FROM download_failures ORDER BY created_at DESC LIMIT ?",
                              (limit,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d["params"] or "{}")
            out.append(d)
        return out

    def delete(self, failure_id: str) -> bool:
        with self._write() as db:
            cur = db.execute("DELETE FROM download_failures WHERE failure_id = ?", (failure_id,))
            return (cur.rowcount or 0) > 0

    def clear(self) -> int:
        with self._write() as db:
            cur = db.execute("DELETE FROM download_failures")
            return cur.rowcount or 0


def _instance_id() -> str:
    """This server process: its live-reload boot id when there is one."""
    try:
        from .live_reload import _BOOT_ID
        return str(_BOOT_ID)[:16]
    except Exception:
        return str(os.getpid())


_FORMATS = ("step", "stl", "svg", "dxf", "zip", "bundle")


def _fmt_of(path: str) -> str:
    """The file format a route makes: /download/flange-stl -> stl,
    /api/download/all-step-async -> step, /api/download/bundle -> bundle."""
    import re
    words = re.split(r"[/\-_.]", path.lower())
    return next((w for w in words if w in _FORMATS), "")


def _route_of(path: str) -> bool:
    return path.startswith(PREFIXES) and not path.startswith(SKIP_PREFIXES)


def record_job_failure(store: Optional[DownloadFailures], job, error: str, *, app_name: str,
                       app_version: str = "") -> None:
    """From the app's finish_job when a background download fails. Never raises.
    Not for a test request's job: one run in its request (Cloud Run), or a
    zip (download_bundle) wherever it runs."""
    if store is None or job is None or is_testing():
        return
    try:
        started = getattr(job, "started", None) or getattr(job, "created", None)
        ms = None
        if started is not None:
            from datetime import datetime
            ms = int((datetime.now() - started).total_seconds() * 1000)
        params = getattr(job, "params", None) or {}
        store.add(source="job", app=app_name, app_version=app_version,
                  route=f"job:{getattr(job, 'type', 'job')}", status=None, error=error,
                  duration_ms=ms, job_id=getattr(job, "id", ""), params=params,
                  fmt=str(params.get("format", "")) if isinstance(params, dict) else "")
    except Exception:
        import logging
        logging.getLogger(__name__).exception("could not record a failed download job")


def register_download_failure_log(app, store: Optional[DownloadFailures], *, app_name: str,
                                  app_version: str = "") -> None:
    """The server half (an after_request on the download routes) and the
    page's report route, POST /api/download-failure. A store of None mounts
    the route as a no-op, so the page needn't know whether it's kept."""
    from flask import g, jsonify, request

    budget = {"minute": 0, "count": 0}
    lock = threading.Lock()

    @app.before_request
    def _cct_dl_started():
        if _route_of(request.path):
            g._cct_dl_t0 = time.monotonic()

    @app.after_request
    def _cct_dl_failed(resp):
        if store is None or resp.status_code < 400 or not _route_of(request.path):
            return resp
        if g.get("cct_download_failure_recorded"):                     # a zip's file said it already
            resp.headers[KEPT_HEADER] = "kept"
            return resp
        bundle = request.environ.get(BUNDLE_ENVIRON)                  # a zip's own call to this file
        if is_testing() or (isinstance(bundle, dict) and bundle.get("testing")):
            if isinstance(bundle, dict):
                bundle["recorded"] = True                             # nor the zip's own row
            return resp
        try:
            t0 = getattr(g, "_cct_dl_t0", None)
            ms = int((time.monotonic() - t0) * 1000) if t0 is not None else None
            err = ""
            if resp.is_json:
                body = resp.get_json(silent=True) or {}
                err = str(body.get("error") or body.get("code") or "")
            if not err and not resp.direct_passthrough:
                err = resp.get_data(as_text=True)[:300]
            params = dict(request.args)
            if request.is_json:
                body = request.get_json(silent=True)
                if isinstance(body, dict):
                    params.update(body)
            fid = store.add(source="server", app=app_name, app_version=app_version, route=request.path,
                      part=str(params.get("part") or params.get("pulley") or ""),
                      fmt=_fmt_of(request.path),
                      status=resp.status_code, error=err, duration_ms=ms, params=params,
                      job_id=(bundle or {}).get("job_id", "") if isinstance(bundle, dict) else "")
            if fid is not None:
                resp.headers[KEPT_HEADER] = "kept"
                if isinstance(bundle, dict):
                    bundle["recorded"] = True
        except Exception:
            app.logger.exception("could not record a failed download")
        return resp

    def report():
        if store is None or is_testing():
            return jsonify({"kept": False})
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            return jsonify({"error": "cross-origin"}), 403
        body = request.get_json(silent=True) if request.content_length and request.content_length < 64000 else None
        if not isinstance(body, dict):
            return jsonify({"error": "send JSON"}), 400
        with lock:
            minute = int(time.time() // 60)
            if budget["minute"] != minute:
                budget.update(minute=minute, count=0)
            budget["count"] += 1
            if budget["count"] > PAGE_REPORTS_PER_MIN:
                return jsonify({"kept": False, "error": "too many reports"}), 429
        try:
            status = body.get("status")
            status = int(status) if status not in (None, "") else None
            ms = body.get("duration_ms")
            ms = int(ms) if ms not in (None, "") else None
        except (TypeError, ValueError):
            return jsonify({"error": "status and duration_ms are numbers"}), 400
        fid = store.add(source="page", app=app_name, app_version=app_version,
                        route=str(body.get("route") or "")[:300], part=str(body.get("part") or ""),
                        fmt=str(body.get("fmt") or ""), status=status,
                        error=str(body.get("error") or ""), duration_ms=ms,
                        job_id=str(body.get("job_id") or ""), params=body.get("params"))
        return jsonify({"kept": fid is not None})

    app.add_url_rule("/api/download-failure", endpoint="_cct_download_failure",
                     view_func=report, methods=["POST"])
