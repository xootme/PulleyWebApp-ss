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

Where the files live: a folder in the log directory, or — when
RESULTS_BUCKET is set (Cloud Run, several servers, no shared disk) — a
Cloud Storage bucket, so the server that serves the link needn't be the one
that made the file. Links older than an hour are refused either way; the
bucket's lifecycle rule deletes the objects themselves after a day.
"""
from __future__ import annotations

import io
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime
from urllib.parse import quote

RESULT_TTL_S = 3600
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,64}")
_LEGACY_STEP = re.compile(r"[0-9a-f]{8}\.step")
_MIMETYPES = {".step": "application/step", ".zip": "application/zip",
              ".stl": "model/stl", ".dxf": "application/dxf", ".svg": "image/svg+xml"}


def _mimetype(filename: str) -> str:
    return _MIMETYPES.get(os.path.splitext(filename)[1].lower(), "application/octet-stream")


# ── Cloud Storage (JSON API over plain HTTP) ──────────────────────────────

_METADATA_TOKEN_URL = ("http://metadata.google.internal/computeMetadata/v1/"
                       "instance/service-accounts/default/token")
_GCS = "https://storage.googleapis.com"


class GcsResults:
    """Result files in a Cloud Storage bucket. Authenticates as the Cloud Run
    service's own account (the metadata server hands out short-lived access
    tokens), so there is no key to store. `http` is a requests-like session,
    swappable in tests."""

    def __init__(self, bucket: str, http=None):
        import requests
        self.bucket = bucket
        self.http = http or requests.Session()
        self._token, self._token_expiry = "", 0.0
        self._lock = threading.Lock()

    def _auth(self) -> dict:
        with self._lock:
            if time.time() > self._token_expiry - 60:
                r = self.http.get(_METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"}, timeout=5)
                r.raise_for_status()
                body = r.json()
                self._token = body["access_token"]
                self._token_expiry = time.time() + float(body.get("expires_in", 300))
            return {"Authorization": f"Bearer {self._token}"}

    def _object_url(self, name: str) -> str:
        return f"{_GCS}/storage/v1/b/{self.bucket}/o/{quote(name, safe='')}"

    def save(self, name: str, data: bytes, content_type: str) -> None:
        r = self.http.post(f"{_GCS}/upload/storage/v1/b/{self.bucket}/o",
                           params={"uploadType": "media", "name": name}, data=data,
                           headers={**self._auth(), "Content-Type": content_type}, timeout=60)
        r.raise_for_status()

    def load(self, name: str, max_age_s: float) -> bytes | None:
        """The object's bytes, or None if it's missing or older than max_age_s."""
        meta = self.http.get(self._object_url(name), headers=self._auth(), timeout=10)
        if meta.status_code == 404:
            return None
        meta.raise_for_status()
        created = datetime.fromisoformat(meta.json()["timeCreated"].replace("Z", "+00:00"))
        if time.time() - created.timestamp() > max_age_s:
            return None
        r = self.http.get(self._object_url(name), params={"alt": "media"},
                          headers=self._auth(), timeout=60)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content


_gcs: GcsResults | None = None


def configure(*, bucket: str | None = None, http=None) -> None:
    """Keep results in a Cloud Storage bucket (None: the local folder)."""
    global _gcs
    _gcs = GcsResults(bucket, http) if bucket else None


# ── local folder ──────────────────────────────────────────────────────────

def _root(log_dir: str) -> str:
    return os.path.join(log_dir, "results")


def purge(log_dir: str) -> None:
    """Delete results older than RESULT_TTL_S (local folder only; the
    bucket's lifecycle rule does this for Cloud Storage)."""
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
    filename = os.path.basename(filename) or "download"
    token = secrets.token_urlsafe(24)
    if _gcs is not None:
        _gcs.save(f"results/{token}/{filename}", data, _mimetype(filename))
    else:
        purge(log_dir)
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

    expired = "This download has expired — download it again from the page.", 404

    def fetch_result(token, filename):
        filename = os.path.basename(filename)
        if not _TOKEN.fullmatch(token):
            return expired
        if _gcs is not None:
            data = _gcs.load(f"results/{token}/{filename}", RESULT_TTL_S)
            if data is None:
                return expired
            return send_file(io.BytesIO(data), mimetype=_mimetype(filename),
                             as_attachment=True, download_name=filename)
        path = os.path.join(_root(log_dir), token, filename)
        if not os.path.isfile(path):
            return expired
        return send_file(path, mimetype=_mimetype(filename), as_attachment=True,
                         download_name=filename)

    app.add_url_rule("/download/result/<token>/<filename>", view_func=fetch_result)
