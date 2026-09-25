"""
charging.py — charges tokens for exports (ADR-008). Off unless accounts are
on (TOKENS_ENABLED=1); with them off every download behaves as before.

    from charging import charges

    @app.route('/download/stl')
    @charges.charged('stl')
    def download_stl(): ...

    charges.attach(app, accounts_state)   # once, after init_accounts()

Pricing and unlocks live in cct_common.tokens: STEP 3 ⊃ STL 2 ⊃ 2D 1, one
payment unlocks the whole design for 24 h, upgrades pay the difference.

WHAT "THE DESIGN" IS. Every download route receives a different slice of
the design under different names (the flange routes use flat names such as
`flat_depth`; a pulley-2 download reuses pulley 1's names), so hashing each
request's own parameters would give one design several identities, and a
STEP purchase could never unlock that design's STL or DXF. Instead the page
registers the full on-screen design once (POST /api/design) and sends the
returned design_id with each download. The server only accepts that id if
every parameter the download actually uses is part of the registered
design (design_matches); otherwise — or with no id at all — the download
is priced as a design of its own. A made-up id therefore can't unlock
anything: the parameters that shape the file must match what was paid for.

CHARGE, THEN REFUND ON FAILURE. The tokens are taken before generating and
given back if the route answers with an error status — the routes catch
their own exceptions and return error responses, so the status code, not
an exception, is what marks a failed export.
"""
from __future__ import annotations

import json
import math
import secrets
import time
from contextlib import nullcontext
from functools import wraps

from cct_common.sqlite_db import SqliteDB
from cct_common.tokens import FORMAT_TIER, TRANSIENT_KEYS, InsufficientTokens, design_key

DESIGN_TTL_S = 30 * 24 * 3600

# Request keys that select or deliver a part rather than describe the
# design (in addition to cct_common.tokens.TRANSIENT_KEYS).
ROUTE_ONLY_KEYS = frozenset({"design_id", "flange_which", "machine_id"})

# The flange routes send some hub values under flat names.
ALIASES = {
    "flat_depth": "hub_flat_depth",
    "keyway_w": "hub_keyway_w",
    "keyway_h": "hub_keyway_h",
}

_EMPTY = {"", "0", "0.0", "false", "none", "null", "off"}

_DESIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    design_id   TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    design      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS designs_created ON designs(created_at);
"""


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


def canonical_design(design: dict) -> dict:
    return {str(k): str(v).strip() for k, v in design.items()
            if k not in TRANSIENT_KEYS and k not in ROUTE_ONLY_KEYS}


def design_matches(route_params: dict, design: dict) -> bool:
    """True if every design parameter this download uses is part of the
    registered design, under its own name, its pulley-2 name, or the flange
    routes' flat alias."""
    norm_design = {k: _norm(v) for k, v in design.items()}
    for k, v in route_params.items():
        if k in TRANSIENT_KEYS or k in ROUTE_ONLY_KEYS:
            continue
        want = _norm(v)
        names = [k, "p2_" + k]
        if k in ALIASES:
            names += [ALIASES[k], "p2_" + ALIASES[k]]
        if not any(norm_design.get(n, "") == want for n in names):
            return False
    return True


class DesignStore(SqliteDB):
    """Registered designs, kept 30 days, in the accounts database file."""

    def __init__(self, path: str):
        super().__init__(path, _DESIGN_SCHEMA)

    def register(self, account_id: str, design: dict) -> str:
        canon = canonical_design(design)
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


class _ExportFailed(Exception):
    def __init__(self, response):
        super().__init__(response.status)
        self.response = response


class Charges:
    """Module-level singleton so routes can be decorated before the
    accounts store exists; attach() wires it up at the end of app.py."""

    def __init__(self):
        self.state = None
        self.designs = None
        self.buy_url = ""
        # endpoint name -> export format, filled in by charged() and
        # begin_async() users, so a price check can find a route's format
        self.formats = {"api_download_step_async": "step",
                        "api_download_all_step_async": "step"}
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

    def attach(self, app, state, *, buy_url: str = "") -> None:
        self.state = state
        self.buy_url = buy_url
        if not (state.enabled and state.healthy):
            return
        self.designs = DesignStore(state.tokens.path)
        self.designs.purge()

        from flask import jsonify, request
        from cct_common.account_routes import current_account_id

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
        from cct_common.account_routes import current_account_id
        acct = current_account_id()
        if not acct:
            return None, (jsonify({"error": "Sign in to download files.",
                                   "code": "SIGN_IN_REQUIRED"}), 401)
        return acct, None

    def design_key_for(self, params: dict) -> str:
        """The key this download is priced under — the registered design
        when the download's own parameters belong to it, else its own."""
        did = params.get("design_id")
        if did and self.designs:
            design = self.designs.get(str(did))
            if design is not None and design_matches(params, design):
                return str(did)
        return design_key(params, ignore=TRANSIENT_KEYS | ROUTE_ONLY_KEYS)

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
                try:
                    with tokens.charge(acct, key, fmt, detail=request.path) as quote:
                        resp = make_response(view(*args, **kwargs))
                        if resp.status_code >= 400:
                            raise _ExportFailed(resp)
                except _ExportFailed as failed:
                    return failed.response          # charge already refunded
                except InsufficientTokens as e:
                    return self._short_of_tokens(e)
                resp.headers["X-CCT-Tokens-Charged"] = str(quote.cost)
                resp.headers["X-CCT-Tokens-Balance"] = str(tokens.balance(acct))
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
        acct, refusal = self._refusal()
        if refusal:
            return None, refusal
        key = self.design_key_for(params)
        quote = self.state.tokens.quote(acct, key, fmt)
        balance = self.state.tokens.balance(acct)
        if quote.cost > balance:
            return None, self._short_of_tokens(InsufficientTokens(quote.cost, balance))
        return (acct, key, fmt), None

    def charge_in_job(self, context, detail: str):
        """Context manager around the job's generation: charges when it
        starts, refunds if it raises. A no-op when charging is off."""
        if context is None:
            return nullcontext()
        acct, key, fmt = context
        return self.state.tokens.charge(acct, key, fmt, detail=detail)


charges = Charges()
