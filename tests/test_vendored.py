"""The vendored cct_common is a released copy, and the app says which (cct_common 0.26.0):
the night fails on a copy edited in place or synced between releases, and /api/health
gives the admin page's Status tab the copy's version and hash."""
import os
import re
from pathlib import Path

import cct_common
from cct_common.testing.vendored import check_vendored

ROOT = Path(__file__).resolve().parent.parent
# the cct_common repo whose v<version> tag the copy must match: a test host names its clone
UPSTREAM = Path(os.environ.get("CCT_COMMON_CHECKOUT") or r"C:\Users\cmyer\Documents\cct_common")


def test_vendored_cct_common_is_released():
    check_vendored(repo=UPSTREAM if (UPSTREAM / ".git").exists() else None)


def test_health_names_the_released_cct_common(client):
    from app import APP_VERSION
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok" and body["version"] == APP_VERSION
    assert body["cct_common"] == cct_common.__version__
    assert body["cct_common_released"] is True and len(body["cct_common_hash"]) == 64


def test_the_admin_opens_a_design_in_the_app():
    """The admin page is at the site's /admin, so without the app's address its "Open this
    design" went to the site's home page (CCT_WebSite's find, 2026-10-27)."""
    call = re.search(r"register_admin\(app,.*?\)\n", (ROOT / "app.py").read_text(encoding="utf-8"), re.S)
    assert "app_url='https://cheapcadtools.com/tools/pulleys'" in call.group(0)
