"""
notices.py — account notices: a one-off email to every account holder about
a change they need to know of, such as new Terms (0.50.0; the owner's
approval of 2026-10-30). Not the newsletter: it goes to every open account,
ticked or not, and only when the owner sends it from the admin page's
Notices tab.

- The text is fixed here (NOTICES), as approved. "[DATE]" stays in it until
  the owner types the date on the card; nothing goes out while it's blank.
  The first "Send to all" fixes the date: later passes use the same one.
- Who: every open account (closed ones keep no email), each address once,
  except the directory reviewers' test account (REVIEW_LOGIN_EMAIL).
- "Send me a test": one copy to the signed-in admin's own address only.
- "Send to all": only after typing SEND. The page then asks for batches of
  BATCH, about two a second (Resend's limit), so no request runs long and
  no background thread is needed.
- Once per address: each account it reached is recorded (notice id +
  account id; no address is copied), so a second click, or a retry after an
  error, sends only to those not yet reached. A failure is recorded with
  the sender's error and is tried again on the next "Send to all", not in
  the same pass. Each address is claimed ("sending") before its email goes,
  so two passes at once (two admin tabs) never both send it; a claim whose
  request died is free again after CLAIM_S.
- From: the sender's default, "CheapCADTools <info@cheapcadtools.com>",
  the address sign-in links already come from.
"""
from __future__ import annotations

import os
import secrets
import time
from typing import Callable, Optional

from .sqlite_db import SqliteDB

BATCH = 10
GAP_S = 0.5            # between two emails: Resend allows about 2 a second
CLAIM_S = 600          # a send that never reported back is tried again after this
CONFIRM = "SEND"
PLACEHOLDER = "[DATE]"

# CCT_WebSite's approved draft (site-phase4-privacy notes), word for word; 0.50.1:
# "those you started with" for "the 20 you started with" (the grant is a setting).
NOTICES = {
    "terms-2026-11": {
        "title": "Terms and privacy policy change",
        "subject": "Changes to our terms and privacy policy on [DATE]",
        "body": (
            "Hello,\n\n"
            "On [DATE] we're updating the CheapCADTools Terms of Service and Privacy Policy. What changes for you:\n\n"
            "• Bonus tokens. Your first token purchase comes with 25% extra tokens. After every 200 purchased "
            "tokens you spend, your next purchase comes with 10% extra; after every 1,000, 20% extra. Bonus tokens "
            "are free tokens: they can't be refunded, and refunding a purchase takes back its bonus.\n"
            "• Tokens you buy are used first. From [DATE], downloads use your purchased tokens before your free "
            "ones (those you started with, promotions, prizes and bonuses). Downloads before [DATE] aren't "
            "affected.\n"
            "• Download again at no charge. Files you've paid for can be downloaded again at no charge for as "
            "long as you have your account (it was 24 hours). The designs you download are kept in My designs on "
            "your account, where you can rename or delete them.\n"
            "• An optional newsletter. You can now ask for our monthly newsletter under \"Your tokens\". You "
            "won't get it unless you turn it on.\n"
            "• To give the first-purchase bonus only once per card, we keep an identifier for the card or PayPal "
            "account you pay with. It isn't the card number.\n\n"
            "Tokens you've already bought keep their value, and you don't need to do anything. If you use "
            "CheapCADTools after [DATE], the new terms apply. If you'd rather not, you can ask for a refund of your "
            "unused purchased tokens at support@cheapcadtools.com (Terms, section 4).\n\n"
            "Terms: https://cheapcadtools.com/terms/\n"
            "Privacy policy: https://cheapcadtools.com/privacy-policy/\n\n"
            "CheapCADTools (Myerchin Enterprises Inc.), 12311 SE 23rd St., Bellevue, WA 98005, United States\n"
            "You're getting this because you have a CheapCADTools account. It's an account notice, not marketing."),
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS notice_runs (
    notice_id   TEXT PRIMARY KEY,
    date_text   TEXT NOT NULL,
    started_by  TEXT NOT NULL,
    started_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notice_passes (
    pass_id     TEXT PRIMARY KEY,
    notice_id   TEXT NOT NULL,
    started_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notice_sends (
    notice_id   TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    pass_id     TEXT,
    at          REAL NOT NULL,
    PRIMARY KEY (notice_id, account_id)
);
"""


class NoticeError(Exception):
    """Shown on the card."""


def fill(text: str, date: str) -> str:
    return text.replace(PLACEHOLDER, date)


def clean_date(date) -> str:
    date = " ".join(str(date or "").split())
    if not date:
        raise NoticeError(f"Type the date for {PLACEHOLDER} first.")
    if len(date) > 40 or any(c in date for c in "<>[]{}"):
        raise NoticeError("The date is a short text, such as November 15, 2026.")
    return date


def reviewer_email() -> str:
    return (os.environ.get("REVIEW_LOGIN_EMAIL") or "").strip().lower()


class Notices(SqliteDB):
    """The notices' send records, in the accounts database."""

    def __init__(self, path: str, *, sender: Callable, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        super().__init__(path, SCHEMA)
        self._send, self._clock, self._sleep = sender, clock, sleep

    @staticmethod
    def notice(notice_id: str) -> dict:
        try:
            return NOTICES[notice_id]
        except KeyError:
            raise NoticeError("No such notice.") from None

    def _recipients(self, db) -> list:
        """(account id, email) of every open account but the reviewers'. Each
        address once: accounts' emails are unique (lower-cased) already."""
        skip = reviewer_email()
        rows = db.execute("SELECT id, email FROM accounts WHERE email NOT LIKE 'deleted:%' "
                          "ORDER BY created_at, id").fetchall()
        return [(r["id"], r["email"]) for r in rows if str(r["email"]).strip().lower() != skip]

    def status(self, notice_id: str) -> dict:
        self.notice(notice_id)
        with self._read() as db:
            people = self._recipients(db)
            rows = {r["account_id"]: dict(r) for r in db.execute(
                "SELECT account_id, status, error FROM notice_sends WHERE notice_id = ?", (notice_id,)).fetchall()}
            run = db.execute("SELECT date_text, started_by, started_at FROM notice_runs WHERE notice_id = ?",
                             (notice_id,)).fetchone()
        sent = sum(1 for a, _ in people if (rows.get(a) or {}).get("status") == "sent")
        failed = [{"email": e, "error": rows[a]["error"]} for a, e in people
                  if (rows.get(a) or {}).get("status") == "failed"]
        return {"id": notice_id, "recipients": len(people), "sent": sent, "failed": len(failed),
                "left": len(people) - sent, "errors": failed[:50], "date": run["date_text"] if run else None,
                "started_by": run["started_by"] if run else None, "started_at": run["started_at"] if run else None}

    def test(self, notice_id: str, date, to: str) -> tuple:
        """One copy to `to` (the signed-in admin), with the date filled in."""
        n, date = self.notice(notice_id), clean_date(date)
        return self._send(to, fill(n["subject"], date), fill(n["body"], date))

    def start(self, notice_id: str, date, confirm, admin: str) -> dict:
        """A "Send to all" pass: needs SEND typed. The first one fixes the date."""
        self.notice(notice_id)
        if str(confirm or "").strip() != CONFIRM:
            raise NoticeError(f"Type {CONFIRM} to send to every account.")
        date = clean_date(date)
        now = self._clock()
        with self._write() as db:
            run = db.execute("SELECT date_text FROM notice_runs WHERE notice_id = ?", (notice_id,)).fetchone()
            if run and run["date_text"] != date:
                raise NoticeError(f"This notice already went out with \"{run['date_text']}\" for {PLACEHOLDER}; "
                                  "the rest get the same.")
            if not run:
                db.execute("INSERT INTO notice_runs (notice_id, date_text, started_by, started_at) VALUES (?, ?, ?, ?)",
                           (notice_id, date, admin, now))
            pass_id = secrets.token_urlsafe(12)
            db.execute("INSERT INTO notice_passes (pass_id, notice_id, started_at) VALUES (?, ?, ?)",
                       (pass_id, notice_id, now))
        return {"pass": pass_id, **self.status(notice_id)}

    def batch(self, notice_id: str, pass_id: str) -> dict:
        """Send to up to BATCH accounts not yet reached (and not already
        failed in this pass); returns the status with done=True at the end."""
        n = self.notice(notice_id)
        with self._read() as db:
            ok = db.execute("SELECT 1 FROM notice_passes WHERE pass_id = ? AND notice_id = ?",
                            (str(pass_id or ""), notice_id)).fetchone()
            run = db.execute("SELECT date_text FROM notice_runs WHERE notice_id = ?", (notice_id,)).fetchone()
            if not ok or not run:
                raise NoticeError("Start with Send to all.")
            todo = self._todo(db, notice_id, pass_id)[:BATCH]
        subject, body = fill(n["subject"], run["date_text"]), fill(n["body"], run["date_text"])
        for i, (acct, email) in enumerate(todo):
            if i:
                self._sleep(GAP_S)
            if not self._claim(notice_id, acct, pass_id):   # another pass has it, or reached it
                continue
            try:
                sent, err = self._send(email, subject, body)
            except Exception as e:                 # noqa: BLE001 — record it, go on
                sent, err = False, str(e)
            with self._write() as db:
                db.execute("UPDATE notice_sends SET status = ?, error = ?, at = ? WHERE notice_id = ? AND account_id = ?",
                           ("sent" if sent else "failed", None if sent else str(err or "")[:300], self._clock(),
                            notice_id, acct))
        with self._read() as db:
            more = bool(self._todo(db, notice_id, pass_id))
        return {**self.status(notice_id), "done": not more}

    def _claim(self, notice_id: str, acct: str, pass_id: str) -> bool:
        """Mark the account "sending" for this pass before its email goes,
        in one write, so two passes (two admin tabs) never both send it."""
        with self._write() as db:
            row = db.execute("SELECT status, pass_id, at FROM notice_sends WHERE notice_id = ? AND account_id = ?",
                             (notice_id, acct)).fetchone()
            if row and not self._open(row, pass_id, self._clock()):
                return False
            db.execute("DELETE FROM notice_sends WHERE notice_id = ? AND account_id = ?", (notice_id, acct))
            db.execute("INSERT INTO notice_sends (notice_id, account_id, status, error, pass_id, at) "
                       "VALUES (?, ?, 'sending', NULL, ?, ?)", (notice_id, acct, pass_id, self._clock()))
        return True

    @staticmethod
    def _open(row, pass_id: str, now: float) -> bool:
        """May this pass try the account, given its record? Not once sent; not
        while another send of it is under way (a claim left by a request
        that died is free again after CLAIM_S); a failure, on a later pass."""
        if row["status"] == "sent":
            return False
        if row["status"] == "sending":
            return row["at"] < now - CLAIM_S
        return row["pass_id"] != pass_id

    def _todo(self, db, notice_id: str, pass_id: str) -> list:
        """Who this pass still has to try."""
        now = self._clock()
        done = {r["account_id"]: r for r in db.execute(
            "SELECT account_id, status, pass_id, at FROM notice_sends WHERE notice_id = ?", (notice_id,)).fetchall()}
        return [(a, e) for a, e in self._recipients(db) if a not in done or self._open(done[a], pass_id, now)]
