"""
dxf.py — CCT metadata for DXF files.

Convention: a group-code 999 comment entity inserted immediately before the
file's EOF marker. Ported from PulleyWebApp's app.py `_embed_dxf`.
"""
from __future__ import annotations

import re

from .core import dump_blob, parse_meta

_EOF_MARKERS = (b"  0\r\nEOF\r\n", b"  0\nEOF\n", b"0\r\nEOF\r\n", b"0\nEOF\n")
_BLOB_RE = re.compile(r"999\nCCT:(\{.+\})")


def embed_dxf(dxf_bytes: bytes, params: dict, *, tool: str | None = None,
              version: str | None = None,
              schema_version: int | None = None) -> bytes:
    """Insert a CCT metadata group-code-999 comment before the DXF EOF
    marker. Best-effort: returns `dxf_bytes` unchanged on failure."""
    try:
        blob = dump_blob(params, tool=tool, version=version,
                         schema_version=schema_version)
        comment = f"999\nCCT:{blob}\n".encode("utf-8")
        for marker in _EOF_MARKERS:
            if marker in dxf_bytes:
                return dxf_bytes.replace(marker, comment + marker, 1)
        return dxf_bytes + comment
    except Exception:
        return dxf_bytes


def extract_dxf(dxf_bytes: bytes) -> dict | None:
    """Read back the CCT metadata dict embedded by embed_dxf, or None."""
    try:
        text = dxf_bytes.decode("utf-8", errors="replace")
        m = _BLOB_RE.search(text)
        if not m:
            return None
        return parse_meta(m.group(1))
    except Exception:
        return None
