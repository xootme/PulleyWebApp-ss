"""
accounts.py — sign-in for the token model (PulleyWebApp-ss ADR-008):
linked sign-in identities, one-time email sign-in links, and revocable
sessions for browsers and CAD add-ins. Shares one SQLite file with
cct_common.tokens, whose `accounts` table and signup grant it reuses.

    from cct_common.tokens import TokenStore
    from cct_common.accounts import AccountStore

    tokens = TokenStore(path)
    accounts = AccountStore(tokens, signup_grant=10)
    link = accounts.create_login_link("a@b.com", ip=request.remote_addr)
    ...  # email the link; when it's confirmed:
    account_id = accounts.redeem_login_link(link)
    session = accounts.create_session(account_id, kind="web")

Security choices:
- Nothing that grants access is stored in the clear: sign-in links,
  browser sessions and add-in device tokens are random 256-bit secrets
  kept only as SHA-256 hashes, so a copy of the database can't be used
  to sign in.
- Sign-in links expire after 15 minutes, work once, and are rate-limited
  per email address and per IP.
- An account is found by its linked identity (provider + the provider's
  user id), not by email alone, so one person can sign in by email link,
  Microsoft, Google or GitHub and reach the same tokens. A new identity
  joins an existing account only when its email is verified and matches.
- Deleting an account strips its email, identities and sessions; ledger
  rows stay as financial records with no personal data left on them.
  Only a hash of the closed email is kept, so re-registering the same
  address doesn't earn the signup tokens a second time.

CAD add-ins sign in with a device code (start_device_login and friends):
the add-in shows a short code, the person approves it in a signed-in
browser, and the add-in's polling then receives a device session token,
once. Codes last 10 minutes; the add-in's polling secret is stored only
as a hash; user codes use consonants only (no 0/O or 1/I mix-ups, no
accidental words).

Inactivity (housekeeping(), run daily) — a way to clear out dead accounts
without ever taking anything a customer paid for:
- Free tokens (signup grant, promos) expire after 2 years without a sign-in.
  Purchased tokens never expire.
- After 5 years without a sign-in, an account holding no purchased tokens
  has its personal data deleted (delete_account; the ledger stays).
- Each is preceded by a reminder email at least 30 days ahead, and happens
  only once that reminder has gone out. Every sign-in resets both clocks:
  it stamps the identity's last_used_at, which idle time is measured from,
  and a new idle period needs a new reminder.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Callable, Optional

from .sqlite_db import SqliteDB
from .tokens import TokenStore

LOGIN_LINK_TTL_S = 15 * 60
LINKS_PER_EMAIL_PER_HOUR = 5
LINKS_PER_IP_PER_HOUR = 20
SESSION_TTL_S = {"web": 30 * 24 * 60 * 60, "device": 365 * 24 * 60 * 60}
_YEAR_S = int(365.25 * 24 * 60 * 60)
FREE_TOKEN_IDLE_S = 2 * _YEAR_S       # free tokens expire after this without a sign-in
DEAD_ACCOUNT_IDLE_S = 5 * _YEAR_S     # an account with nothing bought is removed after this
REMINDER_LEAD_S = 30 * 24 * 60 * 60   # reminder email this long before either
DEFAULT_DEVICE_DAILY_BUDGET = 100   # tokens per rolling 24 h per add-in/agent token
DEVICE_CODE_TTL_S = 10 * 60
DEVICE_POLL_INTERVAL_S = 5
DEVICE_STARTS_PER_IP_PER_HOUR = 20
_USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
PROVIDERS = frozenset({"email", "microsoft", "google", "github"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    provider     TEXT NOT NULL,
    subject      TEXT NOT NULL,
    account_id   TEXT NOT NULL REFERENCES accounts(id),
    email        TEXT,
    created_at   REAL NOT NULL,
    last_used_at REAL,
    PRIMARY KEY (provider, subject)
);
CREATE INDEX IF NOT EXISTS identities_account ON identities(account_id);
CREATE TABLE IF NOT EXISTS login_links (
    token_hash  TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    ip          TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL
);
CREATE INDEX IF NOT EXISTS login_links_email ON login_links(email, created_at);
CREATE INDEX IF NOT EXISTS login_links_ip ON login_links(ip, created_at);
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash   TEXT NOT NULL UNIQUE,
    account_id   TEXT NOT NULL REFERENCES accounts(id),
    kind         TEXT NOT NULL,
    label        TEXT,
    created_at   REAL NOT NULL,
    last_used_at REAL,
    expires_at   REAL NOT NULL,
    revoked_at   REAL
);
CREATE INDEX IF NOT EXISTS sessions_account ON sessions(account_id);
CREATE TABLE IF NOT EXISTS closed_emails (
    email_hash  TEXT PRIMARY KEY,
    closed_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS device_codes (
    device_hash   TEXT PRIMARY KEY,
    user_code     TEXT NOT NULL UNIQUE,
    label         TEXT,
    ip            TEXT,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    last_poll_at  REAL,
    account_id    TEXT REFERENCES accounts(id),
    approved_at   REAL,
    denied_at     REAL,
    issued_at     REAL
);
CREATE INDEX IF NOT EXISTS device_codes_ip ON device_codes(ip, created_at);
CREATE TABLE IF NOT EXISTS reminders (
    account_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    idle_since  REAL NOT NULL,
    sent_at     REAL NOT NULL,
    PRIMARY KEY (account_id, kind, idle_since)
);
"""


_DEFAULT = object()   # "use the store's default" (None means no limit)


class RateLimited(Exception):
    """Too many sign-in links requested; try again later."""


class IdentityInUse(Exception):
    """That sign-in identity already belongs to a different account."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    local, at, domain = email.partition("@")
    if not (local and at and "." in domain) or len(email) > 254 or " " in email:
        raise ValueError("invalid email address")
    return email


class AccountStore(SqliteDB):
    def __init__(self, tokens: TokenStore, *, signup_grant: int = 0,
                 clock: Optional[Callable[[], float]] = None,
                 device_daily_budget: Optional[int] = DEFAULT_DEVICE_DAILY_BUDGET):
        """device_daily_budget: the daily token limit a new add-in/agent
        token gets unless the person picks another (None: no limit)."""
        self.tokens = tokens
        self.signup_grant = signup_grant
        self.device_daily_budget = device_daily_budget
        self._clock = clock or tokens._clock
        super().__init__(tokens.path, _SCHEMA)
        # Daily limits on add-in/agent tokens. Older databases lack these.
        self._ensure_columns("sessions", {"daily_budget": "INTEGER",
                                          "budget_notified_at": "REAL"})
        self._ensure_columns("device_codes", {"daily_budget": "INTEGER",
                                              "budget_chosen": "INTEGER NOT NULL DEFAULT 0"})

    # ── identities ────────────────────────────────────────────────────────

    def sign_in(self, provider: str, subject: str, email: Optional[str], *,
                email_verified: bool) -> str:
        """Account id for this sign-in identity, linking or creating as
        needed. A never-seen identity joins the account that already owns
        its email only if the provider verified that email; with no
        verified email at all, sign-in is refused (ValueError), since the
        account's email is how it gets receipts and sign-in links."""
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider: {provider!r}")
        subject = str(subject)
        now = self._clock()
        with self._write() as db:
            row = db.execute("SELECT account_id FROM identities WHERE provider = ? AND subject = ?",
                             (provider, subject)).fetchone()
            if row:
                db.execute("UPDATE identities SET last_used_at = ? WHERE provider = ? AND subject = ?",
                           (now, provider, subject))
                return row["account_id"]
        if not email or not email_verified:
            raise ValueError("a verified email address is required to create an account")
        email = normalize_email(email)
        with self._read() as db:
            closed = db.execute("SELECT 1 FROM closed_emails WHERE email_hash = ?",
                                (_hash(email),)).fetchone()
        grant = 0 if closed else self.signup_grant
        account_id = self.tokens.get_or_create_account(email, signup_grant=grant)
        with self._write() as db:
            db.execute(
                """INSERT OR IGNORE INTO identities
                   (provider, subject, account_id, email, created_at, last_used_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (provider, subject, account_id, email, now, now))
            # A concurrent first sign-in may have linked it first; theirs wins.
            row = db.execute("SELECT account_id FROM identities WHERE provider = ? AND subject = ?",
                             (provider, subject)).fetchone()
            return row["account_id"]

    def link_identity(self, account_id: str, provider: str, subject: str,
                      email: Optional[str] = None) -> None:
        """Attach another sign-in method to a signed-in account."""
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider: {provider!r}")
        now = self._clock()
        with self._write() as db:
            row = db.execute("SELECT account_id FROM identities WHERE provider = ? AND subject = ?",
                             (provider, str(subject))).fetchone()
            if row:
                if row["account_id"] != account_id:
                    raise IdentityInUse(provider)
                return
            db.execute(
                """INSERT INTO identities (provider, subject, account_id, email, created_at, last_used_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (provider, str(subject), account_id, email, now, now))

    def identities(self, account_id: str) -> list[dict]:
        with self._read() as db:
            rows = db.execute(
                """SELECT provider, email, created_at, last_used_at FROM identities
                   WHERE account_id = ? ORDER BY created_at""", (account_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── email sign-in links ───────────────────────────────────────────────

    def create_login_link(self, email: str, *, ip: Optional[str] = None) -> str:
        """A one-time sign-in secret for this email (put it in the emailed
        link). Raises RateLimited past the per-email / per-IP limits."""
        email = normalize_email(email)
        now = self._clock()
        with self._write() as db:
            since = now - 3600
            n_email = db.execute("SELECT COUNT(*) FROM login_links WHERE email = ? AND created_at > ?",
                                 (email, since)).fetchone()[0]
            n_ip = db.execute("SELECT COUNT(*) FROM login_links WHERE ip = ? AND created_at > ?",
                              (ip, since)).fetchone()[0] if ip else 0
            if n_email >= LINKS_PER_EMAIL_PER_HOUR or n_ip >= LINKS_PER_IP_PER_HOUR:
                raise RateLimited()
            token = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO login_links (token_hash, email, ip, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (_hash(token), email, ip, now, now + LOGIN_LINK_TTL_S))
        return token

    def redeem_login_link(self, token: str) -> Optional[str]:
        """Account id if the link is valid (unused, unexpired), else None.
        Uses the link up. Creates the account on first sign-in."""
        if not token:
            return None
        now = self._clock()
        with self._write() as db:
            row = db.execute("SELECT * FROM login_links WHERE token_hash = ?",
                             (_hash(token),)).fetchone()
            if not row or row["used_at"] is not None or row["expires_at"] < now:
                return None
            db.execute("UPDATE login_links SET used_at = ? WHERE token_hash = ?",
                       (now, row["token_hash"]))
            email = row["email"]
        return self.sign_in("email", email, email, email_verified=True)

    # ── sessions (browser cookies and add-in device tokens) ───────────────

    def create_session(self, account_id: str, *, kind: str = "web",
                       label: Optional[str] = None, daily_budget=_DEFAULT) -> str:
        """daily_budget applies to device sessions only (add-ins, agents):
        the store's default unless given; None means no limit. Browser
        sessions never have one."""
        if kind not in SESSION_TTL_S:
            raise ValueError(f"unknown session kind: {kind!r}")
        budget = None
        if kind == "device":
            budget = self.device_daily_budget if daily_budget is _DEFAULT else daily_budget
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._write() as db:
            db.execute(
                """INSERT INTO sessions (token_hash, account_id, kind, label, created_at, last_used_at,
                                         expires_at, daily_budget)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (_hash(token), account_id, kind, (label or "")[:80], now, now,
                 now + SESSION_TTL_S[kind], budget))
        return token

    def session_info(self, token: Optional[str]) -> Optional[dict]:
        """The live session for a token — id, account_id, kind, label,
        daily_budget — else None. Marks it used."""
        if not token:
            return None
        now = self._clock()
        with self._write() as db:
            row = db.execute(
                """SELECT id, account_id, kind, label, daily_budget, expires_at, revoked_at
                   FROM sessions WHERE token_hash = ?""", (_hash(token),)).fetchone()
            if not row or row["revoked_at"] is not None or row["expires_at"] < now:
                return None
            db.execute("UPDATE sessions SET last_used_at = ? WHERE id = ?", (now, row["id"]))
        return {k: row[k] for k in ("id", "account_id", "kind", "label", "daily_budget")}

    def session_account(self, token: Optional[str]) -> Optional[str]:
        """Account id for a live session token, else None."""
        info = self.session_info(token)
        return info["account_id"] if info else None

    def set_session_budget(self, account_id: str, session_id: int, budget: Optional[int]) -> bool:
        """Change (or, with None, remove) the daily limit of one of this
        account's add-in/agent tokens. False if it isn't theirs or is gone."""
        if budget is not None and budget < 0:
            raise ValueError("a daily limit can't be negative")
        with self._write() as db:
            cur = db.execute(
                """UPDATE sessions SET daily_budget = ?, budget_notified_at = NULL
                   WHERE id = ? AND account_id = ? AND kind = 'device' AND revoked_at IS NULL""",
                (budget, session_id, account_id))
            return cur.rowcount == 1

    def claim_budget_notice(self, session_id: int) -> bool:
        """True once per 24 hours per token: whether to email the owner that
        the token hit its daily limit (so a busy agent sends one email, not
        one per refused request)."""
        now = self._clock()
        with self._write() as db:
            cur = db.execute(
                """UPDATE sessions SET budget_notified_at = ?
                   WHERE id = ? AND (budget_notified_at IS NULL OR budget_notified_at < ?)""",
                (now, session_id, now - 24 * 3600))
            return cur.rowcount == 1

    def session_id(self, token: str) -> Optional[int]:
        with self._read() as db:
            row = db.execute("SELECT id FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
        return row["id"] if row else None

    def revoke_session(self, token: str) -> None:
        with self._write() as db:
            db.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                       (self._clock(), _hash(token)))

    def revoke_session_id(self, account_id: str, session_id: int) -> bool:
        """Revoke one of this account's sessions by its listed id (e.g. a
        lost laptop's add-in). False if it isn't theirs or already revoked."""
        with self._write() as db:
            cur = db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE id = ? AND account_id = ? AND revoked_at IS NULL",
                (self._clock(), session_id, account_id))
            return cur.rowcount == 1

    def list_sessions(self, account_id: str) -> list[dict]:
        now = self._clock()
        with self._read() as db:
            rows = db.execute(
                """SELECT id, kind, label, created_at, last_used_at, expires_at, daily_budget FROM sessions
                   WHERE account_id = ? AND revoked_at IS NULL AND expires_at > ?
                   ORDER BY last_used_at DESC""", (account_id, now)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["spent_24h"] = self.tokens.spent_by_session(d["id"])
            out.append(d)
        return out

    # ── inactivity ────────────────────────────────────────────────────────

    _LAST_SIGN_IN_SQL = """COALESCE(
        (SELECT MAX(COALESCE(i.last_used_at, i.created_at))
         FROM identities i WHERE i.account_id = a.id),
        a.created_at)"""

    def last_sign_in(self, account_id: str) -> Optional[float]:
        """When this account last signed in by any method (its creation if
        it never has); None for an unknown account."""
        with self._read() as db:
            row = db.execute(f"SELECT {self._LAST_SIGN_IN_SQL} AS t FROM accounts a WHERE a.id = ?",
                             (account_id,)).fetchone()
        return row["t"] if row else None

    def housekeeping(self, notify=None) -> dict:
        """The daily inactivity run (see the module docstring). notify(email,
        kind, deadline, tokens) -> bool sends a reminder: kind "free_tokens"
        (tokens = free tokens that will expire) or "account" (tokens = 0).
        Nothing expires or is deleted without a reminder sent at least
        REMINDER_LEAD_S earlier in the same idle period, so with no notify
        this only tidies up. Returns counts of what it did."""
        now = self._clock()
        done = {"reminded": 0, "free_expired": 0, "accounts_deleted": 0}
        with self._read() as db:
            accounts = [dict(r) for r in db.execute(
                f"SELECT a.id, a.email, {self._LAST_SIGN_IN_SQL} AS last FROM accounts a "
                f"WHERE a.email NOT LIKE 'deleted:%' AND {self._LAST_SIGN_IN_SQL} < ?",
                (now - FREE_TOKEN_IDLE_S + REMINDER_LEAD_S,)).fetchall()]

        for a in accounts:
            idle = now - a["last"]
            free = self.tokens.free_remaining(a["id"])
            if free > 0:
                deadline = a["last"] + FREE_TOKEN_IDLE_S
                if self._remind(a, "free_tokens", deadline, free, notify, now):
                    done["reminded"] += 1
                if idle >= FREE_TOKEN_IDLE_S and self._reminded_early(a, "free_tokens", deadline):
                    if self.tokens.expire_free(a["id"], detail="no sign-in for 2 years"):
                        done["free_expired"] += 1
            if idle >= DEAD_ACCOUNT_IDLE_S - REMINDER_LEAD_S and self.tokens.purchased_remaining(a["id"]) == 0:
                deadline = a["last"] + DEAD_ACCOUNT_IDLE_S
                if self._remind(a, "account", deadline, 0, notify, now):
                    done["reminded"] += 1
                if idle >= DEAD_ACCOUNT_IDLE_S and self._reminded_early(a, "account", deadline):
                    self.tokens.expire_free(a["id"], detail="account closed after 5 years without a sign-in")
                    self.delete_account(a["id"])
                    done["accounts_deleted"] += 1
        self.purge_expired()
        return done

    def _remind(self, a, kind, deadline, tokens, notify, now) -> bool:
        """Send this idle period's reminder once, when inside the lead time."""
        if notify is None or now < deadline - REMINDER_LEAD_S:
            return False
        with self._read() as db:
            if db.execute("SELECT 1 FROM reminders WHERE account_id = ? AND kind = ? AND idle_since = ?",
                          (a["id"], kind, a["last"])).fetchone():
                return False
        try:
            sent = notify(a["email"], kind, deadline, tokens)
        except Exception:
            sent = False
        if not sent:
            return False                 # try again tomorrow; nothing happens without it
        with self._write() as db:
            db.execute("INSERT OR IGNORE INTO reminders (account_id, kind, idle_since, sent_at) "
                       "VALUES (?, ?, ?, ?)", (a["id"], kind, a["last"], now))
        return True

    def _reminded_early(self, a, kind, deadline) -> bool:
        """A reminder for this idle period went out at least the lead time
        before the deadline (or, if sent late, that long ago)."""
        with self._read() as db:
            row = db.execute("SELECT sent_at FROM reminders WHERE account_id = ? AND kind = ? AND idle_since = ?",
                             (a["id"], kind, a["last"])).fetchone()
        return bool(row) and self._clock() - row["sent_at"] >= REMINDER_LEAD_S

    # ── add-in sign-in (device code) ──────────────────────────────────────

    @staticmethod
    def normalize_user_code(code: str) -> str:
        """'bcdf ghjk', 'BCDF-GHJK' -> 'BCDF-GHJK' (as shown to the person)."""
        c = "".join(ch for ch in (code or "").upper() if ch in _USER_CODE_ALPHABET)
        return f"{c[:4]}-{c[4:]}" if len(c) == 8 else ""

    def start_device_login(self, *, label: str = "", ip: Optional[str] = None) -> dict:
        """Begin an add-in sign-in. Returns the add-in's secret device_code
        (poll with it) and the user_code to show the person. Raises
        RateLimited past DEVICE_STARTS_PER_IP_PER_HOUR."""
        now = self._clock()
        device_code = secrets.token_urlsafe(32)
        with self._write() as db:
            if ip and db.execute("SELECT COUNT(*) FROM device_codes WHERE ip = ? AND created_at > ?",
                                 (ip, now - 3600)).fetchone()[0] >= DEVICE_STARTS_PER_IP_PER_HOUR:
                raise RateLimited()
            while True:
                raw = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
                user_code = f"{raw[:4]}-{raw[4:]}"
                if not db.execute("SELECT 1 FROM device_codes WHERE user_code = ?", (user_code,)).fetchone():
                    break
            db.execute(
                """INSERT INTO device_codes (device_hash, user_code, label, ip, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_hash(device_code), user_code, (label or "CAD add-in")[:80], ip, now,
                 now + DEVICE_CODE_TTL_S))
        return {"device_code": device_code, "user_code": user_code,
                "expires_in": DEVICE_CODE_TTL_S, "interval": DEVICE_POLL_INTERVAL_S}

    def device_request(self, user_code: str) -> Optional[dict]:
        """What the approval page shows for a code: its label and state
        ("pending", "approved", "denied", "expired"). None if unknown."""
        code = self.normalize_user_code(user_code)
        if not code:
            return None
        with self._read() as db:
            row = db.execute("SELECT * FROM device_codes WHERE user_code = ?", (code,)).fetchone()
        if not row:
            return None
        return {"user_code": code, "label": row["label"], "state": self._device_state(row)}

    def _device_state(self, row) -> str:
        if row["denied_at"] is not None:
            return "denied"
        if row["approved_at"] is not None:
            return "approved"
        if row["expires_at"] < self._clock():
            return "expired"
        return "pending"

    def decide_device(self, user_code: str, account_id: str, *, approve: bool,
                      daily_budget=_DEFAULT) -> bool:
        """The signed-in person approves (or denies) a pending code, and may
        pick the token's daily limit (None: no limit; default: the store's).
        False if the code is unknown, expired or already decided."""
        code = self.normalize_user_code(user_code)
        now = self._clock()
        with self._write() as db:
            row = db.execute("SELECT * FROM device_codes WHERE user_code = ?", (code,)).fetchone()
            if not row or self._device_state(row) != "pending":
                return False
            if approve:
                chosen = daily_budget is not _DEFAULT
                db.execute(
                    """UPDATE device_codes SET account_id = ?, approved_at = ?, daily_budget = ?,
                                               budget_chosen = ? WHERE user_code = ?""",
                    (account_id, now, daily_budget if chosen else None, 1 if chosen else 0, code))
            else:
                db.execute("UPDATE device_codes SET denied_at = ? WHERE user_code = ?", (now, code))
        return True

    def poll_device(self, device_code: str) -> tuple[str, Optional[str]]:
        """The add-in's poll. Returns (status, token): "pending", "slow_down"
        (polled faster than the interval), "denied", "expired", "invalid",
        or "approved" with the device session token — handed out once; the
        code is spent after that."""
        now = self._clock()
        with self._write() as db:
            row = db.execute("SELECT * FROM device_codes WHERE device_hash = ?",
                             (_hash(device_code or ""),)).fetchone()
            if not row or row["issued_at"] is not None:
                return "invalid", None
            state = self._device_state(row)
            if state == "pending":
                too_fast = (row["last_poll_at"] is not None
                            and now - row["last_poll_at"] < DEVICE_POLL_INTERVAL_S - 0.5)
                db.execute("UPDATE device_codes SET last_poll_at = ? WHERE device_hash = ?",
                           (now, row["device_hash"]))
                return ("slow_down" if too_fast else "pending"), None
            if state != "approved":
                return state, None
            db.execute("UPDATE device_codes SET issued_at = ? WHERE device_hash = ?",
                       (now, row["device_hash"]))
            account_id, label = row["account_id"], row["label"]
            budget = row["daily_budget"] if row["budget_chosen"] else _DEFAULT
        return "approved", self.create_session(account_id, kind="device", label=label,
                                               daily_budget=budget)

    # ── deletion and housekeeping ─────────────────────────────────────────

    def delete_account(self, account_id: str) -> None:
        """Remove the personal data: email, identities, sessions and pending
        sign-in links. Ledger rows stay (financial records) but point at an
        account with no email left on it."""
        now = self._clock()
        with self._write() as db:
            row = db.execute("SELECT email FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if not row:
                raise KeyError(account_id)
            db.execute("DELETE FROM login_links WHERE email = ?", (row["email"],))
            db.execute("DELETE FROM identities WHERE account_id = ?", (account_id,))
            db.execute("UPDATE sessions SET revoked_at = ? WHERE account_id = ? AND revoked_at IS NULL",
                       (now, account_id))
            db.execute("UPDATE accounts SET email = ? WHERE id = ?",
                       (f"deleted:{account_id}", account_id))
            db.execute("INSERT OR IGNORE INTO closed_emails (email_hash, closed_at) VALUES (?, ?)",
                       (_hash(row["email"]), now))

    def purge_expired(self) -> None:
        """Drop sign-in links older than a day and sessions dead for 30 days."""
        now = self._clock()
        with self._write() as db:
            db.execute("DELETE FROM login_links WHERE created_at < ?", (now - 24 * 3600,))
            db.execute("DELETE FROM device_codes WHERE created_at < ?", (now - 24 * 3600,))
            db.execute("DELETE FROM sessions WHERE expires_at < ? OR revoked_at < ?",
                       (now - 30 * 24 * 3600, now - 30 * 24 * 3600))
