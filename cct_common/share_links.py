"""
share_links.py — share a saved design by a link (0.42.0; account plan step
4; referrals, 0.49.0, are referrals.py). Behind the share_links
business setting, off until the owner turns it on, and needs saved_designs.

- "Share" on a card in My designs makes a link:
  <the app's page>?shared=<code>, e.g.
  https://cheapcadtools.com/tools/gears/?shared=k7m2p9qr4x (the site's
  Worker already sends /tools/* to the apps).
- The code is CODE_LEN characters of ALPHABET (no 0/o, 1/l/i), random.
- The link opens a snapshot: the design's settings, name and picture as
  they were when it was shared. Later edits to the saved design don't change
  it. Whoever opens it gets their own copy in the tool (tokens.js, through
  the app's cctApplyDesign); the sharer's design is never touched.
- It's public: anyone with the link sees those settings and the picture,
  never the sharer's email or name. "Stop sharing" turns it off for good.
  Each link counts its opens: a number, nothing about who.
- One live link per saved design; MAX_LIVE per account. Deleting the account
  or the saved design turns its links off; deleting the account deletes them.
- Shared views send X-Robots-Tag: noindex (noindex(), 0.46.1).
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Callable, Optional

from .referrals import SCHEMA as _REFERRALS_SCHEMA
from .sqlite_db import INTEGRITY_ERRORS, SqliteDB

ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
CODE_LEN = 10
MAX_LIVE = 100
NOTICE = ("Anyone with this link can open this design in {tool} and see its settings and picture. "
          "Your email isn't shown. You can turn the link off at any time.")
BANNER = "A shared design. Your changes are your own; the original stays as it was."
GONE = "This design isn't shared any more."

SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_designs (
    code        TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    saved_id    TEXT NOT NULL,
    app         TEXT NOT NULL,
    name        TEXT,
    params      TEXT NOT NULL,
    thumb       TEXT,
    created_at  REAL NOT NULL,
    revoked_at  REAL,
    opens       INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS shared_designs_account ON shared_designs (account_id, created_at);
CREATE INDEX IF NOT EXISTS shared_designs_saved ON shared_designs (saved_id);
""" + _REFERRALS_SCHEMA     # referrals (0.49.0) go with the links they came from


class Full(Exception):
    """MAX_LIVE links are live already."""


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def valid_code(code: str) -> bool:
    return len(code or "") == CODE_LEN and all(c in ALPHABET for c in code)


class ShareLinks(SqliteDB):
    """The shared_designs table, in the accounts database."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        super().__init__(path, SCHEMA)
        self._clock = clock
        from .saved_designs import ensure_note_columns
        ensure_note_columns(self)

    def share(self, account_id: str, saved_id: str, *, include_note: bool = False) -> Optional[str]:
        """The saved design's live link (made now as a snapshot, if it has
        none); None if there's no such design. Full past MAX_LIVE.
        include_note (0.43.0): the snapshot keeps the design's note too."""
        with self._write() as db:
            live = db.execute("SELECT code FROM shared_designs WHERE account_id = ? AND saved_id = ? "
                              "AND revoked_at IS NULL", (account_id, saved_id)).fetchone()
            if live:
                return live["code"]
            row = db.execute("SELECT app, name, params, thumb, note FROM saved_designs WHERE account_id = ? AND id = ?",
                             (account_id, saved_id)).fetchone()
            if not row:
                return None
            n = db.execute("SELECT COUNT(*) FROM shared_designs WHERE account_id = ? AND revoked_at IS NULL",
                           (account_id,)).fetchone()[0]
            if n >= MAX_LIVE:
                raise Full()
            for _ in range(5):
                code = new_code()
                try:
                    db.execute("INSERT INTO shared_designs (code, account_id, saved_id, app, name, params, thumb, "
                               "created_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (code, account_id, saved_id, row["app"], row["name"], row["params"], row["thumb"],
                                self._clock(), row["note"] if include_note else None))
                    return code
                except INTEGRITY_ERRORS:
                    continue
        raise RuntimeError("no free share code")

    def stop(self, account_id: str, saved_id: str) -> bool:
        """Turn the saved design's live link off for good; False if it had none."""
        with self._write() as db:
            n = db.execute("UPDATE shared_designs SET revoked_at = ? WHERE account_id = ? AND saved_id = ? "
                           "AND revoked_at IS NULL", (self._clock(), account_id, saved_id)).rowcount
        return bool(n)

    def stop_for_saved(self, db, saved_id: str) -> None:
        """Inside a saved design's delete: its link goes with it."""
        db.execute("UPDATE shared_designs SET revoked_at = ? WHERE saved_id = ? AND revoked_at IS NULL",
                   (self._clock(), saved_id))

    def open(self, code: str) -> Optional[dict]:
        """{app, name, params} of a live link, counting the open; None if it's not live."""
        if not valid_code(code):
            return None
        with self._write() as db:
            row = db.execute("SELECT app, name, params, note FROM shared_designs WHERE code = ? AND revoked_at IS NULL",
                             (code,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE shared_designs SET opens = opens + 1 WHERE code = ?", (code,))
        out = {"app": row["app"], "name": row["name"], "params": json.loads(row["params"])}
        if row["note"]:
            out["note"] = row["note"]                  # only when the sharer ticked "Include my note"
        return out

    def app_of(self, code: str) -> Optional[str]:
        """Which app a live link is for, without counting an open."""
        if not valid_code(code):
            return None
        with self._read() as db:
            row = db.execute("SELECT app FROM shared_designs WHERE code = ? AND revoked_at IS NULL",
                             (code,)).fetchone()
        return row["app"] if row else None

    def thumb(self, code: str) -> Optional[str]:
        if not valid_code(code):
            return None
        with self._read() as db:
            row = db.execute("SELECT thumb FROM shared_designs WHERE code = ? AND revoked_at IS NULL",
                             (code,)).fetchone()
        return row["thumb"] if row else None

    def live_for(self, account_id: str) -> dict:
        """{saved id: {code, opens, created_at}} for the account's live links."""
        with self._read() as db:
            rows = db.execute("SELECT saved_id, code, opens, created_at, app, name FROM shared_designs "
                              "WHERE account_id = ? AND revoked_at IS NULL ORDER BY created_at DESC",
                              (account_id,)).fetchall()
        return {r["saved_id"]: dict(r) for r in rows}


def noindex(app) -> None:
    """Keep shared views out of search engines (0.46.1): X-Robots-Tag: noindex
    on api/shared/*, /account/shared/<code> (projects) and any page opened
    with ?shared=. A header, so it holds for crawlers that don't run scripts."""
    if app.extensions.get("cct_shared_noindex"):
        return
    app.extensions["cct_shared_noindex"] = True
    from flask import request

    @app.after_request
    def _shared_noindex(response):
        path = request.path or ""
        if "/api/shared/" in path or path.startswith("/account/shared/") or "shared" in request.args:
            response.headers["X-Robots-Tag"] = "noindex"
        return response


def link(page: str, code: str, host_url: str = "") -> str:
    """The share link: the app's page with ?shared=<code>."""
    return f"{host_url.rstrip('/')}{page}?shared={code}"
