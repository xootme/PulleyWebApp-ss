"""
charging.py — charges tokens for exports (Timing Pulley Generator's ADR-008,
moved into cct_common with the Download window so every CCT app charges the
same way; CCT_App_Baseline §4–5). Off unless accounts are on
(TOKENS_ENABLED=1); with them off every download behaves as before.

    from cct_common.charging import Charges
    charges = Charges(part2_prefix="p2_", aliases={...}, route_only_keys={...})

    @app.route('/download/stl')
    @charges.charged('stl')
    def download_stl(): ...

    charges.attach(app, accounts_state)   # once, after init_accounts()

What is the app's own: how its part 2 names its parameters (part2_prefix:
"p2_", "s2_", "g2_"), routes that send a design value under another name
(aliases), request keys that select a part rather than describe the design
(route_only_keys), and background routes' formats (formats).

Pricing and unlocks live in cct_common.tokens: STEP > STL > 2D, one payment
unlocks the whole design for 24 h, upgrades pay the difference.

WHAT "THE DESIGN" IS. Every download route receives a different slice of
the design under different names (a part-2 download reuses part 1's names;
the pulley app's flange routes use flat names), so hashing each request's
own parameters would give one design several identities, and a STEP
purchase could never unlock that design's STL or DXF. Instead the page
registers the full on-screen design once (POST /api/design) and sends the
returned design_id with each download. The server only accepts that id if
every parameter the download actually uses is part of the registered
design (design_matches); otherwise — or with no id at all — the download
is priced as a design of its own. A made-up id therefore can't unlock
anything: the parameters that shape the file must match what was paid for.

DAILY LIMITS. An add-in or agent signs in with its own device token, which
carries a daily token limit (100 by default, set by the customer on the
approval page or /account/devices). Past it, that token's downloads get a
429 DAILY_LIMIT_REACHED and the owner gets one email a day saying how to
change the limit. Browser sessions have none.

CHARGE, THEN REFUND ON FAILURE. The tokens are taken before generating and
given back if the route answers with an error status — the routes catch
their own exceptions and return error responses, so the status code, not
an exception, is what marks a failed export.

PAID ONLY FOR WHAT ARRIVES (owner, 2026-10-02: a download failed and was
still charged). Taking the tokens first stops two parallel downloads
spending the same balance, but the charge only stands once the file has
been sent in full:

  * A charged route's response is wrapped (_Delivery): if the server stops
    sending before the last byte — the connection dropped, the client gave
    up — the charge is refunded.
  * A background job (the Download window's zip, a STEP job) is charged
    while it builds, but its file is fetched later from a result link. The
    job hands its charge to await_delivery(quote, url); the result route
    calls serve_delivery(), which confirms the charge once the file has
    been sent in full and refunds it at once if the file is gone. A result
    nobody fetches is refunded after DELIVERY_TTL_S (sweep_undelivered,
    run at most once a minute from any request).

"Sent in full" is as far as the server can see: the last byte handed to the
connection. A browser that then fails to save the file isn't charged again
when it retries — one payment unlocks the design for 24 h.
"""
from __future__ import annotations

import json
import math
import secrets
import time
from contextlib import nullcontext
from functools import wraps

from .sqlite_db import SqliteDB
from .tokens import (
    FORMAT_TIER, TRANSIENT_KEYS, BudgetReached, InsufficientTokens, design_key,
)

DESIGN_TTL_S = 30 * 24 * 3600

# Request keys that select or deliver a part rather than describe the
# design, in every app (in addition to cct_common.tokens.TRANSIENT_KEYS);
# an app adds its own with route_only_keys.
ROUTE_ONLY_KEYS = frozenset({"design_id", "machine_id"})

_EMPTY = {"", "0", "0.0", "false", "none", "null", "off"}

_DESIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    design_id   TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    design      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS designs_created ON designs(created_at);
CREATE TABLE IF NOT EXISTS pending_deliveries (
    delivery_key TEXT PRIMARY KEY,
    spend_id     INTEGER NOT NULL,
    account_id   TEXT NOT NULL,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS pending_deliveries_created ON pending_deliveries(created_at);
"""

# A job's charge waits this long for its file to be fetched before it is
# given back (results are kept an hour in the pulley app, plus a margin).
DELIVERY_TTL_S = 3600 + 600


def _norm(v) -> str:
    """Compare values the way the page writes them: '8', '8.0' and 8 are
    equal; '', '0', 'false' and a missing value all mean "off"."""
    s = "" if v is None else str(v).strip().lower()
    if s in _EMPTY:
        return ""
    try:
        f = float(s)
        if math.isfinite(f):
            return repr(round(f, 9))
    except ValueError:
        pass
    return s


def canonical_design(design: dict, route_only=ROUTE_ONLY_KEYS) -> dict:
    return {str(k): str(v).strip() for k, v in design.items()
            if k not in TRANSIENT_KEYS and k not in route_only}


def design_matches(route_params: dict, design: dict, *, part2_prefix: str = "p2_",
                   aliases: dict | None = None, route_only=ROUTE_ONLY_KEYS) -> bool:
    """True if every design parameter this download uses is part of the
    registered design, under its own name, its part-2 name, or an alias
    (the pulley app's flange routes' flat names)."""
    aliases = aliases or {}
    norm_design = {k: _norm(v) for k, v in design.items()}
    for k, v in route_params.items():
        if k in TRANSIENT_KEYS or k in route_only:
            continue
        want = _norm(v)
        names = [k, part2_prefix + k]
        if k in aliases:
            names += [aliases[k], part2_prefix + aliases[k]]
        if not any(norm_design.get(n, "") == want for n in names):
            return False
    return True


class DesignStore(SqliteDB):
    """Registered designs, kept 30 days, in the accounts database file."""

    def __init__(self, path: str, route_only=ROUTE_ONLY_KEYS):
        super().__init__(path, _DESIGN_SCHEMA)
        self.route_only = frozenset(route_only)

    def register(self, account_id: str, design: dict) -> str:
        canon = canonical_design(design, self.route_only)
        did = design_key(canon)
        with self._write() as db:
            db.execute("INSERT OR IGNORE INTO designs (design_id, account_id, design, created_at) "
                       "VALUES (?, ?, ?, ?)",
                       (did, account_id, json.dumps(canon, sort_keys=True), time.time()))
        return did

    def get(self, design_id: str):
        with self._read() as db:
            row = db.execute("SELECT design FROM designs WHERE design_id = ?", (design_id,)).fetchone()
        return json.loads(row["design"]) if row else None

    def purge(self) -> None:
        with self._write() as db:
            db.execute("DELETE FROM designs WHERE created_at < ?", (time.time() - DESIGN_TTL_S,))

    # ── charges waiting for their file to be fetched ──────────────────────

    def add_pending(self, delivery_key: str, spend_id: int, account_id: str,
                    now: float | None = None) -> None:
        with self._write() as db:
            db.execute("INSERT INTO pending_deliveries (delivery_key, spend_id, account_id, created_at) "
                       "VALUES (?, ?, ?, ?)",
                       (delivery_key, spend_id, account_id, time.time() if now is None else now))

    def claim_pending(self, delivery_key: str):
        """Take a pending charge off the list (one caller wins): its spend id,
        or None if it wasn't pending (already settled, or never charged)."""
        with self._write() as db:
            row = db.execute("DELETE FROM pending_deliveries WHERE delivery_key = ? RETURNING spend_id",
                             (delivery_key,)).fetchone()
        return row["spend_id"] if row else None

    def overdue_pending(self, older_than: float) -> list:
        with self._read() as db:
            rows = db.execute("SELECT delivery_key FROM pending_deliveries WHERE created_at < ?",
                              (older_than,)).fetchall()
        return [r["delivery_key"] for r in rows]


class _Delivery:
    """A response body that knows whether it was sent in full. The WSGI
    server iterates it and calls close(); reaching the end of the body means
    every byte was handed to the connection. on_done() runs then; on_abort()
    runs if close() comes first (the client went away, the server gave up)."""

    def __init__(self, body, on_done, on_abort):
        self._body = body
        self._on_done, self._on_abort = on_done, on_abort
        self._settled = False

    def _settle(self, fn) -> None:
        if not self._settled:
            self._settled = True
            try:
                fn()
            except Exception:
                pass                 # settling a charge must never break the download

    def __iter__(self):
        for chunk in self._body:
            yield chunk
        self._settle(self._on_done)

    def close(self) -> None:
        try:
            close = getattr(self._body, "close", None)
            if close:
                close()
        finally:
            self._settle(self._on_abort)


def on_delivery(response, on_done, on_abort):
    """Wrap a Flask response so on_done / on_abort tell whether its body was
    sent in full. HEAD requests and empty bodies count as done at once."""
    from flask import request
    if request.method == "HEAD":
        on_done()
        return response
    response.response = _Delivery(response.response, on_done, on_abort)
    return response


def delivery_key(url: str) -> str:
    """The key a result link is tracked under: its path, unquoted — what the
    result route sees as request.path."""
    from urllib.parse import unquote, urlsplit
    return unquote(urlsplit(url).path)


class _ExportFailed(Exception):
    def __init__(self, response):
        super().__init__(response.status)
        self.response = response


class Charges:
    """One per app, made at import so routes can be decorated before the
    accounts store exists; attach() wires it up at the end of app.py."""

    def __init__(self, *, part2_prefix: str = "p2_", aliases: dict | None = None,
                 route_only_keys=frozenset(), formats: dict | None = None):
        self.part2_prefix = part2_prefix
        self.aliases = dict(aliases or {})
        self.route_only = ROUTE_ONLY_KEYS | frozenset(route_only_keys)
        self.state = None
        self.designs = None
        self.buy_url = ""
        self.limit_notify = None   # limit_notify(email, label, budget, settings_url)
        # endpoint name -> export format, filled in by charged() and
        # begin_async() users, so a price check can find a route's format
        self.formats = dict(formats or {})
        # Marks the zip job's own calls to the download routes (bundles.py):
        # they are part of one already-charged purchase, so they skip the
        # per-route charge, the queue-session check and add-in mirroring.
        # Random per process and never sent to a browser.
        self.internal_secret = secrets.token_hex(32)

    def is_internal(self) -> bool:
        from flask import has_request_context, request
        return has_request_context() and secrets.compare_digest(
            request.headers.get("X-CCT-Internal", ""), self.internal_secret)

    @property
    def enabled(self) -> bool:
        return bool(self.state and self.state.enabled)

    def attach(self, app, state, *, buy_url: str = "", limit_notify=None) -> None:
        self.state = state
        self.buy_url = buy_url
        self.limit_notify = limit_notify
        if "cct_accounts" in app.extensions:        # /api/account offers it too (balance dialog)
            app.extensions["cct_accounts"]["buy_url"] = buy_url
        if not (state.enabled and state.healthy):
            return
        self.designs = DesignStore(state.tokens.path, self.route_only)
        self.designs.purge()

        # Job charges whose file was never fetched go back — checked at most
        # once a minute, from whatever request comes along.
        last_sweep = [0.0]

        @app.before_request
        def _sweep_undelivered():
            now = time.time()
            if now - last_sweep[0] >= 60:
                last_sweep[0] = now
                try:
                    self.sweep_undelivered(now=now)
                except Exception:
                    pass             # never let the sweep break a request

        from flask import jsonify, request
        from .account_routes import current_account_id

        def register_design():
            acct = current_account_id()
            if not acct:
                return jsonify({"error": "sign in required", "code": "SIGN_IN_REQUIRED"}), 401
            data = request.get_json(silent=True) or {}
            design = data.get("design")
            if not isinstance(design, dict) or not design or len(json.dumps(design)) > 32_000:
                return jsonify({"error": "design must be a non-empty object"}), 400
            return jsonify({"design_id": self.designs.register(acct, design)})

        def quote():
            """What a download would cost right now — priced exactly as the
            download itself will be. Downloads run in hidden iframes, whose
            answer the page can't read, so it asks here first.
            Body: {"path": "/download/stl", "params": {...query or body...}}"""
            acct = current_account_id()
            if not acct:
                return jsonify({"error": "sign in required", "code": "SIGN_IN_REQUIRED"}), 401
            data = request.get_json(silent=True) or {}
            if data.get("fmt"):
                # A whole-design price (the download window's zip): the
                # registered design at the highest tier ticked.
                did = str(data.get("design_id") or "")
                if data["fmt"] not in FORMAT_TIER or not self.designs.get(did):
                    return jsonify({"error": "unknown design or format"}), 400
                q = self.state.tokens.quote(acct, did, data["fmt"])
                return jsonify({"fmt": q.fmt, "tier": q.tier, "cost": q.cost,
                                "held_tier": q.held_tier, "unlocked_until": q.unlocked_until,
                                "balance": self.state.tokens.balance(acct),
                                "buy_url": self.buy_url})
            params = data.get("params") if isinstance(data.get("params"), dict) else {}
            try:
                endpoint, _ = app.url_map.bind("localhost").match(
                    str(data.get("path", "")),
                    method="POST" if str(data.get("path", "")).startswith("/api/") else "GET")
            except Exception:
                endpoint = None
            fmt = self.formats.get(endpoint)
            if not fmt:
                return jsonify({"error": "not a charged download"}), 400
            q = self.state.tokens.quote(acct, self.design_key_for(params), fmt)
            return jsonify({"fmt": q.fmt, "tier": q.tier, "cost": q.cost,
                            "held_tier": q.held_tier, "unlocked_until": q.unlocked_until,
                            "balance": self.state.tokens.balance(acct),
                            "buy_url": self.buy_url})

        app.add_url_rule("/api/design", view_func=register_design, methods=["POST"])
        app.add_url_rule("/api/tokens/quote", view_func=quote, methods=["POST"])

    # ── the parts every charge needs ──────────────────────────────────────

    def _refusal(self):
        """(account_id, None) when a charge can go ahead, else (None, response)."""
        from flask import jsonify
        if not self.state.healthy:
            return None, (jsonify({"error": "Accounts are temporarily unavailable.",
                                   "code": "ACCOUNTS_UNAVAILABLE"}), 503)
        from .account_routes import current_account_id
        acct = current_account_id()
        if not acct:
            return None, (jsonify({"error": "Sign in to download files.",
                                   "code": "SIGN_IN_REQUIRED"}), 401)
        return acct, None

    @staticmethod
    def _session() -> tuple:
        """(session_id, daily_budget) of the signed-in session."""
        from .account_routes import current_session
        s = current_session() or {}
        return s.get("id"), s.get("daily_budget")

    def _limit_reached(self, e: BudgetReached, session_id):
        """429 for a token past its daily limit — and, once a day per token,
        an email to the owner saying how to change it."""
        from flask import jsonify, request
        from .account_routes import current_session
        settings_url = f"{request.host_url}account/devices"
        if self.limit_notify and session_id is not None:
            try:
                if self.state.accounts.claim_budget_notice(session_id):
                    s = current_session() or {}
                    email = self.state.tokens.account_email(s.get("account_id"))
                    self.limit_notify(email, s.get("label") or "Your add-in", e.budget, settings_url)
            except Exception:
                pass                     # a missed email must never break the answer
        return jsonify({"error": f"This add-in has reached its daily limit of {e.budget} tokens.",
                        "code": "DAILY_LIMIT_REACHED", "budget": e.budget, "spent": e.spent,
                        "needed": e.needed, "settings_url": settings_url}), 429

    def _begin(self, key: str, fmt: str):
        """The checks before a background charge: signed in, can afford it,
        within the token's daily limit. (context, None) or (None, response)."""
        acct, refusal = self._refusal()
        if refusal:
            return None, refusal
        sid, budget = self._session()
        tokens = self.state.tokens
        quote = tokens.quote(acct, key, fmt)
        if budget is not None and quote.cost:
            spent = tokens.spent_by_session(sid)
            if spent + quote.cost > budget:
                return None, self._limit_reached(BudgetReached(budget, spent, quote.cost), sid)
        balance = tokens.balance(acct)
        if quote.cost > balance:
            return None, self._short_of_tokens(InsufficientTokens(quote.cost, balance))
        return (acct, key, fmt, sid, budget), None

    def begin_design(self, design_id: str, fmt: str):
        """begin_async for a whole registered design at one tier (the
        download window's zip)."""
        return self._begin(design_id, fmt)

    def design_key_for(self, params: dict) -> str:
        """The key this download is priced under — the registered design
        when the download's own parameters belong to it, else its own."""
        did = params.get("design_id")
        if did and self.designs:
            design = self.designs.get(str(did))
            if design is not None and self.matches(params, design):
                return str(did)
        return design_key(params, ignore=TRANSIENT_KEYS | self.route_only)

    def matches(self, route_params: dict, design: dict) -> bool:
        """design_matches with this app's names."""
        return design_matches(route_params, design, part2_prefix=self.part2_prefix,
                              aliases=self.aliases, route_only=self.route_only)

    def _short_of_tokens(self, e: InsufficientTokens):
        from flask import jsonify
        return jsonify({"error": f"This download needs {e.needed} tokens; you have {e.balance}.",
                        "code": "NOT_ENOUGH_TOKENS", "needed": e.needed,
                        "balance": e.balance, "buy_url": self.buy_url}), 402

    @staticmethod
    def _request_params() -> dict:
        from flask import request
        if request.method == "GET":
            return request.args.to_dict()
        body = request.get_json(silent=True) or {}
        return body if isinstance(body, dict) else {}

    # ── synchronous download routes ───────────────────────────────────────

    def charged(self, fmt: str, params_from=None):
        """Decorator for a download route that returns the file itself.
        params_from(request_params) picks the design parameters out of the
        request when they aren't the top level (the add-in API routes)."""
        def deco(view):
            self.formats[view.__name__] = fmt

            @wraps(view)
            def wrapped(*args, **kwargs):
                if not self.enabled or self.is_internal():
                    return view(*args, **kwargs)
                from flask import make_response, request
                acct, refusal = self._refusal()
                if refusal:
                    return refusal
                raw = self._request_params()
                params = params_from(raw) if params_from else raw
                if params_from and raw.get("design_id"):
                    params = dict(params, design_id=raw["design_id"])
                key = self.design_key_for(params)
                tokens = self.state.tokens
                sid, budget = self._session()
                try:
                    with tokens.charge(acct, key, fmt, detail=request.path,
                                       session_id=sid, daily_budget=budget) as quote:
                        resp = make_response(view(*args, **kwargs))
                        if resp.status_code >= 400:
                            raise _ExportFailed(resp)
                except _ExportFailed as failed:
                    return failed.response          # charge already refunded
                except BudgetReached as e:
                    return self._limit_reached(e, sid)
                except InsufficientTokens as e:
                    return self._short_of_tokens(e)
                resp.headers["X-CCT-Tokens-Charged"] = str(quote.cost)
                resp.headers["X-CCT-Tokens-Balance"] = str(tokens.balance(acct))
                if quote.spend_id is not None:
                    # Paid only if the file is sent in full (module docstring).
                    spend = quote.spend_id
                    return on_delivery(
                        resp, lambda: None,
                        lambda: tokens.refund(spend, detail="download not completed"))
                return resp
            return wrapped
        return deco

    # ── background (async) STEP jobs ──────────────────────────────────────

    def begin_async(self, fmt: str, params: dict):
        """Call in the request that starts a background job. Returns
        (context, None) to pass to charge_in_job(), or (None, response) to
        return right away (not signed in, or can't afford it — checked up
        front so an unaffordable job never starts)."""
        if not self.enabled:
            return None, None
        return self._begin(self.design_key_for(params), fmt)

    def charge_in_job(self, context, detail: str):
        """Context manager around the job's generation: charges when it
        starts, refunds if it raises. A no-op when charging is off. Yields
        the Quote (None when off): hand it to await_delivery() with the
        result's link, so the charge stands only once the file is fetched."""
        if context is None:
            return nullcontext()
        acct, key, fmt, sid, budget = context
        return self.state.tokens.charge(acct, key, fmt, detail=detail,
                                        session_id=sid, daily_budget=budget)

    # ── delivery: a job's charge stands once its file is fetched ──────────

    def await_delivery(self, quote, url: str) -> None:
        """Call in the job, inside charge_in_job, once the file is stored at
        url (the result link): the charge waits for the file to be fetched
        in full (serve_delivery) and is refunded if it never is."""
        if quote is None or getattr(quote, "spend_id", None) is None or not self.designs:
            return
        acct = self.state.tokens.account_of_spend(quote.spend_id)
        self.designs.add_pending(delivery_key(url), quote.spend_id, acct or "")

    def not_delivered(self, key: str, detail: str = "download not available") -> bool:
        """The result behind key can't be served: give its charge back now.
        True if a charge was refunded."""
        if not self.designs:
            return False
        spend = self.designs.claim_pending(key)
        if spend is None:
            return False
        self.state.tokens.refund(spend, detail=detail)
        return True

    def serve_delivery(self, response, key: str | None = None):
        """For a result route: wrap the response serving key (default: this
        request's path). Sent in full, the pending charge stands; cut short,
        it stays pending, so fetching the link again can still complete it —
        and the sweep refunds it if nothing does."""
        if not self.designs:
            return response
        from flask import request
        key = request.path if key is None else key
        return on_delivery(response, lambda: self.designs.claim_pending(key), lambda: None)

    def sweep_undelivered(self, ttl_s: float = DELIVERY_TTL_S, now: float | None = None) -> int:
        """Refund every job charge whose file wasn't fetched within ttl_s.
        Returns how many were refunded."""
        if not self.designs:
            return 0
        now = time.time() if now is None else now
        refunded = 0
        for key in self.designs.overdue_pending(now - ttl_s):
            if self.not_delivered(key, detail="download never fetched"):
                refunded += 1
        return refunded
