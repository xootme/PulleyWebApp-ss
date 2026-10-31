"""
newsletter_sync.py — the newsletter list in Brevo follows our consent record
(0.39.0; the owner, 2026-10-30: CheapCADTools' own Brevo account, Controller
task cctcommon-brevo-sync-for-the-newsletter-opt-in).

- newsletter_consents (cct_common.newsletter) stays the truth; Brevo follows.
  Each change queues a job (newsletter_sync): ticking the box adds the
  contact to the list (newsletter_list_id); unticking it, or deleting a
  subscribed account, DELETES the contact, not just unlists it (the privacy
  text promises removal). A contact that's also on another list in the same
  Brevo account (the test environment's) is only taken off ours (0.41.0). A job keeps the email until it's done, then
  forgets it. A later change for the same account supersedes a waiting one.
- Jobs run in the background straight after the change (kick), so nothing a
  person does waits for Brevo; a failed one retries after BACKOFF_S. A 404 on
  delete is done. With no BREVO_API_KEY or no list id, jobs wait, uncounted.
- The queue is the shared database's, so only one service needs the key
  (pulley): it runs every app's waiting jobs, at most every DRAIN_EVERY_S
  during its requests (maybe_kick, 0.41.0), and at the nightly reconcile.
- Brevo's unsubscribe link: the webhook (account_routes) records a 0 row
  with source "brevo", which queues the delete. It needs BREVO_WEBHOOK_SECRET.
- reconcile(), with the daily account housekeeping: the whole list against
  who's subscribed here. Missing ones are added, extras deleted, and anyone
  Brevo blocklisted gets a 0 row (an unsubscribe whose webhook we missed).
  It also catches choices made in apps still on an older cct_common.
- While newsletter_opt_in is off (0.55.0; found by Pulley), reconcile()
  doesn't even read the list, so a list that already has contacts (imported
  another way) is never emptied because nobody here is subscribed. The
  drain then sends only "delete" jobs: one person's own unsubscribe or
  account deletion, which the privacy text promises to carry out (0.55.1).
  Adds, and the reconcile's bulk deletions ("extra" jobs), wait until it's
  on. Each says why in one log line, and the admin page says it too.
- A reconcile that would delete more than RECONCILE_GUARD_MIN contacts and
  more than RECONCILE_GUARD_SHARE of the list deletes none of them, and the
  log and the admin page say how many it held back. It still adds the
  missing ones and records Brevo's unsubscribes. A big clean-up is a
  deliberate reconcile(allow_big=True).
- A contact is just its email: no name, account id or tool.
- The key (0.44.0): one that starts "xsmtpsib-" is Brevo's SMTP key, which its
  API refuses, so nothing is sent with it and the admin page says so; a 401
  or 403 from Brevo is shown there too. Only the prefix is ever looked at:
  the key is never logged or shown.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .sqlite_db import SqliteDB

log = logging.getLogger(__name__)

# The reconcile's guard (0.55.0): it deletes no extras when they're more than
# both of these: RECONCILE_GUARD_MIN contacts and this share of the list.
RECONCILE_GUARD_MIN = 10
RECONCILE_GUARD_SHARE = 0.2
OFF = "newsletter sign-up (newsletter_opt_in) is off"
_said_off = False                                    # the drain's one log line while it's off

API = "https://api.brevo.com/v3"
SMTP_KEY_PREFIX = "xsmtpsib-"         # Brevo's SMTP key; its API needs an API key (xkeysib-...)


class BrevoRefused(RuntimeError):
    """Brevo refused the key (401 or 403)."""

    def __init__(self, what: str, code: int):
        super().__init__(f"{what}: Brevo refused the API key ({code})")
        self.code = code
BACKOFF_S = (60, 300, 1800, 7200, 43200)
FAILED_AFTER = 3                    # attempts before the admin page counts a job as failing
BATCH = 50
PAGE = 500
KICK = "thread"                     # "thread": drain in the background; "inline" (tests); "off"

SCHEMA = """
CREATE TABLE IF NOT EXISTS newsletter_sync (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT NOT NULL,
    email       TEXT,
    action      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_at     REAL NOT NULL,
    done_at     REAL,
    last_error  TEXT
);
CREATE INDEX IF NOT EXISTS newsletter_sync_due ON newsletter_sync (done_at, next_at);
CREATE TABLE IF NOT EXISTS newsletter_sync_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
"""


def enqueue(db, account_id: str, email: Optional[str], action: str, now: float) -> None:
    """Queue a Brevo change inside the caller's write (Newsletter.record,
    delete_account): the latest one for the account wins."""
    if action not in ("add", "delete", "extra") or not email:
        return
    if account_id:
        db.execute("UPDATE newsletter_sync SET done_at = ?, email = NULL, last_error = 'superseded' "
                   "WHERE account_id = ? AND done_at IS NULL", (now, account_id))
    db.execute("INSERT INTO newsletter_sync (account_id, email, action, created_at, next_at) VALUES (?, ?, ?, ?, ?)",
               (account_id or "", email, action, now, now))


class _Response:
    def __init__(self, status: int, body: bytes):
        self.status_code, self._body = status, body

    def json(self):
        return json.loads(self._body or b"{}")


class _Http:
    """The few calls Brevo needs, over urllib (no extra dependency)."""

    def request(self, method: str, url: str, *, headers: dict, body=None, timeout: float = 15) -> _Response:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={**headers, **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _Response(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Response(e.code, e.read())


def default_http():
    return _Http()


class Brevo:
    def __init__(self, api_key: str, http=None):
        self.api_key, self.http = api_key, http or default_http()

    def _call(self, method: str, path: str, body=None) -> _Response:
        r = self.http.request(method, API + path, body=body, timeout=15,
                              headers={"api-key": self.api_key, "accept": "application/json",
                                       "User-Agent": "CCT-App/1.0"})
        if r.status_code in (401, 403):
            raise BrevoRefused(f"{method} {path.split('?')[0].split('/')[1]}", r.status_code)
        return r

    def add(self, email: str, list_id: int) -> None:
        r = self._call("POST", "/contacts", {"email": email, "listIds": [int(list_id)], "updateEnabled": True})
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"add: Brevo answered {r.status_code}")

    def delete(self, email: str, list_id: int) -> None:
        """Remove the contact (0.41.0): from our list, and the whole contact
        only when it's on no other list. One Brevo account holds the real list
        and the test environment's, and a contact is the account's, so the
        test environment must never delete a real subscriber's contact."""
        path = "/contacts/" + urllib.parse.quote(email, safe="")
        r = self._call("GET", path)
        if r.status_code == 404:
            return                                           # already gone
        if r.status_code != 200:
            raise RuntimeError(f"look up: Brevo answered {r.status_code}")
        others = {int(i) for i in (r.json().get("listIds") or [])} - {int(list_id)}
        if others:
            r = self._call("POST", f"/contacts/lists/{int(list_id)}/contacts/remove", {"emails": [email]})
            if r.status_code not in (200, 201, 204, 400):     # 400: not on our list (already off it)
                raise RuntimeError(f"unlist: Brevo answered {r.status_code}")
            return
        r = self._call("DELETE", path)
        if r.status_code not in (200, 204, 404):            # 404: already gone
            raise RuntimeError(f"delete: Brevo answered {r.status_code}")

    def contacts(self, list_id: int) -> dict:
        """{email (lower case): blocklisted} for every contact on the list."""
        out, offset = {}, 0
        while True:
            r = self._call("GET", f"/contacts/lists/{int(list_id)}/contacts?limit={PAGE}&offset={offset}")
            if r.status_code != 200:
                raise RuntimeError(f"list: Brevo answered {r.status_code}")
            page = r.json().get("contacts") or []
            for c in page:
                if c.get("email"):
                    out[c["email"].strip().lower()] = bool(c.get("emailBlacklisted"))
            if len(page) < PAGE:
                return out
            offset += PAGE


class NewsletterSync(SqliteDB):
    """The queue (newsletter_sync) and its runs, in the accounts database."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time, http=None):
        super().__init__(path, SCHEMA)
        self._clock = clock
        self._http = http

    def config(self) -> tuple:
        """(api key, list id): either None while not set up. An SMTP key
        counts as none (Brevo's API refuses it); see key_problem()."""
        from .settings import setting
        key = (os.environ.get("BREVO_API_KEY") or "").strip() or None
        if key and key.startswith(SMTP_KEY_PREFIX):
            key = None
        return key, setting(self.path, "newsletter_list_id")

    @staticmethod
    def key_problem() -> Optional[str]:
        """"smtp" when BREVO_API_KEY is Brevo's SMTP key (by its prefix only)."""
        key = (os.environ.get("BREVO_API_KEY") or "").strip()
        return "smtp" if key.startswith(SMTP_KEY_PREFIX) else None

    def _note_refused(self, code: Optional[int]) -> None:
        """Keep (or clear, with None) Brevo's last refusal of the key, for the admin page."""
        with self._write() as db:
            if code is None:
                db.execute("DELETE FROM newsletter_sync_state WHERE key = 'refused'")
            else:
                db.execute("INSERT INTO newsletter_sync_state (key, value, updated_at) VALUES ('refused', ?, ?) "
                           "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                           (json.dumps({"code": code}), self._clock()))

    def brevo(self) -> Optional[Brevo]:
        key, list_id = self.config()
        return Brevo(key, self._http) if key and list_id else None

    def opt_in_on(self) -> bool:
        """The sign-up box (newsletter_opt_in): while it's off nothing reaches Brevo."""
        from .settings import setting
        return setting(self.path, "newsletter_opt_in") is True

    def drain(self, limit: int = BATCH) -> dict:
        """Run the jobs that are due; {"done", "failed", "waiting"}."""
        brevo, (_, list_id) = self.brevo(), self.config()
        now = self._clock()
        on = self.opt_in_on()                         # off: only one person's own deletes go (0.55.1)
        with self._read() as db:
            jobs = [dict(r) for r in db.execute(
                "SELECT * FROM newsletter_sync WHERE done_at IS NULL AND next_at <= ?"
                + ("" if on else " AND action = 'delete'") + " ORDER BY id LIMIT ?", (now, limit)).fetchall()]
        done = failed = 0
        if brevo is None:
            return {"done": 0, "failed": 0, "waiting": self.waiting()}
        global _said_off
        if not on:
            held = self.waiting() - len(jobs)
            if held and not _said_off:
                log.warning("newsletter sync: %s, so only people's own deletions are sent to Brevo; "
                            "%d other jobs wait", OFF, held)
                _said_off = True
        else:
            _said_off = False
        for job in jobs:
            try:
                (brevo.add(job["email"], list_id) if job["action"] == "add" else brevo.delete(job["email"], list_id))
            except BrevoRefused as e:
                self._note_refused(e.code)            # every job would be refused alike: stop here
                log.warning("newsletter sync: Brevo refused the API key (%s)", e.code)
                failed += 1
                break
            except Exception as e:
                failed += 1
                wait = BACKOFF_S[min(job["attempts"], len(BACKOFF_S) - 1)]
                with self._write() as db:
                    db.execute("UPDATE newsletter_sync SET attempts = attempts + 1, next_at = ?, last_error = ? "
                               "WHERE id = ? AND done_at IS NULL", (self._clock() + wait, str(e)[:300], job["id"]))
                log.warning("newsletter sync %s for %s: %s", job["action"], job["account_id"] or "-", e)
                continue
            done += 1
            if done == 1:
                self._note_refused(None)              # the key works
            with self._write() as db:
                db.execute("UPDATE newsletter_sync SET done_at = ?, email = NULL, last_error = NULL "
                           "WHERE id = ? AND done_at IS NULL", (self._clock(), job["id"]))
        return {"done": done, "failed": failed, "waiting": self.waiting()}

    def waiting(self) -> int:
        with self._read() as db:
            return int(db.execute("SELECT COUNT(*) FROM newsletter_sync WHERE done_at IS NULL").fetchone()[0])

    def reconcile(self, *, allow_big: bool = False) -> Optional[dict]:
        """Make the list match who's subscribed here (None while not set up).
        Nothing at all while newsletter_opt_in is off; no deletions when
        they'd be more than the guard allows, unless `allow_big`."""
        brevo, (_, list_id) = self.brevo(), self.config()
        if brevo is None:
            return None
        if not self.opt_in_on():
            log.warning("newsletter sync: %s, so the reconcile left Brevo's list %s as it is", OFF, list_id)
            return self._keep_result({"added": 0, "deleted": 0, "unsubscribed": 0, "skipped": OFF})
        from .newsletter import Newsletter
        news = Newsletter(self.path, clock=self._clock)
        try:
            theirs = brevo.contacts(list_id)
        except BrevoRefused as e:
            self._note_refused(e.code)
            raise
        self._note_refused(None)
        ours = {s["email"].strip().lower(): s for s in news.subscribers()}
        out = {"added": 0, "deleted": 0, "unsubscribed": 0}
        now = self._clock()
        with self._read() as db:
            ids = {r["email"].lower(): r["id"] for r in db.execute(
                "SELECT id, email FROM accounts WHERE email NOT LIKE 'deleted:%'").fetchall()}
        extras = [e for e, blocked in theirs.items() if e not in ours]
        limit = max(RECONCILE_GUARD_MIN, len(theirs) * RECONCILE_GUARD_SHARE)
        hold = len(extras) > limit and not allow_big
        if hold:
            out.update(held=len(extras), of=len(theirs))
            log.warning("newsletter sync: the reconcile would delete %d of the %d contacts on Brevo's list %s "
                        "(more than %d, or %d%% of the list), so it deleted none; a deliberate clean-up is "
                        "reconcile(allow_big=True)", len(extras), len(theirs), list_id, RECONCILE_GUARD_MIN,
                        round(RECONCILE_GUARD_SHARE * 100))
        for email, blocked in theirs.items():
            if blocked and email in ours:                     # unsubscribed in Brevo, webhook missed
                news.record(ids[email], False, app="brevo", source="brevo")   # queues the delete
                out["unsubscribed"] += 1
            elif email not in ours and not hold:          # "extra": waits while sign-up is off
                with self._write() as db:
                    enqueue(db, ids.get(email, ""), email, "extra", now)
                out["deleted"] += 1
        for email in ours:
            if email not in theirs and email in ids:
                with self._write() as db:
                    enqueue(db, ids[email], email, "add", now)
                out["added"] += 1
        out.update(self.drain(limit=10_000))
        return self._keep_result(out)

    def _keep_result(self, out: dict) -> dict:
        """The reconcile's result, for the admin page."""
        with self._write() as db:
            db.execute("INSERT INTO newsletter_sync_state (key, value, updated_at) VALUES ('reconcile', ?, ?) "
                       "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                       (json.dumps(out, sort_keys=True), self._clock()))
        return out

    def status(self) -> dict:
        """For the admin page: set up or not, waiting, failing, the last reconcile."""
        key, list_id = self.config()
        with self._read() as db:
            failing = int(db.execute("SELECT COUNT(*) FROM newsletter_sync WHERE done_at IS NULL AND attempts >= ?",
                                     (FAILED_AFTER,)).fetchone()[0])
            row = db.execute("SELECT value, updated_at FROM newsletter_sync_state WHERE key = 'reconcile'").fetchone()
            refused = db.execute("SELECT value FROM newsletter_sync_state WHERE key = 'refused'").fetchone()
        return {"configured": bool(key and list_id), "api_key": bool(key), "list_id": list_id,
                "key_problem": self.key_problem(), "opt_in": self.opt_in_on(),
                "refused": json.loads(refused["value"])["code"] if refused else None,
                "waiting": self.waiting(), "failing": failing,
                "last_reconcile": row["updated_at"] if row else None,
                "last_result": json.loads(row["value"]) if row else None}


_KICKING = threading.Lock()
DRAIN_EVERY_S = 300
_next_drain = 0.0


def maybe_kick(path: str) -> None:
    """During a request (register_account_routes' hook): at most every
    DRAIN_EVERY_S, run every waiting job, whichever app queued it. Only a
    process with BREVO_API_KEY does (pulley's), so the key sits in one
    service and another app's changes reach Brevo within minutes (0.41.0)."""
    global _next_drain
    if not (os.environ.get("BREVO_API_KEY") or "").strip():
        return
    now = time.monotonic()
    if now < _next_drain:
        return
    _next_drain = now + DRAIN_EVERY_S
    kick(path)


def kick(path: str, *, clock: Callable[[], float] = time.time) -> None:
    """Run the due jobs now, in the background (KICK = "thread"), so the
    request that queued them never waits for Brevo."""
    if KICK == "off" or not (os.environ.get("BREVO_API_KEY") or "").strip():
        return                                       # not set up: the jobs wait, no thread

    def run():
        if not _KICKING.acquire(blocking=False):
            return                                   # one drain at a time; it picks this job up
        try:
            NewsletterSync(path, clock=clock).drain()
        except Exception as e:
            log.warning("newsletter sync: %s", e)
        finally:
            _KICKING.release()

    if KICK == "inline":
        run()
    else:
        threading.Thread(target=run, name="newsletter-sync", daemon=True).start()
