"""
mcp_oauth.py — OAuth 2.1 for the hosted MCP gateway (docs/AGENT_API.md,
"Hosting"). An AI app (Claude, ChatGPT, Cursor …) adds
https://cheapcadtools.com/mcp as a connector; it registers itself, sends the
person here to sign in and approve, and gets back a token it then sends
with every tool call.

Built on the add-ins' device sign-in (cct_common.accounts), not beside it:

  1. The app registers (RFC 7591) and starts authorization; the `mcp` SDK
     checks the request (PKCE, redirect URI) and calls authorize() below.
  2. authorize() opens a device sign-in for "<app> (AI connector)" and sends
     the person to the ordinary approval page, /account/device, with
     then=/mcp/oauth/callback?request=… — they sign in, pick the daily
     token limit, approve.
  3. The approval page returns them to callback(), which turns the approved
     request into a one-time authorization code for the app's redirect URI.
  4. The app exchanges the code (the SDK checks PKCE); exchange collects the
     device token through poll_device — the same one-time hand-out an
     add-in gets — and that token is the access token.

So an AI connection is a device session like any add-in's: charged the same
way, under its daily limit, listed and revocable at /account/devices. No
refresh tokens: a device session lasts a year; after that the app signs in
again. The gateway checks a token with AccountStore.session_info().

Both this and the accounts store must use the same database (DATABASE_URL):
the gateway runs as its own service, the approval page in the apps.
"""
from __future__ import annotations

import json
import secrets
import time
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .account_routes import MCP_CALLBACK_PATH
from .accounts import SESSION_TTL_S, AccountStore, _hash
from .sqlite_db import SqliteDB

SCOPE = "cct"
CODE_TTL_S = 5 * 60          # an authorization code must be exchanged within this
REQUEST_TTL_S = 15 * 60      # an authorization started must be approved within this

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_clients (
    client_id   TEXT PRIMARY KEY,
    info        TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mcp_auth_requests (
    request_id  TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL,
    params      TEXT NOT NULL,
    device_code TEXT NOT NULL,
    user_code   TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mcp_auth_codes (
    code_hash   TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL,
    params      TEXT NOT NULL,
    device_code TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
"""


def with_query(url: str, **params) -> str:
    """url with params added to its query (keeping any it already has)."""
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v is not None]
    return urlunsplit(parts._replace(query=urlencode(q)))


class OAuthStore(SqliteDB):
    """Registered AI apps, authorizations awaiting approval, issued codes."""

    def __init__(self, path: str, clock=time.time):
        super().__init__(path, _SCHEMA)
        self.clock = clock

    def save_client(self, client_id: str, info_json: str) -> None:
        with self._write() as db:
            db.execute("DELETE FROM mcp_clients WHERE client_id = ?", (client_id,))
            db.execute("INSERT INTO mcp_clients (client_id, info, created_at) VALUES (?, ?, ?)",
                       (client_id, info_json, self.clock()))

    def client(self, client_id: str):
        with self._read() as db:
            row = db.execute("SELECT info FROM mcp_clients WHERE client_id = ?", (client_id,)).fetchone()
        return row["info"] if row else None

    def add_request(self, client_id: str, params: dict, device_code: str, user_code: str) -> str:
        rid = secrets.token_urlsafe(24)
        with self._write() as db:
            db.execute("DELETE FROM mcp_auth_requests WHERE created_at < ?", (self.clock() - REQUEST_TTL_S,))
            db.execute("INSERT INTO mcp_auth_requests (request_id, client_id, params, device_code, user_code, "
                       "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (rid, client_id, json.dumps(params), device_code, user_code, self.clock()))
        return rid

    def request(self, request_id: str):
        with self._read() as db:
            row = db.execute("SELECT * FROM mcp_auth_requests WHERE request_id = ? AND created_at >= ?",
                             (request_id, self.clock() - REQUEST_TTL_S)).fetchone()
        return dict(row) if row else None

    def drop_request(self, request_id: str) -> None:
        with self._write() as db:
            db.execute("DELETE FROM mcp_auth_requests WHERE request_id = ?", (request_id,))

    def add_code(self, client_id: str, params: dict, device_code: str) -> str:
        code = secrets.token_urlsafe(32)
        with self._write() as db:
            db.execute("DELETE FROM mcp_auth_codes WHERE expires_at < ?", (self.clock(),))
            db.execute("INSERT INTO mcp_auth_codes (code_hash, client_id, params, device_code, expires_at) "
                       "VALUES (?, ?, ?, ?, ?)",
                       (_hash(code), client_id, json.dumps(params), device_code, self.clock() + CODE_TTL_S))
        return code

    def code(self, code: str, client_id: str):
        with self._read() as db:
            row = db.execute("SELECT * FROM mcp_auth_codes WHERE code_hash = ? AND client_id = ? "
                             "AND expires_at >= ?", (_hash(code), client_id, self.clock())).fetchone()
        return dict(row) if row else None

    def take_code(self, code: str, client_id: str):
        """Spend a code (one caller wins): its device_code, or None."""
        with self._write() as db:
            row = db.execute("DELETE FROM mcp_auth_codes WHERE code_hash = ? AND client_id = ? "
                             "AND expires_at >= ? RETURNING device_code",
                             (_hash(code), client_id, self.clock())).fetchone()
        return row["device_code"] if row else None


class CCTOAuthProvider:
    """The `mcp` SDK's OAuthAuthorizationServerProvider, on CCT accounts."""

    def __init__(self, accounts: AccountStore, store: OAuthStore, site_url: str):
        self.accounts = accounts
        self.store = store
        self.site = site_url.rstrip("/")

    # ── clients (RFC 7591 dynamic registration) ──────────────────────────
    async def get_client(self, client_id: str):
        from mcp.shared.auth import OAuthClientInformationFull
        info = self.store.client(client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info) -> None:
        self.store.save_client(client_info.client_id, client_info.model_dump_json())

    # ── authorization: the approval page, then back via callback() ───────
    async def authorize(self, client, params) -> str:
        label = f"{(client.client_name or 'AI app').strip()[:40]} (AI connector)"
        d = self.accounts.start_device_login(label=label)
        rid = self.store.add_request(client.client_id, {
            "state": params.state, "scopes": params.scopes or [SCOPE],
            "code_challenge": params.code_challenge, "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource}, d["device_code"], d["user_code"])
        then = f"{MCP_CALLBACK_PATH}?request={rid}"
        return f"{self.site}/account/device?code={d['user_code']}&then={quote(then, safe='')}"

    async def callback(self, request):
        """GET /mcp/oauth/callback?request=… — where the approval page sends
        the person back. Approved: a one-time code to the app's redirect URI.
        Denied: access_denied to it. Otherwise a page saying what to do."""
        from starlette.responses import HTMLResponse, RedirectResponse
        rid = request.query_params.get("request", "")
        req = self.store.request(rid)
        if not req:
            return HTMLResponse(_page("This sign-in has expired",
                                      "Start connecting CheapCAD Tools again from your AI app."), 400)
        params = json.loads(req["params"])
        state = (self.accounts.device_request(req["user_code"]) or {}).get("state")
        if state == "approved":
            code = self.store.add_code(req["client_id"], params, req["device_code"])
            self.store.drop_request(rid)
            return RedirectResponse(with_query(params["redirect_uri"], code=code, state=params["state"]), 302)
        if state in ("denied", "expired", None):
            self.store.drop_request(rid)
            return RedirectResponse(with_query(params["redirect_uri"], error="access_denied",
                                               error_description="The request was not approved.",
                                               state=params["state"]), 302)
        approve = f"{self.site}/account/device?code={req['user_code']}&then=" + \
            quote(f"{MCP_CALLBACK_PATH}?request={rid}", safe="")
        return HTMLResponse(_page("Approve the connection first",
                                  f"<a href='{approve}'>Open the approval page</a> to finish."), 200)

    async def load_authorization_code(self, client, authorization_code: str):
        from mcp.server.auth.provider import AuthorizationCode
        row = self.store.code(authorization_code, client.client_id)
        if not row:
            return None
        p = json.loads(row["params"])
        return AuthorizationCode(code=authorization_code, scopes=p["scopes"], expires_at=row["expires_at"],
                                 client_id=row["client_id"], code_challenge=p["code_challenge"],
                                 redirect_uri=p["redirect_uri"],
                                 redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"],
                                 resource=p.get("resource"))

    async def exchange_authorization_code(self, client, authorization_code):
        from mcp.server.auth.provider import TokenError
        from mcp.shared.auth import OAuthToken
        device_code = self.store.take_code(authorization_code.code, client.client_id)
        status, token = self.accounts.poll_device(device_code) if device_code else ("invalid", None)
        if status != "approved" or not token:
            raise TokenError(error="invalid_grant", error_description="This authorization code is no longer valid.")
        return OAuthToken(access_token=token, expires_in=SESSION_TTL_S["device"],
                          scope=" ".join(authorization_code.scopes))

    # ── no refresh tokens: a device session lasts a year ─────────────────
    async def load_refresh_token(self, client, refresh_token: str):
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        from mcp.server.auth.provider import TokenError
        raise TokenError(error="invalid_grant", error_description="Refresh tokens aren't issued; sign in again.")

    # ── the gateway checks every call's token here ───────────────────────
    async def load_access_token(self, token: str):
        from mcp.server.auth.provider import AccessToken
        info = self.accounts.session_info(token)
        if not info or info["kind"] != "device":
            return None
        return AccessToken(token=token, client_id=info.get("label") or "cct", scopes=[SCOPE],
                           subject=info["account_id"])

    async def revoke_token(self, token) -> None:
        self.accounts.revoke_session(token.token)


def _page(title: str, body: str) -> str:
    return ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{title} — CheapCAD Tools</title>"
            "<body style='font-family:system-ui,sans-serif;max-width:34rem;margin:3rem auto;padding:0 1rem'>"
            f"<h1 style='font-size:1.4rem'>{title}</h1><p>{body}</p></body>")
