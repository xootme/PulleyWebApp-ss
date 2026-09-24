"""
db_backup.py — scheduled, verified backups of a cct_common SQLite store
(the token ledger + accounts; PulleyWebApp-ss ADR-008 "corruption and
loss").

    from cct_common.db_backup import start_backup_thread

    start_backup_thread(store, "/path/to/backups", name="accounts",
                        on_failure=lambda msg: alert_admin(msg),
                        upload=None)   # later: copy each file off the server

Each run:
  1. takes an online backup of the live database (SqliteDB.backup — no
     need to stop writers) into <dir>/hourly/<name>-YYYYmmdd-HHMMSS.sqlite3;
  2. verifies the copy with PRAGMA integrity_check, and deletes it if it
     fails — an unverified backup is worse than none, because it gets
     trusted in a restore;
  3. keeps the first good backup of each day as <dir>/daily/<name>-YYYYmmdd.sqlite3;
  4. prunes to the newest `keep_hourly` hourly and `keep_daily` daily
     copies (default 7 days and 90 days);
  5. hands each new file to `upload(path)` if given — the off-server copy.
     A backup on the same disk dies with it, so production needs this.
     Encrypt inside `upload`: the files contain account emails.

Any failure, and a backup that's gone missing for more than two
intervals, is reported through `on_failure(message)` — never raised into
the app.

Several gunicorn workers may each start the thread. A run is skipped when
a backup newer than ~90% of the interval already exists, and a lock file
keeps two workers from backing up at the same moment.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

HOURLY_DIR = "hourly"
DAILY_DIR = "daily"
LOCK_NAME = ".backup.lock"
LOCK_STALE_S = 15 * 60


@dataclass
class BackupResult:
    ok: bool
    path: Optional[str] = None
    daily_path: Optional[str] = None
    skipped: bool = False
    errors: list = field(default_factory=list)
    pruned: list = field(default_factory=list)


def _verify(path: str) -> list:
    try:
        db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = [r[0] for r in db.execute("PRAGMA integrity_check").fetchall()]
        finally:
            db.close()
    except sqlite3.DatabaseError as e:
        return [str(e)]
    return [] if rows == ["ok"] else rows


def _backups(folder: str, name: str) -> list:
    """Backup files for `name` in folder, oldest first (names sort by time)."""
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                  if f.startswith(name + "-") and f.endswith(".sqlite3"))


def newest_backup_age(backup_dir: str, name: str, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds since the newest hourly backup was written, or None if none."""
    files = _backups(os.path.join(backup_dir, HOURLY_DIR), name)
    if not files:
        return None
    return (now if now is not None else time.time()) - os.path.getmtime(files[-1])


def _acquire_lock(backup_dir: str, now: float) -> Optional[str]:
    path = os.path.join(backup_dir, LOCK_NAME)
    try:
        if os.path.exists(path) and now - os.path.getmtime(path) > LOCK_STALE_S:
            os.remove(path)          # left behind by a worker that died mid-backup
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return path
    except FileExistsError:
        return None


def run_backup(store, backup_dir: str, *, name: str = "accounts",
               keep_hourly: int = 7 * 24, keep_daily: int = 90,
               upload: Optional[Callable[[str], None]] = None,
               min_age_s: float = 0, now: Optional[Callable[[], float]] = None) -> BackupResult:
    """One backup run. `store` is any cct_common.sqlite_db.SqliteDB
    (e.g. a TokenStore). Returns a BackupResult; never raises for a
    backup problem. With min_age_s, skips if the newest backup is younger."""
    clock = now or time.time
    t = clock()
    hourly = os.path.join(backup_dir, HOURLY_DIR)
    daily = os.path.join(backup_dir, DAILY_DIR)
    os.makedirs(hourly, exist_ok=True)
    os.makedirs(daily, exist_ok=True)

    age = newest_backup_age(backup_dir, name, now=t)
    if min_age_s and age is not None and age < min_age_s:
        return BackupResult(ok=True, skipped=True)

    lock = _acquire_lock(backup_dir, t)
    if lock is None:
        return BackupResult(ok=True, skipped=True)   # another worker is on it
    try:
        stamp = datetime.fromtimestamp(t)
        path = os.path.join(hourly, f"{name}-{stamp:%Y%m%d-%H%M%S}.sqlite3")
        try:
            store.backup(path)
        except Exception as e:
            return BackupResult(ok=False, errors=[f"backup failed: {e}"])
        problems = _verify(path)
        if problems:
            os.remove(path)
            return BackupResult(ok=False, errors=["backup copy failed its integrity check: "
                                                  + "; ".join(problems)])
        os.utime(path, (t, t))
        result = BackupResult(ok=True, path=path)

        daily_path = os.path.join(daily, f"{name}-{stamp:%Y%m%d}.sqlite3")
        if not os.path.exists(daily_path):
            shutil.copy2(path, daily_path)
            result.daily_path = daily_path

        for folder, keep in ((hourly, keep_hourly), (daily, keep_daily)):
            files = _backups(folder, name)
            for old in files[:max(0, len(files) - keep)]:
                os.remove(old)
                result.pruned.append(old)

        if upload:
            for p in filter(None, (result.path, result.daily_path)):
                try:
                    upload(p)
                except Exception as e:
                    result.ok = False
                    result.errors.append(f"off-server upload of {os.path.basename(p)} failed: {e}")
        return result
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def start_backup_thread(store, backup_dir: str, *, name: str = "accounts",
                        interval_s: float = 3600,
                        on_failure: Optional[Callable[[str], None]] = None,
                        upload: Optional[Callable[[str], None]] = None,
                        keep_hourly: int = 7 * 24, keep_daily: int = 90,
                        first_delay_s: float = 30) -> threading.Event:
    """Run run_backup every interval_s in a daemon thread (first run after
    first_delay_s, so app start-up isn't slowed). Returns an Event; set()
    it to stop the thread. Failures and a backup gone missing for more
    than two intervals go to on_failure."""
    stop = threading.Event()
    report = on_failure or (lambda msg: None)

    def loop():
        if stop.wait(first_delay_s):
            return
        while True:
            try:
                r = run_backup(store, backup_dir, name=name, keep_hourly=keep_hourly,
                               keep_daily=keep_daily, upload=upload,
                               min_age_s=interval_s * 0.9)
                if not r.ok:
                    report("Database backup problem: " + " | ".join(r.errors))
                age = newest_backup_age(backup_dir, name)
                if age is None or age > 2 * interval_s:
                    report(f"No database backup in the last {int(2 * interval_s)} s "
                           f"(newest: {'none' if age is None else f'{int(age)} s old'}).")
            except Exception as e:     # a bug here must never kill the app
                try:
                    report(f"Database backup thread error: {e}")
                except Exception:
                    pass
            if stop.wait(interval_s):
                return

    threading.Thread(target=loop, name=f"cct-backup-{name}", daemon=True).start()
    return stop
