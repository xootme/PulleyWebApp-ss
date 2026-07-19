"""
machine_id.py — resolve a stable-ish machine identifier for a Flask
request: explicit request attribute, X-Machine-ID header, or a hash of
IP+User-Agent as a last resort.
"""
from __future__ import annotations

import hashlib


def get_machine_id(request) -> str:
    if getattr(request, "machine_id", None):
        return request.machine_id
    mid = request.headers.get("X-Machine-ID")
    if mid:
        return mid
    ip = request.remote_addr or "0.0.0.0"
    ua = request.headers.get("User-Agent", "unknown")
    return hashlib.sha256(f"{ip}:{ua}".encode()).hexdigest()[:16]
