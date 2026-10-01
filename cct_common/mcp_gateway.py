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
    def __init__(self, apps: dict, token: str | None = None, http=None, token_path: Path = TOKEN_PATH):
        self.apps = dict(apps)
        self.token_path = Path(token_path)
        self.token = token or os.environ.get("CCT_TOKEN") or self._read_token()
        self.http = http or _urllib_http
        self._pending = {}                       # app -> device_code awaiting approval

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
            raise GatewayError("not signed in: call sign_in")
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


def build_server(gateway: Gateway):
    from mcp.server.mcpserver import MCPServer
    server = MCPServer(name="cct", title="Cheap CAD Tools", instructions=INSTRUCTIONS)

    def run(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except GatewayError as e:
            return {"error": str(e)}

    @server.tool(description="The CCT apps this gateway reaches: name, title, summary, parts.")
    def list_apps() -> list:
        return run(gateway.list_apps)

    @server.tool(description="An app's parameters (name, type, unit, min/max, choices, default, "
                             "when they matter), its parts and formats. Read before check/export.")
    def describe(app: str) -> dict:
        return run(gateway.describe, app)

    @server.tool(description="Check a design: dimensions, spec warnings, and an Auto-fix — "
                             "fix.set holds parameter changes that clear the warnings.")
    def check(app: str, params: dict) -> dict:
        return run(gateway.check, app, params)

    @server.tool(description="What exporting the design would cost in tokens (formats: step, "
                             "stl, svg, dxf). tokens=false means the app doesn't charge.")
    def quote(app: str, params: dict, formats: list[str] | None = None) -> dict:
        return run(gateway.quote, app, params, formats)

    @server.tool(description="Write the design's files (formats: step, stl, svg, dxf; parts from "
                             "describe) into out_dir; returns their paths. May cost tokens.")
    def export(app: str, params: dict, formats: list[str] | None = None,
               parts: list[str] | None = None, out_dir: str = ".") -> dict:
        return run(gateway.export, app, params, formats, parts, out_dir)

    @server.tool(description="Start device sign-in: returns a code and a URL for the person to "
                             "approve in their browser. Then call sign_in_finish.")
    def sign_in(app: str) -> dict:
        return run(gateway.sign_in, app)

    @server.tool(description="Finish sign-in once the person has approved the code; saves the token.")
    def sign_in_finish(app: str) -> dict:
        return run(gateway.sign_in_finish, app)

    @server.tool(description="The signed-in account's token balance.")
    def balance(app: str) -> dict:
        return run(gateway.balance, app)

    return server


def main(argv=None):
    ap = argparse.ArgumentParser(description="One MCP server for the CCT apps (stdio).")
    ap.add_argument("--app", action="append", default=[], help="name=url (repeatable); or CCT_APPS")
    args = ap.parse_args(argv)
    apps = parse_apps(args.app) or parse_apps(os.environ.get("CCT_APPS", ""))
    if not apps:
        sys.exit("no apps: give --app name=url or set CCT_APPS")
    build_server(Gateway(apps)).run("stdio")


if __name__ == "__main__":
    main()
