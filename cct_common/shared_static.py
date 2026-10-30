"""
shared_static.py — the page files every CCT app shares, served from
cct_common itself (0.29.0): static/cct/<name>.

    from cct_common.shared_static import register_shared_static
    register_shared_static(app)        # register_agent_api and register_account_routes do this

The files live in cct_common/static/ inside the package (tokens.js,
cct_download.js, cct_units.js, cct_busy.js, cct_theme.css, cct_layout.css,
cct_viewer3d.js), so an app's ordinary sync brings them, the release hash and
check_vendored cover them, and nobody copies them by hand. A page asks for
static/cct/tokens.js: relative, so under /tools/<app>/ it reaches its own
app; /static/cct/... from a page at the site's root (the pulley app's).

Each answer carries an ETag and Cache-Control: no-cache, so a browser keeps
its copy and asks again cheaply (304) and a new release shows at once. Only
the files in that folder are served; .js as text/javascript and .css as
text/css whatever the server's own type table says (Windows may say
text/plain, which a module import refuses).
"""
from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).with_name("static")
URL_RULE = "/static/cct/<path:name>"
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
         ".png": "image/png", ".svg": "image/svg+xml"}


def shared_files() -> list[str]:
    return sorted(p.name for p in STATIC_DIR.iterdir() if p.is_file() and p.suffix in TYPES)


def register_shared_static(app) -> None:
    """Serve cct_common/static/<name> at static/cct/<name>. Once per app."""
    if app.extensions.get("cct_shared_static"):
        return
    from flask import abort, send_from_directory

    allowed = set(shared_files())

    def cct_shared_static(name):
        if name not in allowed:
            abort(404)
        r = send_from_directory(STATIC_DIR, name, mimetype=TYPES[Path(name).suffix], max_age=0)
        r.headers["Cache-Control"] = "no-cache"
        return r

    app.add_url_rule(URL_RULE, endpoint="cct_shared_static", view_func=cct_shared_static, methods=["GET"])
    app.extensions["cct_shared_static"] = True
