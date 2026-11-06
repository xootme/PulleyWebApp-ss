"""
stl.py — CCT metadata for STL files.

Convention: the same `/* CCT:{...} */` comment as STEP, appended as a text
trailer after the file's binary content. Slicers and CAD tools read exactly
`80-byte header + uint32 triangle count + 50 bytes * count` and stop, so
they ignore the trailer; strict readers (trimesh) check the length and
refuse the file. Ported from PulleyWebApp's app.py `_embed_stl`.

Pure STL (0.57.0; the owner, 2026-11-05, proposal (c)), behind the
`pure_stl` business setting, off: off, every file is byte for byte as
before. On:

- embed_stl writes no trailer. The file stays exactly 84 + 50·n bytes and
  its 80-byte header reads `CCT <tool> <version>` (never "solid", which
  makes readers try ASCII), and ` #<design key>` when the saved_designs
  setting is on and the download is an account's: the key that account's
  saved design is filed under, looked up for that account only
  (cct_common.imports). Nothing is kept for an anonymous download.
- The settings go beside the STL as `<stem>.cct.json` (settings_sidecar):
  the Download window's zip adds it by itself (download_bundle), an app's
  own zip writes stl_files(). A bare .stl download stays a bare .stl.
- extract_stl reads the trailer forever, so every file already out still
  imports.

The setting, the account and the key come from the request: the app's
accounts database (`app.extensions["cct_accounts"]`), the charged route's
account and design key (charging, DOWNLOAD_ENVIRON) or the Download
window's (its mark). Outside a request (a job's thread) pass db=, account=
and key=; with no database the setting is off.
"""
from __future__ import annotations

import re
import struct

from .core import COMMENT_BLOB_RE, dump_blob, parse_meta

HEADER = 80
# The charged route's {"account", "key"}, in its request's environ (charging).
DOWNLOAD_ENVIRON = "cct.stl_download"
SIDECAR = ".cct.json"
# What embed_stl hands the Download window's zip: the last STL's settings (download_bundle).
MARK_KEY = "stl_settings"
_HEADER_RE = re.compile(r"CCT (\S+) (\S+)(?: #([0-9a-f]{32}))?\Z")
_KEY_RE = re.compile(r"[0-9a-f]{32}\Z")


def _counted(data: bytes) -> bool:
    """Exactly 84 + 50·n bytes for its triangle count n."""
    return len(data) >= HEADER + 4 and len(data) == HEADER + 4 + 50 * struct.unpack_from("<I", data, HEADER)[0]


def is_strict_stl(data: bytes) -> bool:
    """A binary STL a strict reader takes: exactly 84 + 50·n bytes for its
    count n, and a header that doesn't begin with "solid" (ASCII's)."""
    return _counted(data) and data[:5].lower() != b"solid"


def stl_header(tool: str | None, version: str | None, key: str | None = None) -> bytes:
    """The 80-byte header: `CCT <tool> <version>[ #<key>]`, ASCII, padded
    with spaces. The key is whole or left out; tool and version are cut to fit."""
    def word(v, default):
        w = re.sub(r"\s+", "_", str(v).strip()) if v not in (None, "") else default
        return w.encode("ascii", "replace").decode("ascii")
    tail = f" #{key}" if key and _KEY_RE.match(key) else ""
    tool_w, ver_w = word(tool, "-"), word(version, "-")
    room = HEADER - len("CCT  ") - len(tail)
    ver_w = ver_w[:max(1, room // 3)]
    tool_w = tool_w[:room - len(ver_w)]
    return f"CCT {tool_w} {ver_w}{tail}".encode("ascii").ljust(HEADER, b" ")


def stl_key(stl_bytes: bytes) -> dict | None:
    """A pure CCT STL's header: {"tool", "v", "key"} (key None when it has
    none), or None when the header isn't ours."""
    if len(stl_bytes) < HEADER:
        return None
    try:
        text = stl_bytes[:HEADER].decode("ascii").rstrip(" \0")
    except UnicodeDecodeError:
        return None
    m = _HEADER_RE.match(text)
    if not m:
        return None
    tool, v, key = m.groups()
    return {"tool": None if tool == "-" else tool, "v": None if v == "-" else v, "key": key}


def settings_sidecar(stem: str, params: dict, *, tool: str | None = None, version: str | None = None,
                     schema_version: int | None = None) -> tuple[str, bytes]:
    """(`<stem>.cct.json`, its bytes): the same blob the trailer holds, so
    parse_meta reads it."""
    blob = dump_blob(params, tool=tool, version=version, schema_version=schema_version)
    return stem + SIDECAR, (blob + "\n").encode("utf-8")


def _request():
    """(environ, extensions) of the current Flask request, or (None, None)."""
    try:
        from flask import current_app, has_request_context, request
    except ImportError:
        return None, None
    if not has_request_context():
        return None, None
    return request.environ, current_app.extensions


def _context(db, account, key):
    """(db, account, key, mark): those given, else the request's."""
    environ, extensions = _request()
    if environ is None:
        return db, account, key, None
    from .download_failures import BUNDLE_ENVIRON
    mark = environ.get(BUNDLE_ENVIRON)
    mark = mark if isinstance(mark, dict) else None
    if db is None:
        accounts = (extensions.get("cct_accounts") or {}).get("accounts")
        db = getattr(getattr(accounts, "tokens", None), "path", None)
    if account is None and key is None:
        who = environ.get(DOWNLOAD_ENVIRON) or mark or {}
        account, key = who.get("account"), who.get("key")
    return db, account, key, mark


def pure_stl_on(db=None) -> bool:
    """The pure_stl setting, from db or the request's accounts database."""
    from .settings import setting
    db = _context(db, None, None)[0]
    return bool(db) and setting(db, "pure_stl") is True


def embed_stl(stl_bytes: bytes, params: dict, *, tool: str | None = None,
              version: str | None = None,
              schema_version: int | None = None,
              db=None, account: str | None = None, key: str | None = None) -> bytes:
    """Append a CCT metadata trailer after an STL's triangle data; with
    pure_stl on, write the header instead (module docstring).

    Best-effort: returns `stl_bytes` unchanged on failure.
    """
    try:
        blob = dump_blob(params, tool=tool, version=version,
                         schema_version=schema_version)
        db, account, key, mark = _context(db, account, key)
        from .settings import setting
        if not (db and setting(db, "pure_stl") is True):
            trailer = f"\n/* CCT:{blob} */\n".encode("utf-8")
            return stl_bytes + trailer
        if mark is not None:
            mark[MARK_KEY] = blob
        if not _counted(stl_bytes):
            return stl_bytes                   # not a binary STL: left as it is, its settings beside it
        saved = account and key and setting(db, "saved_designs") is True
        return stl_header(tool, version, key if saved else None) + stl_bytes[HEADER:]
    except Exception:
        return stl_bytes


def stl_files(stem: str, stl_bytes: bytes, params: dict, *, tool: str | None = None,
              version: str | None = None, schema_version: int | None = None,
              db=None, account: str | None = None, key: str | None = None) -> list[tuple[str, bytes]]:
    """An app's own zip: [(name, bytes), ...] for one STL, its settings
    beside it as `<stem>.cct.json` when pure_stl is on.

        for name, data in stl_files(stem, stl, params, tool=TOOL, version=V):
            z.writestr(name, data)
    """
    kw = dict(tool=tool, version=version, schema_version=schema_version)
    out = [(stem + ".stl", embed_stl(stl_bytes, params, **kw, db=db, account=account, key=key))]
    if pure_stl_on(db):
        out.append(settings_sidecar(stem, params, **kw))
    return out


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
