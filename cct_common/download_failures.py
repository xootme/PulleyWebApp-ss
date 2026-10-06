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
allowed (403), the trial limit (429), not enough tokens. A page report for a
job the server already recorded is dropped (same job id), so a job failure
is one row. Rows older than RETENTION_S are deleted as new ones arrive.

    store = DownloadFailures(os.environ["DATABASE_URL"])
    register_download_failure_log(app, store, app_name="pulleys", app_version=V)
    register_admin(app, accounts, ..., download_failures=store)
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Optional

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
# Page reports per server process per minute: enough for a bad hour, not a flood.
PAGE_REPORTS_PER_MIN = 30

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
    if not isinstance(params, dict):
        return {}
    return {str(k): v for k, v in params.items() if str(k) not in PRIVATE_KEYS}


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
        refusal, or a page report of a job already recorded)."""
        if is_refusal(status, error):
            return None
        now = self.clock()
        fid = secrets.token_hex(8)
        p = json.dumps(_clean_params(params), default=str)
        if len(p) > MAX_PARAMS_CHARS:
            p = json.dumps({"_truncated": p[:MAX_PARAMS_CHARS]})
        with self._write() as db:
            if source == "page" and job_id and db.execute(
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
    """From the app's finish_job when a background download fails. Never raises."""
    if store is None or job is None:
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
            store.add(source="server", app=app_name, app_version=app_version, route=request.path,
                      part=str(params.get("part") or params.get("pulley") or ""),
                      fmt=_fmt_of(request.path),
                      status=resp.status_code, error=err, duration_ms=ms, params=params)
        except Exception:
            app.logger.exception("could not record a failed download")
        return resp

    def report():
        if store is None:
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
