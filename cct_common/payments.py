"""
payments.py — buying token packs (PulleyWebApp-ss ADR-008) through PayPal
and Stripe, credited to the TokenStore ledger.

    register_payment_routes(app, accounts, packs=DEFAULT_PACKS,
        paypal=PayPalConfig(client_id, secret, webhook_id, live=False),
        stripe=StripeConfig(secret_key, webhook_secret))

Routes:
    GET  /account/buy                          the buy page (signed in)
    POST /api/payments/paypal/orders           {pack} -> {id}  (PayPal JS SDK createOrder)
    POST /api/payments/paypal/orders/<id>/capture   -> {balance}  (onApprove)
    POST /api/payments/paypal/webhook          PayPal's signed event notifications
    POST /api/payments/stripe/checkout         {pack} -> {url}  (Stripe-hosted card page)
    GET  /account/buy/stripe-return?session_id=...  back from Stripe
    POST /api/payments/stripe/webhook          Stripe's signed event notifications

The buy panel (0.35.0, the inline_checkout business setting; tokens.js), on
the page the person is on, with no redirect:
    GET  /api/payments/config                  {inline, packs, last_pack, stripe, paypal}
    POST /api/payments/stripe/intent           {pack} -> {id, client_secret}  (a PaymentIntent:
                                               Stripe.js's Express Checkout and Payment Elements)
    POST /api/payments/stripe/intent/<id>/confirm   -> {status: paid, credited, balance}, or
                                               {status: processing | requires_action}, or 402
PayPal's buttons use the PayPal routes above. A PaymentIntent is recorded
under its own id, so the browser's confirm and the payment_intent.succeeded
webhook credit it exactly once, and refunds and disputes find it by its
payment_ref as a Checkout Session's are.

How a payment becomes tokens — the rules every path follows:
- Our server creates each order at our price for a pack we sell, and
  records it (the `payments` table) against the signed-in account. The
  browser never names an amount; a notification is only acted on for an
  order we created, at the amount we recorded.
- Tokens are credited as ledger kind "purchase" with ref
  "<provider>:<order id>". The ledger refuses a second credit with the same
  ref, so the browser's confirmation and the provider's webhook — whichever
  arrives first — credit exactly once between them.
- Webhooks are verified before anything is read from them: PayPal's by
  asking PayPal (verify-webhook-signature, against our webhook id); Stripe's
  by the HMAC in its Stripe-Signature header, within 5 minutes.
- Refunds and chargebacks take back the refunded share of the pack's tokens
  (a negative "adjust", ref'd by the refund id, so also exactly once). The
  balance may go below zero if the tokens were already spent; downloads
  then wait for the next purchase.

Rewards (0.37.0, cct_common.rewards; the rewards business setting, off):
- While on, a purchase's bonus tokens are credited in the same write as
  the pack, exactly once ("bonus:<provider>:<order id>"), and the order
  keeps which rule gave them (bonus_rule, bonus_tokens) and its payer key: a
  hash of Stripe's card fingerprint or PayPal's payer id, read from the
  provider before that write (never holding up the purchase).
- A refund or chargeback of an order with a bonus also takes back the same
  share of it ("bonusback:<provider>:<refund id>", bonus_back), whether or
  not the setting is still on. Off and never on, nothing here changes.

Referrals (0.49.0, cct_common.referrals; the referrals setting, off):
- While on, the first purchase of an account made from a share link pays
  the sharer's referral tokens in the same write as the pack (the order
  keeps them: referral_tokens), and every purchase keeps its payer key, for
  the self-referral check (the payer is asked as with rewards).
- A refund or chargeback of that order takes back the same share of them
  from the sharer, never below zero (referral_back: the share settled),
  whether or not the setting is still on.

Refunding an account (0.52.0, rewards step 8; the owner's design of
2026-10-31): spending isn't tracked per purchase, so which order a refund is
booked against decides how much bonus comes back. account_refund_quote
splits the account's unused purchased tokens across its paid orders newest
first (the oldest tokens are the ones spent), each order at most what it
has left, each with its own bonus and referral take-back. An order counts
the tokens taken back from it (tokens_back): refunded_cents is the money,
after the fee for an admin refund, so it undercounts them. Orders refunded
before 0.52.0 have no count and are estimated from refunded_cents, as before.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .sqlite_db import SqliteDB


@dataclass(frozen=True)
class Pack:
    provider: str      # "paypal" | "stripe"
    cents: int         # price in US cents
    tokens: int

    @property
    def id(self) -> str:
        return f"{self.provider}-{self.cents}"

    @property
    def price(self) -> str:
        return f"${self.cents // 100}" + (f".{self.cents % 100:02d}" if self.cents % 100 else "")


# PayPal (micropayments rate) for the small packs, Stripe from $5 up; the $5
# pack is offered by both. Flat 5¢ a token. The built-in list: what's on sale
# is the packs business setting (settings.py, 0.28.0), or packs=... .
DEFAULT_PACKS = (
    Pack("paypal", 200, 40), Pack("paypal", 500, 100),
    Pack("stripe", 500, 100), Pack("stripe", 1000, 200), Pack("stripe", 2500, 500),
)


def parse_packs(spec: str) -> tuple:
    """'paypal:2=20,5=50;stripe:5=50,10=100' -> Packs (dollars=tokens)."""
    packs = []
    for group in filter(None, (g.strip() for g in spec.split(";"))):
        provider, _, items = group.partition(":")
        for item in filter(None, (i.strip() for i in items.split(","))):
            dollars, _, tokens = item.partition("=")
            packs.append(Pack(provider.strip(), round(float(dollars) * 100), int(tokens)))
    if not packs or any(p.provider not in ("paypal", "stripe") or p.cents < 100 or p.tokens < 1
                        for p in packs):
        raise ValueError(f"bad token pack list: {spec!r}")
    return tuple(packs)


@dataclass
class PayPalConfig:
    client_id: str
    secret: str
    webhook_id: str = ""        # from the PayPal app's webhook settings; needed to verify events
    live: bool = False          # False: the sandbox

    @property
    def api(self) -> str:
        return "https://api-m.paypal.com" if self.live else "https://api-m.sandbox.paypal.com"


@dataclass
class StripeConfig:
    secret_key: str             # sk_test_... or sk_live_...
    webhook_secret: str = ""    # whsec_... ; needed to verify events
    publishable_key: str = ""   # pk_test_... or pk_live_...; the buy panel needs it (0.35.0)


class PaymentError(Exception):
    """Shown to the buyer."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    order_id      TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    account_id    TEXT NOT NULL,
    pack_id       TEXT NOT NULL,
    tokens        INTEGER NOT NULL,
    cents         INTEGER NOT NULL,
    status        TEXT NOT NULL,
    payment_ref   TEXT,
    refunded_cents INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    completed_at  REAL
);
CREATE INDEX IF NOT EXISTS payments_ref ON payments (provider, payment_ref);
CREATE INDEX IF NOT EXISTS payments_account ON payments (account_id, created_at);
"""
# The rewards (0.37.0): who paid (a hash) and the order's bonus. Older
# databases lack them; their orders have no bonus.
_REWARD_COLUMNS = {"payer_key": "TEXT", "bonus_rule": "TEXT",
                   "bonus_tokens": "INTEGER NOT NULL DEFAULT 0", "bonus_back": "INTEGER NOT NULL DEFAULT 0",
                   # 0.49.0: the referral tokens this order paid its sharer, and the share taken back
                   "referral_tokens": "INTEGER NOT NULL DEFAULT 0", "referral_back": "INTEGER NOT NULL DEFAULT 0",
                   # 0.52.0: the tokens taken back by refunds (NULL: none counted yet, see _tokens_back)
                   "tokens_back": "INTEGER"}


def _tokens_back(order: dict) -> int:
    """The tokens refunds have taken back from this order: counted since
    0.52.0, before that estimated from the money refunded."""
    if order.get("tokens_back") is not None:
        return int(order["tokens_back"])
    return round(order["tokens"] * order["refunded_cents"] / order["cents"]) if order["cents"] else 0


def _share_due(order: dict, total: str, back: str, tokens: int) -> int:
    """What refunding `tokens` more of the order takes back of `total` (its
    bonus_tokens or referral_tokens) beyond `back`, the share already taken."""
    whole = int(order.get(total) or 0)
    if not whole or tokens <= 0 or not order["tokens"]:
        return 0
    part = min(order["tokens"], _tokens_back(order) + int(tokens))
    return max(0, min(whole, round(whole * part / order["tokens"])) - int(order.get(back) or 0))


class Payments(SqliteDB):
    """Orders we created, and the ledger effects of their outcomes."""

    def __init__(self, tokens, clock: Callable[[], float] = time.time):
        super().__init__(tokens.path, _SCHEMA)
        self._receipts = None
        self._ensure_columns("payments", _REWARD_COLUMNS)
        with self._write() as db:
            db.execute("CREATE INDEX IF NOT EXISTS payments_payer ON payments (payer_key)")
        self.tokens = tokens
        self.clock = clock

    def record(self, provider: str, order_id: str, account_id: str, pack: Pack) -> None:
        with self._write() as db:
            db.execute("INSERT INTO payments (order_id, provider, account_id, pack_id, tokens, cents, "
                       "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'created', ?)",
                       (order_id, provider, account_id, pack.id, pack.tokens, pack.cents, self.clock()))

    def get(self, provider: str, order_id: str) -> Optional[dict]:
        with self._read() as db:
            row = db.execute("SELECT * FROM payments WHERE provider = ? AND order_id = ?",
                             (provider, order_id)).fetchone()
        return dict(row) if row else None

    def last_pack(self, account_id: str) -> Optional[str]:
        """The pack this account last paid for (the buy panel starts on it)."""
        with self._read() as db:
            row = db.execute("SELECT pack_id FROM payments WHERE account_id = ? AND completed_at IS NOT NULL "
                             "ORDER BY completed_at DESC LIMIT 1", (account_id,)).fetchone()
        return row["pack_id"] if row else None

    def by_payment_ref(self, provider: str, ref: str) -> Optional[dict]:
        with self._read() as db:
            row = db.execute("SELECT * FROM payments WHERE provider = ? AND payment_ref = ?",
                             (provider, ref)).fetchone()
        return dict(row) if row else None

    def complete(self, provider: str, order_id: str, *, paid_cents: int, currency: str,
                 payment_ref: str, payer: Optional[Callable[[], Optional[tuple]]] = None) -> bool:
        """Credit the pack for a confirmed payment. False if it was already
        credited. Refuses (PaymentError) an order we didn't create or a
        payment that isn't exactly the pack's price in USD.
        payer() -> (kind, id) of who paid, or None: asked only while the
        rewards are on, for the first-purchase check, or the referrals, for
        the self-referral check."""
        order = self.get(provider, order_id)
        if order is None:
            raise PaymentError("unknown order")
        if currency.upper() != "USD" or int(paid_cents) != order["cents"]:
            raise PaymentError(f"paid {paid_cents} {currency}, expected {order['cents']} USD")
        from .referrals import referrals_on
        from .rewards import rewards_on
        rewards, referrals = rewards_on(self.tokens.path), referrals_on(self.tokens.path)
        if rewards or referrals:
            credited = self._complete_with_rewards(order, payment_ref, payer, rewards=rewards, referrals=referrals)
            self._receipt()
            return credited
        credited = self.tokens.credit(order["account_id"], order["tokens"], kind="purchase",
                                      ref=f"{provider}:{order_id}",
                                      detail=f"{order['tokens']} tokens, {provider} {order_id}")
        with self._write() as db:
            db.execute("UPDATE payments SET status = 'completed', payment_ref = ?, "
                       "completed_at = COALESCE(completed_at, ?) WHERE order_id = ? AND status = 'created'",
                       (payment_ref, self.clock(), order_id))
        self._receipt()
        return credited

    def receipts(self):
        """The receipts store (0.40.0), or None while the receipts setting is off."""
        from .receipts import Receipts, receipts_on
        if not receipts_on(self.tokens.path):
            return None
        if self._receipts is None:
            self._receipts = Receipts(self.tokens.path, clock=self.tokens._clock)
        return self._receipts

    def _receipt(self) -> None:
        """Number the receipts now due (this order's, and any paid before)."""
        store = self.receipts()
        if store is not None:
            store.issue_pending()

    def _credit_note(self, order: dict, refund_id: str, *, cents: int, tokens: int, bonus: int) -> None:
        store = self.receipts()
        if store is not None:
            store.credit_note(order, f"{order['provider']}-refund:{refund_id}", cents=cents, tokens=tokens, bonus=bonus)

    def _complete_with_rewards(self, order: dict, payment_ref: str, payer, *, rewards: bool = True,
                               referrals: bool = False) -> bool:
        """complete() while the rewards or referrals are on: the pack, its
        bonus and its sharer's referral tokens in one write, so a held reward
        is used by one purchase only, and a month's cap holds."""
        import logging
        from .referrals import on_purchase
        from .rewards import bonus_detail, bonus_for, decide, payer_key
        from .sqlite_db import INTEGRITY_ERRORS
        provider, order_id, acct = order["provider"], order["order_id"], order["account_id"]
        key = None
        if order["status"] == "created" and payer is not None:
            try:
                who = payer()
                key = payer_key(*who) if who else None
            except Exception as e:          # never hold up the purchase: the account check alone
                logging.getLogger(__name__).warning("rewards/referrals: who paid %s %s: %s", provider, order_id, e)
        since = self.tokens.paid_first_since()
        tokens, ref, now = self.tokens, f"{provider}:{order_id}", self.tokens._clock()
        with self._write() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id = ?", (acct,)).fetchone():
                raise KeyError(acct)
            try:
                db.execute("INSERT INTO ledger (account_id, ts, kind, amount, ref, detail) "
                           "VALUES (?, ?, 'purchase', ?, ?, ?)",
                           (acct, now, order["tokens"], ref, f"{order['tokens']} tokens, {provider} {order_id}"))
                credited = True
            except INTEGRITY_ERRORS:
                credited = False
            if credited:
                rule, bonus = None, 0
                if rewards:
                    rule, pct = decide(db, tokens, acct, ref, key, since if since is not None else now)
                    bonus = bonus_for(order["tokens"], pct)
                if bonus:
                    db.execute("INSERT INTO ledger (account_id, ts, kind, amount, ref, detail) "
                               "VALUES (?, ?, 'bonus', ?, ?, ?)",
                               (acct, now, bonus, f"bonus:{provider}:{order_id}",
                                bonus_detail(rule, bonus, provider, order_id)))
                paid = on_purchase(db, acct, ref, key, now) if referrals else 0
                db.execute("UPDATE payments SET payer_key = ?, bonus_rule = ?, bonus_tokens = ?, referral_tokens = ? "
                           "WHERE order_id = ?", (key, rule if bonus else None, bonus, paid, order_id))
            db.execute("UPDATE payments SET status = 'completed', payment_ref = ?, "
                       "completed_at = COALESCE(completed_at, ?) WHERE order_id = ? AND status = 'created'",
                       (payment_ref, self.clock(), order_id))
        return credited

    def _take_bonus_back(self, order: dict, refund_id: str, upto: int) -> int:
        """Take back the order's bonus so that `upto` of it is back in all
        (never more than it gave); returns how many this time. Exactly once
        per refund id."""
        due = min(int(order.get("bonus_tokens") or 0), max(0, int(upto))) - int(order.get("bonus_back") or 0)
        if due <= 0:
            return 0
        provider, order_id = order["provider"], order["order_id"]
        if not self.tokens.credit(order["account_id"], -due, kind="adjust",
                                  ref=f"bonusback:{provider}:{refund_id}",
                                  detail=f"bonus back: refund of {provider} {order_id}"):
            return 0
        with self._write() as db:
            db.execute("UPDATE payments SET bonus_back = bonus_back + ? WHERE order_id = ?", (due, order_id))
        return due

    def _take_referral_back(self, order: dict, refund_id: str, upto: int) -> int:
        """The referrals: take back the order's referral tokens from its
        sharer so that `upto` of them is settled, never below zero."""
        if not order.get("referral_tokens"):
            return 0
        from .referrals import take_back
        with self._write() as db:
            return take_back(db, order, refund_id, upto, self.tokens._clock())

    def refund(self, provider: str, order_id: str, *, refund_id: str, refunded_cents: int) -> int:
        """Take back the tokens for `refunded_cents` more of this order's
        price (one refund or chargeback); returns how many. The order's
        running refunded total caps it, so refunds plus a chargeback never
        take back more than the pack gave; a repeated refund_id takes
        nothing (0 too for an unknown order)."""
        order = self.get(provider, order_id)
        if order is None or refunded_cents <= 0:
            return 0
        before = min(order["cents"], order["refunded_cents"])
        after = min(order["cents"], before + int(refunded_cents))
        share = lambda c: round(order["tokens"] * c / order["cents"])
        back = min(share(after) - share(before), order["tokens"] - _tokens_back(order))
        if back <= 0:
            return 0          # nothing left to take, or less than a token's worth
        if not self.tokens.credit(order["account_id"], -back, kind="adjust",
                                  ref=f"{provider}-refund:{refund_id}",
                                  detail=f"refund of {provider} {order_id}"):
            return 0          # this refund was already applied
        with self._write() as db:
            db.execute("UPDATE payments SET status = 'refunded', refunded_cents = ?, tokens_back = ? "
                       "WHERE order_id = ?", (after, _tokens_back(order) + back, order_id))
        bonus = 0
        if order.get("bonus_tokens"):     # the rewards: the same share of the bonus
            bonus = self._take_bonus_back(order, refund_id, round(order["bonus_tokens"] * after / order["cents"]))
        if order.get("referral_tokens"):  # the referrals: the same share of the sharer's tokens
            self._take_referral_back(order, refund_id, round(order["referral_tokens"] * after / order["cents"]))
        self._credit_note(order, refund_id, cents=after - before, tokens=back, bonus=bonus)
        return back

    # ── admin: listing and refunds by the terms (unused purchased tokens, net of fees) ──

    def list_orders(self, limit: int = 500) -> list[dict]:
        """Orders, newest first, with the buyer's email — for the admin Sales page."""
        with self._read() as db:
            rows = db.execute(
                "SELECT p.*, a.email FROM payments p LEFT JOIN accounts a ON a.id = p.account_id "
                "ORDER BY p.created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def refund_quote(self, provider: str, order_id: str, fee_cents: int = 0) -> dict:
        """What refunding this order owes under the terms: its tokens the
        account still has unused (never more than it has purchased and
        unspent, nor than the order has left unrefunded), valued at the
        price paid, less the processor's fee — capped at what's left of the
        payment. Raises PaymentError for an order that can't be refunded."""
        order = self.get(provider, order_id)
        if order is None or order["status"] not in ("completed", "refunded"):
            raise PaymentError("That order hasn't been paid.")
        unused = self.tokens.purchased_remaining(order["account_id"])
        q = self._part(order, max(0, min(order["tokens"] - _tokens_back(order), unused)), fee_cents)
        # Step 8: what the newest-first split gives the account's newer orders
        # before this one; refunding this one first takes back more bonus.
        newer = 0
        for o, take in self._newest_first(order["account_id"], unused)[0]:
            if o["order_id"] == order_id:
                break
            newer += take
        return {**q, "unused_purchased": unused, "newer_unused": newer}

    def _part(self, order: dict, tokens: int, fee_cents: int) -> dict:
        """Refunding `tokens` of this order: their value at the price paid,
        less the processor's fee, capped at what's left of the payment; and
        the bonus and referral tokens that takes back."""
        fee = max(0, int(fee_cents))
        value = round(tokens * order["cents"] / order["tokens"]) if order["tokens"] else 0
        amount = max(0, min(value - fee, order["cents"] - order["refunded_cents"]))
        return {"provider": order["provider"], "order_id": order["order_id"], "tokens": tokens,
                "value_cents": value, "fee_cents": fee, "amount_cents": amount,
                "bonus_back": _share_due(order, "bonus_tokens", "bonus_back", tokens),
                "referral_back": _share_due(order, "referral_tokens", "referral_back", tokens)}

    def _newest_first(self, account_id: str, unused: int) -> tuple[list, int]:
        """The account's unused purchased tokens on its paid orders, newest
        first, each at most what it has left: [(order, tokens)], and what no
        order holds (tokens bought some other way)."""
        with self._read() as db:
            rows = db.execute(
                "SELECT * FROM payments WHERE account_id = ? AND status IN ('completed', 'refunded') "
                "ORDER BY COALESCE(completed_at, created_at) DESC, created_at DESC, order_id DESC",
                (account_id,)).fetchall()
        left, split = max(0, int(unused)), []
        for r in rows:
            o = dict(r)
            take = min(max(0, o["tokens"] - _tokens_back(o)), left)
            if take > 0:
                split.append((o, take))
                left -= take
        return split, left

    def account_refund_quote(self, account_id: str, fees: Optional[dict] = None) -> dict:
        """Step 8: refunding all of the account's unused purchased tokens,
        newest order first. `fees`: {(provider, order id): cents}, each
        order's processor fee (0 when not given). An order whose fee is more
        than its tokens are worth is listed, refunds nothing and keeps them."""
        fees = fees or {}
        unused = self.tokens.purchased_remaining(account_id)
        split, left = self._newest_first(account_id, unused)
        parts = []
        for o, take in split:
            part = self._part(o, take, fees.get((o["provider"], o["order_id"]), 0))
            parts.append({**part, "completed_at": o["completed_at"] or o["created_at"],
                          "pack_tokens": o["tokens"], "cents": o["cents"]})
        paid = [p for p in parts if p["amount_cents"] > 0]
        return {"account_id": account_id, "unused_purchased": unused, "parts": parts, "unbooked": left,
                "tokens": sum(p["tokens"] for p in paid), "amount_cents": sum(p["amount_cents"] for p in paid),
                "bonus_back": sum(p["bonus_back"] for p in paid),
                "referral_back": sum(p["referral_back"] for p in paid)}

    def record_admin_refund(self, provider: str, order_id: str, *, refund_id: str,
                            amount_cents: int, tokens: int, admin: str) -> bool:
        """Book a refund the admin made through the provider: take back
        exactly `tokens` and add the money to the order's refunded total.
        The ledger ref is the one the provider's refund webhook uses
        ("<provider>-refund:<refund id>"), and the refunded total is what
        Stripe's webhook compares against, so the notification that follows
        takes nothing more. False if this refund was already booked."""
        order = self.get(provider, order_id)
        if order is None:
            raise PaymentError("unknown order")
        if tokens > 0 and not self.tokens.credit(
                order["account_id"], -int(tokens), kind="adjust",
                ref=f"{provider}-refund:{refund_id}",
                detail=f"refund of {provider} {order_id} by {admin}"):
            return False
        bonus = 0
        if tokens > 0:              # the rewards and referrals: the same share of each
            part = min(order["tokens"], _tokens_back(order) + int(tokens))
            if order.get("bonus_tokens"):
                bonus = self._take_bonus_back(order, refund_id, round(order["bonus_tokens"] * part / order["tokens"]))
            if order.get("referral_tokens"):
                self._take_referral_back(order, refund_id, round(order["referral_tokens"] * part / order["tokens"]))
        with self._write() as db:
            db.execute("UPDATE payments SET status = 'refunded', tokens_back = ?, "
                       "refunded_cents = MIN(cents, refunded_cents + ?) WHERE order_id = ?"
                       if not self.is_postgres else
                       "UPDATE payments SET status = 'refunded', tokens_back = ?, "
                       "refunded_cents = LEAST(cents, refunded_cents + ?) WHERE order_id = ?",
                       (_tokens_back(order) + max(0, int(tokens)), int(amount_cents), order_id))
        self._credit_note(order, refund_id, cents=min(int(amount_cents), order["cents"] - order["refunded_cents"]),
                          tokens=int(tokens), bonus=bonus)
        return True


def _cents(value: str) -> int:
    return round(float(value) * 100)


# ── PayPal ────────────────────────────────────────────────────────────────

class PayPal:
    def __init__(self, cfg: PayPalConfig, http):
        self.cfg, self.http = cfg, http
        self._token, self._expiry = "", 0.0
        self._lock = threading.Lock()

    def _auth(self) -> dict:
        with self._lock:
            if time.time() > self._expiry - 60:
                r = self.http.post(f"{self.cfg.api}/v1/oauth2/token",
                                   auth=(self.cfg.client_id, self.cfg.secret),
                                   data={"grant_type": "client_credentials"}, timeout=15)
                r.raise_for_status()
                body = r.json()
                self._token = body["access_token"]
                self._expiry = time.time() + float(body.get("expires_in", 300))
            return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def create_order(self, pack: Pack, account_id: str, app_name: str) -> str:
        body = {"intent": "CAPTURE", "purchase_units": [{
            "reference_id": pack.id, "custom_id": account_id,
            "description": f"{pack.tokens} {app_name} tokens",
            "amount": {"currency_code": "USD", "value": f"{pack.cents / 100:.2f}"}}],
            "application_context": {"shipping_preference": "NO_SHIPPING", "brand_name": app_name}}
        r = self.http.post(f"{self.cfg.api}/v2/checkout/orders", headers=self._auth(),
                           data=json.dumps(body), timeout=15)
        r.raise_for_status()
        return r.json()["id"]

    def capture(self, order_id: str) -> dict:
        """The completed capture ({id, amount}), or PaymentError."""
        r = self.http.post(f"{self.cfg.api}/v2/checkout/orders/{order_id}/capture",
                           headers=self._auth(), data="{}", timeout=30)
        body = r.json() if r.content else {}
        if r.status_code == 422 and any(d.get("issue") == "ORDER_ALREADY_CAPTURED"
                                        for d in body.get("details", [])):
            r = self.http.get(f"{self.cfg.api}/v2/checkout/orders/{order_id}",
                              headers=self._auth(), timeout=15)
            body = r.json()
        if r.status_code >= 400:
            raise PaymentError("PayPal didn't take the payment. You haven't been charged.")
        captures = [c for u in body.get("purchase_units", [])
                    for c in u.get("payments", {}).get("captures", [])]
        done = [c for c in captures if c.get("status") == "COMPLETED"]
        if not done:
            raise PaymentError("PayPal hasn't completed this payment yet. If you were charged, "
                               "your tokens will appear once it clears.")
        return dict(done[0], payer_id=(body.get("payer") or {}).get("payer_id"))

    def payer_of(self, order_id: str) -> Optional[tuple]:
        """("paypal-payer", the order's payer id), or None (the rewards' check)."""
        r = self.http.get(f"{self.cfg.api}/v2/checkout/orders/{order_id}", headers=self._auth(), timeout=15)
        r.raise_for_status()
        payer_id = (r.json().get("payer") or {}).get("payer_id")
        return ("paypal-payer", payer_id) if payer_id else None

    def verify_webhook(self, headers, event: dict) -> bool:
        if not self.cfg.webhook_id:
            return False
        h = {k.lower(): v for k, v in headers.items()}
        body = {"auth_algo": h.get("paypal-auth-algo"), "cert_url": h.get("paypal-cert-url"),
                "transmission_id": h.get("paypal-transmission-id"),
                "transmission_sig": h.get("paypal-transmission-sig"),
                "transmission_time": h.get("paypal-transmission-time"),
                "webhook_id": self.cfg.webhook_id, "webhook_event": event}
        if not all(body.values()):
            return False
        r = self.http.post(f"{self.cfg.api}/v1/notifications/verify-webhook-signature",
                           headers=self._auth(), data=json.dumps(body), timeout=15)
        return r.status_code == 200 and r.json().get("verification_status") == "SUCCESS"

    def capture_fee(self, capture_id: str) -> Optional[int]:
        """PayPal's fee on a capture, in cents (None if PayPal doesn't say)."""
        r = self.http.get(f"{self.cfg.api}/v2/payments/captures/{capture_id}",
                          headers=self._auth(), timeout=15)
        if r.status_code >= 400:
            return None
        fee = r.json().get("seller_receivable_breakdown", {}).get("paypal_fee", {}).get("value")
        return _cents(fee) if fee is not None else None

    def refund_capture(self, capture_id: str, cents: int) -> str:
        """Refund part or all of a capture; returns PayPal's refund id."""
        body = {"amount": {"currency_code": "USD", "value": f"{cents / 100:.2f}"}}
        r = self.http.post(f"{self.cfg.api}/v2/payments/captures/{capture_id}/refund",
                           headers=self._auth(), data=json.dumps(body), timeout=30)
        if r.status_code >= 400:
            raise PaymentError(f"PayPal refused the refund ({r.status_code}): {r.text[:300]}")
        return r.json()["id"]


# ── Stripe ────────────────────────────────────────────────────────────────

STRIPE_API = "https://api.stripe.com/v1"
STRIPE_TOLERANCE_S = 300


def stripe_signature_ok(payload: bytes, header: str, secret: str, now: float) -> bool:
    """Stripe-Signature: t=<unix>,v1=<hex hmac-sha256 of "<t>.<payload>">."""
    if not secret or not header:
        return False
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    ts = next((v for k, v in parts if k == "t"), None)
    sigs = [v for k, v in parts if k == "v1"]
    if not ts or not sigs or not ts.isdigit() or abs(now - int(ts)) > STRIPE_TOLERANCE_S:
        return False
    want = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, s) for s in sigs)


class Stripe:
    def __init__(self, cfg: StripeConfig, http):
        self.cfg, self.http = cfg, http

    def _post(self, path: str, data: dict) -> dict:
        r = self.http.post(f"{STRIPE_API}{path}", auth=(self.cfg.secret_key, ""), data=data, timeout=20)
        if r.status_code >= 400:
            raise PaymentError("Stripe couldn't start the payment. You haven't been charged.")
        return r.json()

    def create_session(self, pack: Pack, account_id: str, email: str, app_name: str,
                       base_url: str) -> dict:
        return self._post("/checkout/sessions", {
            "mode": "payment",
            "client_reference_id": account_id,
            "customer_email": email,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(pack.cents),
            "line_items[0][price_data][product_data][name]": f"{pack.tokens} {app_name} tokens",
            "metadata[pack]": pack.id,
            "payment_intent_data[metadata][pack]": pack.id,
            "success_url": f"{base_url}account/buy/stripe-return?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base_url}account/buy",
        })

    def create_intent(self, pack: Pack, account_id: str, email: str, app_name: str) -> dict:
        """A PaymentIntent for the buy panel: every method the Stripe account
        has turned on (cards, Apple Pay, Google Pay, Link)."""
        data = {
            "amount": str(pack.cents), "currency": "usd",
            "automatic_payment_methods[enabled]": "true",
            "description": f"{pack.tokens} {app_name} tokens",
            "metadata[pack]": pack.id, "metadata[account]": account_id,
        }
        if email:
            data["receipt_email"] = email
        return self._post("/payment_intents", data)

    def get_intent(self, intent_id: str) -> dict:
        r = self.http.get(f"{STRIPE_API}/payment_intents/{intent_id}", auth=(self.cfg.secret_key, ""), timeout=20)
        r.raise_for_status()
        return r.json()

    def card_of(self, payment_intent: str) -> Optional[tuple]:
        """("stripe-card", the card's fingerprint) for a payment, or None (Link
        and other methods without one; the rewards' check)."""
        r = self.http.get(f"{STRIPE_API}/payment_intents/{payment_intent}",
                          params={"expand[]": "latest_charge"}, auth=(self.cfg.secret_key, ""), timeout=20)
        r.raise_for_status()
        charge = r.json().get("latest_charge") or {}
        card = ((charge.get("payment_method_details") or {}).get("card") or {}) if isinstance(charge, dict) else {}
        return ("stripe-card", card["fingerprint"]) if card.get("fingerprint") else None

    def get_session(self, session_id: str) -> dict:
        r = self.http.get(f"{STRIPE_API}/checkout/sessions/{session_id}",
                          auth=(self.cfg.secret_key, ""), timeout=20)
        r.raise_for_status()
        return r.json()

    def intent_fee(self, payment_intent: str) -> Optional[int]:
        """Stripe's fee on a payment, in cents (None if not available yet)."""
        r = self.http.get(f"{STRIPE_API}/payment_intents/{payment_intent}",
                          params={"expand[]": "latest_charge.balance_transaction"},
                          auth=(self.cfg.secret_key, ""), timeout=20)
        if r.status_code >= 400:
            return None
        bt = (r.json().get("latest_charge") or {}).get("balance_transaction") or {}
        return int(bt["fee"]) if isinstance(bt, dict) and "fee" in bt else None

    def refund(self, payment_intent: str, cents: int) -> str:
        """Refund part or all of a payment; returns Stripe's refund id."""
        r = self.http.post(f"{STRIPE_API}/refunds", auth=(self.cfg.secret_key, ""),
                           data={"payment_intent": payment_intent, "amount": str(int(cents))},
                           timeout=30)
        if r.status_code >= 400:
            raise PaymentError(f"Stripe refused the refund ({r.status_code}): {r.text[:300]}")
        return r.json()["id"]


# ── routes ────────────────────────────────────────────────────────────────

def packs_from_setting(value: dict) -> tuple:
    """The packs business setting ({provider: [{cents, tokens}]}) as Packs."""
    return tuple(Pack(provider, int(p["cents"]), int(p["tokens"]))
                 for provider, items in (value or {}).items() for p in items)


def register_payment_routes(app, accounts, *, packs=None,
                            paypal: Optional[PayPalConfig] = None,
                            stripe: Optional[StripeConfig] = None,
                            app_name: str = "CheapCADTools", http=None,
                            clock: Callable[[], float] = time.time) -> list[str]:
    """Registers the buy page and each provider whose keys are given.
    Returns the providers enabled. packs: fixed packs for this app; None
    (the default) is the packs business setting, read per request (0.28.0)."""
    from flask import Response, current_app, jsonify, request

    from .account_routes import current_account_id

    if http is None:
        import requests
        http = requests.Session()
    pp = PayPal(paypal, http) if paypal and paypal.client_id and paypal.secret else None
    st = Stripe(stripe, http) if stripe and stripe.secret_key else None
    enabled = [n for n, p in (("paypal", pp), ("stripe", st)) if p]
    fixed = None if packs is None else tuple(packs)

    def _packs() -> tuple:
        """What's on sale now: the fixed packs, else the packs setting; only enabled providers."""
        if fixed is not None:
            source = fixed
        else:
            from .settings import setting
            source = packs_from_setting(setting(accounts.tokens.path, "packs"))
        return tuple(p for p in source if p.provider in enabled)
    store = Payments(accounts.tokens, clock=clock)
    tokens = accounts.tokens
    # For the admin dashboard (admin.py): the orders and the provider clients.
    app.extensions["cct_payments"] = {"store": store, "paypal": pp, "stripe": st}

    def _pack(data) -> Pack:
        pack = {p.id: p for p in _packs()}.get(str((data or {}).get("pack", "")))
        if pack is None:
            raise PaymentError("That pack isn't on sale.")
        return pack

    def _json_error(e, status=400):
        return jsonify({"error": str(e)}), status

    def _rewards_on() -> bool:
        from .rewards import rewards_on
        return rewards_on(tokens.path)

    def _offer(acct) -> dict:
        """Step 6 (0.51.0): what this account's next pack comes with, while the rewards are on."""
        from .rewards import offer
        with tokens._read() as db:
            return offer(db, tokens, acct, tokens.paid_first_since())

    def _paid_words(provider: str, order_id: str) -> dict:
        """Step 6: {"bonus", "first_used"?} for the line after paying, while the rewards are on."""
        if not _rewards_on():
            return {}
        from .rewards import after_paying
        with tokens._read() as db:
            return after_paying(db, store.get(provider, order_id))

    def _signed_in():
        acct = current_account_id()
        return acct, (None if acct else (jsonify({"error": "Sign in to buy tokens.",
                                                  "code": "SIGN_IN_REQUIRED"}), 401))

    # ── the buy page ──
    def buy_page():
        acct = current_account_id()
        head = ("<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>Buy tokens — {html.escape(app_name)}</title><style>"
                "body{font-family:system-ui,sans-serif;max-width:34rem;margin:3rem auto;padding:0 1rem;"
                "line-height:1.5;color:#222}h1{font-size:1.5rem}h2{font-size:1.1rem;margin-top:2rem}"
                ".packs{display:flex;gap:.6rem;flex-wrap:wrap;margin:.6rem 0 1rem}"
                ".pack{border:1px solid #ccc;border-radius:6px;padding:.6rem 1rem;background:#fff;"
                "cursor:pointer;font:inherit;text-align:center;min-width:6.5rem}"
                ".pack b{display:block;font-size:1.15rem}.pack[aria-pressed=true]{border-color:#0078d4;"
                "box-shadow:0 0 0 2px #0078d4}#msg{min-height:1.4em;color:#b00020}.ok{color:#11702b!important}"
                ".note{font-size:.85rem;color:#666}.offer{margin:.8rem 0 0}</style></head><body>")
        if not acct:
            return Response(head + f"<h1>Buy {html.escape(app_name)} tokens</h1>"
                            "<p>Sign in first, then come back to this page.</p>"
                            "<p><a href='/'>Go to sign in</a></p></body></html>", mimetype="text/html")
        email = tokens.account_email(acct) or ""
        packs, prices = _packs(), tokens.tier_prices()
        # Step 6 (0.51.0), while the rewards are on: the offer above the packs, the
        # bonus on each, and the line after a card payment comes back.
        from .rewards import bonus_for, offer_lines, thanks
        o = _offer(acct) if _rewards_on() else {"pct": 0, "first": False}
        label = lambda p: (f"<b>{p.price}</b>{p.tokens} tokens"
                           + (f" + {bonus_for(p.tokens, o['pct'])} bonus" if bonus_for(p.tokens, o["pct"]) else ""))
        lines = offer_lines(o)
        offer_html = (f"<p class='offer'><b>{html.escape(lines[0])}</b>"
                      + (f"<br><span class='note'>{html.escape(lines[1])}</span>" if lines[1] else "") + "</p>"
                      if lines else "")
        back = ""
        sid = request.args.get("session_id", "") if request.args.get("paid") == "stripe" else ""
        if sid and _rewards_on():
            order = store.get("stripe", sid)
            if order and order["account_id"] == acct and order["status"] in ("completed", "refunded"):
                back = f"<p class='ok'>{html.escape(thanks(order['tokens'], _paid_words('stripe', sid)))}</p>"
        body = [f"<h1>Buy {html.escape(app_name)} tokens</h1>",
                f"<p>Signed in as <b>{html.escape(email)}</b> · balance "
                f"<b id='bal'>{tokens.balance(acct)}</b> tokens</p>",
                f"<p class='note'>2D drawing {prices['2d']} tokens · 3D-print (STL) {prices['stl']} · "
                f"CAD (STEP) {prices['step']}. "
                "Tokens last as long as you sign in at least once every 5 years."
                f"</p>{back}{offer_html}<p id='msg' role='status'></p>"]
        if pp:
            body.append("<h2>Pay with PayPal</h2><div class='packs' id='pp-packs'>" + "".join(
                f"<button class='pack' data-pack='{p.id}' aria-pressed='false'>{label(p)}"
                f"</button>" for p in packs if p.provider == "paypal")
                + "</div><div id='pp-buttons'></div>")
        if st:
            body.append("<h2>Pay by card</h2><div class='packs'>" + "".join(
                f"<button class='pack stripe' data-pack='{p.id}'>{label(p)}"
                f"</button>" for p in packs if p.provider == "stripe")
                + "</div><p class='note'>Card payments go through Stripe's secure page.</p>")
        if not enabled:
            body.append("<p>Buying tokens isn't open yet.</p>")
        body.append("<p><a href='/'>Back to the app</a></p>")
        script = """<script>
const msg = document.getElementById('msg');
const say = (t, ok) => { msg.textContent = t; msg.className = ok ? 'ok' : ''; };
async function post(url, data) {
  const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data || {})});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || ('Something went wrong (' + r.status + ')'));
  return d;
}
document.querySelectorAll('.pack.stripe').forEach(b => b.addEventListener('click', async () => {
  say('Opening the card payment page…', true);
  try { location.href = (await post('/api/payments/stripe/checkout', {pack: b.dataset.pack})).url; }
  catch (e) { say(e.message); }
}));
let ppPack = null;
document.querySelectorAll('#pp-packs .pack').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('#pp-packs .pack').forEach(x => x.setAttribute('aria-pressed', x === b));
  ppPack = b.dataset.pack; say('');
}));
function paypalButtons() {
  if (!window.paypal) return;
  paypal.Buttons({
    onClick: (data, actions) => { if (!ppPack) { say('Choose a pack first.'); return actions.reject(); } },
    createOrder: async () => (await post('/api/payments/paypal/orders', {pack: ppPack})).id,
    onApprove: async data => {
      try {
        const d = await post('/api/payments/paypal/orders/' + encodeURIComponent(data.orderID) + '/capture');
        document.getElementById('bal').textContent = d.balance;
        say(d.bonus ? 'Thank you — ' + d.credited + ' tokens added, plus ' + d.bonus + ' bonus tokens.'
            : d.first_used ? 'Thank you — ' + d.credited + ' tokens added. This card or PayPal account '
              + 'has already had its first-purchase bonus.'
            : 'Thank you — ' + d.credited + ' tokens added.', true);
      } catch (e) { say(e.message); }
    },
    onError: () => say('PayPal ran into a problem. You haven\\'t been charged.'),
  }).render('#pp-buttons');
}
</script>"""
        sdk = ""
        if pp:
            sdk = (f"<script src='https://www.paypal.com/sdk/js?client-id={html.escape(pp.cfg.client_id)}"
                   "&currency=USD&intent=capture&components=buttons' onload='paypalButtons()'></script>")
        page = head + "".join(body) + script + sdk + "</body></html>"
        r = Response(page, mimetype="text/html")
        r.headers["Cache-Control"] = "no-store"
        return r

    # ── PayPal ──
    def paypal_create():
        acct, err = _signed_in()
        if err:
            return err
        try:
            pack = _pack(request.get_json(silent=True))
            if pack.provider != "paypal":
                raise PaymentError("That pack isn't sold through PayPal.")
            order_id = pp.create_order(pack, acct, app_name)
        except PaymentError as e:
            return _json_error(e)
        except Exception:
            current_app.logger.exception("PayPal create order failed")
            return _json_error("Couldn't reach PayPal. Please try again.", 502)
        store.record("paypal", order_id, acct, pack)
        return jsonify({"id": order_id})

    def paypal_capture(order_id):
        acct, err = _signed_in()
        if err:
            return err
        order = store.get("paypal", order_id)
        if order is None or order["account_id"] != acct:
            return _json_error("Unknown order.", 404)
        try:
            cap = pp.capture(order_id)
            store.complete("paypal", order_id, paid_cents=_cents(cap["amount"]["value"]),
                           currency=cap["amount"]["currency_code"], payment_ref=cap["id"],
                           payer=lambda: ("paypal-payer", cap["payer_id"]) if cap.get("payer_id") else None)
        except PaymentError as e:
            current_app.logger.warning("PayPal capture %s: %s", order_id, e)
            return _json_error(e)
        except Exception:
            current_app.logger.exception("PayPal capture failed")
            return _json_error("Couldn't confirm the payment with PayPal. If you were charged, "
                               "your tokens will appear shortly.", 502)
        return jsonify({"credited": order["tokens"], "balance": tokens.balance(acct),
                        **_paid_words("paypal", order_id)})

    def paypal_webhook():
        event = request.get_json(silent=True) or {}
        if not pp.verify_webhook(request.headers, event):
            current_app.logger.warning("PayPal webhook with a bad signature ignored")
            return "", 400
        kind, res = event.get("event_type", ""), event.get("resource", {})
        try:
            if kind == "PAYMENT.CAPTURE.COMPLETED":
                order_id = (res.get("supplementary_data", {}).get("related_ids", {}).get("order_id"))
                if order_id and store.get("paypal", order_id):
                    store.complete("paypal", order_id, paid_cents=_cents(res["amount"]["value"]),
                                   currency=res["amount"]["currency_code"], payment_ref=res["id"],
                                   payer=lambda: pp.payer_of(order_id))
            elif kind in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
                if kind == "PAYMENT.CAPTURE.REFUNDED":   # resource: the refund; "up" links its capture
                    up = next((l["href"] for l in res.get("links", []) if l.get("rel") == "up"), "")
                    capture_id, refund_id = up.rstrip("/").rsplit("/", 1)[-1], res["id"]
                else:                                    # resource: the reversed capture itself
                    capture_id, refund_id = res["id"], "reversal-" + res["id"]
                order = store.by_payment_ref("paypal", capture_id)
                if order:
                    store.refund("paypal", order["order_id"], refund_id=refund_id,
                                 refunded_cents=_cents(res["amount"]["value"]))
        except PaymentError as e:
            current_app.logger.error("PayPal webhook %s refused: %s", kind, e)
        return "", 200

    # ── the buy panel (0.35.0) ──
    def _inline() -> bool:
        from .settings import setting
        return setting(tokens.path, "inline_checkout") is True

    def checkout_config():
        """What the buy panel offers this account: the packs on sale, the one
        it last bought, and each provider's public key."""
        acct, err = _signed_in()
        if err:
            return err
        if not _inline():
            return jsonify({"inline": False})
        packs = _packs()
        return jsonify({
            "inline": True,
            "packs": [{"id": p.id, "provider": p.provider, "cents": p.cents, "tokens": p.tokens, "price": p.price}
                      for p in packs],
            "last_pack": store.last_pack(acct),
            "balance": tokens.balance(acct),
            "stripe": {"publishable_key": st.cfg.publishable_key} if st and st.cfg.publishable_key else None,
            "paypal": {"client_id": pp.cfg.client_id} if pp else None,
            **({"rewards": _offer(acct)} if _rewards_on() else {}),     # step 6 (0.51.0)
        })

    def stripe_intent():
        acct, err = _signed_in()
        if err:
            return err
        if not _inline():
            return _json_error("not found", 404)
        try:
            pack = _pack(request.get_json(silent=True))
            if pack.provider != "stripe":
                raise PaymentError("That pack isn't sold by card.")
            intent = st.create_intent(pack, acct, tokens.account_email(acct) or "", app_name)
        except PaymentError as e:
            return _json_error(e)
        except Exception:
            current_app.logger.exception("Stripe payment intent failed")
            return _json_error("Couldn't reach the card payment service. Please try again.", 502)
        store.record("stripe", intent["id"], acct, pack)
        return jsonify({"id": intent["id"], "client_secret": intent["client_secret"]})

    def _intent_complete(intent: dict) -> bool:
        """Credit a PaymentIntent that succeeded; False if it was already."""
        return store.complete("stripe", intent["id"], paid_cents=int(intent.get("amount_received") or 0),
                              currency=intent.get("currency") or "", payment_ref=intent["id"],
                              payer=lambda: st.card_of(intent["id"]))

    def stripe_intent_confirm(intent_id):
        # Don't wait for the webhook: ask Stripe now, so the tokens are there
        # when the panel closes. The webhook credits it otherwise (exactly once).
        acct, err = _signed_in()
        if err:
            return err
        order = store.get("stripe", intent_id)
        if order is None or order["account_id"] != acct:
            return _json_error("Unknown order.", 404)
        try:
            intent = st.get_intent(intent_id)
            if intent.get("status") == "succeeded":
                _intent_complete(intent)
                return jsonify({"status": "paid", "credited": order["tokens"], "balance": tokens.balance(acct),
                                **_paid_words("stripe", intent_id)})
        except PaymentError as e:
            current_app.logger.warning("Stripe intent %s: %s", intent_id, e)
            return _json_error(e)
        except Exception:
            current_app.logger.exception("Stripe intent check failed")
            return _json_error("Couldn't confirm the payment with Stripe. If you were charged, "
                               "your tokens will appear shortly.", 502)
        status = intent.get("status", "")
        if status in ("processing", "requires_action", "requires_confirmation"):
            return jsonify({"status": status}), 202
        return _json_error("The payment didn't go through. You haven't been charged.", 402)

    # ── Stripe ──
    def _stripe_complete(session: dict) -> None:
        if session.get("payment_status") != "paid" or session.get("mode") != "payment":
            return
        intent = session.get("payment_intent") or ""
        store.complete("stripe", session["id"], paid_cents=int(session["amount_total"]),
                       currency=session["currency"], payment_ref=intent,
                       payer=lambda: st.card_of(intent) if intent else None)

    def stripe_checkout():
        acct, err = _signed_in()
        if err:
            return err
        try:
            pack = _pack(request.get_json(silent=True))
            if pack.provider != "stripe":
                raise PaymentError("That pack isn't sold by card.")
            session = st.create_session(pack, acct, tokens.account_email(acct) or "", app_name,
                                        request.url_root)
        except PaymentError as e:
            return _json_error(e)
        except Exception:
            current_app.logger.exception("Stripe checkout failed")
            return _json_error("Couldn't reach the card payment service. Please try again.", 502)
        store.record("stripe", session["id"], acct, pack)
        return jsonify({"url": session["url"]})

    def stripe_return():
        # Don't wait for the webhook: ask Stripe now, so the tokens are there
        # when the page loads. The webhook credits it otherwise (exactly once).
        sid = request.args.get("session_id", "")
        order = store.get("stripe", sid) if sid else None
        if order and order["account_id"] == current_account_id():
            try:
                _stripe_complete(st.get_session(sid))
            except Exception:
                current_app.logger.exception("Stripe session check failed")
        from flask import redirect
        # Step 6: the buy page says what was added (and any bonus) while the rewards are on.
        from urllib.parse import quote
        return redirect("/account/buy?paid=stripe" + (f"&session_id={quote(sid)}" if sid and _rewards_on() else ""),
                        code=303)

    def stripe_webhook():
        payload = request.get_data()
        if not stripe_signature_ok(payload, request.headers.get("Stripe-Signature", ""),
                                   st.cfg.webhook_secret, clock()):
            current_app.logger.warning("Stripe webhook with a bad signature ignored")
            return "", 400
        event = json.loads(payload)
        kind, obj = event.get("type", ""), event.get("data", {}).get("object", {})
        try:
            if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
                if store.get("stripe", obj.get("id", "")):
                    _stripe_complete(obj)
            elif kind == "payment_intent.succeeded":
                if store.get("stripe", obj.get("id", "")):          # the buy panel's (a session's isn't)
                    _intent_complete(obj)
            elif kind == "charge.refunded":
                order = store.by_payment_ref("stripe", obj.get("payment_intent") or "")
                if order:
                    # amount_refunded is the charge's running total: act on
                    # what's new since the last refund we applied.
                    total = int(obj.get("amount_refunded", 0))
                    store.refund("stripe", order["order_id"],
                                 refund_id=f"{obj['id']}:{total}",
                                 refunded_cents=total - order["refunded_cents"])
            elif kind == "charge.dispute.created":
                order = store.by_payment_ref("stripe", obj.get("payment_intent") or "")
                if order:
                    store.refund("stripe", order["order_id"], refund_id="dispute-" + obj["id"],
                                 refunded_cents=int(obj["amount"]))
        except PaymentError as e:
            current_app.logger.error("Stripe webhook %s refused: %s", kind, e)
        return "", 200

    app.add_url_rule("/account/buy", view_func=buy_page, methods=["GET"])
    if pp:
        app.add_url_rule("/api/payments/paypal/orders", view_func=paypal_create, methods=["POST"])
        app.add_url_rule("/api/payments/paypal/orders/<order_id>/capture",
                         view_func=paypal_capture, methods=["POST"])
        app.add_url_rule("/api/payments/paypal/webhook", view_func=paypal_webhook, methods=["POST"])
    if st:
        app.add_url_rule("/api/payments/stripe/checkout", view_func=stripe_checkout, methods=["POST"])
        app.add_url_rule("/account/buy/stripe-return", view_func=stripe_return, methods=["GET"])
        app.add_url_rule("/api/payments/stripe/webhook", view_func=stripe_webhook, methods=["POST"])
        app.add_url_rule("/api/payments/stripe/intent", view_func=stripe_intent, methods=["POST"])
        app.add_url_rule("/api/payments/stripe/intent/<intent_id>/confirm", view_func=stripe_intent_confirm,
                         methods=["POST"])
    app.add_url_rule("/api/payments/config", view_func=checkout_config, methods=["GET"])
    from .receipts import register_receipts
    register_receipts(app, store)                     # 0.40.0, behind the receipts setting
    return enabled
