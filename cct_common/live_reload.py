"""
live_reload.py — auto-reload open tabs after a dev-server restart.

Pairs with flask_shutdown: after a controlling process calls /api/shutdown
and relaunches the server, any tab left open on the old page notices the
new process (a fresh boot_id) and reloads itself automatically, instead of
showing stale/broken state or needing to be found and closed by hand.

    from cct_common.live_reload import register_live_reload

    register_live_reload(app)  # adds /api/_boot_id + /_cct_live_reload.js

Then in the page's HTML (once, near the end of <body>):

    <script src="/_cct_live_reload.js"></script>

Flask is only imported inside register_live_reload(), so importing
cct_common never requires Flask unless a caller wires this in.
"""
from __future__ import annotations

import uuid

# Generated once per process start. A page that polls this and sees it
# change knows a new server process is now serving requests.
_BOOT_ID = uuid.uuid4().hex

_JS_TEMPLATE = """\
(function () {
  var bootId = null;
  function poll() {
    fetch(%(boot_path)r, { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (bootId === null) {
          bootId = data.boot_id;
        } else if (data.boot_id !== bootId) {
          location.reload();
        }
      })
      .catch(function () { /* server down mid-restart — keep polling */ });
  }
  poll();
  setInterval(poll, %(poll_ms)d);
})();
"""


def register_live_reload(app, boot_path: str = "/api/_boot_id",
                         script_path: str = "/_cct_live_reload.js",
                         poll_ms: int = 3000):
    """Register the boot-id endpoint and the JS that polls it.

    `boot_id` is a random token generated once per process start; a
    change means the server restarted (e.g. via flask_shutdown's
    /api/shutdown plus a controller relaunching it), so any open tab
    reloads itself rather than being left showing a stale/dead page.
    """
    from flask import Response, jsonify

    def _boot_id():
        return jsonify({"boot_id": _BOOT_ID})

    def _script():
        js = _JS_TEMPLATE % {"poll_ms": poll_ms, "boot_path": boot_path}
        return Response(js, mimetype="application/javascript")

    _boot_id.__name__ = "_cct_common_boot_id"
    _script.__name__ = "_cct_common_live_reload_script"
    app.add_url_rule(boot_path, endpoint="_cct_common_boot_id",
                     view_func=_boot_id, methods=["GET"])
    app.add_url_rule(script_path, endpoint="_cct_common_live_reload_script",
                     view_func=_script, methods=["GET"])
