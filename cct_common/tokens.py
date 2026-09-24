"""
tokens.py — pay-per-export token ledger shared by CCT apps (PulleyWebApp-ss
ADR-008). The apps are free; exporting a file costs tokens.

    from cct_common.tokens import TokenStore, design_key, InsufficientTokens

    store = TokenStore(os.path.join(LOG_DIR, "tokens.sqlite3"))
    acct = store.get_or_create_account("a@b.com", signup_grant=10)
    key = design_key(request.args)
    with store.charge(acct, key, "step", detail="/download/step"):
        data = generate_step(...)      # an exception here refunds the charge

Pricing is by tier, and one payment unlocks the whole design at that tier
and every tier below it for UNLOCK_WINDOW_S (24 h):

    2d    SVG, DXF   1 token
    stl   STL        2 tokens   (also unlocks 2d)
    step  STEP       3 tokens   (also unlocks stl + 2d)

Re-downloading an unlocked format is free; moving up a tier inside the
window costs only the difference (STL -> STEP = 1).

The balance is never stored: it is the sum of the append-only ledger, so
every signup grant, purchase, spend and refund stays auditable. The
check-then-spend runs inside one write transaction (BEGIN IMMEDIATE), so
two workers or processes can't both spend the last token.

SQLite for now (local runs, tests, single-instance deploy). The SQL is
kept plain so a Postgres backend can sit behind the same class once
hosting moves off the Render disk.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from .sqlite_db import SqliteDB

TOKEN_PRICE_CENTS = 10
UNLOCK_WINDOW_S = 24 * 60 * 60

TIER_PRICE = {"2d": 1, "stl": 2, "step": 3}
FORMAT_TIER = {"svg": "2d", "dxf": "2d", "stl": "stl", "step": "step"}

# Query/body keys that change how a file is delivered, not what the design
# is. Two downloads that differ only in these are the same design.
TRANSIENT_KEYS = frozenset({
    "_ts", "_fpt", "session_id", "machine_id", "pulley", "include_data",
    "which", "format", "fmt", "account_token",
})

CREDIT_KINDS = frozenset({"signup", "purchase", "referral", "adjust"})


class InsufficientTokens(Exception):
    """Raised by TokenStore.charge when the balance can't cover the cost."""

    def __init__(self, needed: int, balance: int):
        super().__init__(f"needs {needed} tokens, balance is {balance}")
        self.needed = needed
        self.balance = balance


@dataclass(frozen=True)
class Quote:
    fmt: str
    tier: str             # tier the format needs
    cost: int             # tokens this download would take now
    held_tier: Optional[str]    # highest tier already unlocked, if any
    unlocked_until: Optional[float]  # when held_tier's unlock expires


def format_tier(fmt: str) -> str:
    try:
        return FORMAT_TIER[fmt.lower()]
    except KeyError:
        raise ValueError(f"unknown export format: {fmt!r}") from None


def design_key(params, ignore=TRANSIENT_KEYS) -> str:
    """Stable id for a design: a hash of its parameters, minus the keys
    that only affect delivery. Accepts a dict or a Flask MultiDict (first
    value per key). Key order and surrounding whitespace don't matter."""
    items = params.items()
    canon = {str(k): str(v).strip() for k, v in items if k not in ignore}
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    ref         TEXT,
    design_key  TEXT,
    tier        TEXT,
    fmt         TEXT,
    detail      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ledger_kind_ref
    ON ledger(kind, ref) WHERE ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS ledger_unlock
    ON ledger(account_id, design_key, ts);
"""


class TokenStore(SqliteDB):
    """Accounts plus the append-only token ledger.

    Ledger kinds: ``signup``/``purchase``/``referral`` (credits),
    ``adjust`` (admin, either sign), ``spend`` (a paid download, negative),
    ``refund`` (reverses one spend; ``ref`` is ``spend:<id>``) and
    ``download`` (a free download of an already-unlocked design, amount 0,
    kept only for history)."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time,
                 unlock_window_s: int = UNLOCK_WINDOW_S):
        self._clock = clock
        self.unlock_window_s = unlock_window_s
        super().__init__(path, _SCHEMA)

    @staticmethod
    def _balance(db, account_id: str) -> int:
        row = db.execute("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE account_id = ?",
                         (account_id,)).fetchone()
        return int(row[0])

    def _held(self, db, account_id: str, key: str, now: float):
        """Highest tier this account has paid for on this design inside the
        unlock window, ignoring refunded spends. Returns (tier, expires)."""
        rows = db.execute(
            """SELECT s.tier, s.ts FROM ledger s
               WHERE s.account_id = ? AND s.design_key = ? AND s.kind = 'spend'
                 AND s.ts > ?
                 AND NOT EXISTS (SELECT 1 FROM ledger r
                                 WHERE r.kind = 'refund' AND r.ref = 'spend:' || s.id)""",
            (account_id, key, now - self.unlock_window_s)).fetchall()
        best, until = None, None
        for r in rows:
            if best is None or TIER_PRICE[r["tier"]] > TIER_PRICE[best]:
                best, until = r["tier"], r["ts"] + self.unlock_window_s
            elif r["tier"] == best:
                until = max(until, r["ts"] + self.unlock_window_s)
        return best, until

    def _quote(self, db, account_id: str, key: str, fmt: str, now: float) -> Quote:
        tier = format_tier(fmt)
        held, until = self._held(db, account_id, key, now)
        paid = TIER_PRICE[held] if held else 0
        cost = max(0, TIER_PRICE[tier] - paid)
        return Quote(fmt=fmt.lower(), tier=tier, cost=cost,
                     held_tier=held, unlocked_until=until)

    # ── accounts ──────────────────────────────────────────────────────────

    def get_or_create_account(self, email: str, *, signup_grant: int = 0) -> str:
        """Account id for this email, creating it (and granting the signup
        tokens once) if it doesn't exist."""
        email = email.strip().lower()
        if not email:
            raise ValueError("email required")
        with self._write() as db:
            row = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()
            if row:
                return row["id"]
            account_id = uuid.uuid4().hex
            now = self._clock()
            db.execute("INSERT INTO accounts (id, email, created_at) VALUES (?, ?, ?)",
                       (account_id, email, now))
            if signup_grant > 0:
                db.execute(
                    "INSERT INTO ledger (account_id, ts, kind, amount, ref) VALUES (?, ?, 'signup', ?, ?)",
                    (account_id, now, signup_grant, f"signup:{account_id}"))
            return account_id

    def account_by_email(self, email: str) -> Optional[str]:
        with self._read() as db:
            row = db.execute("SELECT id FROM accounts WHERE email = ?",
                             (email.strip().lower(),)).fetchone()
        return row["id"] if row else None

    def account_email(self, account_id: str) -> Optional[str]:
        with self._read() as db:
            row = db.execute("SELECT email FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return row["email"] if row else None

    # ── balance and credits ───────────────────────────────────────────────

    def balance(self, account_id: str) -> int:
        with self._read() as db:
            return self._balance(db, account_id)

    def credit(self, account_id: str, amount: int, *, kind: str = "purchase",
               ref: Optional[str] = None, detail: Optional[str] = None) -> bool:
        """Add tokens. ``ref`` makes it idempotent (e.g. the order id, so a
        retried webhook can't credit twice): returns False if a credit of
        this kind with this ref already exists. ``adjust`` may be negative."""
        if kind not in CREDIT_KINDS:
            raise ValueError(f"not a credit kind: {kind!r}")
        if amount == 0 or (amount < 0 and kind != "adjust"):
            raise ValueError("credit amount must be positive (only 'adjust' may be negative)")
        with self._write() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone():
                raise KeyError(account_id)
            try:
                db.execute(
                    "INSERT INTO ledger (account_id, ts, kind, amount, ref, detail) VALUES (?, ?, ?, ?, ?, ?)",
                    (account_id, self._clock(), kind, amount, ref, detail))
            except sqlite3.IntegrityError:
                return False
        return True

    # ── charging ──────────────────────────────────────────────────────────

    def quote(self, account_id: str, key: str, fmt: str) -> Quote:
        """What downloading ``fmt`` of this design would cost right now."""
        with self._read() as db:
            return self._quote(db, account_id, key, fmt, self._clock())

    @contextmanager
    def charge(self, account_id: str, key: str, fmt: str, *,
               detail: Optional[str] = None) -> Iterator[Quote]:
        """Charge for one download around the code that generates it.

        Takes the tokens up front (raising InsufficientTokens, with nothing
        recorded, if the balance can't cover it), then refunds them if the
        body raises — so a failed export is free and doesn't unlock the
        design. A download that's already unlocked costs 0 and is logged
        only once it succeeds."""
        with self._write() as db:
            q = self._quote(db, account_id, key, fmt, self._clock())
            spend_id = None
            if q.cost:
                bal = self._balance(db, account_id)
                if bal < q.cost:
                    raise InsufficientTokens(q.cost, bal)
                cur = db.execute(
                    """INSERT INTO ledger (account_id, ts, kind, amount, design_key, tier, fmt, detail)
                       VALUES (?, ?, 'spend', ?, ?, ?, ?, ?)""",
                    (account_id, self._clock(), -q.cost, key, q.tier, q.fmt, detail))
                spend_id = cur.lastrowid
        try:
            yield q
        except BaseException:
            if spend_id is not None:
                self.refund(spend_id, detail="export failed")
            raise
        if spend_id is None:
            with self._write() as db:
                db.execute(
                    """INSERT INTO ledger (account_id, ts, kind, amount, design_key, tier, fmt, detail)
                       VALUES (?, ?, 'download', 0, ?, ?, ?, ?)""",
                    (account_id, self._clock(), key, q.tier, q.fmt, detail))

    def refund(self, spend_id: int, *, detail: Optional[str] = None) -> bool:
        """Reverse one spend: returns its tokens and removes the unlock it
        gave. Returns False if it was already refunded."""
        with self._write() as db:
            row = db.execute("SELECT * FROM ledger WHERE id = ? AND kind = 'spend'",
                             (spend_id,)).fetchone()
            if not row:
                raise KeyError(spend_id)
            try:
                db.execute(
                    """INSERT INTO ledger (account_id, ts, kind, amount, ref, design_key, tier, fmt, detail)
                       VALUES (?, ?, 'refund', ?, ?, ?, ?, ?, ?)""",
                    (row["account_id"], self._clock(), -row["amount"], f"spend:{spend_id}",
                     row["design_key"], row["tier"], row["fmt"], detail))
            except sqlite3.IntegrityError:
                return False
        return True

    # ── history ───────────────────────────────────────────────────────────

    def history(self, account_id: str, limit: int = 50) -> list[dict]:
        """Most recent ledger rows first."""
        with self._read() as db:
            rows = db.execute(
                "SELECT * FROM ledger WHERE account_id = ? ORDER BY id DESC LIMIT ?",
                (account_id, limit)).fetchall()
        return [dict(r) for r in rows]
