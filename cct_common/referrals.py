"""
referrals.py — referral tokens for share links (0.49.0; account plan step 4,
the owner's decisions of 2026-10-30, a-f). Behind the `referrals` business
setting, off until the owner turns it on; it needs share_links (and so
saved_designs).

- No cookie and nothing kept in the browser (decision e): the share code
  goes from the ?shared= page through that one sign-up only. api/shared
  answers "referral": true while this is on; tokens.js then keeps the code
  in the page's memory and sends it with the sign-in it starts: the email
  link's request (kept on its login_links row, gone within a day) or the
  OAuth start (its oauth_states row). A visitor who leaves and comes back
  later isn't counted.
- Only a new account counts, never a sign-in to an existing one, and the
  link must still be live. An email that had a closed account is recorded
  as "returning" and never pays (as it gets no signup grant).
- The referred account's first purchase pays the sharer REWARD free tokens
  (ledger kind "referral", ref "referral:<referred account id>", so once), in
  the same write as the purchase (decision a). At most MONTHLY_CAP a month
  per sharer, by UTC calendar month; past it the referral is recorded and
  pays nothing. Nothing extra for the newcomer (decision b).
- Not for referring yourself: no reward when the purchase's payer key (as
  in rewards: a hash of the card fingerprint or PayPal payer id) has paid
  any of the sharer's orders. While this is on, every purchase keeps its key.
- Only the first purchase decides: a later one never pays, nor a first one
  made while this was off. The order keeps the reward it paid
  (payments.referral_tokens).
- A refund or chargeback of that purchase takes back the same share of the
  reward (decision d), whether or not this is still on, but never takes the
  sharer's balance below zero: what can't be taken is let go. Ledger: an
  adjust, ref "referralback:<provider>:<refund id>".
- Shared links carry no name (decision c); opens are a number only (f).
- The record is kept while both accounts are open (the privacy text):
  deleting either deletes it (forget_account, 0.49.1). The reward stays in
  the token history: the ledger's "referral" row, ref'd by the referred
  account's id, is what the cap counts and what a later refund or
  chargeback finds the sharer by, so both still hold after a deletion.
"""
from __future__ import annotations

import calendar
import time
from typing import Optional

REWARD = 50
MONTHLY_CAP = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS referrals (
    referred_id  TEXT PRIMARY KEY,
    sharer_id    TEXT NOT NULL,
    app          TEXT,
    created_at   REAL NOT NULL,
    outcome      TEXT,
    decided_at   REAL,
    order_ref    TEXT
);
CREATE INDEX IF NOT EXISTS referrals_sharer ON referrals (sharer_id, outcome, decided_at);
"""

# Why a referral paid or didn't, for the admin page. None: no purchase yet.
OUTCOMES = {"rewarded": "rewarded", "over_cap": "over the month's cap", "self": "self-referral (same card or PayPal)",
            "returning": "returning email (a closed account)", "not_first": "first purchase made while off"}


def referrals_on(path) -> bool:
    from .settings import setting
    return all(setting(path, k) is True for k in ("referrals", "share_links", "saved_designs"))


def carried(path, code) -> Optional[str]:
    """The share code a sign-up carries: a well-formed one, while this is on."""
    from .share_links import valid_code
    return code if isinstance(code, str) and valid_code(code) and referrals_on(path) else None


def month_bounds(ts: float) -> tuple:
    """[start, end) of ts's UTC calendar month."""
    t = time.gmtime(ts)
    y, m = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    return calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0)), calendar.timegm((y, m, 1, 0, 0, 0))


def record(db, code: str, account_id: str, now: float, *, returning: bool) -> None:
    """Inside a new account's write: who shared the live link it came from."""
    row = db.execute("SELECT account_id, app FROM shared_designs WHERE code = ? AND revoked_at IS NULL",
                     (code,)).fetchone()
    if not row or row["account_id"] == account_id:
        return
    db.execute("INSERT OR IGNORE INTO referrals (referred_id, sharer_id, app, created_at, outcome, decided_at) "
               "VALUES (?, ?, ?, ?, ?, ?)",
               (account_id, row["account_id"], row["app"], now, "returning" if returning else None,
                now if returning else None))


def rewarded_in_month(db, sharer_id: str, now: float) -> int:
    """Rewards paid this UTC month, from the ledger: a deleted referred
    account's record is gone, its reward isn't."""
    start, end = month_bounds(now)
    return db.execute("SELECT COUNT(*) FROM ledger WHERE account_id = ? AND kind = 'referral' "
                      "AND ts >= ? AND ts < ?", (sharer_id, start, end)).fetchone()[0]


def forget_account(db, account_id: str) -> None:
    """Inside an account's deletion: its referral records, as sharer or as
    the referred account. The ledger's rows stay (financial records)."""
    db.execute("DELETE FROM referrals WHERE referred_id = ? OR sharer_id = ?", (account_id, account_id))


def on_purchase(db, account_id: str, order_ref: str, key: Optional[str], now: float) -> int:
    """Inside a purchase's write, while this is on: decide the referral of
    the buying account, if it has one waiting. Returns the tokens paid."""
    from .sqlite_db import INTEGRITY_ERRORS
    ref = db.execute("SELECT sharer_id FROM referrals WHERE referred_id = ? AND outcome IS NULL",
                     (account_id,)).fetchone()
    if not ref:
        return 0
    sharer = ref["sharer_id"]
    if db.execute("SELECT 1 FROM ledger WHERE account_id = ? AND kind = 'purchase' AND ref <> ? LIMIT 1",
                  (account_id, order_ref)).fetchone():
        outcome = "not_first"
    elif key and db.execute("SELECT 1 FROM payments WHERE account_id = ? AND payer_key = ? LIMIT 1",
                            (sharer, key)).fetchone():
        outcome = "self"
    elif rewarded_in_month(db, sharer, now) >= MONTHLY_CAP:
        outcome = "over_cap"
    else:
        outcome = "rewarded"
    paid = 0
    if outcome == "rewarded":
        try:
            db.execute("INSERT INTO ledger (account_id, ts, kind, amount, ref, detail) "
                       "VALUES (?, ?, 'referral', ?, ?, ?)",
                       (sharer, now, REWARD, f"referral:{account_id}", "referral: a referred account's first purchase"))
            paid = REWARD
        except INTEGRITY_ERRORS:
            pass
    db.execute("UPDATE referrals SET outcome = ?, decided_at = ?, order_ref = ? WHERE referred_id = ?",
               (outcome, now, order_ref, account_id))
    return paid


def take_back(db, order: dict, refund_id: str, upto: int, now: float) -> int:
    """Inside one write: take back the order's referral reward so that `upto`
    of it is settled in all, never below the sharer's zero; returns the
    tokens taken this time. What the balance can't cover is let go."""
    from .sqlite_db import INTEGRITY_ERRORS
    due = min(int(order.get("referral_tokens") or 0), max(0, int(upto))) - int(order.get("referral_back") or 0)
    if due <= 0:
        return 0
    # The sharer, by the reward's own ledger row: there even once either account is deleted.
    row = db.execute("SELECT account_id AS sharer_id FROM ledger WHERE kind = 'referral' AND ref = ?",
                     (f"referral:{order['account_id']}",)).fetchone()
    if not row:
        return 0
    bal = int(db.execute("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE account_id = ?",
                         (row["sharer_id"],)).fetchone()[0])
    take = min(due, max(0, bal))
    if take:
        try:
            db.execute("INSERT INTO ledger (account_id, ts, kind, amount, ref, detail) VALUES (?, ?, 'adjust', ?, ?, ?)",
                       (row["sharer_id"], now, -take, f"referralback:{order['provider']}:{refund_id}",
                        "referral tokens back: the referred purchase was refunded"))
        except INTEGRITY_ERRORS:
            return 0
    db.execute("UPDATE payments SET referral_back = referral_back + ? WHERE order_id = ?", (due, order["order_id"]))
    return take


def summary(db, sharer_id: str, now: float) -> dict:
    """The account page's line: referred accounts that bought their first
    pack, tokens earned (net of any taken back), rewards left this month."""
    bought = db.execute("SELECT COUNT(*) FROM referrals WHERE sharer_id = ? AND outcome IN ('rewarded', 'over_cap')",
                        (sharer_id,)).fetchone()[0]
    earned = db.execute("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE account_id = ? AND "
                        "(kind = 'referral' OR (kind = 'adjust' AND ref LIKE 'referralback:%'))",
                        (sharer_id,)).fetchone()[0]
    return {"bought": int(bought), "earned": int(earned),
            "left": max(0, MONTHLY_CAP - rewarded_in_month(db, sharer_id, now))}


def line(s: dict) -> str:
    return (f"Accounts you referred that bought their first pack: {s['bought']:,} · "
            f"{s['earned']:,} tokens earned ({s['left']} left this month).")


HOW = (f"When someone opens one of your links, makes a new account from it and buys their first pack, "
       f"you get {REWARD} free tokens, for up to {MONTHLY_CAP} accounts a month.")
