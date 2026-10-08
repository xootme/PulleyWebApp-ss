"""
account_routes.py — Flask routes for email-link sign-in and the account
page's data, plus CAD add-in sign-in by device code (PulleyWebApp-ss
ADR-008). "Continue with Google / Microsoft / GitHub" comes from
cct_common.oauth, registered here when oauth_clients are given.

    from cct_common.account_routes import register_account_routes, current_account_id

    register_account_routes(app, accounts, email_sender=send_email,
                            app_name="CheapCAD Tools")
    ...
    account_id = current_account_id()   # inside a request; None if signed out

Routes:
    GET    /account/sign-in?next=... a sign-in page of its own: Continue with Google /
                                     Microsoft / GitHub, or email me a link; back to `next`
    POST   /account/sign-in          {email, next}  email the link
    POST   /api/account/login-link   {email, next?}  email a sign-in link
    GET    /account/login?token=...  confirmation page (a button, see below); with no
                                     token, on to /account/sign-in
    POST   /account/login            uses the link, sets the session cookie
    POST   /api/account/logout
    GET    /api/account              email, balance (free / bought), expiry date, buy page,
                                     sign-in methods, sessions
    GET    /api/account/history      ledger rows, newest first
    DELETE /api/account/sessions/<id>   revoke one session (e.g. a lost laptop)
    POST   /api/account/delete       {confirm_email}  delete the account
    GET    /api/account/providers    OAuth sign-in methods on offer (see oauth.py)
    GET    /account/oauth/<p>/start, /account/oauth/<p>/callback

CAD add-in sign-in (device code; see AccountStore.start_device_login):
    POST   /api/account/device/start {label}  -> device_code, user_code, verify_url, interval
    POST   /api/account/device/poll  {device_code} -> {"status": ...} (+ "token" once approved)
    GET    /account/device?code=...  the page where a signed-in person approves the code
    POST   /account/device           {code, decision}  approve / deny (browser form)
    POST   /account/device/sign-in   {email, code|next} email a link back to that page
    GET    /account/devices          connected add-ins/agents: spend, daily limit, revoke
    POST   /account/devices          {session_id, action=save|revoke, daily_budget}

Add-in/agent tokens carry a daily token limit (AccountStore
device_daily_budget, 100 by default; chosen on the approval page, changed
on /account/devices). Browser sessions never have one.

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
from urllib.parse import quote

from .accounts import DEAD_ACCOUNT_IDLE_S, AccountStore, RateLimited, normalize_email

BUDGET_CHOICES = (25, 50, 100, 250, 1000)   # offered on the pages; None = no limit

COOKIE_NAME = "cct_session"
_EXT_KEY = "cct_accounts"
_SESSION_KEY = "cct.session"


def _default_email_body(app_name: str, link: str) -> str:
    return (
        f"Hi,\n\nUse this link to sign in to {app_name}:\n\n    {link}\n\n"
        f"It works once and expires in 15 minutes. If you didn't ask to sign in, "
        f"you can ignore this email — nobody can use it without this link.\n"
    )


def current_session() -> Optional[dict]:
    """The current request's session — {id, account_id, kind, label,
    daily_budget} — from an add-in/agent's Bearer token or the browser
    cookie; None if signed out."""
    from flask import current_app, request

    # Cached on the request, not flask.g: g belongs to the app context, which
    # outlives a request whenever one is already pushed (a job's internal
    # test_client calls, a test client used as a context manager) — so a
    # g cache could hand one request another request's session.
    if _SESSION_KEY in request.environ:
        return request.environ[_SESSION_KEY]
    accounts: AccountStore = current_app.extensions[_EXT_KEY]["accounts"]
    token = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get(COOKIE_NAME)
    request.environ[_SESSION_KEY] = accounts.session_info(token)
    return request.environ[_SESSION_KEY]


def current_account_id() -> Optional[str]:
    """Signed-in account for the current request, or None."""
    s = current_session()
    return s["account_id"] if s else None


def parse_budget(value) -> object:
    """A daily-limit form value: '' or 'none' -> None (no limit); a whole
    number of tokens -> int; anything else -> ValueError."""
    v = str(value or "").strip().lower()
    if v in ("", "none", "off"):
        return None
    n = int(v)
    if n < 1:
        raise ValueError("limit must be at least 1")
    return n


def _safe_next(target: Optional[str]) -> str:
    # Only same-site paths: blocks open redirects like //evil.com or https://…
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


# Where the approval page may send the person after they decide: only the
# hosted MCP gateway's sign-in callback (cct_common.mcp_oauth), which turns the
# approved device code into an OAuth code for the AI app that asked.
MCP_CALLBACK_PATH = "/mcp/oauth/callback"


def _safe_then(target: Optional[str]) -> Optional[str]:
    """The approval page's return address, if it's the MCP callback; else None."""
    t = _safe_next(target)
    return t if t.startswith(MCP_CALLBACK_PATH + "?") else None


def register_account_routes(app, accounts: AccountStore, *, email_sender,
                            app_name: str = "CheapCAD Tools",
                            email_subject: Optional[str] = None,
                            email_body=None, secure_cookies: bool = True,
                            oauth_clients: Optional[dict] = None, oauth_http=None):
    """email_sender(to, subject, body) -> (ok, err), e.g.
    cct_common.resend_email.send. oauth_clients: {"google": (client_id,
    client_secret), "microsoft": ..., "github": ...} for OAuth sign-in
    (see cct_common.oauth); oauth_http is a requests-like session (tests)."""
    from flask import jsonify, redirect, request, Response

    app.extensions[_EXT_KEY] = {"accounts": accounts}
    tokens = accounts.tokens
    subject = email_subject or f"Sign in to {app_name}"
    body_fn = email_body or (lambda link: _default_email_body(app_name, link))

    def _client_ip():
        # remote_addr, never the raw X-Forwarded-For header: its first entry
        # is whatever the visitor chose to send, so per-IP limits keyed on it
        # could be dodged at will. Behind a proxy, the app sets remote_addr
        # from the entries its own proxies added (werkzeug's ProxyFix).
        return request.remote_addr or None

    def _page(title: str, inner: str, status: int = 200) -> Response:
        doc = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='referrer' content='no-referrer'>"
            f"<title>{html.escape(title)}</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:28rem;margin:4rem auto;"
            "padding:0 1rem;line-height:1.5}button,.btn{font-size:1rem;padding:.6rem 1.4rem;"
            "cursor:pointer}.btn{display:inline-block;border:1px solid #888;border-radius:4px;"
            "color:inherit;text-decoration:none;min-width:14rem;text-align:center}"
            ".oauth{display:flex;flex-direction:column;gap:.6rem;margin:1.2rem 0}"
            ".btn-oauth{display:flex;align-items:center;justify-content:center;gap:.7rem;"
            "box-sizing:border-box;width:100%;padding:.7rem 1rem;border:1px solid #dadce0;"
            "border-radius:6px;background:#fff;color:#1f1f1f;font-weight:500;text-decoration:none;"
            "box-shadow:0 1px 2px rgba(0,0,0,.06)}.btn-oauth:hover{background:#f6f8fa;border-color:#c4c7cc}"
            ".btn-oauth svg{flex:none}.or{display:flex;align-items:center;gap:.7rem;color:#666;"
            "font-size:.9rem;margin:1.2rem 0}.or::before,.or::after{content:'';flex:1;"
            "border-top:1px solid #ddd}"
            "input[type=email]{box-sizing:border-box;width:100%;font-size:1rem;padding:.6rem .7rem;"
            "border:1px solid #c8c8c8;border-radius:6px}"
            "button{border:1px solid #0078d4;border-radius:6px;background:#0078d4;color:#fff;"
            "font-weight:500}button:hover{filter:brightness(.95)}button+button{margin-left:.5rem}"
            "button.secondary{background:#fff;color:#333;border-color:#c8c8c8}"
            ".logo{display:block;margin-bottom:1.5rem}.logo img{height:44px;width:auto}"
            "h1{font-size:1.6rem;line-height:1.25}</style></head><body>"
            # Every CCT app ships the shared logo at /static/logo.png (cct_common/static).
            "<a class='logo' href='/'><img src='/static/logo.png' alt='CheapCAD Tools'></a>"
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
        if not token:       # not from an emailed link: a button here could never work
            return redirect(f"/account/sign-in?next={quote(nxt, safe='/')}", code=302)
        return _page(f"Sign in to {app_name}", (
            "<p>Press the button to finish signing in.</p>"
            "<form method='post' action='/account/login'>"
            f"<input type='hidden' name='token' value='{html.escape(token, quote=True)}'>"
            f"<input type='hidden' name='next' value='{html.escape(nxt, quote=True)}'>"
            "<button type='submit'>Sign in</button></form>"))

    def login_submit():
        account_id = accounts.redeem_login_link(request.form.get("token", ""),
                                                ip=_client_ip())
        if not account_id:
            return _page("Link expired", (
                "<p>This sign-in link has expired or was already used. "
                "Links work once and last 15 minutes.</p>"
                "<p><a href='/'>Request a new one</a></p>"), status=400)
        return _finish_sign_in(account_id, request.form.get("next"))

    # ── Directory reviewers' sign-in ──────────────────────────────────────
    # Anthropic's and OpenAI's directory reviewers need a test account they
    # can enter without our emailed links. A secret link signs into one
    # account: /account/review?key=<REVIEW_LOGIN_KEY>. It exists only while
    # REVIEW_LOGIN_EMAIL and REVIEW_LOGIN_KEY (32+ characters) are set; unset
    # either to shut it. Linked from nowhere; any wrong key is a plain 404.
    import hmac
    import os
    review_email = (os.environ.get("REVIEW_LOGIN_EMAIL") or "").strip()
    review_key = os.environ.get("REVIEW_LOGIN_KEY") or ""
    review_on = bool(review_email) and len(review_key) >= 32

    def _review_ok(key: str) -> bool:
        return review_on and hmac.compare_digest(key.encode(), review_key.encode())

    def review_page():
        key = request.args.get("key", "")
        if not _review_ok(key):
            return _page("Not found", "<p>That page isn't here.</p>", status=404)
        nxt = _safe_next(request.args.get("next"))
        # A button, not a sign-in on GET: link previews and prefetchers mustn't sign in.
        return _page(f"Sign in to {app_name}", (
            "<p>This is the test account for directory reviewers.</p>"
            "<form method='post' action='/account/review'>"
            f"<input type='hidden' name='key' value='{html.escape(key, quote=True)}'>"
            f"<input type='hidden' name='next' value='{html.escape(nxt, quote=True)}'>"
            "<button type='submit'>Sign in to the test account</button></form>"))

    def review_submit():
        if not _review_ok(request.form.get("key", "")):
            return _page("Not found", "<p>That page isn't here.</p>", status=404)
        account_id = tokens.get_or_create_account(normalize_email(review_email), signup_grant=0)
        return _finish_sign_in(account_id, request.form.get("next"))

    # The same account by email and password, for a review form that wants
    # a login rather than a link (OpenAI's): /account/review-login, with
    # REVIEW_LOGIN_EMAIL and REVIEW_LOGIN_KEY as the password. A 404 while off.
    def _review_login_form(nxt: str, error: str = "", status: int = 200) -> Response:
        return _page(f"Sign in to {app_name}", (
            "<p>The test account for directory reviewers.</p>"
            + (f"<p><strong>{html.escape(error)}</strong></p>" if error else "")
            + "<form method='post' action='/account/review-login'>"
            f"<input type='hidden' name='next' value='{html.escape(nxt, quote=True)}'>"
            "<p><label>Email<br><input type='email' name='email' autocomplete='username' required></label></p>"
            "<p><label>Password<br><input type='password' name='password' "
            "autocomplete='current-password' required></label></p>"
            "<button type='submit'>Sign in</button></form>"), status=status)

    def review_login_page():
        if not review_on:
            return _page("Not found", "<p>That page isn't here.</p>", status=404)
        return _review_login_form(_safe_next(request.args.get("next")))

    def review_login_submit():
        if not review_on:
            return _page("Not found", "<p>That page isn't here.</p>", status=404)
        email_ok = request.form.get("email", "").strip().lower() == review_email.lower()
        if not (_review_ok(request.form.get("password", "")) and email_ok):
            return _review_login_form(_safe_next(request.form.get("next")),
                                      "Wrong email or password.", status=403)
        account_id = tokens.get_or_create_account(normalize_email(review_email), signup_grant=0)
        return _finish_sign_in(account_id, request.form.get("next"))

    def _oauth_buttons(nxt: str, safe: str = "/") -> str:
        """'Continue with Google / GitHub …' with each one's logo, for the
        providers this app has set up; empty if none. Back to `nxt` after."""
        from .oauth import ICONS, PROVIDERS
        enc = html.escape(quote(nxt, safe=safe), quote=True)
        links = "".join(
            f"<a class='btn-oauth' href='/account/oauth/{p}/start?next={enc}'>{ICONS[p]}"
            f"<span>Continue with {html.escape(PROVIDERS[p]['label'])}</span></a>"
            for p in app.extensions[_EXT_KEY].get("oauth_providers") or [])
        return f"<div class='oauth'>{links}</div>" if links else ""

    def sign_in_page():
        """For pages outside the tool (the admin dashboard, a bookmarked
        link) that need a signed-in visitor; the tool itself signs in from
        its own dialog."""
        nxt = _safe_next(request.args.get("next"))
        acct = current_account_id()
        if acct:
            return _page("Signed in", (
                f"<p>You're signed in as <strong>{html.escape(tokens.account_email(acct) or '')}"
                "</strong>.</p>"
                f"<p><a href='{html.escape(nxt, quote=True)}'>Continue</a></p>"
                "<p><button class='secondary' type='button' onclick=\"fetch('/api/account/logout',{method:'POST'})"
                ".then(()=>location.reload())\">Sign in with a different account</button></p>"))
        buttons = _oauth_buttons(nxt)
        return _page(f"Sign in to {app_name}", (
            buttons + ("<div class='or'>or</div>" if buttons else "")
            + "<form method='post' action='/account/sign-in'>"
            f"<input type='hidden' name='next' value='{html.escape(nxt, quote=True)}'>"
            "<p><input type='email' name='email' placeholder='you@example.com' required "
            "style='font-size:1rem;padding:.4rem;width:100%'></p>"
            "<button type='submit'>Email me a sign-in link</button></form>"))

    def sign_in_submit():
        nxt = _safe_next(request.form.get("next"))
        try:
            email = normalize_email(request.form.get("email", ""))
        except ValueError:
            return _page(f"Sign in to {app_name}", "<p>Enter a valid email address.</p>", 400)
        try:
            token = accounts.create_login_link(email, ip=_client_ip())
        except RateLimited:
            return _page(f"Sign in to {app_name}",
                         "<p>Too many sign-in emails requested. Try again in an hour.</p>", 429)
        link = f"{request.host_url}account/login?token={token}&next={quote(nxt, safe='/')}"
        ok, _ = email_sender(email, subject, body_fn(link))
        if not ok:
            return _page(f"Sign in to {app_name}",
                         "<p>Couldn't send the sign-in email. Please try again.</p>", 502)
        return _page("Check your inbox", (
            f"<p>We sent a sign-in link to {html.escape(email)}. It works once, within 15 "
            "minutes, and brings you back here. Check your junk folder if it isn't there.</p>"))

    def _finish_sign_in(account_id: str, nxt: Optional[str]):
        """Start a browser session and go on to `nxt` — shared by the email
        link and OAuth. A new account made without its free tokens (the
        per-network daily limit) is told why first, once."""
        nxt = _safe_next(nxt)
        session = accounts.create_session(account_id, kind="web",
                                          label=(request.user_agent.string or "")[:80])
        if accounts.take_signup_notice(account_id):
            r = _page("You're signed in", (
                "<p>Welcome! Your account is ready.</p>"
                "<p>New accounts usually start with free tokens, but this network has "
                "already had its free tokens for today, so this account starts with none. "
                "You can buy tokens whenever you need them.</p>"
                f"<p><a href='{html.escape(nxt, quote=True)}'>Continue</a></p>"))
        else:
            r = redirect(nxt, code=303)
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
        # When tokens go (accounts.py, inactivity): all of them, when the
        # account closes after 5 years without a sign-in — counted from the
        # last sign-in, so each sign-in pushes it out. Free tokens are shown
        # apart because they can't be refunded.
        last = accounts.last_sign_in(acct)
        free = tokens.free_remaining(acct)
        from flask import current_app
        return jsonify({
            "email": tokens.account_email(acct),
            "balance": tokens.balance(acct),
            "free": free,
            "purchased": tokens.purchased_remaining(acct),
            "last_sign_in": last,
            "account_expires": last + DEAD_ACCOUNT_IDLE_S,
            "buy_url": current_app.extensions[_EXT_KEY].get("buy_url", ""),
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
        request.environ[_SESSION_KEY] = None
        r = jsonify({"ok": True})
        r.delete_cookie(COOKIE_NAME, path="/")
        return r

    # ── CAD add-in sign-in (device code) ──────────────────────────────────

    def device_start():
        data = request.get_json(silent=True) or {}
        try:
            d = accounts.start_device_login(label=str(data.get("label") or ""), ip=_client_ip())
        except RateLimited:
            return jsonify({"error": "Too many sign-in attempts. Try again in an hour.",
                            "code": "RATE_LIMITED"}), 429
        page = f"{request.host_url}account/device"
        return jsonify(dict(d, verify_url=page,
                            verify_url_complete=f"{page}?code={quote(d['user_code'])}"))

    def device_poll():
        data = request.get_json(silent=True) or {}
        status, token = accounts.poll_device(str(data.get("device_code") or ""))
        if status == "invalid":
            return jsonify({"status": status, "error": "unknown or already used code"}), 400
        body = {"status": status}
        if token:
            body["token"] = token
        return jsonify(body)

    def _device_page(title, inner, status=200, ai=False):
        warn = ("Only approve if you just chose to connect CheapCAD Tools from your own AI app."
                if ai else "Only approve a code you just saw in your own CAD add-in.")
        return _page(title, inner + f"<p style='color:#666;font-size:.9rem'>{warn} "
                     "Never approve a request someone sent you.</p>", status)

    def _then_field(then):
        return (f"<input type='hidden' name='then' value='{html.escape(then, quote=True)}'>"
                if then else "")

    def _code_form(value="", then=None):
        return ("<form method='get' action='/account/device'>"
                "<p><label>Code shown in your add-in<br>"
                f"<input name='code' value='{html.escape(value, quote=True)}' autocomplete='off' "
                "style='font:1.2rem monospace;letter-spacing:.1em;padding:.4rem;margin-top:.3rem'>"
                f"</label></p>{_then_field(then)}<button type='submit'>Continue</button></form>")

    def device_page():
        code = accounts.normalize_user_code(request.args.get("code", ""))
        # An AI app connecting through the hosted MCP gateway (cct_common.mcp_oauth)
        # comes here with then=<its callback>: the same approval, then back to it.
        then = _safe_then(request.args.get("then"))
        what = "an AI app" if then else "your CAD add-in"
        heading = "Connect an AI app" if then else "Connect a CAD add-in"
        acct = current_account_id()
        if not acct:
            # Google / GitHub … come back to this very page (code and then kept).
            buttons = _oauth_buttons(request.full_path, safe="")
            return _device_page(f"Sign in to {app_name}", (
                f"<p>To connect {what}, sign in first.</p>"
                + (buttons + "<div class='or'>or get an email link</div>" if buttons else
                   "<p>We'll email you a link that brings you back here.</p>")
                + "<form method='post' action='/account/device/sign-in'>"
                f"<input type='hidden' name='code' value='{html.escape(code, quote=True)}'>"
                f"{_then_field(then)}"
                "<p><input type='email' name='email' placeholder='you@example.com' required "
                "style='font-size:1rem;padding:.4rem;width:100%'></p>"
                "<button type='submit'>Email me a link</button></form>"), ai=bool(then))
        req = accounts.device_request(code) if code else None
        if not req:
            msg = "<p>That code isn't recognised. Check it and try again.</p>" if code else ""
            return _device_page(heading, msg + _code_form(code, then), ai=bool(then))
        if req["state"] != "pending":
            if then and req["state"] in ("approved", "denied"):
                return redirect(then, code=303)          # the callback finishes it
            again = "Start connecting again in your AI app." if then else "Start sign-in again in the add-in."
            text = {"approved": "This add-in is already connected.",
                    "denied": "This request was denied.",
                    "expired": f"This code has expired. {again}"}[req["state"]]
            return _device_page(heading, f"<p>{text}</p>", ai=bool(then))
        limit_for = "this AI app" if then else "this add-in"
        return _device_page(heading, (
            f"<p>Allow <strong>{html.escape(req['label'])}</strong> to use your account "
            f"({html.escape(tokens.account_email(acct) or '')})?</p>"
            + ("" if then else
               f"<p style='font:1.4rem monospace;letter-spacing:.12em'>{html.escape(code)}</p>")
            + "<form method='post' action='/account/device'>"
            f"<input type='hidden' name='code' value='{html.escape(code, quote=True)}'>"
            f"{_then_field(then)}"
            f"<p><label>Daily limit for {limit_for}<br>"
            f"{_budget_select(accounts.device_daily_budget)}</label><br>"
            "<span style='color:#666;font-size:.9rem'>It stops at this many tokens in any "
            "24 hours and we email you. Change it any time on "
            "<a href='/account/devices'>your connected add-ins page</a>.</span></p>"
            "<p style='display:flex;gap:.6rem'>"
            "<button type='submit' name='decision' value='approve'>Approve</button>"
            "<button class='secondary' type='submit' name='decision' value='deny'>Deny</button></p></form>"),
            ai=bool(then))

    def device_decide():
        acct = current_account_id()
        if not acct:
            return redirect("/account/device", code=303)
        code = request.form.get("code", "")
        approve = request.form.get("decision") == "approve"
        extra = {}
        if approve and "daily_budget" in request.form:
            try:
                extra["daily_budget"] = parse_budget(request.form["daily_budget"])
            except ValueError:
                return _device_page("Connect a CAD add-in",
                                    "<p>Choose a daily limit from the list.</p>", status=400)
        then = _safe_then(request.form.get("then"))
        if not accounts.decide_device(code, acct, approve=approve, **extra):
            return _device_page("Connect a CAD add-in",
                                "<p>This code has expired or was already used. "
                                "Start sign-in again in the add-in.</p>", status=400, ai=bool(then))
        if then:
            return redirect(then, code=303)              # back to the AI app, approved or not
        if approve:
            return _device_page("Add-in connected",
                                "<p>Done — go back to your CAD program; it will finish signing in "
                                "by itself. You can close this tab.</p>")
        return _device_page("Request denied", "<p>The add-in was not connected.</p>")

    def device_sign_in():
        code = accounts.normalize_user_code(request.form.get("code", ""))
        back = _safe_next(request.form.get("next"))
        try:
            email = normalize_email(request.form.get("email", ""))
        except ValueError:
            return _device_page(f"Sign in to {app_name}", "<p>Enter a valid email address.</p>", 400)
        try:
            token = accounts.create_login_link(email, ip=_client_ip())
        except RateLimited:
            return _device_page(f"Sign in to {app_name}",
                                "<p>Too many sign-in emails requested. Try again in an hour.</p>", 429)
        then = _safe_then(request.form.get("then"))
        nxt = back if back != "/" else "/account/device" + (f"?code={code}" if code else "")
        if then and back == "/":
            nxt += ("&" if "?" in nxt else "?") + "then=" + quote(then, safe="")
        link = f"{request.host_url}account/login?token={token}&next={quote(nxt, safe='/')}"
        ok, _ = email_sender(email, subject, body_fn(link))
        if not ok:
            return _device_page(f"Sign in to {app_name}",
                                "<p>Couldn't send the sign-in email. Please try again.</p>", 502)
        return _device_page("Check your inbox", (
            f"<p>We sent a sign-in link to {html.escape(email)}. It brings you back here to "
            "approve the add-in. The link works once and expires in 15 minutes.</p>"))

    def _budget_select(current, name="daily_budget"):
        opts = list(BUDGET_CHOICES)
        if current is not None and current not in opts:
            opts = sorted(opts + [current])
        html_opts = "".join(
            f"<option value='{n}'{' selected' if n == current else ''}>{n} tokens a day</option>"
            for n in opts)
        html_opts += f"<option value='none'{' selected' if current is None else ''}>No limit</option>"
        return f"<select name='{name}' style='font-size:1rem;padding:.3rem'>{html_opts}</select>"

    def devices_page(notice=""):
        acct = current_account_id()
        if not acct:
            return _page(f"Sign in to {app_name}", (
                "<p>Sign in to see the add-ins and agents connected to your account. We'll email "
                "you a link that brings you back here.</p>"
                "<form method='post' action='/account/device/sign-in'>"
                "<input type='hidden' name='next' value='/account/devices'>"
                "<p><input type='email' name='email' placeholder='you@example.com' required "
                "style='font-size:1rem;padding:.4rem;width:100%'></p>"
                "<button type='submit'>Email me a link</button></form>"))
        rows = [s for s in accounts.list_sessions(acct) if s["kind"] == "device"]
        if not rows:
            body = "<p>No add-ins or agents are connected.</p>"
        else:
            body = ""
            for s in rows:
                limit = s["daily_budget"]
                body += (
                    "<form method='post' action='/account/devices' "
                    "style='border-top:1px solid #ddd;padding:.8rem 0'>"
                    f"<strong>{html.escape(s['label'] or 'Add-in')}</strong><br>"
                    f"<span style='color:#666'>Spent in the last 24 hours: {s['spent_24h']} "
                    f"token{'s' if s['spent_24h'] != 1 else ''}"
                    + (f" of {limit}" if limit is not None else "") + "</span>"
                    f"<input type='hidden' name='session_id' value='{s['id']}'>"
                    f"<p><label>Daily limit {_budget_select(limit)}</label></p>"
                    "<p style='display:flex;gap:.6rem'>"
                    "<button type='submit' name='action' value='save'>Save</button>"
                    "<button class='secondary' type='submit' name='action' value='revoke'>Disconnect</button></p></form>")
        return _page("Connected add-ins", (notice + body + (
            "<p style='color:#666;font-size:.9rem'>A limit stops an add-in or agent once it has "
            "spent that many tokens in any 24 hours, and emails you. It doesn't affect downloads "
            "you make in the browser.</p>")))

    def devices_update():
        acct = current_account_id()
        if not acct:
            return redirect("/account/devices", code=303)
        try:
            sid = int(request.form.get("session_id", ""))
        except ValueError:
            return devices_page("<p style='color:#b00020'>Unknown add-in.</p>"), 400
        if request.form.get("action") == "revoke":
            ok = accounts.revoke_session_id(acct, sid)
            return devices_page("<p>Disconnected.</p>" if ok
                                else "<p style='color:#b00020'>Unknown add-in.</p>")
        try:
            budget = parse_budget(request.form.get("daily_budget"))
        except ValueError:
            return devices_page("<p style='color:#b00020'>Choose a daily limit.</p>")
        ok = accounts.set_session_budget(acct, sid, budget)
        return devices_page("<p>Saved.</p>" if ok else "<p style='color:#b00020'>Unknown add-in.</p>")

    app.add_url_rule("/account/devices", view_func=devices_page, methods=["GET"])
    app.add_url_rule("/account/devices", endpoint="account_devices_update",
                     view_func=devices_update, methods=["POST"])
    app.add_url_rule("/api/account/device/start", view_func=device_start, methods=["POST"])
    app.add_url_rule("/api/account/device/poll", view_func=device_poll, methods=["POST"])
    app.add_url_rule("/account/device", view_func=device_page, methods=["GET"])
    app.add_url_rule("/account/device", endpoint="account_device_decide",
                     view_func=device_decide, methods=["POST"])
    app.add_url_rule("/account/device/sign-in", view_func=device_sign_in, methods=["POST"])
    app.add_url_rule("/account/review", view_func=review_page, methods=["GET"])
    app.add_url_rule("/account/review", endpoint="account_review_submit",
                     view_func=review_submit, methods=["POST"])
    app.add_url_rule("/account/review-login", view_func=review_login_page, methods=["GET"])
    app.add_url_rule("/account/review-login", endpoint="account_review_login_submit",
                     view_func=review_login_submit, methods=["POST"])
    app.add_url_rule("/account/sign-in", view_func=sign_in_page, methods=["GET"])
    app.add_url_rule("/account/sign-in", endpoint="account_sign_in_submit",
                     view_func=sign_in_submit, methods=["POST"])
    app.add_url_rule("/api/account/login-link", view_func=login_link, methods=["POST"])
    app.add_url_rule("/account/login", view_func=login_page, methods=["GET"])
    app.add_url_rule("/account/login", endpoint="account_login_submit",
                     view_func=login_submit, methods=["POST"])
    app.add_url_rule("/api/account/logout", view_func=logout, methods=["POST"])
    app.add_url_rule("/api/account", view_func=account_info, methods=["GET"])
    app.add_url_rule("/api/account/history", view_func=account_history, methods=["GET"])
    app.add_url_rule("/api/account/sessions/<int:session_id>", view_func=revoke, methods=["DELETE"])
    app.add_url_rule("/api/account/delete", view_func=delete_account, methods=["POST"])

    from .oauth import register_oauth_routes
    app.extensions[_EXT_KEY]["oauth_providers"] = register_oauth_routes(
        app, accounts, clients=oauth_clients or {}, finish=_finish_sign_in, page=_page,
        client_ip=_client_ip, secure_cookies=secure_cookies, http=oauth_http,
        clock=accounts._clock)
