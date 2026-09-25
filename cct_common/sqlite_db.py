"""
sqlite_db.py — the SQLite plumbing shared by cct_common.tokens and
cct_common.accounts, which live in one database file.

Durability choices (see PulleyWebApp-ss ADR-008 "data safety"):
- WAL journal + synchronous=FULL: a committed write survives a crash or
  power loss, and readers never block the writer.
- One connection per operation, so a store is safe to share across
  threads; writes take BEGIN IMMEDIATE so check-then-write sequences
  can't interleave across workers or processes.
- integrity_check() and backup() for the startup check and the scheduled
  off-server copy.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator


class SqliteDB:
    """Base for stores backed by one SQLite file. Subclasses pass their
    schema; several stores may share a file (schemas use IF NOT EXISTS)."""

    def __init__(self, path: str, schema: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with self._read() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(schema)

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None lets us issue BEGIN IMMEDIATE ourselves.
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
        finally:
            db.close()

    def _ensure_columns(self, table: str, columns: dict) -> None:
        """Add columns that a newer version of a schema introduced to a
        database created by an older one ({name: "TYPE ..."})."""
        with self._write() as db:
            have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in have:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def integrity_check(self) -> list[str]:
        """Problems SQLite finds in the file; an empty list means healthy.
        Run at startup and refuse to charge if it isn't empty."""
        try:
            with self._read() as db:
                rows = [r[0] for r in db.execute("PRAGMA integrity_check").fetchall()]
        except sqlite3.DatabaseError as e:
            return [str(e)]
        return [] if rows == ["ok"] else rows

    def backup(self, dest_path: str) -> str:
        """Consistent copy of the live database to dest_path, taken without
        stopping writers (SQLite's online backup API). Returns dest_path.
        Copy the result off the server: a backup on the same disk dies with
        it."""
        parent = os.path.dirname(os.path.abspath(dest_path))
        os.makedirs(parent, exist_ok=True)
        tmp = dest_path + ".partial"
        src = self._connect()
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
                # The copy inherits WAL mode from the live file, and a WAL
                # database grows -wal/-shm side files whenever it's opened.
                # A backup should be one self-contained file.
                dst.execute("PRAGMA journal_mode=DELETE")
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(tmp, dest_path)
        return dest_path
