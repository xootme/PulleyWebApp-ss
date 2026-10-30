"""
saved_designs.py — the designs an account has downloaded or saved, and its
My designs page (0.34.0; the owner, 2026-10-30: Controller
campaigns/2026-10-30-saved-designs-checkout.md).

Behind the saved_designs business setting (settings.py), off until the owner
turns it on: off, nothing is saved, these routes answer 404 and tokens.js
shows nothing new.

- Every download the charge stands for is kept (charging.Charges): a paid one
  once its file was sent in full, a free re-download of a design already
  bought, an AI app's or add-in's too. One row per account, app and design:
  downloading it again brings that row up to date. "Save to My designs" in the
  Download window keeps one without a download (source "save").
- A row holds the app, the design's parameters (its registered design, less
  `design` and `_ts`, at most PARAMS_MAX_CHARS), a name, a thumbnail (the
  page's window.cctDesignThumbnail(), drawn at 320x240 by tokens.js: WebP,
  JPEG or PNG, at most THUMB_MAX_BYTES) and when. What was bought isn't kept
  here: the ledger has it, by design key (TokenStore.bought).
- MAX_DESIGNS an account. Past it a download replaces the oldest design a
  download saved and nobody renamed; a Save is refused (Full).
- Kept while the account is open. Deleting one deletes its row (the ledger
  keeps the purchase, so its re-download stays free while the window lasts);
  closing the account (AccountStore.delete_account) deletes them all.

Routes, each app's at its own base, signed in:
    GET  account/designs                 the page: cards, filter by tool, rename, delete, open
    POST account/designs/<id>            {action: rename | delete, name}
    GET  account/designs/<id>/thumb      its thumbnail
    GET  api/designs/<id>                {app, name, params}: ?saved=<id> (tokens.js)
    POST api/designs                     {design_id, name?}: keep the registered design (Save)

Open is <the app's page>?saved=<id>. tokens.js there asks api/designs/<id> and
hands the params to window.cctApplyDesign(params); a page without that hook
is reloaded with them as its query (as the admin's "Open this design" is).
Each app's page path comes from its page (tokens.js sends it with
api/design); an app not seen yet opens at /tools/<app>/.
"""
from __future__ import annotations

import base64
import html
import json
import logging
import re
import secrets
import time
from typing import Callable, Optional

from .sqlite_db import SqliteDB

log = logging.getLogger(__name__)

MAX_DESIGNS = 500
THUMB_MAX_BYTES = 40_000
PARAMS_MAX_CHARS = 20_000
NAME_MAX = 80
# Not kept: the design's own copy for a file to embed, and a timestamp (0.31.1's rule).
SKIP_KEYS = frozenset({"design", "_ts"})
TIER_NAMES = {"2d": "2D files", "stl": "STL", "step": "STEP"}

_THUMB = re.compile(r"data:(image/(?:webp|jpeg|png));base64,([A-Za-z0-9+/]+=*)\Z")
_MAGIC = {"image/webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
          "image/jpeg": lambda b: b[:3] == b"\xff\xd8\xff",
          "image/png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n"}
_PAGE = re.compile(r"/[A-Za-z0-9._~/-]{0,199}\Z")

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_designs (
    id               TEXT PRIMARY KEY,
    account_id       TEXT NOT NULL,
    app              TEXT NOT NULL,
    design_key       TEXT NOT NULL,
    params           TEXT NOT NULL,
    name             TEXT,
    named            INTEGER NOT NULL DEFAULT 0,
    thumb            TEXT,
    source           TEXT NOT NULL,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    last_download_at REAL,
    UNIQUE (account_id, app, design_key)
);
CREATE INDEX IF NOT EXISTS saved_designs_account ON saved_designs(account_id, updated_at);
CREATE TABLE IF NOT EXISTS saved_design_apps (
    app         TEXT PRIMARY KEY,
    page        TEXT NOT NULL,
    title       TEXT,
    updated_at  REAL NOT NULL
);
"""


class Full(Exception):
    """A Save past MAX_DESIGNS."""


def clean_thumb(value) -> Optional[str]:
    """A thumbnail data URL as it may be kept, or None (not one, not the image
    it says, or too big)."""
    m = _THUMB.match(value) if isinstance(value, str) else None
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        return None
    return value if len(raw) <= THUMB_MAX_BYTES and _MAGIC[m.group(1)](raw) else None


def clean_params(params) -> Optional[dict]:
    """The params a row keeps, or None when they're too big to."""
    if not isinstance(params, dict):
        return None
    p = {str(k): v for k, v in params.items() if str(k) not in SKIP_KEYS}
    return p if p and len(json.dumps(p)) <= PARAMS_MAX_CHARS else None


def clean_name(value) -> Optional[str]:
    name = " ".join(str(value or "").split())[:NAME_MAX]
    return name or None


def clean_page(value) -> Optional[str]:
    """A page path on this site (no query), as an app's Open link uses it."""
    page = str(value or "").split("?")[0].split("#")[0]
    return page if _PAGE.match(page) and "//" not in page else None


class SavedDesigns(SqliteDB):
    """The saved_designs table, in the accounts database."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        super().__init__(path, SCHEMA)
        self._clock = clock
        self._seen: dict = {}

    def keep(self, account_id: str, app: str, design_key: str, params, *, name=None, thumb=None,
             source: str = "download") -> Optional[str]:
        """Keep this design for the account: its row, made or brought up to
        date. Returns its id, or None when its params are too big to keep or
        a download finds the account full of designs it may not replace.
        A Save (source "save") at MAX_DESIGNS raises Full."""
        params = clean_params(params)
        if params is None:
            return None
        thumb, name, now = clean_thumb(thumb), clean_name(name), self._clock()
        downloaded = now if source == "download" else None
        with self._write() as db:
            row = db.execute("SELECT id FROM saved_designs WHERE account_id = ? AND app = ? AND design_key = ?",
                             (account_id, app, design_key)).fetchone()
            if row:
                db.execute(
                    "UPDATE saved_designs SET params = ?, updated_at = ?, "
                    "last_download_at = COALESCE(?, last_download_at), thumb = COALESCE(?, thumb), "
                    "name = CASE WHEN named = 1 THEN name ELSE COALESCE(?, name) END, "
                    "source = CASE WHEN ? = 'save' THEN 'save' ELSE source END WHERE id = ?",
                    (json.dumps(params, sort_keys=True), now, downloaded, thumb, name, source, row["id"]))
                return row["id"]
            count = db.execute("SELECT COUNT(*) FROM saved_designs WHERE account_id = ?",
                               (account_id,)).fetchone()[0]
            if count >= MAX_DESIGNS:
                if source != "download":
                    raise Full(f"You have {MAX_DESIGNS} saved designs — delete some first.")
                old = db.execute("SELECT id FROM saved_designs WHERE account_id = ? AND source = 'download' "
                                 "AND named = 0 ORDER BY updated_at LIMIT 1", (account_id,)).fetchone()
                if not old:
                    return None
                db.execute("DELETE FROM saved_designs WHERE id = ?", (old["id"],))
            sid = secrets.token_hex(8)
            db.execute(
                "INSERT INTO saved_designs (id, account_id, app, design_key, params, name, named, thumb, source, "
                "created_at, updated_at, last_download_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                (sid, account_id, app, design_key, json.dumps(params, sort_keys=True), name, thumb, source,
                 now, now, downloaded))
        return sid

    def list(self, account_id: str) -> list[dict]:
        """The account's designs, the latest first, without params or thumbnail."""
        with self._read() as db:
            rows = db.execute(
                "SELECT id, app, design_key, name, named, source, created_at, updated_at, last_download_at, "
                "thumb IS NOT NULL AS has_thumb FROM saved_designs WHERE account_id = ? "
                "ORDER BY updated_at DESC", (account_id,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, account_id: str, saved_id: str) -> Optional[dict]:
        with self._read() as db:
            row = db.execute("SELECT id, app, design_key, name, params FROM saved_designs "
                             "WHERE account_id = ? AND id = ?", (account_id, saved_id)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["params"] = json.loads(d["params"])
        return d

    def thumb(self, account_id: str, saved_id: str) -> Optional[tuple]:
        """(mimetype, bytes) of its thumbnail, or None."""
        with self._read() as db:
            row = db.execute("SELECT thumb FROM saved_designs WHERE account_id = ? AND id = ?",
                             (account_id, saved_id)).fetchone()
        m = _THUMB.match(row["thumb"]) if row and row["thumb"] else None
        return (m.group(1), base64.b64decode(m.group(2))) if m else None

    def rename(self, account_id: str, saved_id: str, name) -> bool:
        """A name the person gave it (never replaced at the cap); blank goes back to none."""
        name = clean_name(name)
        with self._write() as db:
            cur = db.execute("UPDATE saved_designs SET name = ?, named = ? WHERE account_id = ? AND id = ?",
                             (name, 1 if name else 0, account_id, saved_id))
            return (cur.rowcount or 0) > 0

    def delete(self, account_id: str, saved_id: str) -> bool:
        with self._write() as db:
            cur = db.execute("DELETE FROM saved_designs WHERE account_id = ? AND id = ?", (account_id, saved_id))
            return (cur.rowcount or 0) > 0

    def seen(self, app: str, page, title=None) -> None:
        """Where this app's page is, for the Open links of every app's My designs.
        Written once a process for each page and title it hears."""
        page, title = clean_page(page), clean_name(title)
        if not page or self._seen.get(app) == (page, title):
            return
        with self._write() as db:
            db.execute("INSERT INTO saved_design_apps (app, page, title, updated_at) VALUES (?, ?, ?, ?) "
                       "ON CONFLICT (app) DO UPDATE SET page = excluded.page, title = excluded.title, "
                       "updated_at = excluded.updated_at", (app, page, title, self._clock()))
        self._seen[app] = (page, title)

    def apps(self) -> dict:
        """{app: {"page", "title"}} for every app whose page has been seen."""
        with self._read() as db:
            rows = db.execute("SELECT app, page, title FROM saved_design_apps").fetchall()
        return {r["app"]: {"page": r["page"], "title": r["title"]} for r in rows}


def open_link(app: str, saved_id: str, apps: dict) -> str:
    page = (apps.get(app) or {}).get("page") or f"/tools/{app}/"
    return f"{page}?saved={saved_id}"


def _when(ts: float) -> str:
    return time.strftime("%d %b %Y", time.gmtime(ts)).lstrip("0")


def bought_line(tokens, account_id: str, key: str, forever: bool) -> str:
    """What the account has of this design's files, for its card."""
    have = tokens.bought(account_id, key)
    if not have:
        return "No files bought yet"
    tier, ts = have
    what = TIER_NAMES.get(tier, tier)
    q = tokens.quote(account_id, key, "step")
    if q.held_tier:
        if forever:
            return f"{TIER_NAMES.get(q.held_tier, q.held_tier)} bought — download again free"
        until = time.strftime("%d %b %Y, %H:%M UTC", time.gmtime(q.unlocked_until)).lstrip("0")
        return f"{TIER_NAMES.get(q.held_tier, q.held_tier)} bought — download again free until {until}"
    return f"{what} bought {_when(ts)} — downloading it again costs tokens"


_CSS = (
    "body{font-family:system-ui,sans-serif;max-width:64rem;margin:2rem auto;padding:0 1rem;line-height:1.5;"
    "color:#1f1f1f;background:#fff}"
    "h1{font-size:1.5rem;margin:0 0 .3rem}.sub{color:#666;font-size:.9rem}"
    ".tools{display:flex;flex-wrap:wrap;gap:.4rem;margin:1rem 0}"
    ".tools a{padding:.25rem .7rem;border:1px solid #ccc;border-radius:999px;color:inherit;text-decoration:none;"
    "font-size:.9rem}.tools a.on{background:#1f1f1f;color:#fff;border-color:#1f1f1f}"
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:1rem}"
    ".card{border:1px solid #ddd;border-radius:8px;overflow:hidden;display:flex;flex-direction:column}"
    ".card img,.card .ph{display:block;width:100%;aspect-ratio:4/3;object-fit:contain;background:#f4f5f7}"
    ".card .ph{display:flex;align-items:center;justify-content:center;color:#999;font-size:.9rem}"
    ".card .in{padding:.7rem;display:flex;flex-direction:column;gap:.35rem;flex:1}"
    ".card .name{font-weight:600}.card form{margin:0;display:flex;gap:.4rem}"
    ".card input[type=text]{flex:1;min-width:0;font-size:.9rem;padding:.25rem .4rem}"
    ".card button,.card .open{font-size:.9rem;padding:.3rem .8rem;cursor:pointer}"
    ".card .open{display:inline-block;background:#1f1f1f;color:#fff;border-radius:4px;text-decoration:none;"
    "text-align:center}.row{display:flex;gap:.4rem;margin-top:auto}.msg{margin:1rem 0}"
    "@media (prefers-color-scheme:dark){body{background:#16181c;color:#e8e8e8}.card{border-color:#333}"
    ".card img,.card .ph{background:#22252b}.tools a{border-color:#444}"
    ".tools a.on,.card .open{background:#e8e8e8;color:#16181c}.sub{color:#9aa}}"
)


def _doc(title: str, inner: str, status: int = 200):
    from flask import Response
    return Response(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='referrer' content='no-referrer'><title>{html.escape(title)}</title>"
        f"<style>{_CSS}</style></head><body>{inner}</body></html>", status=status, mimetype="text/html")


def register_saved_designs(app, charges) -> SavedDesigns:
    """The routes above, for an app whose Charges is attached (called from
    Charges.attach). Each answers 404 while the saved_designs setting is off."""
    from flask import jsonify, redirect, request, Response
    from .account_routes import current_account_id
    from .settings import setting

    tokens = charges.state.tokens
    store = SavedDesigns(tokens.path)
    own = tokens.app or "app"

    def on() -> bool:
        return setting(tokens.path, "saved_designs") is True

    def page(notice: str = ""):
        if not on():
            return _doc("Not found", "<p>That page isn't here.</p>", 404)
        acct = current_account_id()
        if not acct:
            nxt = html.escape(request.path, quote=True)
            return _doc("My designs", (
                "<h1>My designs</h1><p>Sign in to see the designs you've downloaded and saved.</p>"
                f"<p><a href='/account/sign-in?next={nxt}'>Sign in</a></p>"))
        rows, apps = store.list(acct), store.apps()
        title = lambda a: (apps.get(a) or {}).get("title") or a      # noqa: E731
        tools = sorted({r["app"] for r in rows}, key=lambda a: title(a).lower())
        pick = request.args.get("app") or ""
        shown = [r for r in rows if not pick or r["app"] == pick]
        forever = setting(tokens.path, "redownload_window") == "forever"
        filt = ""
        if len(tools) > 1:
            filt = "<nav class='tools'>" + "".join(
                f"<a href='designs{'?app=' + html.escape(a, quote=True) if a else ''}'"
                f"{' class=on' if a == pick else ''}>{html.escape(title(a) if a else 'All tools')}</a>"
                for a in [""] + tools) + "</nav>"
        cards = []
        for r in shown:
            sid = html.escape(r["id"], quote=True)
            img = (f"<img src='designs/{sid}/thumb' alt=''>" if r["has_thumb"]
                   else "<div class='ph'>No picture</div>")
            name = r["name"] or f"{title(r['app'])} design"
            cards.append(
                f"<div class='card'>{img}<div class='in'>"
                f"<div class='name'>{html.escape(name)}</div>"
                f"<div class='sub'>{html.escape(title(r['app']))} · {_when(r['updated_at'])}</div>"
                f"<div class='sub'>{html.escape(bought_line(tokens, acct, r['design_key'], forever))}</div>"
                f"<form method='post' action='designs/{sid}'><input type='hidden' name='action' value='rename'>"
                f"<input type='text' name='name' maxlength='{NAME_MAX}' value='{html.escape(r['name'] or '', quote=True)}'"
                " placeholder='Name' aria-label='Name'><button type='submit'>Rename</button></form>"
                f"<div class='row'><a class='open' href='{html.escape(open_link(r['app'], r['id'], apps), quote=True)}'>"
                "Open</a>"
                f"<form method='post' action='designs/{sid}' "
                "onsubmit=\"return confirm('Delete this saved design? Files you bought stay yours.')\">"
                "<input type='hidden' name='action' value='delete'><button type='submit'>Delete</button>"
                "</form></div></div></div>")
        body = ("<div class='grid'>" + "".join(cards) + "</div>") if cards else (
            "<p>Nothing here yet. Every design you download is kept here, and so is any you save "
            "from the Download window.</p>")
        return _doc("My designs", (
            "<h1>My designs</h1><div class='sub'>The designs you've downloaded or saved: open one to go on "
            "where you left off.</div>" + notice + filt + body))

    def update(saved_id):
        if not on():
            return _doc("Not found", "<p>That page isn't here.</p>", 404)
        acct = current_account_id()
        if not acct:
            return redirect("../designs", code=303)
        action = request.form.get("action")
        ok = (store.delete(acct, saved_id) if action == "delete"
              else store.rename(acct, saved_id, request.form.get("name")) if action == "rename" else False)
        if not ok:
            return page("<p class='msg' style='color:#b00020'>That design isn't here any more.</p>"), 404
        return redirect("../designs" + (f"?app={request.args['app']}" if request.args.get("app") else ""),
                        code=303)

    def thumb(saved_id):
        acct = current_account_id()
        found = store.thumb(acct, saved_id) if acct and on() else None
        if not found:
            return Response(status=404)
        r = Response(found[1], mimetype=found[0])
        r.headers["Cache-Control"] = "private, max-age=86400"
        return r

    def one(saved_id):
        if not on():
            return jsonify({"error": "not found"}), 404
        acct = current_account_id()
        if not acct:
            return jsonify({"error": "sign in required", "code": "SIGN_IN_REQUIRED"}), 401
        row = store.get(acct, saved_id)
        if not row:
            return jsonify({"error": "not found"}), 404
        if row["app"] != own:                     # another app's: open it there
            return jsonify({"error": "another tool's design", "open": open_link(row["app"], saved_id,
                                                                                 store.apps())}), 409
        return jsonify({"app": row["app"], "name": row["name"], "params": row["params"]})

    def save():
        if not on():
            return jsonify({"error": "not found"}), 404
        acct = current_account_id()
        if not acct:
            return jsonify({"error": "sign in required", "code": "SIGN_IN_REQUIRED"}), 401
        data = request.get_json(silent=True) or {}
        did = str(data.get("design_id") or "")
        design = charges.designs.get(did) if did else None
        if design is None:
            return jsonify({"error": "unknown design"}), 400
        thumb, name = charges.designs.meta(did)
        try:
            sid = store.keep(acct, own, did, design, name=data.get("name") or name, thumb=thumb, source="save")
        except Full as e:
            return jsonify({"error": str(e), "code": "FULL"}), 409
        if not sid:
            return jsonify({"error": "This design is too big to save."}), 400
        return jsonify({"id": sid})

    app.add_url_rule("/account/designs", endpoint="cct_saved_designs", view_func=page, methods=["GET"])
    app.add_url_rule("/account/designs/<saved_id>", endpoint="cct_saved_design_update", view_func=update,
                     methods=["POST"])
    app.add_url_rule("/account/designs/<saved_id>/thumb", endpoint="cct_saved_design_thumb", view_func=thumb,
                     methods=["GET"])
    app.add_url_rule("/api/designs/<saved_id>", endpoint="cct_saved_design", view_func=one, methods=["GET"])
    app.add_url_rule("/api/designs", endpoint="cct_saved_design_save", view_func=save, methods=["POST"])
    return store
