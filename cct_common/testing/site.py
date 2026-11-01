"""
site.py — an app as cheapcadtools.com serves it, for the browser harnesses
that need the site's paths (cct_common 0.56.0; Sprocket's root cause of
2026-10-31: node 24.15 died with 0xC0000409 about 1 run in 5 when the
harness's own node process ran a Worker-like proxy, http.createServer +
http.request + pipe, under the CPU cap; straight to app.py, 0 of 40).

    python -m cct_common.testing.site --app <dir> --prefix /tools/<tool>
        [--shared <regex>] [--port 0] [--app-port 0] --log <file>
        [--requests <file>] [--module app] [--attr app]

One Python process, no proxy: the app (<module>.<attr> imported from <dir>)
under a WSGI dispatcher on the site's port, as the site's Worker routes it:

- <prefix>/... goes to the app with the prefix stripped (the Host stays the
  site's, so emailed links point at the site);
- a root path matching --shared (the path, with "?query" when it has one:
  the site's shared sign-in paths, /account/... and the sign-in API, which
  the pulley app serves there) goes to the app as it is;
- anything else is answered 404 "not routed on the site".

The bare app is on a second port too (--app-port), for a harness's direct
calls (health, settings).

The requests file (--requests; the log's name + ".requests" if not given)
gets, first, "SITE <port>" and "APP <port>" once both are listening, then a
line for every request through the site: "SITE app <METHOD> <path>",
"SITE shared ...", "SITE stray ...", so a harness can check that nothing
else asked the root; and a line for every connection that dropped while its
request was read or answered (0.56.5): "SITE dropped <METHOD> <path>
<error>" on the site's port, "SITE app-dropped ..." on the bare app's, with
"- -" for a request whose first line never came. werkzeug ignores those
(connection_dropped does nothing), so a page's "Failed to fetch" that never
reached the dispatcher left no trace (Bevel's prefix_ui, 2026-10-31). A
connection closed cleanly with nothing asked (Chrome's unused spare) is no
line. Each line is written whole, under one lock, and
nothing else writes there (0.56.2: in the log file, werkzeug's access log
from other threads glued itself onto them, and site.js lost 58 of Gear's
150 strays). The log file gets everything the app and its server print (the
sign-in email's link included, with no Resend key): stdout and stderr are
the file, never a pipe. cct_common/testing/site.js starts it and reads both.
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
import threading
from typing import Callable, Optional

NOT_ROUTED = b"not routed on the site"


class Site:
    """The site's routing in front of one WSGI app; `note(kind, method,
    path)` records each request ("app", "shared" or "stray")."""

    def __init__(self, app, prefix: str, shared: Optional[str], note: Callable[[str, str, str], None]):
        self.app = app
        self.prefix = "/" + prefix.strip("/")
        self.shared = re.compile(shared) if shared else None
        self.note = note

    def __call__(self, environ, start_response):
        path, query = environ.get("PATH_INFO") or "/", environ.get("QUERY_STRING") or ""
        method = environ.get("REQUEST_METHOD", "GET")
        whole = path + ("?" + query if query else "")
        if path == self.prefix or path.startswith(self.prefix + "/"):
            rest = path[len(self.prefix):] or "/"
            self.note("app", method, rest)
            return self.app({**environ, "PATH_INFO": rest}, start_response)
        if self.shared is not None and self.shared.search(whole):
            self.note("shared", method, path)
            return self.app(environ, start_response)
        self.note("stray", method, whole)
        start_response("404 Not Found", [("Content-Type", "text/plain"), ("Content-Length", str(len(NOT_ROUTED)))])
        return [NOT_ROUTED]


class RequestLines:
    """The requests file's writer: each line whole, one writer at a time,
    whatever the file object does with a write (CPython's happen to land
    whole; this doesn't count on it)."""

    def __init__(self, f):
        self.f = f
        self.lock = threading.Lock()

    def write(self, text: str) -> None:
        with self.lock:
            self.f.write(text)
            self.f.flush()

    def note(self, kind: str, method: str, path: str) -> None:
        self.write(f"SITE {kind} {method} {path}\n")

    def dropped(self, kind: str, method: Optional[str], path: Optional[str], error: BaseException) -> None:
        why = " ".join(f"{type(error).__name__}: {error}".split())          # one line
        self.write(f"SITE {kind} {method or '-'} {path or '-'} {why}\n")


def _handler(kind: str, lines: RequestLines):
    """werkzeug's request handler, a dropped connection noted as `kind`.
    werkzeug calls connection_dropped for a reset while it read the request
    (no environ yet) or while the app read the body or the answer was
    written; the request line's method and path are there once it was read."""
    from werkzeug.serving import WSGIRequestHandler

    class Handler(WSGIRequestHandler):
        def connection_dropped(self, error, environ=None):
            lines.dropped(kind, getattr(self, "command", None), getattr(self, "path", None), error)

    return Handler


def _to_file(log_path: str):
    """stdout and stderr (the file descriptors too, for anything below
    Python) become the log file, line by line."""
    f = open(log_path, "a", encoding="utf-8", buffering=1, errors="replace")
    for fd, name in ((1, "stdout"), (2, "stderr")):
        try:
            os.dup2(f.fileno(), fd)
        except OSError:
            pass
        setattr(sys, name, f)
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cct_common.testing.site")
    ap.add_argument("--app", required=True, help="the app's folder")
    ap.add_argument("--prefix", required=True, help="the app's path on the site, e.g. /tools/sprocket")
    ap.add_argument("--shared", default=None, help="root paths that go to the app as they are (a regex)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--app-port", type=int, default=0)
    ap.add_argument("--log", required=True)
    ap.add_argument("--requests", default=None, help="the request lines' file (the log's name + .requests)")
    ap.add_argument("--module", default="app")
    ap.add_argument("--attr", default="app")
    a = ap.parse_args(argv)
    _to_file(a.log)
    lines = RequestLines(open(a.requests or a.log + ".requests", "a", encoding="utf-8", errors="replace",
                              newline="\n"))                  # LF lines on every system

    app_dir = os.path.abspath(a.app)
    os.chdir(app_dir)
    sys.path.insert(0, app_dir)
    app = getattr(importlib.import_module(a.module), a.attr)
    from werkzeug.serving import make_server
    site = make_server("127.0.0.1", a.port, Site(app, a.prefix, a.shared, lines.note), threaded=True,
                       request_handler=_handler("dropped", lines))
    bare = make_server("127.0.0.1", a.app_port, app, threaded=True, request_handler=_handler("app-dropped", lines))
    for server in (site, bare):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    lines.write(f"SITE {site.server_port}\nAPP {bare.server_port}\n")
    threading.Event().wait()                     # until killed (site.js's stop: the whole tree)
    return 0


if __name__ == "__main__":
    sys.exit(main())
