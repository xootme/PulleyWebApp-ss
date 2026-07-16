"""
stl.py — CCT metadata for STL files.

Convention: the same `/* CCT:{...} */` comment as STEP, appended as a text
trailer after the file's binary content. Binary STL parsers read exactly
`80-byte header + uint32 triangle count + 50 bytes * count` and stop, so
trailing bytes are silently ignored by every standard slicer/CAD tool.
Ported from PulleyWebApp's app.py `_embed_stl`.
"""
from __future__ import annotations

from .core import COMMENT_BLOB_RE, dump_blob, parse_meta


def embed_stl(stl_bytes: bytes, params: dict, *, tool: str | None = None,
              version: str | None = None,
              schema_version: int | None = None) -> bytes:
    """Append a CCT metadata trailer after an STL's triangle data.

    Best-effort: returns `stl_bytes` unchanged on failure.
    """
    try:
        blob = dump_blob(params, tool=tool, version=version,
                         schema_version=schema_version)
        trailer = f"\n/* CCT:{blob} */\n".encode("utf-8")
        return stl_bytes + trailer
    except Exception:
        return stl_bytes


def extract_stl(stl_bytes: bytes) -> dict | None:
    """Read back the CCT metadata dict embedded by embed_stl, or None."""
    try:
        text = stl_bytes.decode("utf-8", errors="replace")
        m = COMMENT_BLOB_RE.search(text)
        if not m:
            return None
        return parse_meta(m.group(1))
    except Exception:
        return None
