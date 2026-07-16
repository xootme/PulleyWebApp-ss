"""
core.py — the shared CCT metadata blob shape.

Every embedded-metadata function across every format (STEP, STL, DXF, SVG)
and every CCT tool (PulleyWebApp, EBoxDesigner, ...) wraps the same JSON
blob, built here, so any CCT reader (web app import, Fusion/FreeCAD addins)
can parse any tool's export with one regex/one JSON.loads.

Field names (`cct`, `v`, `sv`) are load-bearing: the FreeCAD addon
(CCT_Plugins/FreeCAD/PulleyWebApp-FreeCAD-addon/cct_pulley/importer.py) and
the Fusion 360 addin already parse these exact keys. `tool` is new and
additive — old readers ignore unknown keys.
"""
from __future__ import annotations

import json
import re

# Shared by STEP and STL — both use the same "/* CCT:{...} */" comment
# convention (CCT_Architecture.md "Embedded metadata").
COMMENT_BLOB_RE = re.compile(r"/\* CCT:(\{.+?\}) \*/")


def build_meta(params: dict, *, tool: str | None = None,
               version: str | None = None,
               schema_version: int | None = None) -> dict:
    """The metadata dict embedded in every export.

    `params` should be JSON-serializable (the tool's own design-parameter
    dict — a query-arg mapping, a dataclass ``.to_dict()``, whatever the
    calling tool already uses as its serialized model).
    """
    meta: dict = {"cct": dict(params)}
    if tool is not None:
        meta["tool"] = tool
    if version is not None:
        meta["v"] = version
    if schema_version is not None:
        meta["sv"] = schema_version
    return meta


def dump_blob(params: dict, *, tool: str | None = None,
              version: str | None = None,
              schema_version: int | None = None) -> str:
    """Compact single-line JSON, safe to embed in a comment/element."""
    meta = build_meta(params, tool=tool, version=version,
                      schema_version=schema_version)
    return json.dumps(meta, separators=(",", ":"))


def parse_meta(blob: str) -> dict:
    """Inverse of dump_blob: the raw metadata dict — {"cct", "tool", "v",
    "sv"} as embedded, unflattened. Raises ValueError-family exceptions
    (via json.JSONDecodeError) on malformed JSON."""
    return json.loads(blob)


def flatten_params(meta: dict) -> dict:
    """The flat design-params dict a CAD addin actually wants to re-apply.

    Mirrors the FreeCAD addon's extraction rule (importer.py) exactly: if
    `meta` has a top-level "cct" key, unwrap it and fold "sv" in as a param
    default; otherwise treat the whole object as the params dict (for
    foreign/older producers that don't use the "cct" wrapper).
    """
    if isinstance(meta, dict) and "cct" in meta:
        params = dict(meta["cct"])
        if "sv" in meta:
            params.setdefault("sv", meta["sv"])
        return params
    return dict(meta)
