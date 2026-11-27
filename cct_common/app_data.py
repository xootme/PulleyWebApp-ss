"""
app_data.py — what an app keeps for an account, in the shared database
(0.66.0; the PCB Importer cloud move, the owner's "all as recommended",
CCT_Business D-36: Q-C1 a shared store, Q-C2 close and retry, Q-C3 before
any board is stored).

An account can be closed, or its data downloaded, on any app's server, and
that server runs none of the other apps' code. So an app keeps its
per-account data here rather than in tables of its own behind hooks, and
names the storage its files live in. The shared account deletion and
"Download all my data" then handle every app's data the same way.

    from cct_common.app_data import AppData
    pcb = AppData(DATABASE_URL, "pcbimporter", storage="gs://cheapcadtools-pcb")
    pcb.put(acct, "board", "b1", {"name": "Uno", "outline": [...]})   # its version counts the puts
    pcb.get(acct, "board", "b1")          # {"body", "version", "created_at", "modified_at"} or None
    pcb.list(acct, "board"); pcb.delete(acct, "board", "b1")
    pcb.files(acct)                       # cct_common.storage, its names under pcb.prefix(acct)

- Records: (app, account, kind, name) -> a JSON body (MAX_BODY, 1 MB),
  with a version that counts the puts and when it was made and changed.
  Kinds and names follow cct_common.storage's name rule, one segment each.
- Files: an app that keeps files names its storage (gs://<bucket> or a
  folder) when it opens AppData; that's written in the database
  (app_storages), so a server that doesn't run the app knows where they
  are. An account's files are always under accounts/<account id>/ there.
- Closing an account (accounts.delete_account, on any server): its records
  and its jobs (cct_common.jobs) go in the same transaction, and each app
  storage's accounts/<id>/ is queued for the purge (app_purges), due
  PURGE_DELAY_S later so a job the close stopped has noticed first. The
  purge (`python -m cct_common.app_data purge`, the hourly job, whose
  service account may delete in those buckets) deletes them, and retries a
  failure after BACKOFF_S; one still pending after PURGE_DUE_S (30 days,
  the privacy policy's window) is listed as overdue (pending_purges(), for
  the admin). The other apps' servers never touch an app's storage.
- "Download all my data" (data_export) has each app's records under
  apps/<app>/<kind>/<name>.json: the record's data, when it was made and
  last changed. An app's files come with the large export (0.67.0).
  (Q-D1 to Q-D4, the owner's "a a a a": no index of files, the account's
  folder goes whole; one layout for every app; files from 0.67.0; the
  README's apps/ line only when there are records.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Callable, Optional

from .sqlite_db import SqliteDB
from .storage import StorageError, check_name, storage_for

MAX_BODY = 1024 * 1024
PURGE_DELAY_S = 600
BACKOFF_S = (3600, 6 * 3600, 86400)
PURGE_DUE_S = 30 * 86400
KEEP_DONE_S = 30 * 86400
PREFIX = "accounts/{account}/"

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_records (
    app          TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    body         TEXT NOT NULL,
    version      INTEGER NOT NULL,
    created_at   REAL NOT NULL,
    modified_at  REAL NOT NULL,
    PRIMARY KEY (app, account_id, kind, name)
);
CREATE INDEX IF NOT EXISTS app_records_account ON app_records (account_id);
CREATE TABLE IF NOT EXISTS app_storages (
    app          TEXT NOT NULL,
    storage      TEXT NOT NULL,
    added_at     REAL NOT NULL,
    PRIMARY KEY (app, storage)
);
CREATE TABLE IF NOT EXISTS app_purges (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    app          TEXT NOT NULL,
    storage      TEXT NOT NULL,
    prefix       TEXT NOT NULL,
    created_at   REAL NOT NULL,
    next_at      REAL NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    done_at      REAL,
    deleted      INTEGER,
    last_error   TEXT
);
CREATE INDEX IF NOT EXISTS app_purges_due ON app_purges (done_at, next_at);
"""


class AppDataError(ValueError):
    """A record the store refuses (a bad kind or name, a body too large)."""


def _segment(what: str, value: str) -> str:
    if not isinstance(value, str) or "/" in value:
        raise AppDataError(f"a record's {what} is one segment: {value!r}")
    try:
        return check_name(value)
    except StorageError:
        raise AppDataError(f"not a record {what}: {value!r}") from None


def prefix_for(account_id: str) -> str:
    """Where an account's files are, in every app's storage."""
    return PREFIX.format(account=_segment("account", account_id))


class AppData(SqliteDB):
    def __init__(self, path: str, app: str, *, storage: Optional[str] = None,
                 clock: Callable[[], float] = time.time):
        super().__init__(path, SCHEMA)
        self.app, self.clock = _segment("app", app), clock
        self.storage_spec = storage
        if storage:
            self._storage = storage_for(storage)            # refused here if it isn't one
            with self._write() as db:
                db.execute("INSERT OR IGNORE INTO app_storages (app, storage, added_at) VALUES (?, ?, ?)",
                           (self.app, storage, self.clock()))
        else:
            self._storage = None

    # ── records ──
    def put(self, account_id: str, kind: str, name: str, body) -> int:
        """Store the record (replacing it); its version now."""
        key = (self.app, _segment("account", account_id), _segment("kind", kind), _segment("name", name))
        text = json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        if len(text.encode()) > MAX_BODY:
            raise AppDataError(f"a record is at most {MAX_BODY} bytes of JSON; put files in the app's storage")
        now = self.clock()
        with self._write() as db:
            r = db.execute("SELECT version FROM app_records WHERE app = ? AND account_id = ? AND kind = ? AND name = ?",
                           key).fetchone()
            if r is None:
                db.execute("INSERT INTO app_records (app, account_id, kind, name, body, version, created_at, "
                           "modified_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)", (*key, text, now, now))
                return 1
            db.execute("UPDATE app_records SET body = ?, version = ?, modified_at = ? "
                       "WHERE app = ? AND account_id = ? AND kind = ? AND name = ?", (text, r["version"] + 1, now, *key))
            return r["version"] + 1

    @staticmethod
    def _rec(r) -> dict:
        return {"name": r["name"], "body": json.loads(r["body"]), "version": r["version"],
                "created_at": r["created_at"], "modified_at": r["modified_at"]}

    def get(self, account_id: str, kind: str, name: str) -> Optional[dict]:
        with self._read() as db:
            r = db.execute("SELECT * FROM app_records WHERE app = ? AND account_id = ? AND kind = ? AND name = ?",
                           (self.app, _segment("account", account_id), _segment("kind", kind),
                            _segment("name", name))).fetchone()
        return None if r is None else self._rec(r)

    def list(self, account_id: str, kind: str) -> list[dict]:
        with self._read() as db:
            rows = db.execute("SELECT * FROM app_records WHERE app = ? AND account_id = ? AND kind = ? ORDER BY name",
                              (self.app, _segment("account", account_id), _segment("kind", kind))).fetchall()
        return [self._rec(r) for r in rows]

    def delete(self, account_id: str, kind: str, name: str) -> bool:
        key = (self.app, _segment("account", account_id), _segment("kind", kind), _segment("name", name))
        with self._write() as db:
            r = db.execute("SELECT 1 FROM app_records WHERE app = ? AND account_id = ? AND kind = ? AND name = ?",
                           key).fetchone()
            db.execute("DELETE FROM app_records WHERE app = ? AND account_id = ? AND kind = ? AND name = ?", key)
        return r is not None

    def usage(self, account_id: str) -> dict:
        """{"records": n, "bytes": the bodies' size}, for an app's quota."""
        with self._read() as db:
            r = db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(body)), 0) AS b FROM app_records "
                           "WHERE app = ? AND account_id = ?", (self.app, _segment("account", account_id))).fetchone()
        return {"records": int(r["n"]), "bytes": int(r["b"])}

    # ── files ──
    def files(self, account_id: str):
        """The app's storage; the account's files go under prefix(account_id)."""
        if self._storage is None:
            raise AppDataError(f"{self.app} named no storage")
        return self._storage

    def prefix(self, account_id: str) -> str:
        return prefix_for(account_id)


# ── closing an account, and the purge ─────────────────────────────────────

def forget_account(db, account_id: str, now: float) -> int:
    """Inside delete_account's write: the account's records and jobs go, and
    each app storage's accounts/<id>/ is queued for the purge. How many
    prefixes were queued."""
    db.execute("DELETE FROM app_records WHERE account_id = ?", (account_id,))
    db.execute("DELETE FROM jobs WHERE account_id = ?", (account_id,))
    rows = db.execute("SELECT app, storage FROM app_storages ORDER BY app, storage").fetchall()
    if not rows:
        return 0
    prefix = prefix_for(account_id)
    for r in rows:
        db.execute("INSERT INTO app_purges (app, storage, prefix, created_at, next_at) VALUES (?, ?, ?, ?, ?)",
                   (r["app"], r["storage"], prefix, now, now + PURGE_DELAY_S))
    return len(rows)


class Purges(SqliteDB):
    """The purge queue: run() is the hourly job's work, pending() the admin's view."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time, open_storage: Callable = storage_for):
        super().__init__(path, SCHEMA)
        self.clock, self._open = clock, open_storage

    def run(self, *, limit: int = 200) -> dict:
        now, opened = self.clock(), {}
        with self._read() as db:
            due = [dict(zip(r.keys(), tuple(r))) for r in db.execute(
                "SELECT * FROM app_purges WHERE done_at IS NULL AND next_at <= ? ORDER BY next_at, id LIMIT ?",
                (now, int(limit))).fetchall()]
        out = {"done": 0, "failed": 0, "deleted": 0}
        for p in due:
            try:
                st = opened.get(p["storage"]) or opened.setdefault(p["storage"], self._open(p["storage"]))
                n = st.delete_prefix(p["prefix"])
            except Exception as e:
                wait = BACKOFF_S[min(p["attempts"], len(BACKOFF_S) - 1)]
                with self._write() as db:
                    db.execute("UPDATE app_purges SET attempts = attempts + 1, next_at = ?, last_error = ? WHERE id = ?",
                               (self.clock() + wait, " ".join(str(e).split())[:300] or type(e).__name__, p["id"]))
                out["failed"] += 1
                continue
            with self._write() as db:
                db.execute("UPDATE app_purges SET attempts = attempts + 1, done_at = ?, deleted = ?, last_error = NULL "
                           "WHERE id = ?", (self.clock(), n, p["id"]))
            out["done"] += 1
            out["deleted"] += n
        with self._write() as db:
            db.execute("DELETE FROM app_purges WHERE done_at IS NOT NULL AND done_at < ?", (now - KEEP_DONE_S,))
        return out

    def pending(self) -> list[dict]:
        """What's still to purge: app, storage, when it was queued, attempts, the last error, overdue."""
        now = self.clock()
        with self._read() as db:
            rows = db.execute("SELECT app, storage, created_at, attempts, last_error FROM app_purges "
                              "WHERE done_at IS NULL ORDER BY created_at, id").fetchall()
        return [{"app": r["app"], "storage": r["storage"], "queued_at": r["created_at"], "attempts": r["attempts"],
                 "last_error": r["last_error"], "overdue": now - r["created_at"] > PURGE_DUE_S} for r in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cct_common.app_data",
                                 description="Purge the files of closed accounts from the apps' storage.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("purge", help="the hourly job: delete what closed accounts left in the apps' storage")
    p.add_argument("--database", help="default: DATABASE_URL")
    a = ap.parse_args(argv)
    database = a.database or os.environ.get("DATABASE_URL")
    if not database:
        ap.error("no database: pass --database or set DATABASE_URL")
    from .server_use import job_use
    purges = Purges(database)
    with job_use(database, "cct-app-purge", "purge"):
        out = purges.run()
    overdue = sum(x["overdue"] for x in purges.pending())
    print(f"app purge: {out['done']} done ({out['deleted']} files), {out['failed']} to retry, {overdue} overdue",
          flush=True)
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
