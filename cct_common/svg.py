"""
svg.py — CCT metadata for SVG files.

Convention: a `<metadata><cct>{...}</cct></metadata>` element inserted
right after the opening `<svg ...>` tag. Ported from PulleyWebApp's app.py
`_embed_svg`.
"""
from __future__ import annotations

import re

from .core import dump_blob, parse_meta

_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>")
_BLOB_RE = re.compile(r"<cct>([\s\S]+?)</cct>")


def embed_svg(svg_str: str, params: dict, *, tool: str | None = None,
              version: str | None = None,
              schema_version: int | None = None) -> str:
    """Insert a CCT <metadata> element after the opening <svg> tag.

    Best-effort: returns `svg_str` unchanged if no <svg> tag is found or
    anything else goes wrong.
    """
    try:
        blob = dump_blob(params, tool=tool, version=version,
                         schema_version=schema_version)
        meta_tag = f"<metadata><cct>{blob}</cct></metadata>"
        m = _SVG_OPEN_RE.search(svg_str)
        if not m:
            return svg_str
        pos = m.end()
        return svg_str[:pos] + "\n" + meta_tag + svg_str[pos:]
    except Exception:
        return svg_str


def extract_svg(svg_str: str) -> dict | None:
    """Read back the CCT metadata dict embedded by embed_svg, or None."""
    try:
        m = _BLOB_RE.search(svg_str)
        if not m:
            return None
        return parse_meta(m.group(1))
    except Exception:
        return None
