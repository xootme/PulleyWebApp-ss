"""
admin.py — the CCT admin dashboard, shared by every tool that uses the
accounts and token ledger (they share one database, so one dashboard sees
every tool's accounts).

    from cct_common.admin import register_admin
    register_admin(app, accounts, app_name="Timing Pulleys", app_version=APP_VERSION,
                   bug_reports=bug_store, default_app="pulleys")

Sign-in is the normal account sign-in (/account/sign-in); only accounts
whose email is in ADMIN_EMAILS (comma-separated; default DEFAULT_ADMINS)
get in. Anyone else gets a 404 from the JSON routes; the page itself tells
a signed-in non-admin which account they're signed in as (403). Browser sessions only (not add-in / agent device tokens), and
every change must come from the page's own origin.

Pages (one page, admin_page.html, tabs):
  Subscribers   accounts: first subscribed, last sign-in, tokens now (free /
                purchased), lifetime tokens, last 5 uses; grant / adjust
  Sales         token-pack orders; refund by the terms
  Bug reports   the app's bug-report store (if given); delete
  Status        version, Cloud Run revision, database, counts

Routes (JSON under /admin/api/, not /api/admin/ — that prefix carries the old
dashboard's cross-origin headers):
  GET    /admin
  GET    /admin/api/accounts
  POST   /admin/api/accounts/<id>/grant          {amount, reason}
  GET    /admin/api/orders
  GET    /admin/api/orders/<provider>/<id>/refund-quote[?fee_cents=N]
  POST   /admin/api/orders/<provider>/<id>/refund {fee_cents}
  GET    /admin/api/bugs
  DELETE /admin/api/bugs/<id>
  GET    /admin/api/status

Refunds follow the terms (§4): the account's unused *purchased* tokens from
that order, valued at the price paid, less the payment processor's fee —
refunded through the provider's API, with exactly those tokens taken back.
The provider's own refund notification that follows takes nothing more
(payments.Payments.record_admin_refund).
"""
from __future__ import annotations

import html
import os
import time
from pathlib import Path
from typing import Optional

DEFAULT_ADMINS = ("xootme@gmail.com",)
LAST_USES = 5


def admin_emails() -> frozenset:
    raw = os.environ.get("ADMIN_EMAILS", "")
    emails = [e.strip().lower() for e in raw.split(",") if e.strip()] or list(DEFAULT_ADMINS)
    return frozenset(emails)


def register_admin(app, accounts, *, app_name: str = "CheapCAD Tools", app_version: str = "",
                   bug_reports=None, default_app: str = "",
                   clock=time.time) -> None:
    """Mount the admin dashboard. `accounts` is the AccountStore (its
    TokenStore is the ledger); payments come from register_payment_routes
    (app.extensions["cct_payments"]) when it has run. `bug_reports` is an
    optional store with list(limit) and delete(id). `default_app` names the
    tool for ledger rows written before the ledger recorded one."""
    from flask import Response, abort, jsonify, redirect, request, send_file

    from .account_routes import current_session
    from .payments import PaymentError

    tokens = accounts.tokens
    page = Path(__file__).with_name("admin_page.html")

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
        return jsonify({"accounts": out, "app": app_name})

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
        return jsonify({"orders": store.list_orders() if store else [],
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

    # ── bug reports ──
    def bugs_list():
        _need_admin()
        if bug_reports is None:
            return jsonify({"bugs": [], "enabled": False})
        return jsonify({"bugs": bug_reports.list(500), "enabled": True})

    def bug_delete(report_id):
        admin = _need_admin()
        _need_same_origin()
        if bug_reports is None or not bug_reports.delete(report_id):
            abort(404)
        app.logger.info("admin %s deleted bug report %s", admin, report_id)
        return jsonify({"deleted": report_id})

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
            "app": app_name, "version": app_version,
            "service": os.environ.get("K_SERVICE", ""), "revision": os.environ.get("K_REVISION", ""),
            "database": "postgres" if tokens.is_postgres else "sqlite", "database_ok": db_ok,
            "counts": counts, "payments": sorted(k for k in ("paypal", "stripe")
                                                 if _payments().get(k)),
            "admins": sorted(admin_emails()), "now": clock()})

    app.add_url_rule("/admin", view_func=admin_page, methods=["GET"])
    app.add_url_rule("/admin/", endpoint="admin_page_slash", view_func=admin_page, methods=["GET"])
    app.add_url_rule("/admin/api/accounts", view_func=accounts_list, methods=["GET"])
    app.add_url_rule("/admin/api/accounts/<account_id>/grant", view_func=grant, methods=["POST"])
    app.add_url_rule("/admin/api/orders", view_func=orders_list, methods=["GET"])
    app.add_url_rule("/admin/api/orders/<provider>/<order_id>/refund-quote",
                     view_func=refund_quote, methods=["GET"])
    app.add_url_rule("/admin/api/orders/<provider>/<order_id>/refund",
                     view_func=refund, methods=["POST"])
    app.add_url_rule("/admin/api/bugs", view_func=bugs_list, methods=["GET"])
    app.add_url_rule("/admin/api/bugs/<report_id>", view_func=bug_delete, methods=["DELETE"])
    app.add_url_rule("/admin/api/status", view_func=status, methods=["GET"])
