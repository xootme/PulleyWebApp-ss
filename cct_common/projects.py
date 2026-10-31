"""
projects.py — projects: named groups of saved designs for one build (0.46.0;
account plan step 7, phase 7a, built with my recommendations, awaiting the
owner). Behind the projects business setting, off; it needs saved_designs.
The one zip across tools (7b) isn't built.

- A project is a name (NAME_MAX), a private note (NOTE_MAX, text only) and the
  saved designs in it, in the order added. A design can be in several
  projects; its own note and bookmark go with it (it's the same saved design,
  not a copy). Removing it from a project doesn't delete it; deleting the
  saved design takes it out of every project.
- MAX_PROJECTS per account, MAX_PER_PROJECT designs each. No photos.
- Pages, on the shared account page's /account/ (the site's Worker sends
  /account/* to the apps):
    GET  /account/projects             the account's projects
    POST /account/projects             {name}: a new one
    GET  /account/projects/<id>        its name, note and designs
    POST /account/projects/<id>        action=rename|note|remove|delete|share|unshare
    POST /account/designs/<sid>/project  {project | new_name}: My designs' "Add to project"
    GET  /account/shared/<code>        a shared project, public
- Sharing (needs share_links too): a public page with the project's name and
  each design's picture, name and "Open in <tool>" link, each design shared
  by its own share link (cct_common.share_links). The notes only with
  "Include my notes" ticked (off by default). Opens are counted; "Stop
  sharing" ends the project's page (the designs' own links stay, under My
  designs).
- Deleting the account deletes its projects (accounts.delete_account).
"""
from __future__ import annotations

import html
import json
import secrets
import time
from typing import Callable, Optional

from .sqlite_db import INTEGRITY_ERRORS, SqliteDB

NAME_MAX = 80
NOTE_MAX = 2000
MAX_PROJECTS = 50
MAX_PER_PROJECT = 100
FULL = "You have 50 projects. Delete one before you start another."
FULL_DESIGNS = "This project has 100 designs. Remove one before you add another."
GONE = "This project isn't shared any more."
SHOWN_ON_ACCOUNT = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    note        TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_account ON projects (account_id, updated_at);
CREATE TABLE IF NOT EXISTS project_designs (
    project_id  TEXT NOT NULL,
    saved_id    TEXT NOT NULL,
    added_at    REAL NOT NULL,
    position    INTEGER NOT NULL,
    UNIQUE (project_id, saved_id)
);
CREATE INDEX IF NOT EXISTS project_designs_saved ON project_designs (saved_id);
CREATE TABLE IF NOT EXISTS shared_projects (
    code        TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    snapshot    TEXT NOT NULL,
    created_at  REAL NOT NULL,
    revoked_at  REAL,
    opens       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS shared_projects_project ON shared_projects (project_id);
"""


class Full(Exception):
    """A limit: MAX_PROJECTS, or MAX_PER_PROJECT."""


def clean_name(value) -> Optional[str]:
    return " ".join(str(value or "").split())[:NAME_MAX] or None


def clean_note(value) -> Optional[str]:
    from .saved_designs import clean_note as note_of_design
    text = note_of_design(value)          # the same text-only rule as a design's note
    return text[:NOTE_MAX] if text else None


class Projects(SqliteDB):
    """The projects tables, in the accounts database."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        super().__init__(path, SCHEMA)
        self._clock = clock

    def create(self, account_id: str, name) -> str:
        name = clean_name(name)
        if not name:
            raise ValueError("Give the project a name.")
        with self._write() as db:
            n = db.execute("SELECT COUNT(*) FROM projects WHERE account_id = ?", (account_id,)).fetchone()[0]
            if n >= MAX_PROJECTS:
                raise Full(FULL)
            pid, now = secrets.token_hex(8), self._clock()
            db.execute("INSERT INTO projects (id, account_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                       (pid, account_id, name, now, now))
        return pid

    def _own(self, db, account_id: str, pid: str) -> bool:
        return bool(db.execute("SELECT 1 FROM projects WHERE id = ? AND account_id = ?", (pid, account_id)).fetchone())

    def _touch(self, db, pid: str) -> None:
        db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (self._clock(), pid))

    def rename(self, account_id: str, pid: str, name) -> bool:
        name = clean_name(name)
        if not name:
            raise ValueError("Give the project a name.")
        with self._write() as db:
            if not self._own(db, account_id, pid):
                return False
            db.execute("UPDATE projects SET name = ? WHERE id = ?", (name, pid))
            self._touch(db, pid)
        return True

    def set_note(self, account_id: str, pid: str, note) -> bool:
        with self._write() as db:
            if not self._own(db, account_id, pid):
                return False
            db.execute("UPDATE projects SET note = ? WHERE id = ?", (clean_note(note), pid))
            self._touch(db, pid)
        return True

    def delete(self, account_id: str, pid: str) -> bool:
        """The project goes (and its shared page); its designs stay in My designs."""
        with self._write() as db:
            if not self._own(db, account_id, pid):
                return False
            db.execute("DELETE FROM project_designs WHERE project_id = ?", (pid,))
            db.execute("UPDATE shared_projects SET revoked_at = ? WHERE project_id = ? AND revoked_at IS NULL",
                       (self._clock(), pid))
            db.execute("DELETE FROM projects WHERE id = ?", (pid,))
        return True

    def add(self, account_id: str, pid: str, saved_id: str) -> bool:
        """Put one of the account's saved designs in one of its projects."""
        with self._write() as db:
            if not self._own(db, account_id, pid):
                return False
            if not db.execute("SELECT 1 FROM saved_designs WHERE id = ? AND account_id = ?",
                              (saved_id, account_id)).fetchone():
                return False
            n = db.execute("SELECT COUNT(*) FROM project_designs WHERE project_id = ?", (pid,)).fetchone()[0]
            there = db.execute("SELECT 1 FROM project_designs WHERE project_id = ? AND saved_id = ?",
                               (pid, saved_id)).fetchone()
            if there:
                return True
            if n >= MAX_PER_PROJECT:
                raise Full(FULL_DESIGNS)
            try:
                pos = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM project_designs WHERE project_id = ?",
                                 (pid,)).fetchone()[0]          # the order added, even within one second
                db.execute("INSERT INTO project_designs (project_id, saved_id, added_at, position) "
                           "VALUES (?, ?, ?, ?)", (pid, saved_id, self._clock(), pos))
            except INTEGRITY_ERRORS:
                pass
            self._touch(db, pid)
        return True

    def remove(self, account_id: str, pid: str, saved_id: str) -> bool:
        with self._write() as db:
            if not self._own(db, account_id, pid):
                return False
            db.execute("DELETE FROM project_designs WHERE project_id = ? AND saved_id = ?", (pid, saved_id))
            self._touch(db, pid)
        return True

    def list(self, account_id: str) -> list[dict]:
        """The account's projects, the latest updated first, with how many designs each."""
        with self._read() as db:
            rows = db.execute(
                "SELECT p.id, p.name, p.note, p.created_at, p.updated_at, "
                "(SELECT COUNT(*) FROM project_designs d WHERE d.project_id = p.id) AS designs "
                "FROM projects p WHERE p.account_id = ? ORDER BY p.updated_at DESC", (account_id,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, account_id: str, pid: str) -> Optional[dict]:
        """The project and its designs (in the order added), or None."""
        with self._read() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ? AND account_id = ?", (pid, account_id)).fetchone()
            if not row:
                return None
            designs = db.execute(
                "SELECT s.id, s.app, s.name, s.note, s.bookmarked, s.thumb IS NOT NULL AS has_thumb "
                "FROM project_designs d JOIN saved_designs s ON s.id = d.saved_id "
                "WHERE d.project_id = ? ORDER BY d.position", (pid,)).fetchall()
            live = db.execute("SELECT code, opens FROM shared_projects WHERE project_id = ? AND revoked_at IS NULL",
                              (pid,)).fetchone()
        return {**dict(row), "designs": [dict(d) for d in designs], "shared": dict(live) if live else None}

    def projects_of(self, account_id: str, saved_id: str) -> list[str]:
        """The ids of the projects a saved design is in."""
        with self._read() as db:
            rows = db.execute("SELECT d.project_id FROM project_designs d JOIN projects p ON p.id = d.project_id "
                              "WHERE d.saved_id = ? AND p.account_id = ?", (saved_id, account_id)).fetchall()
        return [r["project_id"] for r in rows]

    # ── sharing ───────────────────────────────────────────────────────────

    def share(self, account_id: str, pid: str, *, include_notes: bool = False) -> Optional[str]:
        """The project's public page (its live one, or one made now as a
        snapshot); each design is shared by its own link. None if not ours."""
        from .share_links import ShareLinks, new_code
        project = self.get(account_id, pid)
        if project is None:
            return None
        if project["shared"]:
            return project["shared"]["code"]
        links = ShareLinks(self.path, clock=self._clock)
        designs = []
        for d in project["designs"]:
            code = links.share(account_id, d["id"], include_note=include_notes)
            designs.append({"app": d["app"], "name": d["name"], "code": code,
                            **({"note": d["note"]} if include_notes and d["note"] else {})})
        snapshot = {"name": project["name"], "designs": designs,
                    **({"note": project["note"]} if include_notes and project["note"] else {})}
        with self._write() as db:
            for _ in range(5):
                code = new_code()
                try:
                    db.execute("INSERT INTO shared_projects (code, project_id, account_id, snapshot, created_at) "
                               "VALUES (?, ?, ?, ?, ?)", (code, pid, account_id, json.dumps(snapshot), self._clock()))
                    return code
                except INTEGRITY_ERRORS:
                    continue
        raise RuntimeError("no free share code")

    def stop_sharing(self, account_id: str, pid: str) -> bool:
        with self._write() as db:
            n = db.execute("UPDATE shared_projects SET revoked_at = ? WHERE project_id = ? AND account_id = ? "
                           "AND revoked_at IS NULL", (self._clock(), pid, account_id)).rowcount
        return bool(n)

    def open_shared(self, code: str) -> Optional[dict]:
        """A live shared project's snapshot, counting the open; None if it isn't live."""
        from .share_links import valid_code
        if not valid_code(code):
            return None
        with self._write() as db:
            row = db.execute("SELECT snapshot FROM shared_projects WHERE code = ? AND revoked_at IS NULL",
                             (code,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE shared_projects SET opens = opens + 1 WHERE code = ?", (code,))
        return json.loads(row["snapshot"])


def forget_account(db, account_id: str) -> None:
    """Inside delete_account's write."""
    db.execute("DELETE FROM project_designs WHERE project_id IN (SELECT id FROM projects WHERE account_id = ?)",
               (account_id,))
    db.execute("DELETE FROM shared_projects WHERE account_id = ?", (account_id,))
    db.execute("DELETE FROM projects WHERE account_id = ?", (account_id,))


# ── the pages ─────────────────────────────────────────────────────────────

_CSS = (
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(11rem,1fr));gap:.8rem}"
    ".card .note{white-space:pre-wrap;font-size:.9rem}.plist{list-style:none;padding:0}"
    ".plist li{padding:.5rem 0;border-bottom:1px solid #eee}textarea{width:100%;box-sizing:border-box;font:inherit}"
    "input[type=text]{font:inherit;padding:.35rem .5rem;border:1px solid #c8c8c8;border-radius:6px}"
    ".row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin:.4rem 0}"
)


def register_projects(app, accounts) -> None:
    """The routes above (register_account_routes). 404 while projects (or saved_designs) is off."""
    from flask import redirect, request

    from .account_page import _doc, _date, tool_title
    from .account_routes import current_account_id
    from .settings import setting

    tokens = accounts.tokens

    def on() -> bool:
        return setting(tokens.path, "projects") is True and setting(tokens.path, "saved_designs") is True

    def sharing() -> bool:
        return on() and setting(tokens.path, "share_links") is True

    def store() -> Projects:
        return Projects(tokens.path, clock=accounts._clock)

    def gone():
        return _doc("Not found", "<p>That page isn't here.</p>", 404)

    def doc(title, inner, status=200):
        return _doc(title, inner, status, extra=f"<style>{_CSS}</style>")

    def signed_in(path):
        acct = current_account_id()
        return acct, (None if acct else redirect(f"/account/sign-in?next={path}", code=302))

    def list_page(notice: str = "", status: int = 200):
        if not on():
            return gone()
        acct, err = signed_in("/account/projects")
        if err:
            return err
        rows = store().list(acct)
        items = "".join(
            f"<li><a href='/account/projects/{html.escape(p['id'], quote=True)}'>{html.escape(p['name'])}</a>"
            f"<div class='sub'>{p['designs']} design{'' if p['designs'] == 1 else 's'} · updated {_date(p['updated_at'])}"
            "</div></li>" for p in rows)
        body = (f"<ul class='plist'>{items}</ul>" if rows else
                "<p class='sub'>No projects yet. Group designs for one build: add them from "
                "<a href='/account/designs'>My designs</a>.</p>")
        new = ("<form method='post' action='/account/projects' class='row'><input type='text' name='name' "
               f"maxlength='{NAME_MAX}' placeholder='New project name' aria-label='New project name' required>"
               "<button type='submit'>New project</button></form>")
        return doc("Projects", f"<h1>Projects</h1>{notice}{body}{new}<p><a href='/account/'>Your account</a></p>",
                   status)

    def create():
        if not on():
            return gone()
        acct, err = signed_in("/account/projects")
        if err:
            return err
        try:
            pid = store().create(acct, request.form.get("name"))
        except Full as e:
            return list_page(f"<p class='msg'>{html.escape(str(e))}</p>", 409)
        except ValueError as e:
            return list_page(f"<p class='msg'>{html.escape(str(e))}</p>", 400)
        return redirect(f"/account/projects/{pid}", code=303)

    def project_page(pid, notice: str = "", status: int = 200):
        if not on():
            return gone()
        acct, err = signed_in(f"/account/projects/{pid}")
        if err:
            return err
        p = store().get(acct, pid)
        if not p:
            return gone()
        from .saved_designs import SavedDesigns, open_link
        apps = SavedDesigns(tokens.path).apps()
        titles = {a: v.get("title") for a, v in apps.items() if v.get("title")}
        q = html.escape(pid, quote=True)
        cards = []
        for d in p["designs"]:
            sid = html.escape(d["id"], quote=True)
            tool = tool_title(d["app"], titles)
            img = (f"<img src='/account/designs/{sid}/thumb' alt=''>" if d["has_thumb"]
                   else "<div class='ph'>No picture</div>")
            note = f"<div class='note'>{html.escape(d['note'])}</div>" if d["note"] else ""
            cards.append(f"<div class='card'>{img}<div class='in'><div class='name'>"
                         f"{'★ ' if d['bookmarked'] else ''}{html.escape(d['name'] or tool + ' design')}</div>"
                         f"<div class='sub'>{html.escape(tool)}</div>{note}"
                         f"<p><a href='{html.escape(open_link(d['app'], d['id'], apps), quote=True)}'>Open in "
                         f"{html.escape(tool)}</a></p><form method='post' action='/account/projects/{q}'>"
                         f"<input type='hidden' name='action' value='remove'><input type='hidden' name='saved_id' "
                         f"value='{sid}'><button class='secondary' type='submit'>Remove from project</button></form>"
                         "</div></div>")
        designs = ("<div class='grid'>" + "".join(cards) + "</div>") if cards else (
            "<p class='sub'>No designs in this project yet. Add them from <a href='/account/designs'>My designs</a>.</p>")
        tools = sorted({tool_title(d["app"], titles) for d in p["designs"]})
        files = (f"<h2>Download the files</h2><p class='sub'>Open each design in its tool to download its files: "
                 f"{html.escape(', '.join(tools))}.</p>") if tools else ""
        share = ""
        if sharing():
            if p["shared"]:
                url = f"{request.host_url.rstrip('/')}/account/shared/{p['shared']['code']}"
                share = (f"<h2>Sharing</h2><p><a href='{html.escape(url, quote=True)}'>{html.escape(url)}</a></p>"
                         f"<p class='sub'>Opened {p['shared']['opens']} time{'' if p['shared']['opens'] == 1 else 's'}"
                         f"</p><form method='post' action='/account/projects/{q}'><input type='hidden' name='action' "
                         "value='unshare'><button class='secondary' type='submit'>Stop sharing</button></form>")
            elif p["designs"]:
                share = ("<h2>Sharing</h2><form method='post' action='/account/projects/"
                         f"{q}'><p class='sub'>Anyone with the link can see this project's name and its designs' "
                         "pictures and settings, and open each in its tool. Your email isn't shown. You can turn the "
                         "link off at any time.</p><label class='sub'><input type='checkbox' name='include_notes' "
                         "value='1'> Include my notes</label> <input type='hidden' name='action' value='share'>"
                         "<button type='submit'>Share this project</button></form>")
        head = (f"<h1>{html.escape(p['name'])}</h1>{notice}<form method='post' action='/account/projects/{q}' "
                f"class='row'><input type='hidden' name='action' value='rename'><input type='text' name='name' "
                f"maxlength='{NAME_MAX}' value='{html.escape(p['name'], quote=True)}' aria-label='Name'>"
                "<button class='secondary' type='submit'>Rename</button></form>"
                f"<form method='post' action='/account/projects/{q}'><input type='hidden' name='action' value='note'>"
                f"<textarea name='note' rows='4' maxlength='{NOTE_MAX}' aria-label='Note' placeholder='A note on "
                f"this build (only you see it)'>{html.escape(p['note'] or '')}</textarea>"
                "<button class='secondary' type='submit'>Save note</button></form>")
        delete = (f"<form method='post' action='/account/projects/{q}' onsubmit=\"return confirm("
                  "'Delete this project? Its designs stay in My designs.')\"><input type='hidden' name='action' "
                  "value='delete'><button class='danger' type='submit'>Delete project</button></form>")
        return doc(p["name"], head + "<h2>Designs</h2>" + designs + files + share
                   + f"<p style='margin-top:2rem'>{delete}</p><p><a href='/account/projects'>All projects</a></p>",
                   status)

    def project_update(pid):
        if not on():
            return gone()
        acct, err = signed_in(f"/account/projects/{pid}")
        if err:
            return err
        s, action = store(), request.form.get("action")
        try:
            if action == "rename":
                ok = s.rename(acct, pid, request.form.get("name"))
            elif action == "note":
                ok = s.set_note(acct, pid, request.form.get("note"))
            elif action == "remove":
                ok = s.remove(acct, pid, request.form.get("saved_id") or "")
            elif action == "delete":
                return redirect("/account/projects", code=303) if s.delete(acct, pid) else gone()
            elif action == "share" and sharing():
                ok = s.share(acct, pid, include_notes=request.form.get("include_notes") == "1") is not None
            elif action == "unshare" and sharing():
                ok = s.stop_sharing(acct, pid) or True
            else:
                ok = False
        except ValueError as e:
            return project_page(pid, f"<p class='msg'>{html.escape(str(e))}</p>", 400)
        if not ok:
            return gone()
        return redirect(f"/account/projects/{pid}", code=303)

    def add_design(saved_id):
        """My designs' "Add to project": an existing project, or a new one by name."""
        if not on():
            return gone()
        acct, err = signed_in("/account/designs")
        if err:
            return err
        s = store()
        pid = request.form.get("project") or ""
        try:
            if pid == "new" or not pid:
                pid = s.create(acct, request.form.get("new_name"))
            if not s.add(acct, pid, saved_id):
                return gone()
        except Full as e:
            return doc("Projects", f"<p class='msg'>{html.escape(str(e))}</p><p><a href='/account/designs'>"
                                   "My designs</a></p>", 409)
        except ValueError as e:
            return doc("Projects", f"<p class='msg'>{html.escape(str(e))}</p><p><a href='/account/designs'>"
                                   "My designs</a></p>", 400)
        return redirect(f"/account/projects/{pid}", code=303)

    def shared_page(code):
        if not sharing():
            return doc("Shared project", f"<p>{GONE}</p>", 404)
        snap = store().open_shared(code)
        if not snap:
            return doc("Shared project", f"<p>{GONE}</p>", 404)
        from .saved_designs import SavedDesigns
        from .share_links import link
        apps = SavedDesigns(tokens.path).apps()
        titles = {a: v.get("title") for a, v in apps.items() if v.get("title")}
        cards = []
        for d in snap["designs"]:
            tool = tool_title(d["app"], titles)
            page = (apps.get(d["app"]) or {}).get("page") or f"/tools/{d['app']}/"
            url = link(page, d["code"], request.host_url)
            note = f"<div class='note'>{html.escape(d['note'])}</div>" if d.get("note") else ""
            cards.append(f"<div class='card'><img src='/api/shared/{html.escape(d['code'], quote=True)}/thumb' alt=''>"
                         f"<div class='in'><div class='name'>{html.escape(d['name'] or tool + ' design')}</div>"
                         f"<div class='sub'>{html.escape(tool)}</div>{note}"
                         f"<p><a href='{html.escape(url, quote=True)}'>Open in {html.escape(tool)}</a></p></div></div>")
        note = f"<p class='note'>{html.escape(snap['note'])}</p>" if snap.get("note") else ""
        r = doc(snap["name"], f"<h1>{html.escape(snap['name'])}</h1><p class='sub'>A shared project. Open a design to "
                              f"get your own copy in its tool.</p>{note}<div class='grid'>{''.join(cards)}</div>")
        return r

    app.add_url_rule("/account/projects", endpoint="cct_projects", view_func=list_page, methods=["GET"])
    app.add_url_rule("/account/projects", endpoint="cct_projects_create", view_func=create, methods=["POST"])
    app.add_url_rule("/account/projects/<pid>", endpoint="cct_project", view_func=project_page, methods=["GET"])
    app.add_url_rule("/account/projects/<pid>", endpoint="cct_project_update", view_func=project_update,
                     methods=["POST"])
    app.add_url_rule("/account/designs/<saved_id>/project", endpoint="cct_project_add", view_func=add_design,
                     methods=["POST"])
    app.add_url_rule("/account/shared/<code>", endpoint="cct_shared_project", view_func=shared_page, methods=["GET"])
