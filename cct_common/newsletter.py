"""
newsletter.py — the newsletter opt-in's consent record (0.36.0; the owner's
design, 2026-10-30: Controller task cctcommon-newsletter-opt-in).

    from cct_common.newsletter import Newsletter
    news = Newsletter(database_url)
    news.record(account_id, True, app="pulleys", source="signup", label_version=1)
    news.state(account_id)        # {"subscribed": True, "since": 1793...}

- One append-only table, newsletter_consents, in the accounts database. A
  row is one choice: who, when, subscribed (1) or not (0), the app it was
  made in (the ledger's app name, or "site"), where (signup: the one-time
  welcome page of a new account; account: the "Your tokens" dialog), and
  the label the person saw, by version and word for word.
- The current choice is the account's latest row. No row is not
  subscribed, so the accounts from before need nothing done. Leaving adds a
  0 row: consent and its withdrawal are both kept. A choice that changes
  nothing adds no row. Deleting the account deletes its rows (accounts.py).
- The label lives here, as LABELS. New wording is a new version; each row
  keeps the text that was shown.
- All of it is behind the newsletter_opt_in business setting, off until
  the owner turns it on (after the site's privacy text says so): no
  checkbox, nothing in api/account, and the routes refuse (account_routes).
- Brevo follows this record (0.39.0, cct_common.newsletter_sync): each
  change queues adding or deleting the contact, in the same write.
  Brevo's own unsubscribe comes back as a 0 row with source "brevo".
"""
from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .newsletter_sync import SCHEMA as _SYNC_SCHEMA, enqueue, kick
from .sqlite_db import SqliteDB

LABELS = {
    1: "Once a month we'll tell you about new and upcoming CAD apps, "
       "new features and bug fixes to existing apps.",
}
LABEL_VERSION = max(LABELS)
SOURCES = ("signup", "account", "brevo")

SCHEMA = """
CREATE TABLE IF NOT EXISTS newsletter_consents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     TEXT NOT NULL,
    at             REAL NOT NULL,
    subscribed     INTEGER NOT NULL,
    app            TEXT NOT NULL,
    source         TEXT NOT NULL,
    label_version  INTEGER NOT NULL,
    label          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS newsletter_consents_account ON newsletter_consents(account_id, id);
""" + _SYNC_SCHEMA

# Each account's latest row.
_LATEST = """SELECT n.* FROM newsletter_consents n
             WHERE n.id = (SELECT MAX(m.id) FROM newsletter_consents m WHERE m.account_id = n.account_id)"""


def label_version(value) -> int:
    """A label version the page sent back, if it's one of ours; else today's."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return LABEL_VERSION
    return v if v in LABELS else LABEL_VERSION


class Newsletter(SqliteDB):
    """The newsletter_consents table, in the accounts database."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        super().__init__(path, SCHEMA)
        self._clock = clock

    def state(self, account_id: str) -> dict:
        """{"subscribed", "since"}: the latest choice (since: when it was made; None with no row)."""
        with self._read() as db:
            row = db.execute("SELECT subscribed, at FROM newsletter_consents WHERE account_id = ? "
                             "ORDER BY id DESC LIMIT 1", (account_id,)).fetchone()
        if not row:
            return {"subscribed": False, "since": None}
        return {"subscribed": bool(row["subscribed"]), "since": row["at"]}

    def record(self, account_id: str, subscribed: bool, *, app: str, source: str,
               label_version: int = LABEL_VERSION) -> dict:
        """Keep this choice, unless it's already the account's; the state after."""
        if source not in SOURCES:
            raise ValueError(f"unknown source: {source!r}")
        if label_version not in LABELS:
            raise ValueError(f"unknown label version: {label_version!r}")
        subscribed = bool(subscribed)
        changed = False
        with self._write() as db:
            row = db.execute("SELECT subscribed FROM newsletter_consents WHERE account_id = ? "
                             "ORDER BY id DESC LIMIT 1", (account_id,)).fetchone()
            if bool(row and row["subscribed"]) != subscribed:
                now = self._clock()
                db.execute("INSERT INTO newsletter_consents "
                           "(account_id, at, subscribed, app, source, label_version, label) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (account_id, now, int(subscribed), app or "site", source,
                            label_version, LABELS[label_version]))
                email = db.execute("SELECT email FROM accounts WHERE id = ?", (account_id,)).fetchone()
                enqueue(db, account_id, email["email"] if email else None, "add" if subscribed else "delete", now)
                changed = True
        if changed:
            kick(self.path, clock=self._clock)          # Brevo follows, in the background
        return self.state(account_id)

    def counts(self) -> dict:
        """Subscribed now, and by where their latest choice was made."""
        with self._read() as db:
            rows = db.execute(f"SELECT source, COUNT(*) AS n FROM ({_LATEST}) latest "
                              "WHERE subscribed = 1 GROUP BY source").fetchall()
        by = {r["source"]: int(r["n"]) for r in rows}
        return {"subscribed": sum(by.values()), **{s: by.get(s, 0) for s in SOURCES}}

    def subscribers(self) -> list[dict]:
        """Everyone subscribed now: email, since, app, label_version; oldest first."""
        with self._read() as db:
            rows = db.execute(f"SELECT a.email, latest.at, latest.app, latest.label_version "
                              f"FROM ({_LATEST}) latest JOIN accounts a ON a.id = latest.account_id "
                              "WHERE latest.subscribed = 1 ORDER BY latest.at, latest.id").fetchall()
        return [{"email": r["email"], "since": r["at"], "app": r["app"],
                 "label_version": int(r["label_version"])} for r in rows
                if not str(r["email"]).startswith("deleted:")]


def _cell(value) -> str:
    # A spreadsheet runs a cell that starts with = + - @ as a formula.
    s = str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv(subscribers: list[dict]) -> str:
    """The admin page's Export CSV: email, subscribed since (UTC), app, label version."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["email", "subscribed_since", "app", "label_version"])
    for s in subscribers:
        since = datetime.fromtimestamp(s["since"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        w.writerow([_cell(s["email"]), since, _cell(s["app"]), s["label_version"]])
    return out.getvalue()

