"""
admin_export.py — the admin's numbers as one JSON file per UTC day (0.63.0,
the owner's "Admin visibility A", 2026-11-25), so every session can read
them without holding a secret.

    DATABASE_URL=... python -m cct_common.admin_export --bucket cheapcadtools-backups
        # yesterday (UTC) -> gs://cheapcadtools-backups/admin/<date>.json
    python -m cct_common.admin_export --database <path or url> --date 2026-11-24 --out day.json

Meant to run as a Cloud Run job (an app's image, this command) once a day,
after midnight UTC, with DATABASE_URL exactly as the admin has it. It
writes to Cloud Storage as the job's own service account (the metadata
server's token: no key file). A rerun for the same date replaces the file.

Totals only, per app and per day: nothing that identifies a person. No
email, account id, design parameter, IP, user agent, session, referral
code or order id is read into it, and every name in it (app, route,
format, action, category, provider, pack) is a short word of
[A-Za-z0-9_.:/-]; anything else becomes "other", and a long id-like path
segment becomes ":id". write() refuses a document with an "@" anywhere or
a key that names personal data (FORBIDDEN_KEYS).

Schema "cct-admin-daily", version 1:

    {"schema": "cct-admin-daily", "version": 1,
     "date": "2026-11-24", "from": "2026-11-24T00:00:00Z", "to": "2026-11-25T00:00:00Z",
     "made_at": "...Z", "cct_common": "<version>",
     "apps": {"<app>": {
         "page_views":        {"<category>": n},                   server_use action "page"
         "checks":            {"<category>": n},                   server_use action "check"
         "download_requests": {"<action>": {"<category>": n}},     step, stl, dxf, svg, report, bundle, export
         "downloads":         {"<fmt>": {"paid": n, "free": n}},   the ledger: a spend; an unlocked design again
         "download_failures": [{"route": r, "status": s, "count": n}],
         "token_spend":       {"spent": n, "refunded": n, "net": n},
         "mcp_calls":         {"<mcp tool or method>": {"<category>": n}}}},
     "all": {
         "signups":         {"total": n, "by_surface": {"<surface>": n}},
         "token_purchases": {"orders": n, "tokens": n, "bonus_tokens": n, "cents": n,
                             "by_pack": [{"provider", "pack", "orders", "tokens", "bonus_tokens", "cents"}]}}}

An app is the service's name as server_use writes it (server_use.TOOL_ALIASES:
"pulleys" -> "pulley"), whichever table it came from; the MCP gateway's
own requests that name no app (its upkeep, sign-in pages, list_apps) are the
app "mcp". Categories are server_use.CATEGORIES (bots,
testing and admin included: leave out what you don't want). page_views
are page loads the servers answered, not analytics visits. mcp_calls are
the gateway's rows, under the app named in the call. Sign-ups and token
purchases are the accounts' and payments' (shared by every app, which
they don't record), so they are under "all"; a deleted account is not
counted. An app with nothing that day is left out; a section with nothing
is left out of its app.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

SCHEMA = "cct-admin-daily"
VERSION = 1
OBJECT = "admin/{date}.json"                    # in the bucket
FORBIDDEN_KEYS = frozenset({"email", "emails", "account", "account_id", "account_ids", "params",
                            "ip", "ip_hash", "user_agent", "ua", "session", "session_id",
                            "order_id", "payment_ref", "design_key", "payer_key"})
DOWNLOAD_ACTIONS = ("step", "stl", "dxf", "svg", "report", "bundle", "export")
_NAME = re.compile(r"[A-Za-z0-9_.:/-]{1,80}")
_ID_SEGMENT = re.compile(r"(?=.*\d)[A-Za-z0-9_-]{16,}|[0-9a-fA-F-]{12,}")


class LeakError(ValueError):
    """The document carries something that could identify a person."""


def _name(v, none: str = "none") -> str:
    if v is None or v == "":
        return none
    s = str(v)
    return s if _NAME.fullmatch(s) else "other"


def _app(v) -> str:
    from .server_use import TOOL_ALIASES
    s = _name(v, "unknown")
    return TOOL_ALIASES.get(s, s)


def _route(v) -> str:
    path = str(v or "").split("?", 1)[0].split("#", 1)[0]
    if not path:
        return "none"
    return _name("/".join(":id" if _ID_SEGMENT.fullmatch(seg) else seg for seg in path.split("/")))


def day_bounds(day: date) -> tuple[float, float]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
    return start, start + 86400


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_gateway(service: str) -> bool:
    from .server_use import TOOL_ALIASES
    s = (service or "").removesuffix("-test")
    return TOOL_ALIASES.get(s, s) == "mcp"


def _add(d: dict, keys: tuple, n: int) -> None:
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = d.get(keys[-1], 0) + int(n or 0)


def _stores(database: str):
    """The admin's own stores on one database (they create what's missing,
    as the admin does)."""
    from .download_failures import DownloadFailures
    from .payments import Payments
    from .server_use import ServerUseStore
    from .tokens import TokenStore
    tokens = TokenStore(database)
    return tokens, Payments(tokens), DownloadFailures(database), ServerUseStore(database)


def build(database: str, day: date, *, now: Optional[float] = None) -> dict:
    """The day's document (see the module docstring)."""
    from . import __version__
    tokens, payments, failures, use = _stores(database)
    since, until = day_bounds(day)
    apps: dict = {}

    for r in use.query(since, until):
        # query() ends before `until` itself, but starts at since's month
        if r["period"] not in ("hour", "day") or r["bucket"] < since:
            continue                            # weeks and months can't be split into days
        app, cat, act, n = _app(r["tool"]), _name(r["category"]), _name(r["action"]), r["count"]
        if _is_gateway(r["service"]):
            _add(apps, (app, "mcp_calls", act, cat), n)
        elif act == "page":
            _add(apps, (app, "page_views", cat), n)
        elif act == "check":
            _add(apps, (app, "checks", cat), n)
        elif act in DOWNLOAD_ACTIONS:
            _add(apps, (app, "download_requests", act, cat), n)

    with tokens._read() as db:
        rows = db.execute(
            "SELECT app, kind, fmt, COUNT(*) AS n, COALESCE(SUM(amount), 0) AS amount FROM ledger "
            "WHERE ts >= ? AND ts < ? AND kind IN ('spend', 'download', 'refund') GROUP BY app, kind, fmt",
            (since, until)).fetchall()
        for r in rows:
            app = _app(r["app"])
            if r["kind"] in ("spend", "download"):
                _add(apps, (app, "downloads", _name(r["fmt"]), "paid" if r["kind"] == "spend" else "free"), r["n"])
            if r["kind"] == "spend":
                _add(apps, (app, "token_spend", "spent"), -int(r["amount"]))
            elif r["kind"] == "refund":
                _add(apps, (app, "token_spend", "refunded"), int(r["amount"]))
        signups = db.execute(
            "SELECT signup_surface AS surface, COUNT(*) AS n FROM accounts "
            "WHERE created_at >= ? AND created_at < ? GROUP BY signup_surface", (since, until)).fetchall()
    for spend in (a.get("token_spend") for a in apps.values()):
        if spend is not None:
            spend.setdefault("spent", 0)
            spend.setdefault("refunded", 0)
            spend["net"] = spend["spent"] - spend["refunded"]

    with failures._read() as db:
        rows = db.execute(
            "SELECT app, route, status, COUNT(*) AS n FROM download_failures "
            "WHERE created_at >= ? AND created_at < ? GROUP BY app, route, status", (since, until)).fetchall()
    fails: dict = {}
    for r in rows:
        status = r["status"] if isinstance(r["status"], int) else None
        _add(fails, (_app(r["app"]), (_route(r["route"]), status)), r["n"])
    for app, by in fails.items():
        apps.setdefault(app, {})["download_failures"] = [
            {"route": route, "status": status, "count": n}
            for (route, status), n in sorted(by.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))]

    with payments._read() as db:
        rows = db.execute(
            "SELECT provider, pack_id, COUNT(*) AS n, COALESCE(SUM(tokens), 0) AS tokens, "
            "COALESCE(SUM(bonus_tokens), 0) AS bonus, COALESCE(SUM(cents), 0) AS cents FROM payments "
            "WHERE status IN ('completed', 'refunded') AND completed_at >= ? AND completed_at < ? "
            "GROUP BY provider, pack_id", (since, until)).fetchall()
    packs: dict = {}
    for r in rows:
        p = packs.setdefault((_name(r["provider"]), _name(r["pack_id"])), [0, 0, 0, 0])
        for i, c in enumerate(("n", "tokens", "bonus", "cents")):
            p[i] += int(r[c])
    by_pack = [{"provider": prov, "pack": pack, "orders": v[0], "tokens": v[1], "bonus_tokens": v[2], "cents": v[3]}
               for (prov, pack), v in sorted(packs.items())]
    by_surface: dict = {}
    for r in signups:
        _add(by_surface, (_name(r["surface"]),), r["n"])

    doc = {
        "schema": SCHEMA, "version": VERSION, "date": day.isoformat(),
        "from": _iso(since), "to": _iso(until), "made_at": _iso(now or time.time()),
        "cct_common": __version__,
        "apps": {a: apps[a] for a in sorted(apps)},
        "all": {
            "signups": {"total": sum(by_surface.values()), "by_surface": by_surface},
            "token_purchases": {"orders": sum(p["orders"] for p in by_pack),
                                "tokens": sum(p["tokens"] for p in by_pack),
                                "bonus_tokens": sum(p["bonus_tokens"] for p in by_pack),
                                "cents": sum(p["cents"] for p in by_pack), "by_pack": by_pack},
        },
    }
    check_clean(doc)
    return doc


def check_clean(doc) -> None:
    """Raise LeakError for an "@" anywhere or a key that names personal data."""
    def walk(v, where):
        if isinstance(v, dict):
            for k, x in v.items():
                if str(k).lower() in FORBIDDEN_KEYS:
                    raise LeakError(f"{where}: key {k!r} names personal data")
                walk(k, where)
                walk(x, f"{where}.{k}")
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                walk(x, f"{where}[{i}]")
        elif isinstance(v, str) and "@" in v:
            raise LeakError(f"{where}: a value with an '@'")
    walk(doc, "export")


def to_bytes(doc: dict) -> bytes:
    check_clean(doc)
    return (json.dumps(doc, indent=1, sort_keys=False) + "\n").encode()


def _urlopen(req, timeout):
    import urllib.request
    return urllib.request.urlopen(req, timeout=timeout)


def upload(bucket: str, name: str, data: bytes, *, opener: Callable = _urlopen) -> str:
    """The bytes as gs://<bucket>/<name>, as this job's own service account
    (cct_common.storage, 0.64.0: a busy Cloud Storage is retried twice)."""
    from .storage import GCSStorage
    GCSStorage(bucket, opener=opener).put(name, data, "application/json")
    return f"gs://{bucket}/{name}"


def main(argv=None, *, opener: Callable = _urlopen, now: Optional[float] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cct_common.admin_export",
                                 description="The admin's per-app totals for one UTC day, as JSON.")
    ap.add_argument("--date", help="YYYY-MM-DD (UTC); default yesterday")
    ap.add_argument("--database", help="default: DATABASE_URL")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--bucket", help=f"write gs://<bucket>/{OBJECT}")
    where.add_argument("--out", help="write this local file")
    a = ap.parse_args(argv)
    database = a.database or os.environ.get("DATABASE_URL")
    if not database:
        ap.error("no database: pass --database or set DATABASE_URL")
    t = now or time.time()
    try:
        day = date.fromisoformat(a.date) if a.date else (
            datetime.fromtimestamp(t, timezone.utc).date() - timedelta(days=1))
    except ValueError:
        ap.error(f"--date {a.date!r} is not YYYY-MM-DD")
    if a.bucket:
        from .server_use import job_use
        with job_use(database, "cct-admin-export", "export"):
            data = to_bytes(build(database, day, now=t))
            where_to = upload(a.bucket, OBJECT.format(date=day.isoformat()), data, opener=opener)
    else:
        data = to_bytes(build(database, day, now=t))
        Path(a.out).write_bytes(data)
        where_to = a.out
    print(f"admin export {day.isoformat()}: {len(data)} bytes -> {where_to}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
