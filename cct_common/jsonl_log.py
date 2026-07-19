"""
jsonl_log.py — thread-safe append + retention-based trim for JSON-Lines
log files. No app-specific content; used for lightweight usage/download
analytics without a database.

    from cct_common.jsonl_log import append_jsonl, trim_jsonl

    append_jsonl(path, {"ts": time.time(), "event": "download", "fmt": "step"})
    trim_jsonl(path, retention_days=30)  # drop entries older than 30 days

Entries are plain dicts; `trim_jsonl` expects a numeric "ts" (unix epoch
seconds) field on each one to decide what to keep.
"""
from __future__ import annotations

import json
import threading
import time

_lock = threading.Lock()


def append_jsonl(path, obj: dict) -> None:
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj) + "\n")


def trim_jsonl(path, retention_days: float) -> None:
    """Drop entries older than retention_days from a .jsonl file."""
    cutoff = time.time() - retention_days * 86400
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        kept = []
        for line in lines:
            try:
                if json.loads(line).get("ts", 0) >= cutoff:
                    kept.append(line)
            except Exception:
                pass
        if len(kept) < len(lines):
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(kept)
    except FileNotFoundError:
        pass
    except Exception:
        pass  # trimming is best-effort; a log-file hiccup must never break the app
