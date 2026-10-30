"""
server_use.py — what the servers spend their billed time on (0.27.0).

Cloud Run bills an instance for every moment at least one request is open
on it, whether that request is busy or waiting. So this counts, for every
request, its share of the instance's billed time and the CPU it used, tagged
with who it was for (category), which tool and what it did (action). One
module does all of it for every service: the Flask apps (WSGI), the MCP
gateway (ASGI) and the scheduled jobs.

    from cct_common.server_use import register_server_use
    register_server_use(app, accounts, tool="pulleys")   # register_admin does this

    # the MCP gateway (ASGI):  app = server_use_asgi(app, database_url, tool="mcp")
    # a scheduled job:         with job_use(database_url, "cct-db-backup", "backup"): ...

Billed time: each moment is split equally among the requests open on the
instance at that moment, across all its workers (a small shared file under a
file lock), so the shares add up to the time Google bills, and two requests
side by side each cost half. CPU: the request thread's own CPU
(time.thread_time) for WSGI; for ASGI, where every request shares one
thread, the process's CPU split the same way as the billed time; plus, for
both, the CPU of finished child processes (cadquery runs in one), split
among the process's open requests. CPU that no request used (a job's
thread, the job queue) is counted each minute as background.

Counting is in memory. Each process flushes once a minute (and on exit) as
hourly rows (bucket, service, category, tool, action, client) -> count,
billed_s, cpu_s, wall_s, adding to what is there: no per-request writes. A
request still open at a flush has its share so far flushed then, so a
request killed by its timeout is still counted up to the last minute.

compact() (from the daily housekeeping) rolls hours older than 7 days into
days, days older than 90 into weeks, weeks older than 2 years into months.
Months are kept for ever; years are summed from months on display.
"""
from __future__ import annotations

import atexit
import contextvars
import hashlib
import json
import mmap
import os
import re
import struct
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import forksafe
from .sqlite_db import SqliteDB

FLUSH_S = 60
CACHE_S = 600                   # how long a session's / account's category is remembered

# ── categories (the owner's list, 2026-10-29) ────────────────────────────
CATEGORIES = {
    "anonymous": "Anonymous visitors",
    "free": "Signed in, free credits",
    "paid": "Signed in, paid credits",
    "ai_idle": "AI connector: connection upkeep",
    "ai_tools": "AI connector: tool calls",
    "bots": "Bots, probes and scanners",
    "testing": "Our own testing",
    "admin": "Admin / owner",
    "failed": "Hung or failed requests",
    "scheduled": "Backups, scheduled jobs, webhooks",
    "startup": "Startup / unattributed",
}

# ── the shared route -> action table ──────────────────────────────────────
# First match wins: (method or None, path regex, action). Paths are as the
# app sees them (the Worker strips /tools/<app>). Built from every app's
# routes (2026-10-29); an app adds its own with register_server_use(...,
# actions=[...]) only for a route this doesn't know.
ACTIONS = [
    (None, r"^/admin(/|$)|^/api/admin/", "admin"),
    (None, r"^/api/health$|^/healthz?$|^/mcp/api/health$", "health"),
    (None, r"^/(static|assets|help)/|^/_cct_live_reload\.js$|\.(css|js|mjs|png|jpe?g|gif|ico|woff2?|map|webp)$",
     "static"),
    (None, r"^/api/v1/", "api_v1"),
    (None, r"^/api/download/bundle$|^/download/result/", "bundle"),
    (None, r"^/api/download-status/", "status"),
    (None, r"^/download/[a-z-]*\bstep\b[a-z-]*$|^/download/flange-assembly$|^/api/download/(all-)?step(-async)?$"
           r"|^/api/onshape/import$", "step"),
    (None, r"^/download/[a-z-]*\bstl\b[a-z-]*$|^/api/download/stl$", "stl"),
    (None, r"^/download/[a-z-]*\bdxf\b[a-z-]*$|^/api/download/dxf$", "dxf"),
    (None, r"^/download/[a-z-]*\b(svg|png)\b[a-z-]*$", "svg"),
    (None, r"^/download/(strength-report|readme)$", "report"),
    (None, r"^/api/layout/export$", "export"),
    (None, r"^/api/(preview|belt-preview|chain-preview|spoke-styles|gear$|drawing$)|^/preview/", "preview"),
    (None, r"^/api/(download-failure|report-bug)$|^/api/bug-report-file/", "bug_report"),
    (None, r"^/api/payments/|^/account/buy|^/api/(autodesk-ipn|woo-webhook)$", "payment"),
    (None, r"^/account(/|$)|^/api/account(/|$)|^/api/(design|tokens/quote)$|^/(authorize|token|register|revoke)$"
           r"|^/\.well-known/oauth|^/mcp/oauth/", "account"),
    (None, r"^/api/(fp-token|trial/)", "trial"),
    (None, r"^/api/(desktop/|provision$|subscribers/)", "licensing"),
    (None, r"^/api/import$", "import"),
    (None, r"^/api/(shutdown|_boot_id)$", "other"),
    (None, r"^/api/", "check"),                 # the rest of /api/ is the apps' spec and validation reads
    ("GET", r"^/$|^/onshape$|\.html$", "page"),
]

# The gateway names an app as its MCP `app` argument; rows use the service's name.
TOOL_ALIASES = {"pulleys": "pulley", "sprockets": "sprocket", "gears": "gear", "worm-gears": "wormgear",
                "bevel-gears": "bevelgear", "planetary-gears": "planetarygear",
                "gear-belt-chain": "gearbeltchain", "mcp-gateway": "mcp"}

_BOT_UA = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|probe|scan|check/|monitor|uptime|curl/|wget/|python-requests|"
    r"python-urllib|go-http-client|httpx|aiohttp|okhttp|libwww|java/|headless|zgrab|masscan|nuclei|"
    r"censys|shodan|expanse|nmap|mcpbeat|mcplantern|agentready|agentrank|protogrid|health/", re.I)
_SCANNER_PATH = re.compile(r"\.(php|asp|aspx|jsp|cgi|env|git|bak|sql)\b|/wp-|/\.well-known/security|"
                           r"/(phpmyadmin|xmlrpc|boaform|cgi-bin|actuator|vendor/phpunit)", re.I)
# Ours, as they identify themselves today: release smoke tests, the site's
# nightly checks; and the query the live-sample checks add.
_TEST_UA = re.compile(r"cct-(nightly|smoke|live|test|e2e|site-check|site-release)|CCT-PulleyRelease|"
                      r"CCT-smoke-deploy|cct_test", re.I)
_TEST_QUERY = re.compile(r"(^|&)ref=(verify|sample-zip)(&|$)")
_OWNER_UA = re.compile(r"^cct-admin\b|CCT-Controller-dashboard", re.I)   # the admin page's fan-out, the owner's dashboard
_SCHEDULER_UA = re.compile(r"Google-Cloud-Scheduler|CloudScheduler|cct-(backup|housekeeping|job)|^Stripe/|^PayPal/",
                           re.I)
_WEBHOOK_HEADERS = ("stripe-signature", "paypal-transmission-id", "x-wc-webhook-signature")
_AI_CLIENTS = (("claude", re.compile(r"claude|anthropic", re.I)),
               ("chatgpt", re.compile(r"openai|chatgpt", re.I)))
GATEWAY_UA = "cct-mcp-gateway"
CLIENT_HEADER = "X-CCT-Client"          # the gateway tells an app which AI client it acts for
TEST_HEADER = "X-CCT-Test"              # our own test runs mark themselves with it
INTERNAL_HEADER = "X-CCT-Internal"      # a bundle's own sub-requests: counted in the bundle
AI_UPKEEP = frozenset({"initialize", "notifications/initialized", "server/discover", "tools/list",
                       "ping", "subscriptions/listen", "resources/list", "prompts/list", "GET", "DELETE"})


def _client_name(value: str) -> str:
    """A client name a row may hold: one we know, else "other" (it comes from a header)."""
    return value if value in {name for name, _ in _AI_CLIENTS} else "other"


def ai_client(user_agent: str) -> str:
    for name, rx in _AI_CLIENTS:
        if rx.search(user_agent or ""):
            return name
    return "other"


def action_for(method: str, path: str, extra=()) -> str:
    for m, rx, action in (*extra, *_COMPILED):
        if (m is None or m == method) and rx.search(path):
            return action
    return "other"


_COMPILED = [(m, re.compile(rx, re.I), a) for m, rx, a in ACTIONS]


def tool_name(hint: str = "") -> str:
    """The service this runs as (K_SERVICE, less -test), else the hint, as a row's tool."""
    name = (os.environ.get("K_SERVICE") or "").removesuffix("-test") or hint or "unknown"
    return TOOL_ALIASES.get(name, name)


def is_test_request(headers, ua: str = "", query: str = "") -> bool:
    """Our own test traffic: the X-CCT-Test header (`headers`: lower-case
    names, or Flask's), a test run's user agent, a live-sample check's query."""
    return TEST_HEADER.lower() in headers or bool(_TEST_UA.search(ua or "")) or bool(_TEST_QUERY.search(query or ""))


def categorize(r: dict) -> str:
    """r: method, path, query, ua, headers (lower-case names), status (None:
    no response), service, session ({kind, account_id} or None), admin, paid,
    ai (the request came to or through the MCP gateway), action."""
    h = r.get("headers") or {}
    ua = r.get("ua") or ""
    if is_test_request(h, ua, r.get("query") or "") or (r.get("service") or "").endswith("-test"):
        return "testing"
    status = r.get("status")
    if status is None or status >= 500:
        return "failed"
    if r.get("admin") or r.get("action") == "admin" or _OWNER_UA.search(ua):
        return "admin"
    if _SCHEDULER_UA.search(ua) or any(k in h for k in _WEBHOOK_HEADERS):
        return "scheduled"
    if r.get("ai"):
        if ai_client(ua) == "other" and not ua.startswith(GATEWAY_UA) and (
                _BOT_UA.search(ua) or status in (401, 403) and not r.get("session")):
            return "bots"                       # directory probes knocking on /mcp
        return "ai_idle" if r.get("ai_upkeep") else "ai_tools"
    if _BOT_UA.search(ua) or _SCANNER_PATH.search(r.get("path") or "") or r.get("action") == "health":
        return "bots"
    s = r.get("session")
    if s and s.get("account_id"):
        return "paid" if r.get("paid") else "free"
    return "anonymous"


# ── the instance's billed-time clock, shared by its workers ──────────────
# The integral A(t) = ∫ dt / n(t), n = requests open on the instance. A
# request open from t0 to t1 is billed A(t1) - A(t0); the shares of all
# requests add up to the time the instance had any open.

_SLOTS = 128
_HEAD = struct.Struct("<dd")             # last event time, A
_SLOT = struct.Struct("<qq")             # pid, open requests
_SIZE = _HEAD.size + _SLOTS * _SLOT.size


class _SharedClock:
    """Shared by the processes of one instance: an mmap'd file in the temp
    dir (memory on Cloud Run) under a file lock (flock; msvcrt locally)."""

    def __init__(self, path: Optional[str] = None):
        uid = os.getuid() if hasattr(os, "getuid") else 0
        self.path = path or os.path.join(tempfile.gettempdir(), f"cct_server_use_{uid}.bin")
        self._lock = threading.Lock()
        self._pid = None
        self._fd = self._map = None
        self._checked = 0.0
        forksafe.after_fork_in_child(self._renew)

    def _renew(self):
        self._lock = threading.Lock()

    def _open(self):
        """Open per process: a descriptor inherited across fork would share its flock."""
        if self._pid == os.getpid():
            return
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        if os.fstat(fd).st_size < _SIZE:
            os.ftruncate(fd, _SIZE)
        self._fd, self._map, self._pid = fd, mmap.mmap(fd, _SIZE), os.getpid()

    @contextmanager
    def _held(self):
        with self._lock:
            self._open()
            _file_lock(self._fd)
            try:
                yield
            finally:
                _file_unlock(self._fd)

    def _advance(self, now: float, delta: int) -> float:
        """Bring A up to now, then change this process's open count by delta; returns A."""
        m = self._map
        last, a = _HEAD.unpack_from(m, 0)
        slots = [_SLOT.unpack_from(m, _HEAD.size + i * _SLOT.size) for i in range(_SLOTS)]
        if now - self._checked > 5:              # a worker killed mid-request leaves its count
            self._checked = now
            for i, (pid, _) in enumerate(slots):
                if pid and pid != self._pid and not _alive(pid):
                    slots[i] = (0, 0)
                    _SLOT.pack_into(m, _HEAD.size + i * _SLOT.size, 0, 0)
        n = sum(c for p, c in slots if p)
        if n > 0 and last:
            a += (now - last) / n
        _HEAD.pack_into(m, 0, now, a)
        if delta:
            i = next((i for i, (p, _) in enumerate(slots) if p == self._pid), None)
            if i is None:
                i = next((i for i, (p, _) in enumerate(slots) if not p), 0)
                slots[i] = (self._pid, 0)
            _SLOT.pack_into(m, _HEAD.size + i * _SLOT.size, self._pid, max(0, slots[i][1] + delta))
        return a

    def enter(self) -> float:
        with self._held():
            return self._advance(_now(), +1)

    def leave(self) -> float:
        with self._held():
            return self._advance(_now(), -1)

    def peek(self) -> float:
        with self._held():
            return self._advance(_now(), 0)


def _file_lock(fd):
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
    except ImportError:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                pass


def _file_unlock(fd):
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except ImportError:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _now() -> float:
    # Monotonic and system-wide (CLOCK_MONOTONIC; QueryPerformanceCounter), so the workers agree on it.
    return time.monotonic()


def _alive(pid: int) -> bool:
    if os.name != "posix":
        return True                     # os.kill(pid, 0) would end it on Windows; no forked workers there
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _children_cpu() -> float:
    try:
        import resource
    except ImportError:
        return 0.0
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


# ── the store ─────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS server_use (
    period    TEXT NOT NULL,
    bucket    INTEGER NOT NULL,
    service   TEXT NOT NULL,
    category  TEXT NOT NULL,
    tool      TEXT NOT NULL,
    action    TEXT NOT NULL,
    client    TEXT NOT NULL,
    count     INTEGER NOT NULL,
    billed_s  REAL NOT NULL,
    cpu_s     REAL NOT NULL,
    wall_s    REAL NOT NULL,
    PRIMARY KEY (period, bucket, service, category, tool, action, client)
);
"""
_KEY = ("period", "bucket", "service", "category", "tool", "action", "client")
_SUMS = ("count", "billed_s", "cpu_s", "wall_s")
_UPSERT = (f"INSERT INTO server_use ({', '.join(_KEY + _SUMS)}) VALUES ({', '.join('?' * 11)}) "
           f"ON CONFLICT ({', '.join(_KEY)}) DO UPDATE SET "
           + ", ".join(f"{c} = server_use.{c} + excluded.{c}" for c in _SUMS))
# (period, keep it for, the period it rolls into)
RETENTION = (("hour", 7 * 86400, "day"), ("day", 90 * 86400, "week"), ("week", 730 * 86400, "month"))


def period_start(period: str, t: float) -> int:
    d = datetime.fromtimestamp(t, timezone.utc)
    if period == "hour":
        d = d.replace(minute=0, second=0, microsecond=0)
    elif period == "day":
        d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":                    # Monday
        d = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        d = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(period)
    return int(d.timestamp())


class ServerUseStore(SqliteDB):
    def __init__(self, path: str, clock=time.time):
        super().__init__(path, _SCHEMA)
        self._clock = clock

    def add(self, rows: list) -> None:
        """rows: (bucket, service, category, tool, action, client, count, billed_s, cpu_s, wall_s), hourly."""
        if not rows:
            return
        with self._write() as db:
            for r in rows:
                db.execute(_UPSERT, ("hour", *r))

    def compact(self) -> dict:
        """Roll each period's old rows into the next; returns rows rolled per period."""
        now, done = self._clock(), {}
        for period, keep, into in RETENTION:
            cutoff = period_start(period, now - keep)
            with self._write() as db:
                rows = db.execute(f"SELECT {', '.join(_KEY[1:] + _SUMS)} FROM server_use "
                                  "WHERE period = ? AND bucket < ?", (period, cutoff)).fetchall()
                for r in rows:
                    db.execute(_UPSERT, (into, period_start(into, r[0]), *r[1:]))
                db.execute("DELETE FROM server_use WHERE period = ? AND bucket < ?", (period, cutoff))
            done[period] = len(rows)
        return done

    def query(self, since: float, until: Optional[float] = None) -> list[dict]:
        """Every row overlapping [since, until), whatever its period."""
        until = until or self._clock() + 1
        with self._read() as db:
            rows = db.execute(f"SELECT {', '.join(_KEY + _SUMS)} FROM server_use "
                              "WHERE bucket >= ? AND bucket < ? ORDER BY bucket",
                              (period_start("month", since), until)).fetchall()
        return [dict(zip(_KEY + _SUMS, r)) for r in rows]


# ── the tracker: in-memory counts, flushed once a minute ─────────────────

class Tracker:
    """One per process (forked children start their own, empty)."""

    def __init__(self, store: Optional[ServerUseStore], service: str, tool: str = "",
                 clock: Optional[_SharedClock] = None, wall=time.time):
        self.store, self.service, self.tool = store, service, tool or service
        self.clock = clock or _SharedClock()
        self.wall = wall
        self._lock = threading.Lock()
        self._counts: dict = {}
        self._open: dict = {}             # id -> Request
        self._pid = os.getpid()
        self._thread = None
        self._cpu_mark = time.process_time()
        self._children_mark = _children_cpu()
        self._attributed_cpu = 0.0
        self._ids = 0

    def _fresh(self):
        """In a forked child: nothing of the parent's is ours."""
        if self._pid != os.getpid():
            self._pid = os.getpid()
            self._lock = threading.Lock()
            self._counts, self._open, self._thread = {}, {}, None
            self._cpu_mark, self._children_mark, self._attributed_cpu = time.process_time(), _children_cpu(), 0.0
        if self._thread is None and self.store is not None:
            self._thread = threading.Thread(target=self._loop, name="cct-server-use", daemon=True)
            self._thread.start()
            atexit.register(self._quiet_flush)

    def _add(self, tags: tuple, count: int, billed: float, cpu: float, wall: float, when: float) -> None:
        key = (period_start("hour", when), self.service, *tags)
        c = self._counts.get(key)
        if c is None:
            self._counts[key] = [count, billed, cpu, wall]
        else:
            c[0] += count; c[1] += billed; c[2] += cpu; c[3] += wall

    def start(self, tags: tuple, *, thread_cpu: bool = True) -> "Request":
        self._fresh()
        r = Request(self, tags, thread_cpu)
        with self._lock:
            self._settle_children()
            self._ids += 1
            r.id = self._ids
            self._open[r.id] = r
        return r

    def _settle_children(self):
        """Split the child-process CPU (and, for single-thread ASGI requests,
        the process's CPU) used since the last event among the open requests."""
        kids = _children_cpu()
        dk, self._children_mark = kids - self._children_mark, kids
        if self._open and dk > 0:
            for r in self._open.values():
                r.cpu += dk / len(self._open)
            self._attributed_cpu += dk
        shared = [r for r in self._open.values() if not r.thread_cpu]
        if shared:
            p = time.process_time()
            dp = p - getattr(self, "_shared_mark", p)
            self._shared_mark = p
            for r in shared:
                r.cpu += dp / len(shared)
            self._attributed_cpu += dp
        else:
            self._shared_mark = time.process_time()

    def finish(self, r: "Request", tags: tuple) -> None:
        a = self.clock.leave()
        with self._lock:
            self._settle_children()
            self._open.pop(r.id, None)
            if r.thread_cpu:
                own = time.thread_time() - r.thread_mark
                r.cpu += own
                self._attributed_cpu += own
            self._add(tags, 1, a - r.a_mark, r.cpu, _now() - r.wall_mark, self.wall())

    def flush(self) -> None:
        """Write the counts (and open requests' shares so far) as hourly rows."""
        if self._pid != os.getpid() or self.store is None:
            return
        a = self.clock.peek()
        with self._lock:
            self._settle_children()
            now = self.wall()
            for r in self._open.values():
                if r.retag is not None:                 # who it's for, as far as known now
                    try:
                        r.tags = r.retag()
                    except Exception:
                        pass
                self._add(r.tags, 0, a - r.a_mark, r.cpu, _now() - r.wall_mark, now)
                r.a_mark, r.cpu, r.wall_mark = a, 0.0, _now()
            cpu = time.process_time() + _children_cpu()
            spare = (cpu - self._cpu_mark) - self._attributed_cpu
            self._cpu_mark, self._attributed_cpu = cpu, 0.0
            if spare > 0.01:
                self._add(("startup", self.tool, "background", ""), 0, 0.0, spare, 0.0, now)
            rows = [(*k, *v) for k, v in self._counts.items()]
            self._counts = {}
        try:
            self.store.add(rows)
        except Exception:
            with self._lock:                    # keep them for the next try
                for row in rows:
                    c = self._counts.setdefault(row[:6], [0, 0.0, 0.0, 0.0])
                    for i in range(4):
                        c[i] += row[6 + i]
            raise

    def _quiet_flush(self):
        try:
            self.flush()
        except Exception:
            pass                                # kept in memory; the next flush tries again

    def _loop(self):
        while True:
            time.sleep(FLUSH_S)
            self._quiet_flush()


class Request:
    __slots__ = ("tracker", "tags", "thread_cpu", "id", "a_mark", "wall_mark", "thread_mark", "cpu", "retag")

    def __init__(self, tracker: Tracker, tags: tuple, thread_cpu: bool):
        self.tracker, self.tags, self.thread_cpu = tracker, tags, thread_cpu
        self.a_mark = tracker.clock.enter()
        self.wall_mark = _now()
        self.thread_mark = time.thread_time()
        self.cpu = 0.0
        self.retag = None           # () -> tags, for a flush while it's still open

    def end(self, tags: tuple) -> None:
        self.tracker.finish(self, tags)


# ── who a request was for ─────────────────────────────────────────────────

class _Who:
    """Session -> (account, kind) and account -> (paid, admin), read-only
    and remembered for CACHE_S, so tracking adds no write and few reads."""

    def __init__(self, tokens, clock=time.time):
        self.tokens, self.clock = tokens, clock
        self._sessions: dict = {}
        self._accounts: dict = {}
        self._lock = threading.Lock()
        forksafe.after_fork_in_child(self._renew)

    def _renew(self):
        self._lock = threading.Lock()

    def _cached(self, cache, key, load):
        now = self.clock()
        with self._lock:
            hit = cache.get(key)
            if hit and now - hit[0] < CACHE_S:
                return hit[1]
        try:
            value = load()
        except Exception:
            value = None
        with self._lock:
            if len(cache) > 5000:
                cache.clear()
            cache[key] = (now, value)
        return value

    def session(self, token: Optional[str]) -> Optional[dict]:
        if not token or self.tokens is None:
            return None
        h = hashlib.sha256(token.encode()).hexdigest()

        def load():
            with self.tokens._read() as db:
                row = db.execute("SELECT account_id, kind FROM sessions WHERE token_hash = ? "
                                 "AND revoked_at IS NULL AND expires_at > ?", (h, self.clock())).fetchone()
            return {"account_id": row[0], "kind": row[1]} if row else None
        return self._cached(self._sessions, h, load)

    def account(self, account_id: str) -> dict:
        """{paid: has ever bought tokens, admin: an admin's account}."""
        def load():
            from .admin import admin_emails
            with self.tokens._read() as db:
                paid = db.execute("SELECT 1 FROM ledger WHERE account_id = ? AND kind = 'purchase' LIMIT 1",
                                  (account_id,)).fetchone() is not None
            email = (self.tokens.account_email(account_id) or "").lower()
            return {"paid": paid, "admin": email in admin_emails()}
        return self._cached(self._accounts, account_id, load) or {}


def _token(headers: dict) -> Optional[str]:
    auth = headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    from .account_routes import COOKIE_NAME
    m = re.search(rf"(?:^|;\s*){COOKIE_NAME}=([^;]+)", headers.get("cookie", ""))
    return m.group(1) if m else None


def _describe(r: dict, who: Optional[_Who]) -> dict:
    """Fill in session, paid and admin, then the category."""
    s = r.get("session")
    if s is None and who is not None:
        s = who.session(_token(r["headers"]))
    r["session"] = s
    if s and s.get("account_id") and who is not None:
        a = who.account(s["account_id"])
        r["paid"] = a.get("paid")
        r["admin"] = bool(a.get("admin")) and s.get("kind") == "web"
    r["category"] = categorize(r)
    return r


# ── WSGI (the Flask apps) ─────────────────────────────────────────────────

class ServerUseWSGI:
    def __init__(self, app, tracker: Tracker, who: Optional[_Who] = None, actions=()):
        self.app, self.tracker, self.who = app, tracker, who
        self.actions = [(m, re.compile(rx, re.I), a) for m, rx, a in actions]

    def __call__(self, environ, start_response):
        if self.tracker.store is None or "HTTP_" + INTERNAL_HEADER.upper().replace("-", "_") in environ:
            # Nowhere to write (no database yet), or inside a bundle, which counts it.
            return self.app(environ, start_response)
        method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "")
        headers = {k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")}
        ua = headers.get("user-agent", "")
        action = action_for(method, path, self.actions)
        via_gateway = ua.startswith(GATEWAY_UA)
        client = _client_name(headers.get(CLIENT_HEADER.lower(), "")) if via_gateway else ""
        r = {"method": method, "path": path, "query": environ.get("QUERY_STRING", ""), "ua": ua,
             "headers": headers, "action": action, "service": self.tracker.service, "ai": via_gateway,
             "status": 200}
        req = self.tracker.start((categorize(r), self.tracker.tool, action, client))
        status = [None]
        done = [False]

        def retag():
            from .account_routes import _SESSION_KEY
            open_r = _describe({**r, "session": environ.get(_SESSION_KEY)}, self.who)
            return (open_r["category"], self.tracker.tool, action, client)
        req.retag = retag

        def sr(s, h, exc_info=None):
            status[0] = int(s.split(" ", 1)[0])
            return start_response(s, h, exc_info) if exc_info else start_response(s, h)

        def end():
            if done[0]:
                return
            done[0] = True
            try:
                from .account_routes import _SESSION_KEY
                r.update(status=status[0], session=environ.get(_SESSION_KEY))
                _describe(r, self.who)
                req.end((r["category"], self.tracker.tool, action, client))
            except Exception:
                pass

        try:
            body = self.app(environ, sr)
        except BaseException:
            end()
            raise
        return _on_close(body, end)


def _on_close(body, end):
    """Call end() when the server closes the response. Keep the iterable
    itself where it allows (a file wrapper stays a file wrapper: sendfile)."""
    close = getattr(body, "close", None)
    if close is not None:
        def closing():
            try:
                close()
            finally:
                end()
        try:
            body.close = closing
            return body
        except (AttributeError, TypeError):
            pass
    return _Closing(body, close, end)


class _Closing:
    def __init__(self, body, close, end):
        self._body, self._close, self._end = body, close, end

    def __iter__(self):
        return iter(self._body)

    def close(self):
        try:
            if self._close:
                self._close()
        finally:
            self._end()


# ── ASGI (the MCP gateway) ───────────────────────────────────────────────

current_client: contextvars.ContextVar = contextvars.ContextVar("cct_ai_client", default="")
"""The AI client the gateway is acting for; it passes it on to the apps (CLIENT_HEADER)."""

BODY_PEEK = 64 * 1024


_NAME = re.compile(r"[A-Za-z0-9_./-]{1,40}")


def _bounded(value: str, known=None) -> str:
    """A name a row may hold. It came from the caller, so: one of `known`, or
    (with no list) a short plain name; else "other". Rows can't be made up."""
    if known is not None:
        return value if value in known else "other"
    return value if _NAME.fullmatch(value or "") else "other"


def server_use_asgi(app, tracker: Tracker, who: Optional[_Who] = None, *, mcp_path: str = "/mcp",
                    tools=None, methods=None):
    """Wrap the gateway's ASGI app. A request to mcp_path is the AI
    connector's: its JSON-RPC method says upkeep or a tool call, and a tool
    call's `app` argument is the tool. `tools` (the apps it serves) and
    `methods` (its MCP tool names) bound what a row may say; anything else
    is "other"."""
    known_tools = None if tools is None else {TOOL_ALIASES.get(t, t) for t in tools} | {tracker.tool}
    known_methods = None if methods is None else set(methods) | AI_UPKEEP | {"tools/call", "rpc"}
    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        method, path = scope["method"], scope["path"]
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        ua = headers.get("user-agent", "")
        client = ai_client(ua)
        is_mcp = path.rstrip("/") == mcp_path
        rpc = headers.get("mcp-method") or ("" if method == "POST" else method)
        action = _bounded(rpc or "rpc", known_methods) if is_mcp else action_for(method, path)
        r = {"method": method, "path": path, "query": scope.get("query_string", b"").decode("latin-1"),
             "ua": ua, "headers": headers, "service": tracker.service,
             "ai": True, "ai_upkeep": not is_mcp or rpc in AI_UPKEEP, "action": action, "status": 200}
        req = tracker.start((categorize(r), tracker.tool, action, client), thread_cpu=False)
        body, status, ended = bytearray(), [None], [False]

        def end():
            if ended[0]:
                return
            ended[0] = True
            try:
                tool, act = tracker.tool, action
                if is_mcp and body:
                    try:
                        msg = json.loads(bytes(body))
                    except ValueError:
                        msg = None
                    if isinstance(msg, dict):
                        m = msg.get("method") or rpc
                        params = msg.get("params") or {}
                        if m == "tools/call":
                            act = _bounded(str(params.get("name") or m), known_methods)
                            arg = (params.get("arguments") or {}).get("app")
                            tool = _bounded(TOOL_ALIASES.get(str(arg), str(arg)), known_tools) if arg else tool
                        elif m:
                            act = _bounded(str(m), known_methods)
                        r["ai_upkeep"] = m != "tools/call"
                r.update(status=status[0], action=act)
                _describe(r, who)
                req.end((r["category"], tool, act, client))
            except Exception:
                pass

        async def rcv():
            m = await receive()
            if m.get("type") == "http.request" and len(body) < BODY_PEEK:
                body.extend(m.get("body", b"")[:BODY_PEEK - len(body)])
            return m

        async def snd(m):
            if m["type"] == "http.response.start":
                status[0] = m["status"]
            await send(m)
            if m["type"] == "http.response.body" and not m.get("more_body"):
                end()                       # billed until the response ends, not the handler

        token = current_client.set(client)
        try:
            await app(scope, rcv, snd)
        finally:
            current_client.reset(token)
            end()
    return wrapped


# ── turning it on ─────────────────────────────────────────────────────────

def service_name(tool: str) -> str:
    return os.environ.get("K_SERVICE") or tool


def _record_startup(tracker: Tracker) -> None:
    """Once per instance: how long the process took from its start to here
    (Linux /proc; Cloud Run bills an instance's startup too)."""
    took = _process_age()
    if took is None:
        return
    try:
        os.close(os.open(tracker.clock.path + ".started", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError:
        return                                  # another worker of this instance did
    if tracker.store is None:
        return
    try:
        tracker.store.add([(period_start("hour", tracker.wall()), tracker.service, "startup", tracker.tool,
                            "startup", "", 1, took, time.process_time() + _children_cpu(), took)])
    except Exception:
        pass


def _process_age() -> Optional[float]:
    try:
        with open("/proc/self/stat") as f:
            start_ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return max(0.0, uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _wrap_inside(app, make):
    """Wrap Flask's own wsgi_app where the app's wrappers already call it, so
    they stay outermost (0.27.1). Every app puts EdgeAwareProxyFix on
    app.wsgi_app before registering cct_common: wrapped outside it, this
    would see the edge's raw environ (its secret header) and replace the
    app's proxy as app.wsgi_app. Looks through the wrappers' attributes (an
    EdgeAwareProxyFix holds two ProxyFix, each holding Flask's wsgi_app);
    where it can't see in (a closure), it wraps outside as before."""
    flask_wsgi = getattr(type(app), "wsgi_app", None)

    def is_flask(x):
        return getattr(x, "__self__", None) is app and getattr(x, "__func__", None) is flask_wsgi

    outer = app.wsgi_app
    if is_flask(outer):
        app.extensions["cct_server_use_placement"] = "only"        # no wrappers yet
        app.wsgi_app = make(outer)
        return app.wsgi_app
    holders, seen, todo = [], set(), [(outer, 0)]
    while todo:
        obj, depth = todo.pop()
        if id(obj) in seen or depth > 6:
            continue
        seen.add(id(obj))
        for name, value in list(getattr(obj, "__dict__", {}).items()):
            if is_flask(value):
                holders.append((obj, name))
            elif callable(value) and hasattr(value, "__dict__") and not isinstance(value, type) \
                    and not hasattr(value, "__func__") and not hasattr(value, "__code__"):
                todo.append((value, depth + 1))             # another wrapper object: look inside
    if not holders:
        # An app test can assert the placement (app.extensions["cct_server_use_placement"]).
        import logging
        logging.getLogger(__name__).warning(
            "server_use: can't see inside %r to reach Flask's wsgi_app; wrapping outside it, "
            "so it sees requests before that wrapper does", outer)
        app.extensions["cct_server_use_placement"] = "outside"
        app.wsgi_app = make(outer)
        return app.wsgi_app
    app.extensions["cct_server_use_placement"] = "inside"
    mw = make(getattr(*holders[0]))
    for obj, name in holders:
        setattr(obj, name, mw)
    return mw


def register_server_use(app, accounts=None, *, tool: str = "", database_url: Optional[str] = None,
                        actions=(), clock: Optional[_SharedClock] = None) -> Optional[Tracker]:
    """Count this Flask app's server use. An app needn't call it:
    register_account_routes, register_agent_api and register_admin do, and
    the first call wraps the app (later ones only add what they know).

    Rows go to the accounts' database, else database_url, else DATABASE_URL;
    the tool is the Cloud Run service (K_SERVICE less -test), else `tool`;
    `actions` adds (method or None, path regex, action) for routes ACTIONS
    doesn't know. CCT_SERVER_USE=0 turns it off (a test suite)."""
    if os.environ.get("CCT_SERVER_USE", "1") == "0":
        return None
    tokens = getattr(accounts, "tokens", None)
    path = database_url or (tokens.path if tokens is not None else None) or os.environ.get("DATABASE_URL")
    tracker = app.extensions.get("cct_server_use")
    if tracker is not None:                                 # already on: fill in what was missing
        mw = app.extensions["cct_server_use_wsgi"]
        if mw.who is None and tokens is not None:
            mw.who = _Who(tokens)
        if tracker.store is None and path:
            tracker.store = ServerUseStore(path)
        mw.actions += [(m, re.compile(rx, re.I), a) for m, rx, a in actions]
        return tracker
    name = tool_name(tool)
    tracker = Tracker(ServerUseStore(path) if path else None, service_name(name), name, clock=clock)
    _record_startup(tracker)
    mw = _wrap_inside(app, lambda inner: ServerUseWSGI(inner, tracker, _Who(tokens) if tokens is not None else None,
                                                       actions))
    app.extensions["cct_server_use"] = tracker
    app.extensions["cct_server_use_wsgi"] = mw
    return tracker


def report(store: ServerUseStore, days: int, now: Optional[float] = None) -> dict:
    """The admin tab's numbers: every row of the last `days` days summed by
    UTC day (rows already rolled into weeks or months keep their period)."""
    now = now or time.time()
    since = period_start("day", now - days * 86400)
    sums: dict = {}
    for r in store.query(since, now + 1):
        if r["bucket"] < since and r["period"] in ("hour", "day"):
            continue
        day = period_start("day", r["bucket"]) if r["period"] in ("hour", "day") else r["bucket"]
        key = (day, r["period"] if r["period"] in ("week", "month") else "day",
               r["service"], r["category"], r["tool"], r["action"], r["client"])
        s = sums.setdefault(key, [0, 0.0, 0.0, 0.0])
        for i, c in enumerate(_SUMS):
            s[i] += r[c]
    rows = [dict(zip(("day", "period", "service", "category", "tool", "action", "client") + _SUMS,
                     (*k, v[0], round(v[1], 3), round(v[2], 3), round(v[3], 3)))) for k, v in sorted(sums.items())]
    return {"since": since, "until": now, "rows": rows, "categories": CATEGORIES}


# ── Google's own count: Cloud Monitoring's billable instance time ────────

METADATA = "http://metadata.google.internal/computeMetadata/v1"
MONITORING = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
BILLABLE_METRIC = "run.googleapis.com/container/billable_instance_time"
_google_cache: dict = {}


def _http_json(url: str, headers: Optional[dict] = None, timeout: float = 8.0):
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if body[:1] in (b"{", b"[") else body.decode()


def google_billable(days: int, now: Optional[float] = None, fetch=_http_json) -> dict:
    """{"services": {service: {day: seconds}}} from Cloud Monitoring, read
    with this service's own identity; or {"error": what's missing}. Needs
    roles/monitoring.viewer for the runtime service account. Cached 10 min."""
    import urllib.error
    import urllib.parse
    now = now or time.time()
    key = (days, int(now // 600))
    if key in _google_cache:
        return _google_cache[key]
    md = {"Metadata-Flavor": "Google"}
    if not (os.environ.get("K_SERVICE") or os.environ.get("CCT_GCP_PROJECT")) and fetch is _http_json:
        return {"error": "not running on Google Cloud, so Google's own totals aren't available here"}
    try:
        project = os.environ.get("CCT_GCP_PROJECT") or fetch(f"{METADATA}/project/project-id", md)
        token = fetch(f"{METADATA}/instance/service-accounts/default/token", md)["access_token"]
    except Exception:
        return {"error": "not running on Google Cloud (no metadata server), so Google's own totals aren't available here"}
    end = period_start("day", now) + 86400                  # windows end at UTC midnights
    q = urllib.parse.urlencode({
        "filter": f'metric.type="{BILLABLE_METRIC}"',
        "interval.startTime": _rfc3339(end - days * 86400), "interval.endTime": _rfc3339(end),
        "aggregation.alignmentPeriod": "86400s", "aggregation.perSeriesAligner": "ALIGN_SUM",
        "aggregation.crossSeriesReducer": "REDUCE_SUM", "aggregation.groupByFields": "resource.label.service_name"})
    services: dict = {}
    url = MONITORING.format(project=project) + "?" + q
    try:
        while url:
            page = fetch(url, {"Authorization": f"Bearer {token}"})
            for ts in page.get("timeSeries", []):
                name = ts.get("resource", {}).get("labels", {}).get("service_name", "?")
                for p in ts.get("points", []):
                    t = _parse_rfc3339(p["interval"]["startTime"])
                    v = p["value"].get("doubleValue", p["value"].get("int64Value", 0))
                    services.setdefault(name, {})[period_start("day", t)] = float(v)
            tok = page.get("nextPageToken")
            url = (MONITORING.format(project=project) + "?" + q + "&pageToken=" + tok) if tok else None
    except urllib.error.HTTPError as e:
        if e.code == 403:
            try:
                who = fetch(f"{METADATA}/instance/service-accounts/default/email", md)
            except Exception:
                who = "this service's account"
            return {"error": f"Cloud Monitoring said 403: grant roles/monitoring.viewer on {project} to {who}"}
        return {"error": f"Cloud Monitoring said {e.code}"}
    except Exception as e:
        return {"error": f"Cloud Monitoring: {e}"}
    out = {"services": services, "project": project}
    _google_cache.clear()
    _google_cache[key] = out
    return out


def _rfc3339(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_rfc3339(s: str) -> float:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


@contextmanager
def job_use(database_url: str, service: str, action: str, *, tool: str = "", category: str = "scheduled"):
    """Count a scheduled job's run (a Cloud Run job bills its whole run):
        with job_use(DATABASE_URL, "cct-db-backup", "backup"): ...
    Written once, when the job ends."""
    tracker = Tracker(ServerUseStore(database_url), service, tool or service, clock=_SharedClock(
        os.path.join(tempfile.gettempdir(), f"cct_server_use_job_{os.getpid()}.bin")))
    tracker._thread = False                 # no flush thread: one write at the end
    req = tracker.start((category, tracker.tool, action, ""))
    try:
        yield tracker
    finally:
        req.end((category, tracker.tool, action, ""))
        tracker.flush()
