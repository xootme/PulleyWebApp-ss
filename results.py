"""
results.py — finished background downloads (the async STEP jobs and the
download window's zip), served at /download/result/<token>/<filename>.

The token is 192 random bits, so a finished file's link can't be guessed.
The route this replaces, /download/<job_id>.step, used the 8-hex job id —
about four billion values, enumerable — and never deleted the files, so
anyone could have fetched other people's paid STEP files. Files here are
deleted an hour after they're made; download again from the page after that.

    url = save_result(log_dir, step_bytes, "HTD-5M-20T.step")
    finish_job(job.id, output_file=url)
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import time
from urllib.parse import quote

RESULT_TTL_S = 3600
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,64}")
_LEGACY_STEP = re.compile(r"[0-9a-f]{8}\.step")
_MIMETYPES = {".step": "application/step", ".zip": "application/zip",
              ".stl": "model/stl", ".dxf": "application/dxf", ".svg": "image/svg+xml"}


def _root(log_dir: str) -> str:
    return os.path.join(log_dir, "results")


def purge(log_dir: str) -> None:
    """Delete results older than RESULT_TTL_S."""
    root = _root(log_dir)
    if not os.path.isdir(root):
        return
    cutoff = time.time() - RESULT_TTL_S
    for d in os.listdir(root):
        p = os.path.join(root, d)
        try:
            if os.path.getmtime(p) < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def save_result(log_dir: str, data: bytes, filename: str) -> str:
    """Store a finished download; returns the URL it can be fetched from."""
    purge(log_dir)
    filename = os.path.basename(filename) or "download"
    token = secrets.token_urlsafe(24)
    folder = os.path.join(_root(log_dir), token)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, filename), "wb") as fh:
        fh.write(data)
    return f"/download/result/{token}/{quote(filename)}"


def register_result_routes(app, *, log_dir: str) -> None:
    from flask import send_file

    # Files the old /download/<job_id>.step route left in the log dir.
    try:
        for f in os.listdir(log_dir):
            if _LEGACY_STEP.fullmatch(f):
                os.remove(os.path.join(log_dir, f))
    except OSError:
        pass

    def fetch_result(token, filename):
        filename = os.path.basename(filename)
        path = os.path.join(_root(log_dir), token, filename)
        if not _TOKEN.fullmatch(token) or not os.path.isfile(path):
            return "This download has expired — download it again from the page.", 404
        mimetype = _MIMETYPES.get(os.path.splitext(filename)[1].lower(), "application/octet-stream")
        return send_file(path, mimetype=mimetype, as_attachment=True, download_name=filename)

    app.add_url_rule("/download/result/<token>/<filename>", view_func=fetch_result)
