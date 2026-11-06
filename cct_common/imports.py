"""
imports.py — one call for an app's Import (0.57.0; the owner, 2026-11-05,
proposal (c), with cct_common.stl's pure STL):

    from cct_common.imports import import_meta
    found = import_meta(up.filename, up.read())
    if found["saved"]:      # a pure STL's key: this account's saved design
        ...open it as My designs does: <page>?saved=<found["saved"]["id"]>, or its params
    elif found["meta"]:     # the settings, as embedded: flatten_params(found["meta"])
        ...as before
    else:                   # found["error"]: say it, or the app's own "no settings" line when None
        ...

It reads, by the file's name:
- .stl: the trailer (every file made before pure_stl, for good), else a pure
  CCT STL's header key, looked up in the signed-in account's saved designs
  (saved_designs on); a pure CCT STL with no usable key gets NO_KEY.
- .cct.json: the settings beside an STL (cct_common.stl.settings_sidecar).
- .zip: its .cct.json, else any STEP, DXF, SVG or STL inside that holds them.
- .step/.stp, .dxf, .svg: as extract_step, extract_dxf, extract_svg do.

The account and the database come from the request (the app's accounts
database, its signed-in session); pass db= and account= outside one.
"""
from __future__ import annotations

import io
import zipfile

from .core import parse_meta
from .dxf import extract_dxf
from .step import extract_step
from .stl import SIDECAR, _context, extract_stl, stl_key
from .svg import extract_svg

# The proposal's words (the owner approved them, 2026-11-05).
NO_KEY = ("This STL holds the shape only: import the STEP or the .cct.json from the same download, "
          "or open it from My designs.")
ZIP_MEMBERS = 200                  # a zip's files looked at, at most
MEMBER_MAX = 64 * 1024 * 1024      # bytes one member may unpack to
_ORDER = (SIDECAR, ".step", ".stp", ".dxf", ".svg", ".stl")


def _result(meta=None, saved=None, error=None, found_in=None, tool=None) -> dict:
    return {"meta": meta, "saved": saved, "error": error, "found_in": found_in,
            "tool": tool if tool is not None else (meta or {}).get("tool") if isinstance(meta, dict) else None}


def _sidecar(data: bytes):
    meta = parse_meta(data.decode("utf-8-sig"))
    return meta if isinstance(meta, dict) and isinstance(meta.get("cct"), dict) else None


def _account(account):
    if account is not None:
        return account
    try:
        from .account_routes import current_account_id
        return current_account_id()
    except Exception:
        return None


def _stl(name, data, db, account) -> dict:
    meta = extract_stl(data)
    if meta:
        return _result(meta, found_in=name)
    head = stl_key(data)
    if head is None:
        return _result()
    db = _context(db, None, None)[0]
    if head["key"] and db:
        from .settings import setting
        who = _account(account)
        if who and setting(db, "saved_designs") is True:
            from .saved_designs import SavedDesigns
            row = SavedDesigns(db).by_key(who, head["key"])
            if row:
                return _result(saved=row, found_in=name, tool=head["tool"])
    return _result(error=NO_KEY, found_in=name, tool=head["tool"])


def _one(name: str, data: bytes, db, account, inside: bool) -> dict:
    low = name.lower()
    if low.endswith(SIDECAR) or low.endswith(".json"):
        return _result(_sidecar(data), found_in=name)
    if low.endswith((".step", ".stp")):
        return _result(extract_step(data), found_in=name)
    if low.endswith(".dxf"):
        return _result(extract_dxf(data), found_in=name)
    if low.endswith(".svg"):
        return _result(extract_svg(data.decode("utf-8", errors="replace")), found_in=name)
    if low.endswith(".zip") and not inside:
        return _zip(data, db, account)
    return _stl(name, data, db, account)


def _zip(data: bytes, db, account) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members = [i for i in z.infolist()[:ZIP_MEMBERS] if not i.is_dir() and i.file_size <= MEMBER_MAX]
        ranked = sorted((i for i in members if i.filename.lower().endswith(_ORDER)),
                        key=lambda i: next(k for k, s in enumerate(_ORDER) if i.filename.lower().endswith(s)))
        first_error = None
        for info in ranked:
            try:
                got = _one(info.filename, z.read(info), db, account, inside=True)
            except Exception:
                continue
            if got["meta"] or got["saved"]:
                return got
            first_error = first_error or got
    return first_error or _result()


def import_meta(filename: str, data: bytes, *, db=None, account: str | None = None) -> dict:
    """{"meta", "saved", "error", "found_in", "tool"} for an uploaded file
    (module docstring). Never raises: a file it can't read finds nothing."""
    try:
        return _one(filename or "", data, db, account, inside=False)
    except Exception:
        return _result()
