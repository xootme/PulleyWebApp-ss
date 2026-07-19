"""
text_utils.py — small, dependency-free string/number helpers shared
across CCT Flask apps. No app-specific content; safe to use anywhere.
"""
from __future__ import annotations


def safe_float(val, default) -> float:
    """Convert val to float, falling back to default on empty string or None."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return float(default)


def safe_dl_name(name: str) -> str:
    """Sanitise a download filename for the Content-Disposition header.

    A '+' in the filename caused Chromium to download the file and then
    immediately mark it "Removed" (file never landed on disk). Replace
    '+' and whitespace with '-' so the name only uses plainly-safe
    characters.
    """
    base, dot, ext = name.rpartition(".")
    stem = base if dot else name
    stem = stem.replace("+", "-").replace(" ", "-")
    while "--" in stem:
        stem = stem.replace("--", "-")
    return f"{stem}.{ext}" if dot else stem
