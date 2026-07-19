"""
flask_shutdown.py — a self-shutdown route for local Flask dev servers.

Lets a controlling process (a dev script restarting the server, or a CAD
add-in restarting it during the add-in's own update) ask the server to
exit cleanly instead of hunting PIDs by port. Werkzeug's dev server traps
SIGINT and exits on it; this route just sends that signal to itself.

Flask is only imported inside register_shutdown_route(), so importing
cct_common (or this module) never requires Flask to be installed unless a
caller actually wires this in.

    from cct_common.flask_shutdown import register_shutdown_route
    register_shutdown_route(app)  # adds POST /api/shutdown
"""
from __future__ import annotations

import os
import signal

LOOPBACK_ADDRS = ("127.0.0.1", "::1")


def register_shutdown_route(app, path: str = "/api/shutdown",
                            loopback_only: bool = True):
    """Register a POST route on `app` that sends SIGINT to this process.

    With the debug reloader's watcher/worker process pair (Flask's
    `debug=True`), this only stops the process that receives the request —
    run with `use_reloader=False` when a controller needs a single PID to
    manage cleanly.
    """
    from flask import jsonify, request

    def _shutdown():
        if loopback_only and request.remote_addr not in LOOPBACK_ADDRS:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        os.kill(os.getpid(), signal.SIGINT)
        return jsonify({"ok": True})

    _shutdown.__name__ = "_cct_common_shutdown"
    app.add_url_rule(path, endpoint="_cct_common_shutdown",
                     view_func=_shutdown, methods=["POST"])
    return _shutdown
