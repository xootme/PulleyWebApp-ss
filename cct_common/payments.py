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
# pack is offered by both. Flat 10¢ a token. Override with packs=... .
DEFAULT_PACKS = (
    Pack("paypal", 200, 20), Pack("paypal", 500, 50),
    Pack("stripe", 500, 50), Pack("stripe", 1000, 100), Pack("stripe", 2500, 250),
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


class Payments(SqliteDB):
    """Orders we created, and the ledger effects of their outcomes."""

    def __init__(self, tokens, clock: Callable[[], float] = time.time):
        super().__init__(tokens.path, _SCHEMA)
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

    def by_payment_ref(self, provider: str, ref: str) -> Optional[dict]:
        with self._read() as db:
            row = db.execute("SELECT * FROM payments WHERE provider = ? AND payment_ref = ?",
                             (provider, ref)).fetchone()
        return dict(row) if row else None

    def complete(self, provider: str, order_id: str, *, paid_cents: int, currency: str,
                 payment_ref: str) -> bool:
        """Credit the pack for a confirmed payment. False if it was already
        credited. Refuses (PaymentError) an order we didn't create or a
        payment that isn't exactly the pack's price in USD."""
        order = self.get(provider, order_id)
        if order is None:
            raise PaymentError("unknown order")
        if currency.upper() != "USD" or int(paid_cents) != order["cents"]:
            raise PaymentError(f"paid {paid_cents} {currency}, expected {order['cents']} USD")
        credited = self.tokens.credit(order["account_id"], order["tokens"], kind="purchase",
                                      ref=f"{provider}:{order_id}",
                                      detail=f"{order['tokens']} tokens, {provider} {order_id}")
        with self._write() as db:
            db.execute("UPDATE payments SET status = 'completed', payment_ref = ?, "
                       "completed_at = COALESCE(completed_at, ?) WHERE order_id = ? AND status = 'created'",
                       (payment_ref, self.clock(), order_id))
        return credited

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
        back = share(after) - share(before)
        if back <= 0:
            return 0          # nothing left to take, or less than a token's worth
        if not self.tokens.credit(order["account_id"], -back, kind="adjust",
                                  ref=f"{provider}-refund:{refund_id}",
                                  detail=f"refund of {provider} {order_id}"):
            return 0          # this refund was already applied
        with self._write() as db:
            db.execute("UPDATE payments SET status = 'refunded', refunded_cents = ? "
                       "WHERE order_id = ?", (after, order_id))
        return back


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
        return done[0]

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

    def get_session(self, session_id: str) -> dict:
        r = self.http.get(f"{STRIPE_API}/checkout/sessions/{session_id}",
                          auth=(self.cfg.secret_key, ""), timeout=20)
        r.raise_for_status()
        return r.json()


# ── routes ────────────────────────────────────────────────────────────────

def register_payment_routes(app, accounts, *, packs=DEFAULT_PACKS,
                            paypal: Optional[PayPalConfig] = None,
                            stripe: Optional[StripeConfig] = None,
                            app_name: str = "CheapCAD Tools", http=None,
                            clock: Callable[[], float] = time.time) -> list[str]:
    """Registers the buy page and each provider whose keys are given.
    Returns the providers enabled."""
    from flask import Response, current_app, jsonify, request

    from .account_routes import current_account_id

    if http is None:
        import requests
        http = requests.Session()
    pp = PayPal(paypal, http) if paypal and paypal.client_id and paypal.secret else None
    st = Stripe(stripe, http) if stripe and stripe.secret_key else None
    enabled = [n for n, p in (("paypal", pp), ("stripe", st)) if p]
    packs = tuple(p for p in packs if p.provider in enabled)
    by_id = {p.id: p for p in packs}
    store = Payments(accounts.tokens, clock=clock)
    tokens = accounts.tokens

    def _pack(data) -> Pack:
        pack = by_id.get(str((data or {}).get("pack", "")))
        if pack is None:
            raise PaymentError("That pack isn't on sale.")
        return pack

    def _json_error(e, status=400):
        return jsonify({"error": str(e)}), status

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
                ".note{font-size:.85rem;color:#666}</style></head><body>")
        if not acct:
            return Response(head + f"<h1>Buy {html.escape(app_name)} tokens</h1>"
                            "<p>Sign in first, then come back to this page.</p>"
                            "<p><a href='/'>Go to sign in</a></p></body></html>", mimetype="text/html")
        email = tokens.account_email(acct) or ""
        body = [f"<h1>Buy {html.escape(app_name)} tokens</h1>",
                f"<p>Signed in as <b>{html.escape(email)}</b> · balance "
                f"<b id='bal'>{tokens.balance(acct)}</b> tokens</p>",
                "<p class='note'>2D drawing 1 token · 3D-print (STL) 2 · CAD (STEP) 3. "
                "Tokens you buy never expire.</p><p id='msg' role='status'></p>"]
        if pp:
            body.append("<h2>Pay with PayPal</h2><div class='packs' id='pp-packs'>" + "".join(
                f"<button class='pack' data-pack='{p.id}' aria-pressed='false'><b>{p.price}</b>"
                f"{p.tokens} tokens</button>" for p in packs if p.provider == "paypal")
                + "</div><div id='pp-buttons'></div>")
        if st:
            body.append("<h2>Pay by card</h2><div class='packs'>" + "".join(
                f"<button class='pack stripe' data-pack='{p.id}'><b>{p.price}</b>{p.tokens} tokens"
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
        say('Thank you — ' + d.credited + ' tokens added.', true);
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
                           currency=cap["amount"]["currency_code"], payment_ref=cap["id"])
        except PaymentError as e:
            current_app.logger.warning("PayPal capture %s: %s", order_id, e)
            return _json_error(e)
        except Exception:
            current_app.logger.exception("PayPal capture failed")
            return _json_error("Couldn't confirm the payment with PayPal. If you were charged, "
                               "your tokens will appear shortly.", 502)
        return jsonify({"credited": order["tokens"], "balance": tokens.balance(acct)})

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
                                   currency=res["amount"]["currency_code"], payment_ref=res["id"])
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

    # ── Stripe ──
    def _stripe_complete(session: dict) -> None:
        if session.get("payment_status") != "paid" or session.get("mode") != "payment":
            return
        store.complete("stripe", session["id"], paid_cents=int(session["amount_total"]),
                       currency=session["currency"], payment_ref=session.get("payment_intent") or "")

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
        return redirect("/account/buy?paid=stripe", code=303)

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
    return enabled
