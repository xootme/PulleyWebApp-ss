"""
settings.py — the business settings, one source of truth (0.28.0).

The numbers a customer meets (the signup grant, what each file tier costs,
the token packs each payment provider sells, an add-in's daily limit, how
many new accounts one IP may get a grant for in a day, where "buy tokens"
goes) live in one `settings` table in the shared accounts database, edited
on the admin page's Business settings tab, with every change kept in
`settings_history` (who, when, old, new, why). The owner's plan:
Controller campaigns/2026-10-27-signup-grant-10.md, phase 1.

    from cct_common.settings import settings_for
    grant = settings_for(database_url).get("signup_grant")

- Read where used, cached CACHE_S per process, so an edit reaches every
  server within that.
- First start writes today's values as the rows (DEFAULTS), so rolling
  this out changes nothing anyone sees. A row that's missing or can't be
  read gives the built-in default and logs an error; reading never raises.
  TOKENS_ENABLED stays deploy config, a kill switch that needs no database.
- A value an app still passes itself (AccountStore(signup_grant=20),
  register_payment_routes(packs=...), Charges.attach(buy_url=...)) wins
  for that app until its own release drops it (phase 3).
- tokens per dollar is fixed: 1 token = 5 cents (tokens.TOKEN_PRICE_CENTS),
  which refunds rely on. Packs at another rate are allowed, and flagged.
- A setting in SINCE_KEYS also keeps when it was first turned on, once and
  for good (`settings_since`; since()). The rewards' switch date (0.37.0):
  paid tokens are used first for spends from then, even if it's turned off
  again, so no one's refundable tokens change after the fact.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional

from . import forksafe
from .sqlite_db import SqliteDB

log = logging.getLogger(__name__)

CACHE_S = 30
PROVIDERS = ("paypal", "stripe")
TIERS = ("2d", "stl", "step")
MAX_HISTORY = 200

# Today's values (2026-10-29): every app's code default, none overridden on
# Cloud Run. The owner's rule: the rollout starts here; the grant goes to 10
# only as the last step, by one edit on the admin page.
DEFAULTS = {
    "signup_grant": 20,
    "tier_prices": {"2d": 2, "stl": 3, "step": 4},
    "packs": {"paypal": [{"cents": 200, "tokens": 40}, {"cents": 500, "tokens": 100}],
              "stripe": [{"cents": 500, "tokens": 100}, {"cents": 1000, "tokens": 200},
                         {"cents": 2500, "tokens": 500}]},
    "device_daily_budget": 100,
    "signups_per_ip_per_day": 2,
    "buy_url": "https://cheapcadtools.com/account/buy",
    # 0.34.0 (the owner, 2026-10-30): off until the owner turns it on, with
    # the window set to "forever" then; today's 24 h until that edit.
    "saved_designs": False,
    "redownload_window": "24h",
    # 0.35.0: the buy panel and money beside tokens, off until the owner turns them on.
    "inline_checkout": False,
    "price_in_money": False,
    # 0.36.0: the newsletter checkbox (welcome page, token dialog, admin
    # count and export), off until the owner turns it on.
    "newsletter_opt_in": False,
    # 0.37.0: the rewards program (bonus tokens, paid tokens used first),
    # off until the owner turns it on, after the Terms say so.
    "rewards": False,
    # 0.38.0: the account page, /account/, off until the owner turns it on.
    "account_page": False,
    # 0.39.0: the newsletter list's id in CheapCADTools' Brevo account; none
    # until it's set up (the sync waits).
    "newsletter_list_id": None,
    # 0.40.0: receipts and credit notes, off until the owner turns them on.
    "receipts": False,
    # 0.41.0: printer settings, units and formats on the account, off.
    "printer_settings": False,
    # 0.42.0: share a saved design by a link, off (needs saved_designs).
    "share_links": False,
    # 0.43.0: bookmarks and private notes on saved designs, off (needs saved_designs).
    "bookmarks": False,
    # 0.44.0: My reports (the account's reports and their status), off.
    "my_reports": False,
    # 0.45.0: "Download all my data" on the account page, off.
    "data_download": False,
    # 0.46.0: projects (named groups of saved designs), off (needs saved_designs).
    "projects": False,
    # 0.47.0: "My account" in every tool's header, off (the label is the owner's; the layout isn't seen yet).
    "my_account_button": False,
    # 0.49.0: referral tokens for share links (the owner's a-f), off (needs share_links).
    "referrals": False,
}
SINCE_KEYS = ("rewards",)
REDOWNLOAD_WINDOWS = ("24h", "forever")

DESCRIPTIONS = {
    "signup_grant": "Free tokens a new account gets (0 to 1000).",
    "tier_prices": "Tokens a design's files cost, by tier: 2D (SVG, DXF), STL, STEP. "
                   "Each 1 to 100, with 2D ≤ STL ≤ STEP; a higher tier costs the difference.",
    "packs": "Token packs each payment provider sells: price and tokens. "
             "PayPal and Stripe differ on purpose (their fees).",
    "device_daily_budget": "The daily token limit a new add-in or AI app starts with "
                           "(1 to 100000, or no limit). The person can change theirs.",
    "signups_per_ip_per_day": "New accounts per IP address per day that get the signup grant "
                              "(1 to 100, or no limit). Later ones start with no free tokens.",
    "buy_url": "Where \"Buy tokens\" goes: a https://cheapcadtools.com/ address or a /path.",
    "saved_designs": "Saved designs: every download kept on the account, \"Save to My designs\" in the "
                     "Download window and the My designs page. Off: none of it shows and nothing is saved.",
    "redownload_window": "How long files already bought can be downloaded again for free: "
                         "24h, or forever (for that account).",
    "inline_checkout": "The buy panel in every app: card, Apple Pay, Google Pay and Link (Stripe) and "
                       "PayPal without leaving the page, and the download goes on once paid. "
                       "Off: \"Buy tokens\" opens the buy page.",
    "price_in_money": "Money beside tokens wherever tokens are shown: \"STEP: 4 tokens = 20¢\".",
    "newsletter_opt_in": "The newsletter checkbox: on a new account's welcome page and in the "
                         "\"Your tokens\" dialog, unticked until the person ticks it. Off: no checkbox "
                         "anywhere and nothing is recorded. Turn on once the privacy text says so.",
    "rewards": "Rewards: +25% bonus tokens on a first purchase (once per account and per card or PayPal "
               "account), +10% after every 200 paid tokens spent (+20% at every 1,000), and paid tokens "
               "used before free ones for spends from the day it's first turned on. Off: today's rules, "
               "free tokens first. Turn on only once the Terms say so; the paid-first order stays from "
               "that day even if it's turned off again.",
    "account_page": "The account page, /account/: tokens and buying, My designs, the history in plain "
                    "words, the newsletter box, sign-ins and connected apps, and deleting the account. "
                    "A section shows only while its own setting is on. Off: a 404, and no link to it.",
    "newsletter_list_id": "The newsletter list's id in CheapCADTools' Brevo account (a number). Subscribers "
                          "are added to it and deleted from Brevo when they leave. Empty: nothing is sent "
                          "to Brevo; changes wait until it's set (and BREVO_API_KEY is in Cloud).",
    "receipts": "Receipts and credit notes: a numbered receipt for every purchase (CCT-2026-000001) and a "
                "credit note for every refund or chargeback, at /account/receipts/<number>, linked from the "
                "account page's history and the Sales tab. Off: nothing is numbered or shown. The first time "
                "it's on, orders already paid are numbered in the order they were paid.",
    "printer_settings": "Printer settings on the account: units (mm or inch) and the 3D Printer Compensation "
                        "(0 to 1.00 mm, for STL files only). Set on the account page; each tool starts a new "
                        "design from them. Off: none of it is read or shown.",
    "share_links": "Share links: \"Share\" on a design in My designs makes a public link that opens a copy of "
                   "it in the tool (settings and picture, never the email). Needs saved designs. Off: no "
                   "Share button, and every link answers \"isn't shared any more\".",
    "bookmarks": "Bookmarks and notes on saved designs: a star, a private note (1,000 characters, text "
                 "only) searchable in My designs, bookmarked designs first and never pushed out at the "
                 "design limit. Needs saved designs. Off: none of it shows.",
    "my_reports": "My reports: a signed-in person's bug and feature reports are linked to their account and "
                  "listed on its page with their status, which the admin sets on the Bug reports tab (and may "
                  "send one \"fixed\" email). Off: reports are kept as before, linked to no account.",
    "data_download": "\"Download all my data\" on the account page: a zip of everything the account keeps "
                     "(saved designs and pictures, notes, share links, printer settings, token history, orders, "
                     "receipts, reports, newsletter choices, sessions), made at once, 5 an hour. No CAD files.",
    "projects": "Projects: named groups of saved designs for one build, with a private note; up to 50 per "
                "account, 100 designs each; shared as a public page with share links on. Needs saved designs. "
                "Off: none of it shows.",
    "my_account_button": "\"My account\" in every tool's header in place of \"Sign in\": signed out it opens "
                         "sign-in (then the account page, while that's on); signed in it opens the account "
                         "page, beside the balance and Sign out. Off: today's header.",
    "referrals": "Referrals: someone who opens a share link and makes a new account from it, in that visit, "
                 "earns the sharer 50 free tokens with their first purchase, up to 10 a month; a refund takes "
                 "the same share back, never below zero. No cookie. Needs share links. Off: nothing is "
                 "recorded or paid.",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    updated_by  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT NOT NULL,
    changed_at  REAL NOT NULL,
    changed_by  TEXT NOT NULL,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS settings_since (
    key         TEXT PRIMARY KEY,
    since       REAL NOT NULL,
    by          TEXT NOT NULL
);
"""
SEEDED_BY = "cct_common"
SEED_NOTE = "initial: today's values"


class SettingsError(ValueError):
    """A value the admin tab can't save, in a sentence that says why."""


def _int(value, lo, hi, what, allow_none=False):
    if value is None or value == "":
        if allow_none:
            return None
        raise SettingsError(f"{what} needs a number")
    if isinstance(value, bool):
        raise SettingsError(f"{what} must be a whole number")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{what} must be a whole number") from None
    if float(value) != n:
        raise SettingsError(f"{what} must be a whole number")
    if not lo <= n <= hi:
        raise SettingsError(f"{what} must be {lo} to {hi}" + (", or no limit" if allow_none else ""))
    return n


def validate(key: str, value):
    """The value to store for key, normalised; SettingsError if it can't be."""
    if key == "signup_grant":
        return _int(value, 0, 1000, "The signup grant")
    if key == "device_daily_budget":
        return _int(value, 1, 100000, "The daily limit", allow_none=True)
    if key == "signups_per_ip_per_day":
        return _int(value, 1, 100, "Signups per IP per day", allow_none=True)
    if key == "tier_prices":
        if not isinstance(value, dict) or set(value) != set(TIERS):
            raise SettingsError("Tier prices need 2d, stl and step")
        prices = {t: _int(value[t], 1, 100, f"The {t.upper()} price") for t in TIERS}
        if not prices["2d"] <= prices["stl"] <= prices["step"]:
            raise SettingsError("Prices must rise with the tier: 2D ≤ STL ≤ STEP")
        return prices
    if key == "packs":
        if not isinstance(value, dict) or not value or set(value) - set(PROVIDERS):
            raise SettingsError(f"Packs are per provider: {', '.join(PROVIDERS)}")
        out = {}
        for provider in PROVIDERS:
            if provider not in value:
                continue
            items = value[provider]
            if not isinstance(items, list):
                raise SettingsError(f"{provider}'s packs must be a list")
            packs = []
            for p in items:
                if not isinstance(p, dict):
                    raise SettingsError(f"{provider}: each pack needs a price and tokens")
                cents = _int(p.get("cents"), 100, 100000, f"A {provider} pack's price (cents)")
                tokens = _int(p.get("tokens"), 1, 100000, f"A {provider} pack's tokens")
                packs.append({"cents": cents, "tokens": tokens})
            if len({p["cents"] for p in packs}) != len(packs):
                raise SettingsError(f"{provider} has two packs at the same price")
            out[provider] = sorted(packs, key=lambda p: p["cents"])
        if not any(out.values()):
            raise SettingsError("At least one pack must be on sale")
        return out
    if key in ("saved_designs", "inline_checkout", "price_in_money", "newsletter_opt_in", "rewards", "account_page", "receipts", "printer_settings", "share_links", "bookmarks", "my_reports", "data_download", "projects", "my_account_button", "referrals"):
        if not isinstance(value, bool):
            raise SettingsError(f"{key.replace('_', ' ').capitalize()} is on or off")
        return value
    if key == "redownload_window":
        if value not in REDOWNLOAD_WINDOWS:
            raise SettingsError("The re-download window is 24h or forever")
        return value
    if key == "newsletter_list_id":
        return _int(value, 1, 10**9, "The list id", allow_none=True)
    if key == "buy_url":
        url = str(value or "").strip()
        if not (url.startswith("https://cheapcadtools.com/") or (url.startswith("/") and not url.startswith("//"))):
            raise SettingsError("The buy link must be a https://cheapcadtools.com/ address or a /path")
        if any(c in url for c in " \"'<>"):
            raise SettingsError("The buy link can't hold spaces or quotes")
        return url
    raise SettingsError(f"No setting called {key!r}")


def off_rate_packs(packs: dict, cents_per_token: int) -> list[str]:
    """Packs that don't sell at the fixed token price (allowed, but worth a look)."""
    return [f"{provider} ${p['cents'] / 100:g}: {p['tokens']} tokens"
            for provider, items in (packs or {}).items() for p in items
            if p["tokens"] * cents_per_token != p["cents"]]


class SettingsStore(SqliteDB):
    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        super().__init__(path, _SCHEMA)
        self._clock = clock
        self._cache: dict = {}
        self._since: dict = {}
        self._lock = threading.Lock()
        forksafe.after_fork_in_child(self._renew)
        self.seed()

    def _renew(self):
        self._lock = threading.Lock()
        self._cache = {}
        self._since = {}

    def since(self, key: str) -> Optional[float]:
        """When this SINCE_KEYS setting was first turned on (None: never).
        Once there, it never moves, so it's kept for good; a None for CACHE_S.
        Never raises (None, logged)."""
        hit = self._since.get(key)
        if hit and (hit[1] is not None or time.monotonic() - hit[0] < CACHE_S):
            return hit[1]
        try:
            with self._read() as db:
                row = db.execute("SELECT since FROM settings_since WHERE key = ?", (key,)).fetchone()
        except Exception as e:
            log.error("setting %s: can't read when it was turned on (%s)", key, e)
            return None
        value = float(row[0]) if row else None
        self._since[key] = (time.monotonic(), value)
        return value

    def seed(self) -> list[str]:
        """Write today's value for every setting that has no row; returns the keys written."""
        now, done = self._clock(), []
        with self._write() as db:
            have = {r[0] for r in db.execute("SELECT key FROM settings").fetchall()}
            for key, value in DEFAULTS.items():
                if key in have:
                    continue
                text = json.dumps(value, sort_keys=True)
                db.execute("INSERT OR IGNORE INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                           (key, text, now, SEEDED_BY))
                db.execute("INSERT INTO settings_history (key, old_value, new_value, changed_at, changed_by, note) "
                           "VALUES (?, NULL, ?, ?, ?, ?)", (key, text, now, SEEDED_BY, SEED_NOTE))
                done.append(key)
        return done

    def get(self, key: str):
        """The setting's value, cached CACHE_S. Never raises: a missing or
        unreadable row gives DEFAULTS[key] and logs an error."""
        if key not in DEFAULTS:
            raise KeyError(key)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < CACHE_S:
                return _copy(hit[1])
        value = self._load(key)
        with self._lock:
            self._cache[key] = (now, value)
        return _copy(value)

    def _load(self, key: str):
        try:
            with self._read() as db:
                row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        except Exception as e:
            log.error("setting %s: can't read it (%s); using the built-in %r", key, e, DEFAULTS[key])
            return DEFAULTS[key]
        if row is None:
            log.error("setting %s: no row; using the built-in %r", key, DEFAULTS[key])
            return DEFAULTS[key]
        try:
            return validate(key, json.loads(row[0]))
        except (ValueError, TypeError) as e:
            log.error("setting %s: stored value %r is not valid (%s); using the built-in %r",
                      key, row[0], e, DEFAULTS[key])
            return DEFAULTS[key]

    def set(self, key: str, value, *, by: str, note: str = "") -> object:
        """Validate and save; keeps the change in the history. Returns the stored value."""
        value = validate(key, value)
        text, now = json.dumps(value, sort_keys=True), self._clock()
        with self._write() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            old = row[0] if row else None
            if old == text:
                return value
            if row:
                db.execute("UPDATE settings SET value = ?, updated_at = ?, updated_by = ? WHERE key = ?",
                           (text, now, by, key))
            else:
                db.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                           (key, text, now, by))
            db.execute("INSERT INTO settings_history (key, old_value, new_value, changed_at, changed_by, note) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (key, old, text, now, by, (note or "")[:500]))
            if key in SINCE_KEYS and value is True:
                db.execute("INSERT OR IGNORE INTO settings_since (key, since, by) VALUES (?, ?, ?)",
                           (key, now, by))
        with self._lock:
            self._cache.pop(key, None)
            self._since.pop(key, None)
        return value

    def rows(self) -> list[dict]:
        """Every setting for the admin tab: key, value, default, description, who and when."""
        with self._read() as db:
            stored = {r[0]: (r[1], r[2], r[3]) for r in db.execute(
                "SELECT key, value, updated_at, updated_by FROM settings").fetchall()}
        out = []
        for key, default in DEFAULTS.items():
            text, at, by = stored.get(key, (None, None, None))
            try:
                value = validate(key, json.loads(text)) if text is not None else None
            except (ValueError, TypeError):
                value = None
            out.append({"key": key, "value": value, "default": default, "description": DESCRIPTIONS[key],
                        "updated_at": at, "updated_by": by, "missing": text is None})
        return out

    def history(self, key: Optional[str] = None, limit: int = MAX_HISTORY) -> list[dict]:
        q = "SELECT key, old_value, new_value, changed_at, changed_by, note FROM settings_history"
        args: tuple = ()
        if key:
            q, args = q + " WHERE key = ?", (key,)
        with self._read() as db:
            rows = db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
        return [{"key": r[0], "old": json.loads(r[1]) if r[1] else None, "new": json.loads(r[2]),
                 "changed_at": r[3], "changed_by": r[4], "note": r[5] or ""} for r in rows]


def _copy(value):
    return json.loads(json.dumps(value)) if isinstance(value, (dict, list)) else value


# One store per database. Opening one writes (the table, today's values),
# so it must happen outside any transaction: TokenStore opens it when it is
# made, and a forked worker keeps the parent's (its lock and cache renew).
# Inside a write transaction a store being made would wait on that same
# transaction's lock: 30 s on SQLite, for ever on Postgres.
_STORES: dict = {}
_STORES_LOCK = threading.Lock()
forksafe.renew_after_fork(globals(), "_STORES_LOCK")


def settings_for(path: str) -> SettingsStore:
    """The store for this database, opened (and seeded) on first use."""
    key = str(path)
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = _STORES[key] = SettingsStore(path)
        return store


def setting(path: Optional[str], key: str):
    """The setting from this database's store, or the built-in when there's
    no database or no store opened for it yet (logged); never raises, and
    never opens a store, so it is safe inside a write transaction."""
    if not path:
        return _copy(DEFAULTS[key])
    store = _STORES.get(str(path))
    if store is None:
        log.error("setting %s: no settings store opened for this database; using the built-in", key)
        return _copy(DEFAULTS[key])
    try:
        return store.get(key)
    except Exception as e:
        log.error("setting %s: can't read it (%s); using the built-in", key, e)
        return _copy(DEFAULTS[key])


def setting_since(path: Optional[str], key: str) -> Optional[float]:
    """When this SINCE_KEYS setting was first turned on, or None (never, no
    database or no store opened for it). Never raises or opens a store."""
    store = _STORES.get(str(path)) if path else None
    return store.since(key) if store is not None else None


def offer(path: Optional[str], *, cents_per_token: int, format_tier: dict) -> dict:
    """What /api/offer answers: the grant, prices, packs per provider, tokens per dollar."""
    packs = setting(path, "packs")
    return {
        "signup_grant": setting(path, "signup_grant"),
        "tier_prices": setting(path, "tier_prices"),
        "format_tier": dict(format_tier),
        "packs": {provider: [{**p, "price": f"${p['cents'] // 100}" + (f".{p['cents'] % 100:02d}" if p["cents"] % 100 else "")}
                             for p in items] for provider, items in packs.items()},
        "tokens_per_dollar": 100 // cents_per_token,
        "buy_url": setting(path, "buy_url"),
        # 0.35.0, only while on: the pages show money beside tokens, and buy in a panel
        **({"price_in_money": True, "cents_per_token": cents_per_token}
           if setting(path, "price_in_money") is True else {}),
        **({"inline_checkout": True} if setting(path, "inline_checkout") is True else {}),
        # 0.47.0, only while on: the header's "My account", and whether the account page is there
        **({"my_account_button": True} if setting(path, "my_account_button") is True else {}),
        **({"account_page": True} if setting(path, "account_page") is True else {}),
    }
