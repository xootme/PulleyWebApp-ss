"""
reports.py — every app's bug and feature reports in the shared database
(0.44.0; account plan step 5). The pulley app's own store (its bug_store.py)
moved here; the other apps kept theirs only in logs/bug_reports.log, on a
Cloud Run server's disk, gone with the server.

What goes where stays as cct_common.bug_report and the privacy text say:
this table and the log keep everything, the design (app state) and the typed
contact address included; the GitHub issue has the description only; the
email the description and the address.

My reports (the my_reports business setting, off until the owner turns it
on): while on, a signed-in person's report keeps their account id, their
account page lists their reports and its status, and the admin page's Bug
reports tab sets that status (STATUSES) and may send one "fixed" email.

Deleting an account (accounts.delete_account): its reports, and any sent from
its email address, lose the account link, the typed address and the design;
the words stay, as feedback with nothing personal (forget_account).
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from .sqlite_db import SqliteDB

SCHEMA = """
CREATE TABLE IF NOT EXISTS bug_reports (
    report_id    TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    report_type  TEXT NOT NULL,
    seeing       TEXT,
    should_see   TEXT,
    error_msg    TEXT,
    user_comment TEXT,
    email        TEXT,
    state        TEXT,
    app_version  TEXT,
    issue_url    TEXT
);
CREATE INDEX IF NOT EXISTS bug_reports_created ON bug_reports (created_at);
"""
# 0.44.0: which app, whose (my_reports), and the status the admin sets.
COLUMNS = {"app": "TEXT", "account_id": "TEXT", "status": "TEXT", "status_note": "TEXT", "fixed_in": "TEXT",
           "status_at": "REAL", "emailed_at": "REAL"}
STATUSES = ("received", "looking", "fixed", "wontfix")
NOTE_MAX = 300
VERSION_MAX = 40


def ensure_columns(store) -> None:
    store._ensure_columns("bug_reports", COLUMNS)


def my_reports_on(path) -> bool:
    from .settings import setting
    return setting(path, "my_reports") is True


class ReportStore(SqliteDB):
    """The bug_reports table. add/get/list/delete are pulley's bug_store's, so
    the admin page's Bug reports tab reads it as it did."""

    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        super().__init__(path, SCHEMA)
        self.clock = clock
        ensure_columns(self)

    def add(self, report_id: str, *, report_type: str, seeing: str, should_see: str,
            error_msg: str = "", user_comment: str = "", email: str = "", state=None, app_version: str = "",
            app: Optional[str] = None, account_id: Optional[str] = None) -> None:
        with self._write() as db:
            db.execute(
                "INSERT OR IGNORE INTO bug_reports (report_id, created_at, report_type, seeing, should_see, "
                "error_msg, user_comment, email, state, app_version, app, account_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, self.clock(), report_type, seeing, should_see, error_msg, user_comment, email,
                 json.dumps(state if state is not None else {}), app_version, app, account_id))

    def set_issue_url(self, report_id: str, url: str) -> None:
        with self._write() as db:
            db.execute("UPDATE bug_reports SET issue_url = ? WHERE report_id = ?", (url, report_id))

    def get(self, report_id: str) -> Optional[dict]:
        with self._read() as db:
            row = db.execute("SELECT * FROM bug_reports WHERE report_id = ?", (report_id,)).fetchone()
        return _row(row) if row else None

    def list(self, limit: int = 500) -> list[dict]:
        """Newest first, for the admin dashboard (cct_common.admin)."""
        with self._read() as db:
            rows = db.execute("SELECT * FROM bug_reports ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]

    def delete(self, report_id: str) -> bool:
        """For a deletion request: the design and address go with the row."""
        with self._write() as db:
            n = db.execute("DELETE FROM bug_reports WHERE report_id = ? RETURNING report_id",
                           (report_id,)).fetchall()
        return bool(n)

    def for_account(self, account_id: str) -> list[dict]:
        """The account's own reports, newest first: what the account page shows."""
        with self._read() as db:
            rows = db.execute("SELECT report_id, created_at, report_type, seeing, should_see, app, app_version, "
                              "status, status_note, fixed_in, status_at FROM bug_reports WHERE account_id = ? "
                              "ORDER BY created_at DESC", (account_id,)).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, report_id: str, status: str, *, fixed_in=None, note=None) -> Optional[dict]:
        """The admin's status; fixed needs the version, wontfix the reason.
        The row after, or None if there's no such report (ValueError if the
        status or what it needs is missing)."""
        if status not in STATUSES:
            raise ValueError("The status is received, looking, fixed or wontfix.")
        fixed_in = " ".join(str(fixed_in or "").split())[:VERSION_MAX] or None
        note = " ".join(str(note or "").split())[:NOTE_MAX] or None
        if status == "fixed" and not fixed_in:
            raise ValueError("Say which version fixed it.")
        if status == "wontfix" and not note:
            raise ValueError("Give the reason it won't be fixed.")
        with self._write() as db:
            n = db.execute("UPDATE bug_reports SET status = ?, fixed_in = ?, status_note = ?, status_at = ? "
                           "WHERE report_id = ?", (status, fixed_in if status == "fixed" else None,
                                                   note if status == "wontfix" else None, self.clock(),
                                                   report_id)).rowcount
        return self.get(report_id) if n else None

    def mark_emailed(self, report_id: str) -> None:
        with self._write() as db:
            db.execute("UPDATE bug_reports SET emailed_at = ? WHERE report_id = ?", (self.clock(), report_id))


def _row(row) -> dict:
    d = dict(row)
    d["state"] = json.loads(d["state"] or "null")
    return d


def forget_account(db, account_id: str, email: Optional[str] = None) -> None:
    """Inside delete_account's write: the reports keep their words only. That's
    the account's own reports, and (0.46.1) any whose typed address is the
    account's email, in any case: sent signed out, or before my_reports was on."""
    db.execute("UPDATE bug_reports SET account_id = NULL, email = NULL, state = NULL WHERE account_id = ?",
               (account_id,))
    if email:
        db.execute("UPDATE bug_reports SET email = NULL, state = NULL WHERE LOWER(email) = LOWER(?)", (email,))


# ── the words people see ──────────────────────────────────────────────────

def status_line(r: dict, tool: str) -> str:
    """"Received", "Being looked at", "Fixed in Gear Designer 1.2.3", "Won't fix: …"."""
    status = r.get("status") or "received"
    if status == "looking":
        return "Being looked at"
    if status == "fixed":
        return f"Fixed in {tool} {r.get('fixed_in') or ''}".strip()
    if status == "wontfix":
        return f"Won't fix: {r.get('status_note') or ''}".strip()
    return "Received"


def fixed_email(r: dict, tool: str, page_url: str) -> tuple:
    """(subject, body) of the "fixed" email: the owner's to approve."""
    words = " ".join(x for x in (r.get("seeing"), r.get("should_see")) if x)
    when = time.gmtime(r["created_at"])
    date = f"{time.strftime('%b', when)} {when.tm_mday}, {when.tm_year}"
    subject = f"Fixed in {tool} {r.get('fixed_in')} — thanks for your report"
    body = (f"Hi,\n\nOn {date} you told us: \"{words}\".\n\n"
            f"That's fixed in {tool} {r.get('fixed_in')}, live now at {page_url}\n\n"
            "Thanks for taking the time to tell us.\n\nCheapCADTools\n")
    return subject, body
