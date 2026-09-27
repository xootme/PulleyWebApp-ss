"""
bug_store.py — bug and feature reports kept in the database.

The report log (logs/bug_reports.log) lives on the server's disk, which
on Cloud Run vanishes with each server. So when DATABASE_URL is set, every
report — description, contact address and the design (app state) — is
also written here, where it lasts until it's deleted.

What goes where (see cct_common.bug_report and the privacy policy):
- here and the log: everything, including the design and the address;
- the GitHub issue: the description only (no design, no address);
- the notification email: the description and the reporter's address
  (so we can reply), not the design.
"""
from __future__ import annotations

import json
import time

from cct_common.sqlite_db import SqliteDB

_SCHEMA = """
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


class BugReports(SqliteDB):
    def __init__(self, path: str, clock=time.time):
        super().__init__(path, _SCHEMA)
        self.clock = clock

    def add(self, report_id: str, *, report_type: str, seeing: str, should_see: str,
            error_msg: str, user_comment: str, email: str, state, app_version: str) -> None:
        with self._write() as db:
            db.execute(
                "INSERT OR IGNORE INTO bug_reports (report_id, created_at, report_type, seeing, "
                "should_see, error_msg, user_comment, email, state, app_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, self.clock(), report_type, seeing, should_see, error_msg,
                 user_comment, email, json.dumps(state), app_version))

    def set_issue_url(self, report_id: str, url: str) -> None:
        with self._write() as db:
            db.execute("UPDATE bug_reports SET issue_url = ? WHERE report_id = ?", (url, report_id))

    def get(self, report_id: str) -> dict | None:
        with self._read() as db:
            row = db.execute("SELECT * FROM bug_reports WHERE report_id = ?", (report_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["state"] = json.loads(d["state"] or "null")
        return d

    def list(self, limit: int = 500) -> list[dict]:
        """Newest first, for the admin dashboard (cct_common.admin)."""
        with self._read() as db:
            rows = db.execute("SELECT * FROM bug_reports ORDER BY created_at DESC LIMIT ?",
                              (limit,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["state"] = json.loads(d["state"] or "null")
            out.append(d)
        return out

    def delete(self, report_id: str) -> bool:
        """For a deletion request: the design and address go with the row."""
        with self._write() as db:
            n = db.execute("DELETE FROM bug_reports WHERE report_id = ? RETURNING report_id",
                           (report_id,)).fetchall()
        return bool(n)
