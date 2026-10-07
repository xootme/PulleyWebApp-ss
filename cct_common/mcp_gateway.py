"""
mcp_gateway.py — one MCP server for every CCT app (docs/AGENT_API.md).

A thin gateway: it knows no app's parameters. Each app describes itself at
/api/v1/describe and does its own checking and exporting (cct_common
agent_api); the gateway forwards, downloads the files, and handles sign-in.

    pip install cct_common[mcp]
    CCT_APPS="pulleys=http://127.0.0.1:5002" python -m cct_common.mcp_gateway

In an MCP client (Claude Code, Claude Desktop, …), a stdio server:

    {"command": "python", "args": ["-m", "cct_common.mcp_gateway",
     "--app", "pulleys=https://cheapcadtools.com/tools/pulleys"]}

`Gateway` is plain Python (stdlib HTTP, injectable for tests); `build_server`
wraps it in the `mcp` SDK's MCPServer, imported only there.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_PATH = Path.home() / ".cct" / "token"
USER_AGENT = "cct-mcp-gateway/1"


class GatewayError(Exception):
    """What an app (or the gateway) refused, in a sentence an agent can act on."""


def _urllib_http(method: str, url: str, body: dict | None = None, headers: dict | None = None):
    """(status, headers dict, bytes). No exception for HTTP error statuses."""
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except urllib.error.URLError as e:
        raise GatewayError(f"can't reach {url}: {e.reason}") from None


def parse_apps(spec: str | list | None) -> dict:
    """"name=url,name=url" (or a list of "name=url") → {name: url}."""
    items = spec if isinstance(spec, list) else [s for s in (spec or "").split(",")]
    out = {}
    for item in items:
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise GatewayError(f"an app is name=url, not {item!r}")
        name, url = item.split("=", 1)
        out[name.strip()] = url.strip().rstrip("/")
    return out


class Gateway:
    def __init__(self, apps: dict, token: str | None = None, http=None, token_path: Path = TOKEN_PATH,
                 local: bool = True):
        """local=False is the hosted gateway: never read CCT_TOKEN or a token
        file — each call brings its own (with_token)."""
        self.apps = dict(apps)
        self.token_path = Path(token_path)
        self.token = token or ((os.environ.get("CCT_TOKEN") or self._read_token()) if local else None)
        self.http = http or _urllib_http
        self._pending = {}                       # app -> device_code awaiting approval

    def with_token(self, token: str | None) -> "Gateway":
        """The same gateway acting for one caller (the hosted gateway: the
        OAuth access token of the request being served)."""
        g = Gateway(self.apps, token=token, http=self.http, token_path=self.token_path, local=False)
        return g

    # ── plumbing ─────────────────────────────────────────────────────────
    def _read_token(self):
        try:
            return self.token_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _base(self, app: str) -> str:
        if app not in self.apps:
            raise GatewayError(f"unknown app {app!r}; configured: {', '.join(self.apps) or 'none'}")
        return self.apps[app]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _call(self, app: str, method: str, path: str, body: dict | None = None) -> dict:
        status, _h, data = self.http(method, self._base(app) + path, body, self._headers())
        try:
            payload = json.loads(data or b"{}")
        except ValueError:
            payload = {}                         # an HTML error page: report the status alone
        if status >= 400:
            msg = payload.get("error") if isinstance(payload, dict) else None
            code = payload.get("code") if isinstance(payload, dict) else None
            raise GatewayError(f"{app}: {msg or f'HTTP {status}'}" + (f" [{code}]" if code else ""))
        return payload

    # ── tools ────────────────────────────────────────────────────────────
    def list_apps(self) -> list:
        out = []
        for name in self.apps:
            try:
                d = self._call(name, "GET", "/api/v1/describe")
                out.append({"app": name, "title": d.get("title", name), "summary": d.get("summary", ""),
                            "parts": [p["id"] for p in d.get("parts", [])]})
            except GatewayError as e:
                out.append({"app": name, "error": str(e)})
        return out

    def describe(self, app: str) -> dict:
        return self._call(app, "GET", "/api/v1/describe")

    def check(self, app: str, params: dict) -> dict:
        return self._call(app, "POST", "/api/v1/check", {"params": params})

    def quote(self, app: str, params: dict, formats: list | None = None) -> dict:
        return self._call(app, "POST", "/api/v1/quote", {"params": params, "formats": formats})

    def export(self, app: str, params: dict, formats: list | None = None, parts: list | None = None,
               out_dir: str = ".") -> dict:
        listing = self._call(app, "POST", "/api/v1/files",
                             {"params": params, "formats": formats, "parts": parts})
        out = Path(out_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        base = self._base(app)
        saved, charged, balance, problems = [], 0, None, []
        for f in listing.get("files", []):
            status, headers, data = self.http("GET", base + f["url"], None, self._headers())
            h = {k.lower(): v for k, v in headers.items()}
            if status >= 400:
                try:
                    msg = json.loads(data).get("error")
                except ValueError:
                    msg = None
                problems.append(f"{f['part']} {f['format']}: {msg or f'HTTP {status}'}")
                continue
            name = _filename(h.get("content-disposition", ""), f["name"])
            path = out / name
            path.write_bytes(data)
            charged += int(h.get("x-cct-tokens-charged", 0) or 0)
            if h.get("x-cct-tokens-balance"):
                balance = int(h["x-cct-tokens-balance"])
            saved.append({"part": f["part"], "format": f["format"], "path": str(path), "bytes": len(data)})
        if not saved and problems:
            raise GatewayError(f"{app}: no files downloaded — " + "; ".join(problems))
        return {"files": saved, "problems": problems, "tokens_charged": charged, "balance": balance,
                "design_id": listing.get("design_id")}

    def export_link(self, app: str, params: dict, formats: list | None = None, parts: list | None = None,
                    name: str | None = None, wait_s: float = 240.0, sleep=None) -> dict:
        """The hosted form of export: the files as one zip behind a download
        link — the app's Download-window bundle — for the person to open.
        Charged when that link is downloaded in full (cct_common.charging,
        "delivery"), refunded if it never is. A local gateway writes files
        instead (export)."""
        import time as _time
        from urllib.parse import parse_qsl, urlsplit
        sleep = sleep or _time.sleep
        listing = self._call(app, "POST", "/api/v1/files",
                             {"params": params, "formats": formats, "parts": parts})
        files = []
        for f in listing.get("files", []):
            u = urlsplit(f["url"])
            q = {k: v for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "design_id"}
            files.append({"path": u.path, "params": q})
        if not files:
            raise GatewayError(f"{app}: nothing to export for those formats and parts")
        job = self._call(app, "POST", "/api/download/bundle",
                         {"design_id": listing.get("design_id"), "name": name or app, "files": files})
        if "status_url" not in job:
            raise GatewayError(f"{app}: this app can't hand out a download link yet; use it in the browser")
        waited = 0.0
        while True:
            st = self._call(app, "GET", job["status_url"])
            if st.get("status") == "done":
                break
            if st.get("status") == "failed":
                raise GatewayError(f"{app}: the export failed: {st.get('error') or 'unknown error'}")
            if waited >= wait_s:
                raise GatewayError(f"{app}: the export is still running; try again in a minute")
            sleep(2.0)
            waited += 2.0
        out = st.get("output_file") or ""
        if out.startswith("/"):
            base = urlsplit(self._base(app))
            out = f"{base.scheme}://{base.netloc}{out}"
        return {"download_url": out, "files": [f["name"] for f in listing.get("files", [])],
                "design_id": listing.get("design_id"),
                "note": "Give the person this link. Tokens are taken only when the zip is downloaded "
                        "in full; the link works for about an hour."}

    def sign_in(self, app: str) -> dict:
        d = self._call(app, "POST", "/api/account/device/start", {"label": "MCP agent"})
        self._pending[app] = d["device_code"]
        return {"user_code": d["user_code"], "verify_url": d.get("verify_url_complete") or d.get("verify_url"),
                "next": "Ask the person to open the URL and approve the code, then call sign_in_finish."}

    def sign_in_finish(self, app: str) -> dict:
        code = self._pending.get(app)
        if not code:
            raise GatewayError("call sign_in first")
        d = self._call(app, "POST", "/api/account/device/poll", {"device_code": code})
        if d.get("token"):
            self.token = d["token"]
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(self.token, encoding="utf-8")
            self._pending.pop(app, None)
            return {"status": "signed in", "token_saved_to": str(self.token_path)}
        return {"status": d.get("status", "pending"),
                "next": "Not approved yet: wait a few seconds and call sign_in_finish again."}

    def balance(self, app: str) -> dict:
        if not self.token:
            raise GatewayError("not signed in: call sign_in (hosted: reconnect the CheapCAD Tools connector)")
        d = self._call(app, "GET", "/api/account")
        return {"email": d.get("email"), "balance": d.get("balance")}


def _filename(disposition: str, fallback: str) -> str:
    m = re.search(r'filename="?([^";]+)"?', disposition or "")
    name = (m.group(1) if m else fallback).strip()
    return re.sub(r'[\\/:*?"<>|]', "_", name) or "download"


# ── the MCP server ───────────────────────────────────────────────────────────

INSTRUCTIONS = (
    "CCT (Cheap CAD Tools) parametric part generators. Start with list_apps, then "
    "describe(app) for the app's parameters (their names, units, limits and choices). "
    "check(app, params) returns the design's dimensions, spec warnings and an Auto-fix "
    "(fix.set = parameter changes to apply). export(app, params, formats, parts, out_dir) "
    "writes the files. Downloads can cost tokens: quote first; if an app answers "
    "SIGN_IN_REQUIRED, use sign_in / sign_in_finish. All sizes are mm.")

INSTRUCTIONS_HOSTED = (
    "CCT (Cheap CAD Tools) parametric part generators, at cheapcadtools.com. Start with "
    "list_apps, then describe(app) for the app's parameters (their names, units, limits and "
    "choices). check(app, params) returns the design's dimensions, spec warnings and an "
    "Auto-fix (fix.set = parameter changes to apply) — apply it and check again until there "
    "are no warnings. Exporting costs the person tokens: call quote first and tell them the "
    "price, and export only once they agree. export(app, params, formats, parts) returns a "
    "download link for them to open; tokens are taken only when it is downloaded. "
    "All sizes are mm.")


def build_server(gateway: Gateway, *, hosted: bool = False, provider=None, auth=None):
    """The MCP server. hosted=True is the online gateway: every call acts for
    the caller's OAuth token (provider: cct_common.mcp_oauth), export returns
    a download link, and the device sign-in tools are gone (OAuth does it)."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import Icon, ToolAnnotations
    from . import __version__
    kw = {"auth_server_provider": provider, "auth": auth} if hosted else {}
    server = MCPServer(name="cct", title="CheapCAD Tools", version=__version__,
                       website_url="https://cheapcadtools.com/ai/",
                       icons=[Icon(src="https://cheapcadtools.com/assets/img/brand/icon-512.png",
                                   mime_type="image/png", sizes=["512x512"])],
                       instructions=INSTRUCTIONS_HOSTED if hosted else INSTRUCTIONS, **kw)

    # Every tool has a title and says whether it changes anything (the
    # directories require it; clients use it to decide what to confirm).
    def reads(title):
        return {"title": title, "annotations": ToolAnnotations(
            title=title, read_only_hint=True, destructive_hint=False, idempotent_hint=True,
            open_world_hint=False)}

    def acts(title):
        return {"title": title, "annotations": ToolAnnotations(
            title=title, read_only_hint=False, destructive_hint=False, idempotent_hint=False,
            open_world_hint=False)}

    def gw() -> Gateway:
        if not hosted:
            return gateway
        from mcp.server.auth.middleware.auth_context import get_access_token
        tok = get_access_token()
        return gateway.with_token(tok.token if tok else None)

    def run(name, *a, **kw):
        try:
            return getattr(gw(), name)(*a, **kw)
        except GatewayError as e:
            return {"error": str(e)}

    @server.tool(**reads("List the CAD tools"), description="The CCT apps this gateway reaches: name, title, summary, parts.")
    def list_apps() -> list:
        return run("list_apps")

    @server.tool(**reads("Describe a tool's parameters"), description="An app's parameters (name, type, unit, min/max, choices, default, "
                             "when they matter), its parts and formats. Read before check/export.")
    def describe(app: str) -> dict:
        return run("describe", app)

    @server.tool(**reads("Check a design"), description="Check a design: dimensions, spec warnings, and an Auto-fix — "
                             "fix.set holds parameter changes that clear the warnings.")
    def check(app: str, params: dict) -> dict:
        return run("check", app, params)

    @server.tool(**reads("Quote the token cost"), description="What exporting the design would cost in tokens (formats: step, "
                             "stl, svg, dxf). tokens=false means the app doesn't charge.")
    def quote(app: str, params: dict, formats: list[str] | None = None) -> dict:
        return run("quote", app, params, formats)

    if hosted:
        @server.tool(**acts("Export the design's files"), description="Make the design's files (formats: step, stl, svg, dxf; parts from "
                                 "describe) as one zip and return its download link for the person. "
                                 "Costs tokens when they download it: quote first and ask them.")
        def export(app: str, params: dict, formats: list[str] | None = None,
                   parts: list[str] | None = None) -> dict:
            return run("export_link", app, params, formats, parts)
    else:
        @server.tool(**acts("Export the design's files"), description="Write the design's files (formats: step, stl, svg, dxf; parts from "
                                 "describe) into out_dir; returns their paths. May cost tokens.")
        def export(app: str, params: dict, formats: list[str] | None = None,
                   parts: list[str] | None = None, out_dir: str = ".") -> dict:
            return run("export", app, params, formats, parts, out_dir)

        @server.tool(**acts("Start sign-in"), description="Start device sign-in: returns a code and a URL for the person to "
                                 "approve in their browser. Then call sign_in_finish.")
        def sign_in(app: str) -> dict:
            return run("sign_in", app)

        @server.tool(**acts("Finish sign-in"), description="Finish sign-in once the person has approved the code; saves the token.")
        def sign_in_finish(app: str) -> dict:
            return run("sign_in_finish", app)

    @server.tool(**reads("Token balance"), description="The signed-in account's token balance.")
    def balance(app: str) -> dict:
        return run("balance", app)

    return server


def build_hosted_app(apps: dict, database_url: str, site_url: str, http=None):
    """The online gateway as an ASGI app: MCP at /mcp (streamable HTTP,
    stateless, so any Cloud Run instance can answer any request), OAuth at
    the site's root (/.well-known/…, /authorize, /token, /register, /revoke)
    and the sign-in callback at /mcp/oauth/callback. The accounts database
    is the apps' own (DATABASE_URL), where the approval page records the
    person's decision."""
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
    from .accounts import AccountStore
    from .mcp_oauth import SCOPE, CCTOAuthProvider, OAuthStore
    from .tokens import TokenStore

    site = site_url.rstrip("/")
    accounts = AccountStore(TokenStore(database_url))
    provider = CCTOAuthProvider(accounts, OAuthStore(database_url), site)
    auth = AuthSettings(
        issuer_url=site, resource_server_url=f"{site}/mcp",
        service_documentation_url=f"{site}/agents/",
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True), required_scopes=[SCOPE])
    server = build_server(Gateway(apps, http=http, local=False), hosted=True, provider=provider, auth=auth)
    server.custom_route("/mcp/oauth/callback", methods=["GET"])(provider.callback)
    # DNS-rebinding protection: only our own host names. In production the
    # Cloudflare Worker forwards to the Cloud Run address, which arrives as
    # the Host: list it in CCT_MCP_ALLOWED_HOSTS (comma-separated).
    from urllib.parse import urlsplit
    from mcp.server.transport_security import TransportSecuritySettings
    host = urlsplit(site).netloc
    extra = [h.strip() for h in os.environ.get("CCT_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    hosts = [host, f"{host}:*"] + [x for h in extra for x in (h, f"{h}:*")]
    security = TransportSecuritySettings(allowed_hosts=hosts,
                                         allowed_origins=[site] + [f"https://{h}" for h in extra])
    app = server.streamable_http_app(stateless_http=True, json_response=True, transport_security=security)
    # Ours ahead of the SDK's /revoke (still the one the metadata advertises):
    # see CCTOAuthProvider.revoke for why the SDK's can't revoke our tokens.
    from starlette.routing import Route
    app.router.routes.insert(0, Route("/revoke", provider.revoke, methods=["POST"]))
    return app


def main(argv=None):
    ap = argparse.ArgumentParser(description="One MCP server for the CCT apps: stdio (local), "
                                             "or --http (the hosted gateway).")
    ap.add_argument("--app", action="append", default=[], help="name=url (repeatable); or CCT_APPS")
    ap.add_argument("--http", action="store_true", help="serve the hosted gateway over HTTP")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    ap.add_argument("--site", default=os.environ.get("CCT_SITE_URL", "https://cheapcadtools.com"),
                    help="the public site the gateway and its OAuth live under")
    args = ap.parse_args(argv)
    apps = parse_apps(args.app) or parse_apps(os.environ.get("CCT_APPS", ""))
    if not apps:
        sys.exit("no apps: give --app name=url or set CCT_APPS")
    if not args.http:
        build_server(Gateway(apps)).run("stdio")
        return
    db = os.environ.get("DATABASE_URL")
    if not db:
        sys.exit("the hosted gateway needs DATABASE_URL (the apps' accounts database)")
    import uvicorn
    uvicorn.run(build_hosted_app(apps, db, args.site), host=args.host, port=args.port,
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
