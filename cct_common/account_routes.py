"""
account_routes.py — Flask routes for email-link sign-in and the account
page's data (PulleyWebApp-ss ADR-008). OAuth (Microsoft, Google, GitHub)
and the add-in device flow plug into the same AccountStore later.

    from cct_common.account_routes import register_account_routes, current_account_id

    register_account_routes(app, accounts, email_sender=send_email,
                            app_name="CheapCAD Tools")
    ...
    account_id = current_account_id()   # inside a request; None if signed out

Routes:
    POST   /api/account/login-link   {email, next?}  email a sign-in link
    GET    /account/login?token=...  confirmation page (a button, see below)
    POST   /account/login            uses the link, sets the session cookie
    POST   /api/account/logout
    GET    /api/account              email, balance, sign-in methods, sessions
    GET    /api/account/history      ledger rows, newest first
    DELETE /api/account/sessions/<id>   revoke one session (e.g. a lost laptop)
    POST   /api/account/delete       {confirm_email}  delete the account

Why the sign-in link opens a page with a button instead of signing in on
GET: mail security scanners (Outlook Safe Links and others) fetch every
link in an email before the person clicks it. A GET that used the link up
would sign the scanner in and leave the real click with a dead link.

Requests authenticate by the session cookie (browser) or an
`Authorization: Bearer <device token>` header (CAD add-ins). The cookie is
HttpOnly, SameSite=Lax and — unless secure_cookies=False for plain-http
local runs — Secure. State-changing JSON routes require a JSON body, which
a cross-site form can't send without a CORS preflight this app never
grants.
"""
from __future__ import annotations

import html
from typing import Optional

from .accounts import AccountStore, RateLimited, normalize_email

COOKIE_NAME = "cct_session"
_EXT_KEY = "cct_accounts"


def _default_email_body(app_name: str, link: str) -> str:
    return (
        f"Hi,\n\nUse this link to sign in to {app_name}:\n\n    {link}\n\n"
        f"It works once and expires in 15 minutes. If you didn't ask to sign in, "
        f"you can ignore this email — nobody can use it without this link.\n"
    )


def current_account_id() -> Optional[str]:
    """Signed-in account for the current request, or None."""
    from flask import current_app, g, request

    if "cct_account_id" in g:
        return g.cct_account_id
    accounts: AccountStore = current_app.extensions[_EXT_KEY]["accounts"]
    token = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get(COOKIE_NAME)
    g.cct_account_id = accounts.session_account(token)
    return g.cct_account_id


def _safe_next(target: Optional[str]) -> str:
    # Only same-site paths: blocks open redirects like //evil.com or https://…
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def register_account_routes(app, accounts: AccountStore, *, email_sender,
                            app_name: str = "CheapCAD Tools",
                            email_subject: Optional[str] = None,
                            email_body=None, secure_cookies: bool = True):
    """email_sender(to, subject, body) -> (ok, err), e.g.
    cct_common.resend_email.send."""
    from flask import jsonify, redirect, request, Response, g

    app.extensions[_EXT_KEY] = {"accounts": accounts}
    tokens = accounts.tokens
    subject = email_subject or f"Sign in to {app_name}"
    body_fn = email_body or (lambda link: _default_email_body(app_name, link))

    def _client_ip():
        return (request.headers.get("X-Forwarded-For", request.remote_addr or "")
                .split(",")[0].strip()) or None

    def _page(title: str, inner: str, status: int = 200) -> Response:
        doc = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='referrer' content='no-referrer'>"
            f"<title>{html.escape(title)}</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:28rem;margin:4rem auto;"
            "padding:0 1rem;line-height:1.5}button{font-size:1rem;padding:.6rem 1.4rem;"
            "cursor:pointer}</style></head><body>"
            f"<h1>{html.escape(title)}</h1>{inner}</body></html>")
        r = Response(doc, status=status, mimetype="text/html")
        r.headers["Cache-Control"] = "no-store"
        r.headers["Referrer-Policy"] = "no-referrer"
        return r

    def _require_account():
        acct = current_account_id()
        if not acct:
            return None, (jsonify({"error": "sign in required", "code": "SIGN_IN_REQUIRED"}), 401)
        return acct, None

    def login_link():
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "JSON body required"}), 400
        try:
            email = normalize_email(data.get("email", ""))
        except ValueError:
            return jsonify({"error": "Enter a valid email address."}), 400
        try:
            token = accounts.create_login_link(email, ip=_client_ip())
        except RateLimited:
            return jsonify({"error": "Too many sign-in emails requested. Try again in an hour.",
                            "code": "RATE_LIMITED"}), 429
        nxt = _safe_next(data.get("next"))
        link = f"{request.host_url}account/login?token={token}"
        if nxt != "/":
            from urllib.parse import quote
            link += f"&next={quote(nxt, safe='/')}"
        ok, err = email_sender(email, subject, body_fn(link))
        if not ok:
            return jsonify({"error": "Couldn't send the sign-in email. Please try again."}), 502
        # Same answer whether or not the email has an account, so the
        # route can't be used to find out who's registered.
        return jsonify({"ok": True})

    def login_page():
        token = request.args.get("token", "")
        nxt = _safe_next(request.args.get("next"))
        return _page(f"Sign in to {app_name}", (
            "<p>Press the button to finish signing in.</p>"
            "<form method='post' action='/account/login'>"
            f"<input type='hidden' name='token' value='{html.escape(token, quote=True)}'>"
            f"<input type='hidden' name='next' value='{html.escape(nxt, quote=True)}'>"
            "<button type='submit'>Sign in</button></form>"))

    def login_submit():
        account_id = accounts.redeem_login_link(request.form.get("token", ""))
        if not account_id:
            return _page("Link expired", (
                "<p>This sign-in link has expired or was already used. "
                "Links work once and last 15 minutes.</p>"
                "<p><a href='/'>Request a new one</a></p>"), status=400)
        session = accounts.create_session(account_id, kind="web",
                                          label=(request.user_agent.string or "")[:80])
        r = redirect(_safe_next(request.form.get("next")), code=303)
        r.set_cookie(COOKIE_NAME, session, max_age=30 * 24 * 3600, httponly=True,
                     secure=secure_cookies, samesite="Lax", path="/")
        return r

    def logout():
        token = request.cookies.get(COOKIE_NAME)
        if token:
            accounts.revoke_session(token)
        r = jsonify({"ok": True})
        r.delete_cookie(COOKIE_NAME, path="/")
        return r

    def account_info():
        acct, err = _require_account()
        if err:
            return err
        cookie = request.cookies.get(COOKIE_NAME)
        current = accounts.session_id(cookie) if cookie else None
        sessions = accounts.list_sessions(acct)
        for s in sessions:
            s["current"] = s["id"] == current
        return jsonify({
            "email": tokens.account_email(acct),
            "balance": tokens.balance(acct),
            "identities": accounts.identities(acct),
            "sessions": sessions,
        })

    def account_history():
        acct, err = _require_account()
        if err:
            return err
        try:
            limit = max(1, min(200, int(request.args.get("limit", 50))))
        except ValueError:
            limit = 50
        rows = tokens.history(acct, limit=limit)
        return jsonify([{k: r[k] for k in ("id", "ts", "kind", "amount", "tier", "fmt", "detail")}
                        for r in rows])

    def revoke(session_id: int):
        acct, err = _require_account()
        if err:
            return err
        if not accounts.revoke_session_id(acct, session_id):
            return jsonify({"error": "no such session"}), 404
        return jsonify({"ok": True})

    def delete_account():
        acct, err = _require_account()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        if (data.get("confirm_email") or "").strip().lower() != tokens.account_email(acct):
            return jsonify({"error": "Type your account email to confirm."}), 400
        accounts.delete_account(acct)
        g.cct_account_id = None
        r = jsonify({"ok": True})
        r.delete_cookie(COOKIE_NAME, path="/")
        return r

    app.add_url_rule("/api/account/login-link", view_func=login_link, methods=["POST"])
    app.add_url_rule("/account/login", view_func=login_page, methods=["GET"])
    app.add_url_rule("/account/login", endpoint="account_login_submit",
                     view_func=login_submit, methods=["POST"])
    app.add_url_rule("/api/account/logout", view_func=logout, methods=["POST"])
    app.add_url_rule("/api/account", view_func=account_info, methods=["GET"])
    app.add_url_rule("/api/account/history", view_func=account_history, methods=["GET"])
    app.add_url_rule("/api/account/sessions/<int:session_id>", view_func=revoke, methods=["DELETE"])
    app.add_url_rule("/api/account/delete", view_func=delete_account, methods=["POST"])
