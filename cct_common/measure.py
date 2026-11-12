"""
measure.py — where sign-ups, downloads and purchases come from, and the GA4
tag in the apps (0.62.0). The owner's "Measure first" (2026-11-11, typed in
CCT_Business's tab; Controller campaign 2026-11-11-measure-first): "Ship the
GA tag in the apps and record where signups come from (F1)" and
"CCTCommon records surface and client on downloads, signups and purchases,
along with the ?ref= work already queued".

Both halves are business settings, off until the privacy text covers them
(CCT_WebSite's gate: the policy of Nov 8 says the tools have no analytics):

source_capture
  - A `?ref=` on any page an app serves (the website's links carry the
    placement: site-header, product-cta, ...) is kept in the first-party
    cookie COOKIE ("cct_src") for COOKIE_DAYS (30) days: the first one,
    not replaced by a later ?ref= while it lasts. Its value is a query
    string, `ref=<placement>`, so another key (the website's outside
    source, if the owner takes that up) needs no new cookie. HttpOnly,
    SameSite=Lax, Secure on https.
  - A new account records signup_ref (the cookie's ref when the sign-in
    was asked for), signup_surface and signup_client: "page", or, for a
    sign-in made to approve a device sign-in (/account/device), that
    sign-in's: "mcp" with the AI app for the hosted MCP's connectors
    (claude, chatgpt, ...) and the local MCP ("mcp-other"), "device" for
    any other device token. /mcp sets no cookie, so those have no ref.
  - Every spend, download and refund row records surface and client from
    the session that made it ("page" for a browser); every order its
    surface and ref.
  - Device sign-ins and sessions always keep their surface and client (a
    label's reading, never more about the person): the rows above copy it
    from there once source_capture is on.

analytics_tag
  - tag(path, app) is the GA4 snippet (TAG_ID, the website's own tag) for
    a page's <head>: every app's page includes it (`{{ cct_analytics() }}`,
    a Jinja global register_account_routes adds) and cct_common's own
    account, sign-in and buy pages carry it; the admin pages never do.
  - It loads on (www.)cheapcadtools.com only, as the website's base.html
    does, so a dev host or any other is never tagged, and never under
    /admin. page_location and page_referrer go without their query strings
    and fragments, but for ref: app addresses carry sign-in tokens, OAuth
    codes, share ids and whole designs.
  - Events (CCTAnalytics.event, from the shared JS): sign_up and login
    (method), purchase (transaction_id = the order id, value, currency),
    download (file_type, link_location); every one carries app, surface and
    ref. Custom dimension names: app, method, surface, ref, file_type,
    link_location. Never an email, a name or receipt details.
    A sign-in or purchase finishes on the server, so it leaves the event in
    the short-lived cookie EVENT_COOKIE and the page sends it
    (CCTAnalytics.flush(), on load and after the buy panel's payment).
"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

COOKIE = "cct_src"
COOKIE_DAYS = 30
EVENT_COOKIE = "cct_ev"
EVENT_COOKIE_S = 300
TAG_ID = "GT-5DFRBV7T"
EXT_KEY = "cct_measure"
SURFACES = ("page", "mcp", "agent_api", "addin", "device")
CLIENTS = ("claude", "chatgpt", "cursor", "fusion-addin", "mcp-other", "other")
MCP_LABEL = "MCP agent"                       # cct_mcp's device sign-in (the local MCP server)
CONNECTOR_SUFFIX = " (AI connector)"          # mcp_oauth's: "<client name> (AI connector)"
_REF = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def clean_ref(value) -> Optional[str]:
    """A placement as the website writes them, else None (nothing else is kept)."""
    v = str(value or "").strip()
    return v if _REF.match(v) else None


def capture_on(path: str) -> bool:
    from .settings import setting
    return setting(path, "source_capture") is True


def tag_on(path: str) -> bool:
    from .settings import setting
    return setting(path, "analytics_tag") is True


# ── surface and client ──────────────────────────────────────────────────────

def client_of(name: Optional[str], surface: str) -> Optional[str]:
    """The fixed client name for an AI app's or add-in's own name."""
    n = (name or "").lower()
    if surface == "page":
        return None
    for key, client in (("claude", "claude"), ("chatgpt", "chatgpt"), ("openai", "chatgpt"),
                        ("cursor", "cursor"), ("fusion", "fusion-addin")):
        if key in n:
            return client
    return "mcp-other" if surface == "mcp" else "other"


def label_source(label: Optional[str]) -> tuple:
    """(surface, client) a device sign-in's label says, for one started
    without them (cct_mcp's, and those from before 0.62.0)."""
    label = (label or "").strip()
    if label == MCP_LABEL:
        return "mcp", "mcp-other"
    if label.endswith(CONNECTOR_SUFFIX):
        return "mcp", client_of(label[:-len(CONNECTOR_SUFFIX)], "mcp")
    return "device", "other"


def device_source(label: Optional[str], surface=None, client=None) -> tuple:
    """(surface, client) for a device sign-in: what its starter said, when
    it's one of SURFACES / CLIENTS, else what its label says."""
    surface = surface if surface in SURFACES and surface != "page" else None
    client = client if client in CLIENTS else None
    if surface is None:
        return label_source(label)
    return surface, client or client_of(label, surface)


def session_source(session: Optional[dict]) -> tuple:
    """(surface, client) of the session that made a request: "page" for a
    browser (or none), a device session's own."""
    if not session or session.get("kind") != "device":
        return "page", None
    if session.get("surface"):
        return session["surface"], session.get("client")
    return label_source(session.get("label"))


# ── the request's ref ───────────────────────────────────────────────────────

def cookie_ref(cookie_value: Optional[str]) -> Optional[str]:
    if not cookie_value:
        return None
    try:
        return clean_ref((parse_qs(unquote(cookie_value)).get("ref") or [None])[0])
    except Exception:
        return None


def request_ref() -> Optional[str]:
    """The current request's ref: the cookie's, else its own ?ref= (the
    first page, whose answer sets the cookie)."""
    from flask import request
    return cookie_ref(request.cookies.get(COOKIE)) or clean_ref(request.args.get("ref"))


def request_source(path: str) -> dict:
    """{ref, surface, client} of the current request while source_capture
    is on, else all None (nothing is recorded)."""
    if not capture_on(path):
        return {"ref": None, "surface": None, "client": None}
    from .account_routes import current_session
    surface, client = session_source(current_session())
    return {"ref": request_ref(), "surface": surface, "client": client}


def signup_source(path: str, next_path: Optional[str], device_lookup=None) -> Optional[dict]:
    """What a sign-in asked for now records if it makes an account (kept
    with the sign-in link or the OAuth state, then given to sign_in):
    the request's ref, and the surface of where the sign-in goes — a
    device sign-in's approval page gives that device sign-in's, else
    "page". None while source_capture is off. device_lookup(user_code) ->
    (surface, client) or None."""
    if not capture_on(path):
        return None
    surface, client = "page", None
    parts = urlsplit(next_path or "")
    if parts.path.rstrip("/") == "/account/device":
        code = (parse_qs(parts.query).get("code") or [""])[0]
        found = device_lookup(code) if (device_lookup and code) else None
        surface, client = found or ("device", "other")
    return {"ref": request_ref(), "surface": surface, "client": client}


# ── the cookie and the events ───────────────────────────────────────────────

def _secure() -> bool:
    from flask import request
    return request.is_secure or request.headers.get("X-Forwarded-Proto", "") == "https"


def keep_ref(path: str, response):
    """after_request: a page answering a ?ref= sets the cookie, once."""
    from flask import request
    try:
        if (request.method != "GET" or response.status_code != 200 or response.mimetype != "text/html"
                or request.path.startswith("/admin") or request.cookies.get(COOKIE)):
            return response
        ref = clean_ref(request.args.get("ref"))
        if ref and capture_on(path):
            response.set_cookie(COOKIE, urlencode({"ref": ref}), max_age=COOKIE_DAYS * 24 * 3600, path="/",
                                secure=_secure(), httponly=True, samesite="Lax")
    except Exception:
        pass                                     # never break a page over it
    return response


def queue_event(path: str, response, name: str, params: dict):
    """Leave an event for the page to send (CCTAnalytics.flush) while the
    tag is on: a sign-in or a payment that finished on the server."""
    from flask import request
    try:
        if not tag_on(path):
            return response
        queued = []
        try:
            queued = json.loads(unquote(request.cookies.get(EVENT_COOKIE) or "[]"))
        except ValueError:
            pass
        queued = [q for q in queued if isinstance(q, list)][-4:] + [[name, params]]
        response.set_cookie(EVENT_COOKIE, quote(json.dumps(queued, separators=(",", ":"))),
                            max_age=EVENT_COOKIE_S, path="/", secure=_secure(), httponly=False,
                            samesite="Lax")
    except Exception:
        pass
    return response


# ── the tag ─────────────────────────────────────────────────────────────────

_TAG_JS = r"""window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
(function(c){
var on=/^(www\.)?cheapcadtools\.com$/.test(location.hostname)&&!/^\/admin(\/|$)/.test(location.pathname);
var ref=c.ref||null;
function clean(u){try{var x=new URL(u,location.href),r=x.searchParams.get('ref');
if(r&&!ref&&x.origin===location.origin)ref=r;
return x.origin+x.pathname+(r?'?ref='+encodeURIComponent(r):'');}catch(e){return '';}}
var loc=clean(location.href),from=document.referrer?clean(document.referrer):'';
var base={app:c.app,surface:'page'};if(ref)base.ref=ref;
function event(n,p){if(!on)return;var q={},k;for(k in base)q[k]=base[k];for(k in p||{})q[k]=p[k];
q.page_location=loc;q.page_referrer=from;gtag('event',n,q);}
function flush(){var m=document.cookie.match(/(?:^|;\s*)cct_ev=([^;]*)/);if(!m)return;
document.cookie='cct_ev=; Max-Age=0; Path=/; SameSite=Lax';
try{JSON.parse(decodeURIComponent(m[1])).forEach(function(e){event(e[0],e[1]);});}catch(e){}}
window.CCTAnalytics={on:on,event:event,flush:flush};
if(!on)return;
var s=document.createElement('script');s.async=true;
s.src='https://www.googletagmanager.com/gtag/js?id='+c.id;document.head.appendChild(s);
gtag('js',new Date());
gtag('set',{page_location:loc,page_referrer:from});
var cfg={page_location:loc,page_referrer:from,app:c.app,surface:'page'};if(ref)cfg.ref=ref;
gtag('config',c.id,cfg);
flush();
})(%s);"""


def tag(path: str, app: str) -> str:
    """The GA4 snippet for a page's <head> while analytics_tag is on, else ""."""
    try:
        if not tag_on(path):
            return ""
        ref = None
        try:
            ref = request_ref()
        except RuntimeError:                     # outside a request
            pass
        cfg = json.dumps({"id": TAG_ID, "app": str(app)[:40], "ref": ref}).replace("</", "<\\/")
        return "<script>" + (_TAG_JS % cfg) + "</script>"
    except Exception:
        return ""


def register(app, path: str, app_label: str) -> None:
    """The cookie on pages answering a ?ref=, and `cct_analytics()` for the
    app's templates (register_account_routes calls this)."""
    from markupsafe import Markup
    app.after_request(lambda response: keep_ref(path, response))
    app.jinja_env.globals["cct_analytics"] = lambda: Markup(tag(path, app_label))
    app.extensions[EXT_KEY] = (path, app_label)


def page_tag() -> str:
    """tag() for a page cct_common itself serves (the account, sign-in and
    buy pages) in an app that called register(); "" elsewhere."""
    try:
        from flask import current_app
        found = current_app.extensions.get(EXT_KEY)
        return tag(*found) if found else ""
    except Exception:
        return ""
