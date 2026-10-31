"""
admin.py — the CCT admin dashboard, shared by every tool that uses the
accounts and token ledger (they share one database, so one dashboard sees
every tool's accounts).

    from cct_common.admin import register_admin
    register_admin(app, accounts, app_name="Timing Pulleys", app_version=APP_VERSION,
                   bug_reports=bug_store, default_app="pulleys",
                   app_url="https://cheapcadtools.com/tools/pulleys")

Sign-in is the normal account sign-in (/account/sign-in); only accounts
whose email is in ADMIN_EMAILS (comma-separated; default DEFAULT_ADMINS)
get in. Anyone else gets a 404 from the JSON routes; the page itself tells
a signed-in non-admin which account they're signed in as (403). Browser sessions only (not add-in / agent device tokens), and
every change must come from the page's own origin.

Pages (one page, admin_page.html, tabs):
  Subscribers   accounts: first subscribed, last sign-in, tokens now (free /
                purchased), lifetime tokens, last 5 uses; grant / adjust;
                the newsletter's count and Export CSV (0.36.0, while
                newsletter_opt_in is on or anyone is subscribed); from the
                rewards' switch date (0.37.0), each account's paid tokens
                spent, held reward and unused bonus, and the total unused;
                referrals (0.49.0, while on or any are recorded): each one's
                sharer, new account, tool, when, outcome and tokens, and the
                referral tokens paid this UTC month
  Sales         token-pack orders; refund by the terms; each order's bonus
  Bug reports   the app's bug-report store (if given); delete
  Status        version, Cloud Run revision, database, counts; every
                service's cct_common version and hash (0.26.0)
  Server use    billed hours and CPU by category, tool, action and day, beside
                Google's own billable time per service (0.27.0, server_use)
  Notices       account notices (0.50.0, cct_common.notices): each one's
                subject and body, the date for [DATE], who it goes to and
                how far it got; "Send me a test"; "Send to all" after SEND
  Business      the business settings (cct_common.settings, 0.28.0): signup
  settings      grant, tier prices, packs per provider, limits, buy link;
                edit with a note; every change kept (who, when, old, new)

Routes (JSON under /admin/api/, not /api/admin/ — that prefix carries the old
dashboard's cross-origin headers):
  GET    /admin
  GET    /admin/api/accounts
  GET    /admin/api/newsletter.csv               subscribers: email, since, app, label version
  POST   /admin/api/accounts/<id>/grant          {amount, reason}
  GET    /admin/api/orders
  GET    /admin/api/orders/<provider>/<id>/refund-quote[?fee_cents=N]
  POST   /admin/api/orders/<provider>/<id>/refund {fee_cents}
  GET    /admin/api/accounts/<id>/refund-quote[?fees={"<provider>:<id>": N}]   (0.52.0)
  POST   /admin/api/accounts/<id>/refund {parts: [{provider, order_id, tokens, fee_cents}]}
  GET    /admin/api/bugs
  POST   /admin/api/bugs/<id>/status                {status, fixed_in, note, email}  (0.44.0, my_reports)
  DELETE /admin/api/bugs/<id>
  GET    /admin/api/status
  GET    /admin/api/services                     each service's /api/health
  GET    /admin/api/server-use[?days=N]          server_use rows by day + Google's totals
  GET    /admin/api/notices                      each notice: text, recipients, sent, failed, left
  POST   /admin/api/notices/<id>/test            {date}: one copy to the signed-in admin
  POST   /admin/api/notices/<id>/start           {date, confirm: "SEND"} -> {pass, ...}
  POST   /admin/api/notices/<id>/batch           {pass}: the next 10 -> {sent, failed, left, done}
  GET    /admin/api/settings                     the business settings and their history
  POST   /admin/api/settings/<key>               {value, note}

Services are CCT_SERVICES (name=url, comma-separated), else DEFAULT_SERVICES;
each is asked for <url>/api/health, with a short timeout, all at once.

Refunds follow the terms (§4): the account's unused *purchased* tokens from
that order, valued at the price paid, less the payment processor's fee —
refunded through the provider's API, with exactly those tokens taken back.
The provider's own refund notification that follows takes nothing more
(payments.Payments.record_admin_refund).

"Refund unused tokens" on an account (0.52.0, rewards step 8) books the
refund across its purchases newest first, each with its own bonus and
referral take-back (payments.Payments.account_refund_quote). The admin sees
the split and confirms it; the refund goes ahead only if the split is still
the one shown, one purchase at a time, and a purchase refunded before a
failure stays booked. The per-order refund stays, and says when a newer
purchase still has unused tokens.
"""
from __future__ import annotations

import html
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

DEFAULT_ADMINS = ("xootme@gmail.com",)
LAST_USES = 5
SITE = "https://cheapcadtools.com"
DEFAULT_SERVICES = (
    ("pulleys", f"{SITE}/tools/pulleys"), ("sprockets", f"{SITE}/tools/sprocket"),
    ("gears", f"{SITE}/tools/gears"), ("worm-gears", f"{SITE}/tools/worm-gears"),
    ("bevel-gears", f"{SITE}/tools/bevel-gears"), ("planetary-gears", f"{SITE}/tools/planetary-gears"),
    ("gear-belt-chain", f"{SITE}/tools/gear-belt-chain"), ("mcp-gateway", f"{SITE}/mcp"),
)
HEALTH_TIMEOUT = 4.0


def admin_emails() -> frozenset:
    raw = os.environ.get("ADMIN_EMAILS", "")
    emails = [e.strip().lower() for e in raw.split(",") if e.strip()] or list(DEFAULT_ADMINS)
    return frozenset(emails)


def services() -> list:
    """[(name, base url)] the Status tab asks: CCT_SERVICES, else DEFAULT_SERVICES."""
    raw = os.environ.get("CCT_SERVICES", "")
    pairs = [tuple(x.strip() for x in item.split("=", 1)) for item in raw.split(",") if "=" in item]
    return [(n, u.rstrip("/")) for n, u in pairs if n and u] or list(DEFAULT_SERVICES)


def fetch_health(url: str, timeout: float = HEALTH_TIMEOUT) -> dict:
    """GET <url>/api/health as JSON (raises on any failure)."""
    import json
    import urllib.request
    req = urllib.request.Request(f"{url}/api/health", headers={"Accept": "application/json",
                                                               "User-Agent": "cct-admin"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def service_row(name: str, url: str, fetch=fetch_health) -> dict:
    """One service's line on the Status tab. An app from before 0.26.0
    reports its cct_common version but no hash."""
    row = {"name": name, "url": url, "ok": False, "version": None, "cct_common": None,
           "cct_common_hash": None, "cct_common_released": None, "error": None}
    try:
        h = fetch(url)
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"[:200]
        return row
    cc = h.get("cct_common")
    if isinstance(cc, dict):          # a {version, hash, released} object
        cc, h = cc.get("version"), {**h, "cct_common_hash": cc.get("hash"),
                                   "cct_common_released": cc.get("released")}
    row.update(ok=True, version=h.get("version"), cct_common=cc,
               cct_common_hash=h.get("cct_common_hash"), cct_common_released=h.get("cct_common_released"))
    return row


def register_admin(app, accounts, *, app_name: str = "CheapCADTools", app_version: str = "",
                   bug_reports=None, default_app: str = "", download_failures=None,
                   clock=time.time, fetch=fetch_health, app_url: str = "",
                   server_use: bool = True, google=None, email_sender=None,
                   notice_sleep=time.sleep) -> None:
    """Mount the admin dashboard. `accounts` is the AccountStore (its
    TokenStore is the ledger); payments come from register_payment_routes
    (app.extensions["cct_payments"]) when it has run. `bug_reports` is an
    optional store with list(limit) and delete(id); `download_failures` a
    cct_common.download_failures store (the Download failures tab). `default_app` names the
    tool for ledger rows written before the ledger recorded one. `fetch`
    reads a service's /api/health (the Services list; tests pass their own).
    `app_url` is the app's public address: the Download failures tab opens a
    design there. `server_use` counts this app's requests (cct_common.server_use,
    tool = default_app) for the Server use tab; `google` replaces the Cloud
    Monitoring read (tests). The admin page may be served elsewhere (the pulley app's is
    at the site's /admin), so without it the page guesses from its own path,
    which is right only where the app and its /admin share a base (local)."""
    from flask import Response, abort, jsonify, redirect, request, send_file

    from .account_routes import current_session
    from .payments import PaymentError
    from .release import version_info

    tokens = accounts.tokens
    page = Path(__file__).with_name("admin_page.html")
    if bug_reports is None:              # 0.44.0: every app's reports, in the shared database
        from .reports import ReportStore
        bug_reports = ReportStore(tokens.path, clock=clock)
    if email_sender is None:             # the "fixed" email (my_reports), through Resend
        from .resend_email import send as email_sender
    if server_use:
        from .server_use import register_server_use
        register_server_use(app, accounts, tool=default_app)

    def _admin() -> Optional[str]:
        """The signed-in admin's email, or None."""
        s = current_session()
        if not s or s.get("kind") != "web":
            return None
        email = (tokens.account_email(s["account_id"]) or "").lower()
        return email if email in admin_emails() else None

    def _need_admin() -> str:
        email = _admin()
        if not email:
            abort(404)
        return email

    def _need_same_origin() -> None:
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            abort(403)
        if not request.is_json:
            abort(415)

    def _payments():
        return app.extensions.get("cct_payments") or {}

    # ── the page ──
    def admin_page():
        s = current_session()
        if not s:
            return redirect("/account/sign-in?next=/admin", code=302)
        if not _admin():
            # The page (only) says who you're signed in as, so signing in
            # with the wrong account isn't a mystery; the JSON stays a 404.
            email = tokens.account_email(s["account_id"]) or ""
            r = Response(
                "<!doctype html><meta charset='utf-8'><title>Not an admin account</title>"
                "<body style='font-family:system-ui,sans-serif;max-width:28rem;margin:4rem auto;"
                "padding:0 1rem;line-height:1.5'><h1>Not an admin account</h1>"
                f"<p>You're signed in as <strong>{html.escape(email)}</strong>, which isn't an "
                "admin account.</p><p><a href='/account/sign-in?next=/admin'>Sign in with a "
                "different account</a></p>", status=403, mimetype="text/html")
            r.headers["Cache-Control"] = "no-store"
            return r
        r = send_file(page, mimetype="text/html")
        r.headers["Cache-Control"] = "no-store"
        return r

    # ── subscribers ──
    def accounts_list():
        _need_admin()
        last_sql = accounts._LAST_SIGN_IN_SQL
        with tokens._read() as db:
            rows = db.execute(
                f"""SELECT a.id, a.email, a.created_at, {last_sql} AS last_sign_in,
                       COALESCE((SELECT SUM(l.amount) FROM ledger l WHERE l.account_id = a.id), 0)
                           AS balance,
                       COALESCE((SELECT SUM(l.amount) FROM ledger l WHERE l.account_id = a.id
                                 AND l.amount > 0 AND l.kind IN
                                 ('signup', 'purchase', 'referral', 'promo', 'adjust')), 0)
                           AS lifetime
                    FROM accounts a""").fetchall()
            uses = db.execute(
                """SELECT s.account_id, s.ts, s.amount, s.app, s.fmt, s.tier FROM ledger s
                   WHERE s.kind = 'spend'
                     AND NOT EXISTS (SELECT 1 FROM ledger r
                                     WHERE r.kind = 'refund' AND r.ref = 'spend:' || s.id)
                   ORDER BY s.ts DESC""").fetchall()
        last: dict = {}
        for u in uses:
            lst = last.setdefault(u["account_id"], [])
            if len(lst) < LAST_USES:
                lst.append({"app": u["app"] or default_app or "?", "tokens": -int(u["amount"]),
                            "ts": u["ts"], "fmt": u["fmt"]})
        out = []
        for r in rows:
            free = tokens.free_remaining(r["id"])
            out.append({"id": r["id"], "email": r["email"], "created_at": r["created_at"],
                        "last_sign_in": r["last_sign_in"], "balance": int(r["balance"]),
                        "free": free, "purchased": int(r["balance"]) - free,
                        "lifetime": int(r["lifetime"]), "last_uses": last.get(r["id"], [])})
        from .newsletter import Newsletter
        from .settings import setting
        news = Newsletter(tokens.path).counts()
        news["on"] = setting(tokens.path, "newsletter_opt_in") is True
        from .newsletter_sync import NewsletterSync
        news["brevo"] = NewsletterSync(tokens.path).status()
        return jsonify({"accounts": out, "app": app_name, "rewards": _rewards(out), "referrals": _referrals(),
                        "newsletter": news if news["on"] or news["subscribed"] else None})

    def _rewards(rows: list) -> Optional[dict]:
        """From the rewards' switch date (0.37.0): each row's paid tokens
        spent, held reward and unused bonus tokens (bonus tokens count as the
        last free ones used, so it's an upper bound), and the total."""
        since = tokens.paid_first_since()
        if since is None:
            return None
        from .rewards import rewards_on, state
        from .settings import setting
        total = 0
        with tokens._read() as db:
            net = {r["account_id"]: int(r["n"]) for r in db.execute(
                "SELECT account_id, SUM(amount) AS n FROM ledger WHERE kind = 'bonus' "
                "OR (kind = 'adjust' AND ref LIKE 'bonusback:%') GROUP BY account_id").fetchall()}
            for row in rows:
                st = state(db, tokens, row["id"], since)
                unused = max(0, min(row["free"], net.get(row["id"], 0)))
                row["rewards"] = {"paid_spent": st["paid_spent"], "held": st["held"], "bonus_unused": unused}
                total += unused
        return {"on": rewards_on(tokens.path), "since": since, "bonus_unused": total}

    def _referrals() -> Optional[dict]:
        """Referrals (0.49.0): every one recorded, newest first, and the
        month's referral tokens (net of any taken back); None while off with none."""
        from .referrals import OUTCOMES, month_bounds, referrals_on
        on = referrals_on(tokens.path)
        start, end = month_bounds(clock())
        with tokens._read() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT r.*, s.email AS sharer, n.email AS referred, l.amount AS tokens FROM referrals r "
                "LEFT JOIN accounts s ON s.id = r.sharer_id LEFT JOIN accounts n ON n.id = r.referred_id "
                "LEFT JOIN ledger l ON l.kind = 'referral' AND l.ref = 'referral:' || r.referred_id "
                "ORDER BY r.created_at DESC LIMIT 500").fetchall()]
            month = db.execute("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE ts >= ? AND ts < ? AND "
                               "(kind = 'referral' OR (kind = 'adjust' AND ref LIKE 'referralback:%'))",
                               (start, end)).fetchone()[0]
        if not on and not rows:
            return None
        return {"on": on, "month_tokens": int(month), "rows": [
            {"sharer": r["sharer"], "referred": r["referred"], "app": r["app"], "created_at": r["created_at"],
             "outcome": OUTCOMES.get(r["outcome"] or "", "no purchase yet"), "tokens": int(r["tokens"] or 0)}
            for r in rows]}

    # ── account notices (0.50.0) ──
    def _notices():
        from .notices import Notices
        return Notices(tokens.path, sender=email_sender, clock=clock, sleep=notice_sleep)

    def _notice_call(fn):
        from .notices import NoticeError
        try:
            return jsonify(fn())
        except NoticeError as e:
            return jsonify({"error": str(e)}), 400

    def notices_list():
        _need_admin()
        from .notices import NOTICES, PLACEHOLDER
        store = _notices()
        return jsonify({"placeholder": PLACEHOLDER, "notices": [
            {**store.status(nid), "title": n["title"], "subject": n["subject"], "body": n["body"]}
            for nid, n in NOTICES.items()]})

    def notice_test(notice_id):
        admin = _need_admin()
        _need_same_origin()
        body = request.get_json(silent=True) or {}

        def go():
            ok, err = _notices().test(notice_id, body.get("date"), admin)
            app.logger.info("admin %s sent themselves a test of notice %s: %s", admin, notice_id, ok or err)
            return {"ok": bool(ok), "to": admin, **({} if ok else {"error": f"Not sent: {err}"})}
        return _notice_call(go)

    def notice_start(notice_id):
        admin = _need_admin()
        _need_same_origin()
        body = request.get_json(silent=True) or {}

        def go():
            out = _notices().start(notice_id, body.get("date"), body.get("confirm"), admin)
            app.logger.info("admin %s started sending notice %s (%d left)", admin, notice_id, out["left"])
            return out
        return _notice_call(go)

    def notice_batch(notice_id):
        _need_admin()
        _need_same_origin()
        body = request.get_json(silent=True) or {}
        return _notice_call(lambda: _notices().batch(notice_id, body.get("pass")))

    def newsletter_csv():
        admin = _need_admin()
        from .newsletter import Newsletter, export_csv
        rows = Newsletter(tokens.path).subscribers()
        app.logger.info("admin %s exported %d newsletter subscribers", admin, len(rows))
        r = Response(export_csv(rows), mimetype="text/csv")
        r.headers["Content-Disposition"] = (
            f"attachment; filename=newsletter-{time.strftime('%Y-%m-%d', time.gmtime(clock()))}.csv")
        r.headers["Cache-Control"] = "no-store"
        return r

    def grant(account_id):
        admin = _need_admin()
        _need_same_origin()
        body = request.get_json(silent=True) or {}
        try:
            amount = int(body.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0
        reason = str(body.get("reason", "")).strip()
        if amount == 0 or not reason:
            return jsonify({"error": "Enter a non-zero amount and a reason."}), 400
        if tokens.account_email(account_id) is None:
            return jsonify({"error": "No such account."}), 404
        # A grant is free (not refundable) like signup tokens; a removal is an adjust.
        tokens.credit(account_id, amount, kind="promo" if amount > 0 else "adjust",
                      detail=f"admin {admin}: {reason}"[:500])
        app.logger.info("admin %s %+d tokens to %s: %s", admin, amount, account_id, reason)
        return jsonify({"balance": tokens.balance(account_id)})

    # ── sales and refunds ──
    def orders_list():
        _need_admin()
        store = _payments().get("store")
        rows = [{k: v for k, v in o.items() if k != "payer_key"} for o in (store.list_orders() if store else [])]
        receipts = store.receipts() if store else None
        if receipts is not None:          # 0.40.0: each order's receipt and credit notes
            receipts.issue_pending()
            for o in rows:
                o["receipts"] = [r["number"] for r in receipts.for_order(o["provider"], o["order_id"])]
        return jsonify({"orders": rows,
                        "enabled": bool(store)})

    def _order_or_404(provider, order_id):
        store = _payments().get("store")
        order = store.get(provider, order_id) if store else None
        if order is None:
            abort(404)
        return store, order

    def _fee(provider, order) -> Optional[int]:
        client = _payments().get(provider)
        if not client or not order.get("payment_ref"):
            return None
        try:
            if provider == "paypal":
                return client.capture_fee(order["payment_ref"])
            return client.intent_fee(order["payment_ref"])
        except Exception:
            app.logger.exception("fee lookup failed for %s %s", provider, order["order_id"])
            return None

    def refund_quote(provider, order_id):
        _need_admin()
        store, order = _order_or_404(provider, order_id)
        fee = request.args.get("fee_cents")
        fee_source = "entered"
        if fee is None:
            fee = _fee(provider, order)
            fee_source = "provider" if fee is not None else "unknown"
        try:
            quote = store.refund_quote(provider, order_id, int(fee or 0))
        except PaymentError as e:
            return jsonify({"error": str(e)}), 400
        quote.update(fee_source=fee_source, email=order.get("email") or
                     tokens.account_email(order["account_id"]), cents=order["cents"],
                     refunded_cents=order["refunded_cents"])
        return jsonify(quote)

    def refund(provider, order_id):
        admin = _need_admin()
        _need_same_origin()
        store, order = _order_or_404(provider, order_id)
        body = request.get_json(silent=True) or {}
        try:
            quote = store.refund_quote(provider, order_id, int(body.get("fee_cents", 0)))
        except (PaymentError, TypeError, ValueError) as e:
            return jsonify({"error": str(e)}), 400
        if quote["tokens"] <= 0 or quote["amount_cents"] <= 0:
            return jsonify({"error": "Nothing to refund: no unused purchased tokens from this "
                                     "order, or the fee is more than they're worth."}), 400
        client = _payments().get(provider)
        if client is None:
            return jsonify({"error": f"{provider} isn't configured on this server."}), 400
        try:
            if provider == "paypal":
                refund_id = client.refund_capture(order["payment_ref"], quote["amount_cents"])
            else:
                refund_id = client.refund(order["payment_ref"], quote["amount_cents"])
        except PaymentError as e:
            return jsonify({"error": str(e)}), 502
        store.record_admin_refund(provider, order_id, refund_id=refund_id,
                                  amount_cents=quote["amount_cents"], tokens=quote["tokens"],
                                  admin=admin)
        app.logger.info("admin %s refunded %s %s: %d cents, %d tokens (refund %s)", admin,
                        provider, order_id, quote["amount_cents"], quote["tokens"], refund_id)
        return jsonify({"refund_id": refund_id, **quote,
                        "balance": tokens.balance(order["account_id"])})

    # ── step 8: an account's unused tokens, refunded newest purchase first ──
    account_refunds = threading.Lock()     # one at a time: a second click finds the split changed

    def _account_quote(account_id, entered: dict):
        """The newest-first split with each purchase's fee: as entered, else
        the processor's, else 0 (and said so)."""
        store = _payments().get("store")
        if store is None or tokens.account_email(account_id) is None:
            abort(404)
        q = store.account_refund_quote(account_id)
        fees, source = {}, {}
        for part in q["parts"]:
            key = (part["provider"], part["order_id"])
            if f"{key[0]}:{key[1]}" in entered:
                fees[key], source[key] = max(0, int(entered[f"{key[0]}:{key[1]}"])), "entered"
            else:
                fee = _fee(key[0], store.get(*key))
                fees[key], source[key] = (fee or 0), ("provider" if fee is not None else "unknown")
        q = store.account_refund_quote(account_id, fees)
        for part in q["parts"]:
            part["fee_source"] = source[(part["provider"], part["order_id"])]
        q["email"] = tokens.account_email(account_id)
        return store, q

    def account_refund_quote(account_id):
        _need_admin()
        try:
            entered = json.loads(request.args.get("fees") or "{}")
            assert isinstance(entered, dict)
            _, q = _account_quote(account_id, entered)
        except (ValueError, TypeError, AssertionError):
            return jsonify({"error": "fees must be {\"<provider>:<order id>\": cents}"}), 400
        return jsonify(q)

    def account_refund(account_id):
        admin = _need_admin()
        _need_same_origin()
        body = request.get_json(silent=True) or {}
        shown = body.get("parts")
        if not isinstance(shown, list):
            return jsonify({"error": "parts: the split you confirmed"}), 400
        with account_refunds:
            try:
                entered = {f"{p['provider']}:{p['order_id']}": int(p.get("fee_cents", 0)) for p in shown}
                store, q = _account_quote(account_id, entered)
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                return jsonify({"error": f"parts: {e}"}), 400
            todo = [p for p in q["parts"] if p["amount_cents"] > 0]
            if not todo:
                return jsonify({"error": "Nothing to refund: no unused purchased tokens on this account, "
                                         "or each purchase's fee is more than its tokens are worth."}), 400
            if [(p["provider"], p["order_id"], p["tokens"]) for p in todo] != \
                    [(p.get("provider"), p.get("order_id"), p.get("tokens")) for p in shown
                     if p.get("amount_cents", 1) > 0]:
                return jsonify({"error": "The account changed since you looked: look at the split again."}), 409
            done = []
            for part in todo:
                provider, order_id = part["provider"], part["order_id"]
                order, client = store.get(provider, order_id), _payments().get(provider)
                try:
                    if client is None:
                        raise PaymentError(f"{provider} isn't configured on this server.")
                    if provider == "paypal":
                        refund_id = client.refund_capture(order["payment_ref"], part["amount_cents"])
                    else:
                        refund_id = client.refund(order["payment_ref"], part["amount_cents"])
                except PaymentError as e:
                    return jsonify({"error": f"{provider} {order_id}: {e}", "done": done,
                                    "balance": tokens.balance(account_id)}), 502
                store.record_admin_refund(provider, order_id, refund_id=refund_id,
                                          amount_cents=part["amount_cents"], tokens=part["tokens"], admin=admin)
                app.logger.info("admin %s refunded %s %s for account %s: %d cents, %d tokens (refund %s)",
                                admin, provider, order_id, account_id, part["amount_cents"], part["tokens"],
                                refund_id)
                done.append({**part, "refund_id": refund_id})
        return jsonify({"done": done, "amount_cents": sum(p["amount_cents"] for p in done),
                        "tokens": sum(p["tokens"] for p in done), "bonus_back": sum(p["bonus_back"] for p in done),
                        "balance": tokens.balance(account_id)})

    # ── bug reports ──
    def bugs_list():
        _need_admin()
        if bug_reports is None:
            return jsonify({"bugs": [], "enabled": False})
        from .reports import my_reports_on
        return jsonify({"bugs": bug_reports.list(500), "enabled": True,
                        "my_reports": my_reports_on(tokens.path)})

    def bug_status(report_id):
        """My reports (0.44.0): the status the account page shows, and the one
        "fixed" email, only when asked for and only while my_reports is on."""
        admin = _need_admin()
        _need_same_origin()
        from .reports import fixed_email, my_reports_on
        if not my_reports_on(tokens.path) or not hasattr(bug_reports, "set_status"):
            abort(404)
        body = request.get_json(silent=True) or {}
        try:
            row = bug_reports.set_status(report_id, str(body.get("status") or ""), fixed_in=body.get("fixed_in"),
                                         note=body.get("note"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if row is None:
            abort(404)
        app.logger.info("admin %s set report %s to %s", admin, report_id, row["status"])
        emailed = None
        if row["status"] == "fixed" and body.get("email") is True and not row.get("emailed_at"):
            to = (tokens.account_email(row["account_id"]) if row.get("account_id") else None) or row.get("email")
            if to and not str(to).startswith("deleted:"):
                from .account_page import TOOL_PAGES, tool_title
                tool = tool_title(row.get("app"))
                subject, text = fixed_email(row, tool, SITE + TOOL_PAGES.get(row.get("app") or "", "/"))
                ok, err = email_sender(to, subject, text)
                if ok:
                    bug_reports.mark_emailed(report_id)
                emailed = bool(ok)
            else:
                emailed = False
        return jsonify({"report_id": report_id, "status": row["status"], "emailed": emailed})

    def bug_delete(report_id):
        admin = _need_admin()
        _need_same_origin()
        if bug_reports is None or not bug_reports.delete(report_id):
            abort(404)
        app.logger.info("admin %s deleted bug report %s", admin, report_id)
        return jsonify({"deleted": report_id})

    # ── download failures (cct_common.download_failures) ──
    def failures_list():
        _need_admin()
        if download_failures is None:
            return jsonify({"failures": [], "enabled": False, "app_url": app_url, "app": default_app})
        return jsonify({"failures": download_failures.list(1000), "enabled": True, "app_url": app_url,
                        "app": default_app})

    def failure_delete(failure_id):
        admin = _need_admin()
        _need_same_origin()
        if download_failures is None or not download_failures.delete(failure_id):
            abort(404)
        app.logger.info("admin %s deleted download failure %s", admin, failure_id)
        return jsonify({"deleted": failure_id})

    def failures_clear():
        admin = _need_admin()
        _need_same_origin()
        if download_failures is None:
            abort(404)
        n = download_failures.clear()
        app.logger.info("admin %s cleared %d download failures", admin, n)
        return jsonify({"deleted": n})

    # ── status ──
    def status():
        _need_admin()
        db_ok, counts = True, {}
        try:
            with tokens._read() as db:
                for table in ("accounts", "ledger", "payments"):
                    try:
                        counts[table] = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    except Exception:
                        counts[table] = None
        except Exception:
            db_ok = False
        return jsonify({
            "app": app_name, "version": app_version, "app_url": app_url,
            "service": os.environ.get("K_SERVICE", ""), "revision": os.environ.get("K_REVISION", ""),
            "database": "postgres" if tokens.is_postgres else "sqlite", "database_ok": db_ok,
            "counts": counts, "payments": sorted(k for k in ("paypal", "stripe")
                                                 if _payments().get(k)),
            "admins": sorted(admin_emails()), "cct_common": version_info(), "now": clock()})

    def services_list():
        _need_admin()
        from concurrent.futures import ThreadPoolExecutor
        todo = services()
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            rows = list(pool.map(lambda s: service_row(*s, fetch=fetch), todo))
        return jsonify({"services": rows, "this": version_info(), "now": clock()})

    # ── business settings (0.28.0) ──
    def settings_list():
        _need_admin()
        from .settings import settings_for, off_rate_packs
        from .tokens import TOKEN_PRICE_CENTS
        store = settings_for(tokens.path)
        rows = store.rows()
        packs = next((r["value"] for r in rows if r["key"] == "packs"), None)
        return jsonify({"settings": rows, "history": store.history(),
                        "tokens_per_dollar": 100 // TOKEN_PRICE_CENTS,
                        "off_rate_packs": off_rate_packs(packs or {}, TOKEN_PRICE_CENTS)})

    def settings_save(key):
        admin = _need_admin()
        _need_same_origin()
        from .settings import DEFAULTS, SettingsError, settings_for
        if key not in DEFAULTS:
            return jsonify({"error": "No such setting."}), 404
        body = request.get_json(silent=True) or {}
        if "value" not in body:
            return jsonify({"error": "Send the new value."}), 400
        try:
            value = settings_for(tokens.path).set(key, body["value"], by=admin,
                                                  note=str(body.get("note", "")).strip())
        except SettingsError as e:
            return jsonify({"error": str(e)}), 400
        app.logger.info("admin %s set %s to %r", admin, key, value)
        return jsonify({"key": key, "value": value})

    # ── server use ──
    def server_use_report():
        _need_admin()
        from .server_use import ServerUseStore, google_billable, report
        days = max(1, min(int(request.args.get("days", 30) or 30), 3660))
        out = report(ServerUseStore(tokens.path), days, clock())
        out["google"] = (google or google_billable)(days, clock())
        return jsonify(out)

    app.add_url_rule("/admin", view_func=admin_page, methods=["GET"])
    app.add_url_rule("/admin/", endpoint="admin_page_slash", view_func=admin_page, methods=["GET"])
    app.add_url_rule("/admin/api/accounts", view_func=accounts_list, methods=["GET"])
    app.add_url_rule("/admin/api/newsletter.csv", view_func=newsletter_csv, methods=["GET"])
    app.add_url_rule("/admin/api/accounts/<account_id>/grant", view_func=grant, methods=["POST"])
    app.add_url_rule("/admin/api/accounts/<account_id>/refund-quote", view_func=account_refund_quote,
                     methods=["GET"])
    app.add_url_rule("/admin/api/accounts/<account_id>/refund", view_func=account_refund, methods=["POST"])
    app.add_url_rule("/admin/api/orders", view_func=orders_list, methods=["GET"])
    app.add_url_rule("/admin/api/orders/<provider>/<order_id>/refund-quote",
                     view_func=refund_quote, methods=["GET"])
    app.add_url_rule("/admin/api/orders/<provider>/<order_id>/refund",
                     view_func=refund, methods=["POST"])
    app.add_url_rule("/admin/api/bugs", view_func=bugs_list, methods=["GET"])
    app.add_url_rule("/admin/api/bugs/<report_id>", view_func=bug_delete, methods=["DELETE"])
    app.add_url_rule("/admin/api/bugs/<report_id>/status", view_func=bug_status, methods=["POST"])
    app.add_url_rule("/admin/api/download-failures", view_func=failures_list, methods=["GET"])
    app.add_url_rule("/admin/api/download-failures", endpoint="failures_clear",
                     view_func=failures_clear, methods=["DELETE"])
    app.add_url_rule("/admin/api/download-failures/<failure_id>", view_func=failure_delete,
                     methods=["DELETE"])
    app.add_url_rule("/admin/api/status", view_func=status, methods=["GET"])
    app.add_url_rule("/admin/api/services", view_func=services_list, methods=["GET"])
    app.add_url_rule("/admin/api/server-use", view_func=server_use_report, methods=["GET"])
    app.add_url_rule("/admin/api/notices", view_func=notices_list, methods=["GET"])
    app.add_url_rule("/admin/api/notices/<notice_id>/test", view_func=notice_test, methods=["POST"])
    app.add_url_rule("/admin/api/notices/<notice_id>/start", view_func=notice_start, methods=["POST"])
    app.add_url_rule("/admin/api/notices/<notice_id>/batch", view_func=notice_batch, methods=["POST"])
    app.add_url_rule("/admin/api/settings", view_func=settings_list, methods=["GET"])
    app.add_url_rule("/admin/api/settings/<key>", view_func=settings_save, methods=["POST"])
