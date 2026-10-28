"""Refused settings name the page's label and field and carry a value the Auto-fix
types in (geometry/labels.py; the owner, 2026-10-21). Planetary Gear Designer's
test_every_refusal_has_an_auto_fix_that_clears_it, for the pulley: every setting,
sent "abc" and "99999", is refused with a fix that is a value, and that value
clears the refusal (it may show the next thing to fix, never the same one)."""
import html
import math
import re
from pathlib import Path

import pytest

import app as app_module  # noqa: F401  (configures geometry.labels)
from geometry import labels

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
BASE = {"family": "HTD", "pitch": "5M", "teeth": "20", "bore": "8"}
SET_VALUES = {"hub_od": "30", "hub_screw_size": "M3", "hub_keyway_w": "4"}   # a "set" `when`


def _numeric(p):
    return p.type in ("number", "integer")


def _satisfy(q: dict, name: str, prefix: str):
    """q with what `name` needs to matter (its Param's `when`, and their own)."""
    p = labels.limits()[name]
    if prefix:
        q["dual"] = "true"
    for k, want in (p.when or {}).items():
        kp = labels.limits().get(k)
        key = (prefix if kp is not None and kp.per_part else "") + k
        if kp is not None:
            _satisfy(q, k, prefix if kp.per_part else "")
        if want is True:
            q[key] = "true" if k == "dual" else "1"
        elif want is False:
            q[key] = "0"
        elif want == "set":
            q[key] = SET_VALUES[k]
        else:
            q[key] = str(want)
    return q


CASES = [(prefix + name, prefix) for prefix in ("", "p2_") for name in labels.SETTINGS
         if not prefix or (name in labels.limits() and labels.limits()[name].per_part)]


@pytest.mark.parametrize("key,prefix", CASES, ids=[k for k, _ in CASES])
def test_every_refusal_has_an_auto_fix_that_clears_it(client, key, prefix):
    name = key[len(prefix):]
    p = labels.limits()[name]
    base = _satisfy(dict(BASE), name, prefix)
    refused = 0
    for bad in ("abc", "99999"):
        r = client.get("/api/dimensions", query_string=dict(base, **{key: bad}))
        if r.status_code != 400 or r.get_json().get("field") != labels.field(key):
            continue
        refused += 1
        body = r.get_json()
        assert labels.label(key) in body["error"] and name not in body["error"].split(), body
        assert body["fix"] is not None and "…" not in str(body["fix"]) and " to " not in str(body["fix"]), body
        if _numeric(p):
            v = float(body["fix"])              # a number the field can hold
            assert p.min <= v <= p.max and (p.type == "number" or v.is_integer()), body
        after = client.get("/api/dimensions", query_string=dict(base, **{key: body["fix"]}))
        assert after.status_code == 200 or after.get_json().get("error") != body["error"], (key, bad, after.get_json())
    # text always; 99999 for a list, and for a number whose top is below it
    assert refused == 1 + (not _numeric(p) or p.max < 99999), (key, refused)


def test_the_page_names_each_static_field_as_the_table_does():
    labels_on_page = {m.group(1): html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
                      for m in re.finditer(r'<label[^>]*\bfor="([A-Za-z0-9_]+)"[^>]*>(.*?)</label>', PAGE, re.S)}
    checked = 0
    for name in labels.SETTINGS:
        for key in (name, "p2_" + name):
            fid = labels.field(key)
            if fid not in labels_on_page:
                continue
            shown = re.sub(r"\s*\([^)]*\)\s*$", "", labels_on_page[fid])          # "(mm)", "(%)" apart
            assert shown.lower() in labels.label(key).lower(), (key, fid, shown, labels.label(key))
            checked += 1
    assert checked >= 50


def test_a_download_refuses_a_bad_setting(client):
    r = client.get("/download/svg", query_string=dict(BASE, teeth="99999"))
    assert r.status_code == 400 and r.get_json() == {
        "error": "Num. Teeth must be a whole number from 8 to 500.", "field": "teeth", "fix": "500"}
    r = client.get("/download/dxf", query_string=dict(BASE, dual="true", p2_teeth="20", p2_hub_od="abc"))
    assert r.status_code == 400 and r.get_json()["field"] == "hub2_od"


def test_a_blank_or_unused_setting_is_left_to_the_app(client):
    assert client.get("/api/dimensions", query_string=dict(BASE, bore="")).status_code == 200
    # Custom Offset only matters with CUSTOM; pulley 2's settings only in a drive
    assert client.get("/api/dimensions", query_string=dict(BASE, clearance_custom="99999")).status_code == 200
    assert client.get("/api/dimensions", query_string=dict(BASE, p2_teeth="abc")).status_code == 200


def test_a_blank_number_reaches_the_downloads_as_missing():
    """A number left empty is gone before a download's route reads it, so it takes its
    default (each route's float() failed on it, 2026-10-28); blank text is left as sent."""
    from flask import request
    with app_module.app.test_request_context(
            "/download/svg?family=HTD&teeth=&p2_print_extra=&belt_height=%20&bore_shape=&dual=true"):
        assert app_module.app.preprocess_request() is None
        assert not {"teeth", "p2_print_extra", "belt_height"} & (set(request.args) | set(request.values))
        assert request.args["family"] == "HTD" and request.args["bore_shape"] == ""


def test_the_agent_api_names_the_label(client):
    r = client.post("/api/v1/check", json={"params": dict(BASE, teeth=99999)})
    assert r.status_code == 400
    assert r.get_json()["details"] == ["Num. Teeth (teeth) must be at most 500"]


def test_the_page_applies_the_refusals_fix():
    """The page keeps a refusal's field and fix (refreshDims), marks the field and turns
    the message's mm into inches when it shows inches (inchMessage)."""
    assert "fix: data.field && data.fix != null" in PAGE
    assert "classList.add('setting-refused')" in PAGE
    assert "li.textContent = inchMessage(w)" in PAGE


def test_nearest():
    assert labels.nearest(99999, 8, 500, whole=True) == "500"
    assert labels.nearest(2.6, 2, 24, whole=True) == "3"
    assert labels.nearest(-1, 0, 5) == "0"
    assert labels.nearest(7, -math.inf, math.inf, whole=True) == "7"
