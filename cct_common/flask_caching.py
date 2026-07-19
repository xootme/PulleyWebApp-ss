"""
flask_caching.py — small Flask request/response middlewares shared across
CCT apps: conditional-GET caching, admin-route CORS, and a download-
completion signal cookie. Each is opt-in and independent; register only
what a given app needs.

    from cct_common.flask_caching import (
        register_etag_caching, register_admin_cors, register_download_signal,
    )

    register_etag_caching(app, cacheable_prefixes=["/download/", "/api/spec"],
                          build_time="2026-07-19 12:00")
    register_admin_cors(app, prefixes=["/api/admin/", "/api/subscribers/"])
    register_download_signal(app, download_prefix="/download/")
"""
from __future__ import annotations

import hashlib
import time


def register_etag_caching(app, cacheable_prefixes, build_time: str,
                          max_age: int = 3600, exempt_prefixes=("/download/",)):
    """Return 304 Not Modified for GET requests whose ETag (derived from
    path + sorted query params + build_time) matches the client's
    If-None-Match header.

    `exempt_prefixes` are never 304'd even if also in `cacheable_prefixes`
    — e.g. file downloads are served no-store, so the browser has no
    stored body to fall back on; a 304 there yields an empty download.
    """
    from flask import Response, request

    def _etag():
        raw = (build_time + "|" + request.path + "|"
              + "|".join(f"{k}={v}" for k, v in sorted(request.args.items())))
        return '"' + hashlib.md5(raw.encode()).hexdigest() + '"'

    @app.before_request
    def _check_client_cache():
        if request.method != "GET":
            return
        if any(request.path.startswith(p) for p in exempt_prefixes):
            return
        if not any(request.path.startswith(p) for p in cacheable_prefixes):
            return
        etag = _etag()
        incoming = request.headers.get("If-None-Match", "")
        # flask-compress appends ':gzip' inside the quotes on compressed
        # responses, e.g. "abc123" -> "abc123:gzip" — strip before comparing.
        incoming_norm = incoming[:-6] + '"' if incoming.endswith(':gzip"') else incoming
        if incoming_norm == etag:
            return Response(status=304, headers={
                "ETag": etag, "Cache-Control": f"public, max-age={max_age}",
            })


def register_admin_cors(app, prefixes=("/api/admin/", "/api/subscribers/")):
    """Allow a local admin dashboard page to call these API routes cross-origin."""
    from flask import request

    @app.after_request
    def _admin_cors(response):
        if any(request.path.startswith(p) for p in prefixes):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        return response


def register_download_signal(app, download_prefix: str = "/download/",
                             cookie_name: str = "download_signal", max_age: int = 60):
    """Set a short-lived cookie on every download response so client JS can
    detect that a file download actually started. Browsers download files
    via a hidden iframe pointed at the real URL — there is no JS-visible
    completion event for that, so this cookie is the signal. Value is
    "<epoch_ms>-<status>"; it changes on every download so the client sees
    it differ from the value it snapshotted before triggering the download.
    """
    from flask import request

    @app.after_request
    def _signal_download(response):
        if request.path.startswith(download_prefix):
            marker = f"{int(time.time() * 1000)}-{response.status_code}"
            response.set_cookie(cookie_name, marker, max_age=max_age,
                                path="/", samesite="Lax")
        return response
