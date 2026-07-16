"""
step.py — CCT metadata for STEP files.

Convention (CCT_Architecture.md "Embedded metadata"): a `/* CCT:{...} */`
comment inserted right after the HEADER section's `ENDSEC;`, before `DATA;`.
Ported from PulleyWebApp's app.py `_embed_step` / `_rename_step_product`.
"""
from __future__ import annotations

import re

from .core import COMMENT_BLOB_RE, dump_blob, parse_meta

_INSERT_RE = re.compile(r"(ENDSEC;\s*\n)(DATA;)")
# STEP wraps long entity definitions across physical lines (~80 cols), so the
# comma/quote separators between PRODUCT's two name fields may have
# whitespace (incl. newlines) around them — \s* tolerates that. A prior
# version of this regex (ported byte-for-byte from PulleyWebApp) required
# them adjacent and silently failed to match cadquery's wrapped output.
_PRODUCT_RE = re.compile(r"PRODUCT\(\s*'[^']*'\s*,\s*'[^']*'\s*,")


def embed_step(step_bytes: bytes, params: dict, *, tool: str | None = None,
               version: str | None = None,
               schema_version: int | None = None) -> bytes:
    """Insert a CCT metadata comment into a STEP file's header.

    Best-effort: on any failure (unexpected encoding, no ENDSEC/DATA match)
    returns `step_bytes` unchanged rather than raising, since a failed
    metadata embed should never break an export the user is waiting on.
    """
    try:
        blob = dump_blob(params, tool=tool, version=version,
                         schema_version=schema_version)
        comment = f"/* CCT:{blob} */\n"
        text = step_bytes.decode("utf-8", errors="replace")
        text = _INSERT_RE.sub(rf"\1{comment}\2", text, count=1)
        return text.encode("utf-8")
    except Exception:
        return step_bytes


def extract_step(step_bytes: bytes) -> dict | None:
    """Read back the CCT metadata dict embedded by embed_step, or None if
    no CCT block is present / it doesn't parse."""
    try:
        text = step_bytes.decode("utf-8", errors="replace")
        m = COMMENT_BLOB_RE.search(text)
        if not m:
            return None
        return parse_meta(m.group(1))
    except Exception:
        return None


def rename_step_product(step_bytes: bytes, product_name: str) -> bytes:
    """Replace the STEP PRODUCT name/description so CAD tools (Fusion 360,
    etc.) show the download filename as the component name, not whatever
    the exporting CAD kernel defaulted to. Best-effort, like embed_step."""
    try:
        text = step_bytes.decode("utf-8", errors="replace")
        safe = product_name.replace("'", " ")
        text = _PRODUCT_RE.sub(f"PRODUCT('{safe}','{safe}',", text)
        return text.encode("utf-8")
    except Exception:
        return step_bytes
