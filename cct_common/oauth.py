"""
oauth.py — "Continue with Google / Microsoft / GitHub" sign-in (PulleyWebApp-ss
ADR-008), feeding the same AccountStore.sign_in as the email link.

    register_oauth_routes(app, accounts, finish=..., clients={
        "google": ("<client id>", "<client secret>"), ...})

Routes (a provider appears only when its client id and secret are set):
    GET  /api/account/providers               {"providers": ["google", ...]}
    GET  /account/oauth/<provider>/start?next=/path
    GET  /account/oauth/<provider>/callback   (the redirect URI to register:
                                               <site>/account/oauth/<provider>/callback)

The flow is the authorization-code flow with PKCE (S256):
- /start makes a random `state` and PKCE verifier, keeps them in the
  database for 10 minutes (every server can finish the flow, not just the
  one that started it), and sends the browser to the provider.
- The state is also bound to this browser: /start sets a random cookie and
  the database row keeps its hash. Without that, someone could finish a
  sign-in with their own provider account and send the callback link to a
  victim, signing the victim into the attacker's account (login CSRF).
- /callback accepts a state once, checks the cookie, swaps the code for
  tokens server-to-server (client secret + PKCE verifier), and signs in.

Identity and email, per provider:
- Google (OpenID Connect): `sub`, and `email` only if `email_verified`.
- Microsoft (OpenID Connect, any Microsoft account): the subject is
  tenant id + object id, stable across apps. The email counts as verified
  only for personal Microsoft accounts (they're verified at signup) or when
  the token carries `xms_edov` (the organisation proved it owns the
  domain). A work account's `email` claim can otherwise be set by that
  organisation's admin to anyone's address — trusting it would let a
  stranger's tenant sign into, or link to, someone else's account (the
  "nOAuth" flaw). Add `xms_edov` as an optional ID-token claim in the app
  registration so verified work accounts work; without it they are asked
  to use the email link.
- GitHub (OAuth 2, not OpenID): the numeric user id, and the primary
  email only if GitHub marks it verified (/user/emails, scope user:email).

The ID tokens come straight from the provider's token endpoint over TLS
in exchange for our client secret, so their signature isn't re-checked
(OpenID Connect Core 3.1.3.7); issuer, audience and expiry still are.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
import time
from typing import Callable, Optional
from urllib.parse import urlencode

from .sqlite_db import SqliteDB

STATE_TTL_S = 10 * 60
STATE_COOKIE = "cct_oauth"
MS_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"   # personal Microsoft accounts

# Each provider's mark for its "Continue with …" button (their sign-in button
# guidelines ask for it): inline SVG, 18 px, decorative (the label says who).
_SVG = "<svg class='cct-oauth-icon' width='18' height='18' viewBox='{vb}' aria-hidden='true' focusable='false'>{body}</svg>"
ICONS = {
    "google": _SVG.format(vb="0 0 48 48", body=(
        "<path fill='#EA4335' d='M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 "
        "14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z'/>"
        "<path fill='#4285F4' d='M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 "
        "5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z'/>"
        "<path fill='#FBBC05' d='M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19"
        "C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z'/>"
        "<path fill='#34A853' d='M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 "
        "2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z'/>")),
    "microsoft": _SVG.format(vb="0 0 21 21", body=(
        "<rect x='1' y='1' width='9' height='9' fill='#F25022'/>"
        "<rect x='11' y='1' width='9' height='9' fill='#7FBA00'/>"
        "<rect x='1' y='11' width='9' height='9' fill='#00A4EF'/>"
        "<rect x='11' y='11' width='9' height='9' fill='#FFB900'/>")),
    "github": _SVG.format(vb="0 0 16 16", body=(
        "<path fill='currentColor' d='M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17"
        ".55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15"
        "-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78"
        "-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64"
        "-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51"
        ".56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 "
        "0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z'/>")),
}

PROVIDERS = {
    "google": {
        "label": "Google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "openid email",
        "extra": {"prompt": "select_account"},
    },
    "microsoft": {
        "label": "Microsoft",
        "authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": "openid email profile",
        "extra": {"prompt": "select_account"},
    },
    "github": {
        "label": "GitHub",
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scope": "read:user user:email",
        "extra": {"allow_signup": "true"},
    },
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_states (
    state_hash    TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    verifier      TEXT NOT NULL,
    browser_hash  TEXT NOT NULL,
    next          TEXT NOT NULL,
    created_at    REAL NOT NULL
);
"""


class OAuthError(Exception):
    """A sign-in that can't be completed; the message is shown to the person."""


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise OAuthError("The sign-in provider sent an unreadable answer.") from e


class OAuthStates(SqliteDB):
    """Sign-ins in progress, shared by every server through the database."""

    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        super().__init__(path, _SCHEMA)
        self.clock = clock

    def begin(self, provider: str, browser_secret: str, next_path: str) -> tuple[str, str]:
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        now = self.clock()
        with self._write() as db:
            db.execute("DELETE FROM oauth_states WHERE created_at < ?", (now - STATE_TTL_S,))
            db.execute("INSERT INTO oauth_states (state_hash, provider, verifier, browser_hash, "
                       "next, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (_hash(state), provider, verifier, _hash(browser_secret), next_path, now))
        return state, verifier

    def take(self, state: str, provider: str, browser_secret: Optional[str]) -> dict:
        """The started sign-in for this state, used up; OAuthError if it
        doesn't exist, expired, was for another provider or browser."""
        with self._write() as db:
            row = db.execute("SELECT * FROM oauth_states WHERE state_hash = ?",
                             (_hash(state or ""),)).fetchone()
            if row:
                db.execute("DELETE FROM oauth_states WHERE state_hash = ?", (row["state_hash"],))
        if (not row or row["provider"] != provider
                or self.clock() - row["created_at"] > STATE_TTL_S):
            raise OAuthError("This sign-in expired or was already used. Please try again.")
        if not browser_secret or not secrets.compare_digest(_hash(browser_secret),
                                                             row["browser_hash"]):
            raise OAuthError("This sign-in was started in a different browser. Please try again.")
        return dict(row)


# ── identity from each provider ───────────────────────────────────────────

def _check_id_token(claims: dict, *, client_id: str, issuers, now: float) -> None:
    aud = claims.get("aud")
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        raise OAuthError("The sign-in provider's answer was meant for another app.")
    if claims.get("iss") not in issuers:
        raise OAuthError("The sign-in answer came from an unexpected issuer.")
    if float(claims.get("exp", 0)) < now - 60:
        raise OAuthError("The sign-in provider's answer has expired. Please try again.")


def google_identity(tokens: dict, client_id: str, now: float, http) -> tuple[str, Optional[str], bool]:
    c = _jwt_claims(tokens.get("id_token", ""))
    _check_id_token(c, client_id=client_id, now=now,
                    issuers=("https://accounts.google.com", "accounts.google.com"))
    verified = c.get("email_verified") in (True, "true")
    return str(c["sub"]), c.get("email"), verified


def microsoft_identity(tokens: dict, client_id: str, now: float, http) -> tuple[str, Optional[str], bool]:
    c = _jwt_claims(tokens.get("id_token", ""))
    tid = str(c.get("tid", ""))
    _check_id_token(c, client_id=client_id, now=now,
                    issuers=(f"https://login.microsoftonline.com/{tid}/v2.0",))
    subject = f"{tid}:{c['oid']}" if c.get("oid") else str(c["sub"])
    email = c.get("email")
    verified = bool(email) and (tid == MS_CONSUMER_TENANT or c.get("xms_edov") in (True, "true", 1, "1"))
    return subject, email, verified


def github_identity(tokens: dict, client_id: str, now: float, http) -> tuple[str, Optional[str], bool]:
    access = tokens.get("access_token")
    if not access:
        raise OAuthError("GitHub didn't complete the sign-in. Please try again.")
    headers = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json",
               "User-Agent": "CheapCAD-Tools"}
    user = http.get("https://api.github.com/user", headers=headers, timeout=10)
    user.raise_for_status()
    emails = http.get("https://api.github.com/user/emails", headers=headers, timeout=10)
    emails.raise_for_status()
    primary = next((e for e in emails.json() if e.get("primary") and e.get("verified")), None)
    return str(user.json()["id"]), primary and primary["email"], bool(primary)


IDENTITY = {"google": google_identity, "microsoft": microsoft_identity, "github": github_identity}


# ── routes ────────────────────────────────────────────────────────────────

def register_oauth_routes(app, accounts, *, clients: dict, finish: Callable, page: Callable,
                          client_ip: Callable[[], Optional[str]], secure_cookies: bool = True,
                          http=None, clock: Callable[[], float] = time.time) -> list[str]:
    """clients: {provider: (client_id, client_secret)}; providers with a
    blank id or secret are left out. finish(account_id, next_path) returns
    the signed-in response (sets the session cookie); page(title, html,
    status) renders a plain message page; client_ip() the visitor's IP (the
    signup limit). Returns the enabled providers."""
    from flask import current_app, jsonify, redirect, request

    enabled = {p: c for p, c in (clients or {}).items()
               if p in PROVIDERS and c and c[0] and c[1]}
    states = OAuthStates(accounts.path, clock=clock)
    if http is None:
        import requests
        http = requests.Session()

    def redirect_uri(provider: str) -> str:
        return f"{request.url_root}account/oauth/{provider}/callback"

    def providers():
        return jsonify({"providers": [{"id": p, "label": PROVIDERS[p]["label"], "icon": ICONS[p],
                                       "start": f"/account/oauth/{p}/start"} for p in enabled]})

    def start(provider):
        if provider not in enabled:
            return page("Sign-in unavailable", "<p>That sign-in method isn't available.</p>", 404)
        from .account_routes import _safe_next
        browser = secrets.token_urlsafe(32)
        state, verifier = states.begin(provider, browser, _safe_next(request.args.get("next")))
        cfg = PROVIDERS[provider]
        params = {"client_id": enabled[provider][0], "response_type": "code",
                  "redirect_uri": redirect_uri(provider), "scope": cfg["scope"], "state": state,
                  "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
                  "code_challenge_method": "S256", **cfg["extra"]}
        r = redirect(f"{cfg['authorize']}?{urlencode(params)}", code=302)
        # Lax: the provider's redirect back is a top-level GET, which Lax allows.
        r.set_cookie(STATE_COOKIE, browser, max_age=STATE_TTL_S, httponly=True,
                     secure=secure_cookies, samesite="Lax", path="/account/oauth/")
        return r

    def callback(provider):
        label = PROVIDERS.get(provider, {}).get("label", "that provider")
        try:
            if provider not in enabled:
                raise OAuthError("That sign-in method isn't available.")
            if request.args.get("error"):
                # The person pressed Cancel at the provider, or it refused.
                raise OAuthError(f"{label} sign-in was cancelled.")
            flow = states.take(request.args.get("state", ""), provider,
                               request.cookies.get(STATE_COOKIE))
            client_id, secret = enabled[provider]
            resp = http.post(PROVIDERS[provider]["token"], headers={"Accept": "application/json"},
                             data={"grant_type": "authorization_code",
                                   "code": request.args.get("code", ""),
                                   "redirect_uri": redirect_uri(provider),
                                   "client_id": client_id, "client_secret": secret,
                                   "code_verifier": flow["verifier"]}, timeout=15)
            if resp.status_code != 200 or "error" in (resp.json() or {}):
                raise OAuthError(f"{label} didn't complete the sign-in. Please try again.")
            subject, email, verified = IDENTITY[provider](resp.json(), client_id, clock(), http)
            try:
                account_id = accounts.sign_in(provider, subject, email, email_verified=verified,
                                              ip=client_ip())
            except ValueError as e:
                raise OAuthError(
                    f"{label} didn't confirm an email address for this account, and an account "
                    f"needs one. Please sign in with an email link instead.") from e
        except Exception as e:     # noqa: BLE001 — never a bare 500 mid sign-in
            if not isinstance(e, OAuthError):
                current_app.logger.exception("%s sign-in failed", label)
                e = OAuthError(f"Couldn't reach {label} to finish signing in. Please try again.")
            r = page("Sign-in didn't finish",
                     f"<p>{html.escape(str(e))}</p><p><a href='/'>Back</a></p>", 400)
            r.delete_cookie(STATE_COOKIE, path="/account/oauth/")
            return r
        r = finish(account_id, flow["next"])
        r.delete_cookie(STATE_COOKIE, path="/account/oauth/")
        return r

    app.add_url_rule("/api/account/providers", view_func=providers, methods=["GET"])
    app.add_url_rule("/account/oauth/<provider>/start", view_func=start, methods=["GET"])
    app.add_url_rule("/account/oauth/<provider>/callback", view_func=callback, methods=["GET"])
    return list(enabled)
