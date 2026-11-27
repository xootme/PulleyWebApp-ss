"""
newsletter_site.py — the website's newsletter sign-up box, for people
without an account (0.67.0; the owner's "Newsletter choice B" in
CCT_WebSite's tab and "cctcommon newsletter route go", 2026-11-26; Controller
task cctcommon-newsletter-sign-up-route-for-the-website-plan-on).

    from cct_common.newsletter_site import SiteSignups
    site = SiteSignups(database_url)
    token = site.request("a@example.com", ip)   # None: this address had its email today
    site.confirm(token)                         # the address, now subscribed; None if not a live link

    POST /api/newsletter/subscribe  {email, website} as JSON (202 {"ok": true}, 400, 429)
                                    or a plain form post (an HTML page); website is a spam trap
    GET  /newsletter/confirm?t=...  the emailed link: a page with one button
    POST /newsletter/confirm        the button: subscribes, "You're subscribed"

- Double opt-in: a sign-up is a pending row (newsletter_site_pending) with
  a random single-use token, kept as its hash, valid CONFIRM_TTL_S (7 days).
  Unconfirmed ones are deleted after that (the email's promise), by the
  daily housekeeping (accounts.purge_expired).
- Limits: one confirm email per address per day (PER_ADDRESS_PER_DAY) and
  PER_IP_PER_DAY sign-ups per IP (IPv6: per /64, as account sign-ups),
  over the last 24 h. Past the IP cap, request() raises RateLimited.
- Confirming writes a consent row in newsletter_consents keyed
  "site:<email>" (newsletter.site_key), or on the address's account if it
  has one (Q-N6), app "site", source "site", with the
  box's words as label version 3 (newsletter.SITE_LABEL_VERSION). The
  existing sync adds the contact to the list; the reconcile counts it.
- Brevo's unsubscribe (the webhook) writes the 0 row, which queues the
  contact's deletion; then the address's rows here go (forget), as the
  privacy policy's "until you unsubscribe" says.
- An account made later for the address takes its rows over
  (newsletter.take_site, in AccountStore.sign_in; and the housekeeping for
  accounts made on a server still on an older cct_common).
- Behind newsletter_opt_in, like the account's checkbox (the routes refuse
  while it's off).
"""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Callable, Optional

from .sqlite_db import SqliteDB

CONFIRM_TTL_S = 7 * 24 * 3600
WINDOW_S = 24 * 3600
PER_ADDRESS_PER_DAY = 1
PER_IP_PER_DAY = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS newsletter_site_pending (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    ip_key      TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS newsletter_site_pending_email ON newsletter_site_pending (email, created_at);
CREATE INDEX IF NOT EXISTS newsletter_site_pending_ip ON newsletter_site_pending (ip_key, created_at);
"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _ip_key(ip: Optional[str]) -> Optional[str]:
    """The address for IPv4, its /64 for IPv6, hashed: never the IP itself."""
    if not ip:
        return None
    from .accounts import _network_key
    return _hash("newsletter-site:" + _network_key(ip))


def forget(db, email: str) -> None:
    """Inside a write: the address's consent rows and pending sign-ups go."""
    from .newsletter import site_key
    db.execute("DELETE FROM newsletter_consents WHERE account_id = ?", (site_key(email),))
    db.execute("DELETE FROM newsletter_site_pending WHERE email = ?", (email,))


def purge(db, now: float) -> None:
    """Inside the housekeeping's write: unconfirmed sign-ups past their 7 days."""
    db.execute("DELETE FROM newsletter_site_pending WHERE created_at < ?", (now - CONFIRM_TTL_S,))


class SiteSignups(SqliteDB):
    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        from .newsletter import SCHEMA as _NEWS_SCHEMA
        super().__init__(path, _NEWS_SCHEMA + SCHEMA)
        self._clock = clock

    def request(self, email: str, ip: Optional[str]) -> Optional[str]:
        """A pending sign-up for this (normalized) address, and its token for
        the confirm link; None when the address already had its email today.
        Raises accounts.RateLimited past the IP's cap."""
        from .accounts import RateLimited
        now, key = self._clock(), _ip_key(ip)
        with self._write() as db:
            if key is not None and db.execute(
                    "SELECT COUNT(*) FROM newsletter_site_pending WHERE ip_key = ? AND created_at > ?",
                    (key, now - WINDOW_S)).fetchone()[0] >= PER_IP_PER_DAY:
                raise RateLimited()
            if db.execute("SELECT COUNT(*) FROM newsletter_site_pending WHERE email = ? AND created_at > ?",
                          (email, now - WINDOW_S)).fetchone()[0] >= PER_ADDRESS_PER_DAY:
                return None
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO newsletter_site_pending (email, token_hash, ip_key, created_at) "
                       "VALUES (?, ?, ?, ?)", (email, _hash(token), key, now))
        return token

    def pending_email(self, token: str) -> Optional[str]:
        """The address a live link is for, without using it up."""
        if not token:
            return None
        with self._read() as db:
            r = db.execute("SELECT email FROM newsletter_site_pending WHERE token_hash = ? AND created_at > ?",
                           (_hash(token), self._clock() - CONFIRM_TTL_S)).fetchone()
        return r["email"] if r else None

    def take(self, token: str) -> Optional[str]:
        """Use the link up: its address, or None if it isn't live. The
        address's other pending sign-ups go with it."""
        if not token:
            return None
        with self._write() as db:
            r = db.execute("SELECT email FROM newsletter_site_pending WHERE token_hash = ? AND created_at > ?",
                           (_hash(token), self._clock() - CONFIRM_TTL_S)).fetchone()
            if r is None:
                return None
            db.execute("DELETE FROM newsletter_site_pending WHERE email = ?", (r["email"],))
        return r["email"]

    def cancel(self, token: str) -> None:
        """The confirm email couldn't be sent: the sign-up goes, so a retry can send it."""
        with self._write() as db:
            db.execute("DELETE FROM newsletter_site_pending WHERE token_hash = ?", (_hash(token or ""),))

    def confirm(self, token: str, *, app: str = "site") -> Optional[str]:
        """Use the link up and subscribe its address: on its account if it
        has one (Q-N6), else keyed site:<email>. The address, or None if the
        link isn't live."""
        email = self.take(token)
        if email is None:
            return None
        from .newsletter import SITE_LABEL_VERSION, Newsletter, site_key
        with self._read() as db:
            acct = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()
        Newsletter(self.path, clock=self._clock).record(acct["id"] if acct else site_key(email), True, app=app,
                                                        source="site", label_version=SITE_LABEL_VERSION)
        return email


# ── the routes (registered by account_routes; Pulley's are the website's) ──
# The owner's answers, 2026-11-26: Q-N1 a (the link opens a page with one
# button), Q-N2 a (an address's second sign-up in a day: 202, no email),
# Q-N3 a (PER_IP_PER_DAY), Q-N4 a (a page for a plain form post), Q-N5 a
# (plain text: "Click to confirm: <link>"), Q-N6 a (an address with an
# account is recorded on the account).

SUBJECT = "Confirm your CheapCADTools newsletter"
BODY = ("Someone, hopefully you, asked for the CheapCADTools newsletter at this address. "
        "Click to confirm: {link}\n\n"
        "If it wasn't you, ignore this email. We won't send you anything, and we delete your address after 7 days.\n")
CONFIRM_TITLE = "Confirm your CheapCADTools newsletter"
CONFIRM_BUTTON = "Yes, send me the newsletter"
DONE_TITLE = "You're subscribed"
DONE_TEXT = "The newsletter comes once a month, and every one has an unsubscribe link."
CHECK_TITLE = "Check your email"
CHECK_TEXT = "We sent you a link to confirm the newsletter; nothing is sent until you click it."
SITE_URL = "https://cheapcadtools.com/"
# The www pages post across origins (CCT_WebSite, 2026-11-26); the apex ones are same-origin.
CORS_ORIGINS = ("https://www.cheapcadtools.com", "http://www.cheapcadtools.com")
BAD_EMAIL = "Enter a valid email address."
TOO_MANY = "Too many newsletter sign-ups from your network today. Please try again tomorrow."
NOT_SENT = "Couldn't send the email. Please try again."
EXPIRED = "This link has expired or was already used."


def register_site_routes(app, accounts, *, email_sender, page, client_ip, opt_in_on) -> None:
    """POST /api/newsletter/subscribe and GET/POST /newsletter/confirm.
    page(title, inner_html, status) is account_routes' page."""
    import html
    from flask import Response, jsonify, request
    from .accounts import RateLimited, normalize_email

    site = SiteSignups(accounts.tokens.path, clock=accounts._clock)
    back = f"<p><a href='{SITE_URL}'>Back to cheapcadtools.com</a></p>"

    def _cors(r):
        origin = request.headers.get("Origin")
        if origin in CORS_ORIGINS:
            r.headers["Access-Control-Allow-Origin"] = origin
            r.headers["Access-Control-Allow-Methods"] = "POST"
            r.headers["Access-Control-Allow-Headers"] = "Content-Type"
            r.headers["Access-Control-Max-Age"] = "600"
        r.headers["Vary"] = "Origin"
        return r

    def subscribe():
        if request.method == "OPTIONS":
            return _cors(Response(status=204))
        if not opt_in_on():
            return _cors(jsonify({"error": "not found"})), 404
        form = not request.is_json
        data = request.form if form else request.get_json(silent=True)
        if not form and not isinstance(data, dict):
            data = {}

        def answer(status, error=None, code=None):
            if form:                                      # Q-N4: a browser without JavaScript gets a page
                if status == 202:
                    return page(CHECK_TITLE, f"<p>{html.escape(CHECK_TEXT)}</p>{back}", 200)
                return page("Newsletter", f"<p>{html.escape(error)}</p>{back}", status)
            body = {"ok": True} if status == 202 else {"error": error, **({"code": code} if code else {})}
            return _cors(jsonify(body)), status

        try:
            email = normalize_email(str(data.get("email") or ""))
        except ValueError:
            return answer(400, BAD_EMAIL)
        if str(data.get("website") or "").strip():          # the spam trap: the same answer, nothing done
            return answer(202)
        try:
            token = site.request(email, client_ip())
        except RateLimited:
            return answer(429, TOO_MANY, "RATE_LIMITED")
        if token is None:                                     # its email went today (Q-N2): say nothing
            return answer(202)
        link = f"{request.url_root}newsletter/confirm?t={token}"
        ok, _ = email_sender(email, SUBJECT, BODY.format(link=link))
        if not ok:
            site.cancel(token)                                # so a retry can send it
            return answer(502, NOT_SENT)
        return answer(202)

    def confirm_page():
        if not opt_in_on():
            return jsonify({"error": "not found"}), 404
        token = request.args.get("t", "")
        if site.pending_email(token) is None:
            return page("Link expired", f"<p>{EXPIRED}</p>{back}", 400)
        return page(CONFIRM_TITLE, (                         # Q-N1: a scanner opening the link confirms nothing
            "<form method='post' action='confirm'>"
            f"<input type='hidden' name='t' value='{html.escape(token, quote=True)}'>"
            f"<button type='submit'>{html.escape(CONFIRM_BUTTON)}</button></form>"))

    def confirm_submit():
        if not opt_in_on():
            return jsonify({"error": "not found"}), 404
        if site.confirm(request.form.get("t", "")) is None:
            return page("Link expired", f"<p>{EXPIRED}</p>{back}", 400)
        return page(DONE_TITLE, f"<p>{html.escape(DONE_TEXT)}</p>{back}")

    app.add_url_rule("/api/newsletter/subscribe", view_func=subscribe, methods=["POST", "OPTIONS"],
                     provide_automatic_options=False)
    app.add_url_rule("/newsletter/confirm", view_func=confirm_page, methods=["GET"])
    app.add_url_rule("/newsletter/confirm", endpoint="newsletter_confirm_submit",
                     view_func=confirm_submit, methods=["POST"])
