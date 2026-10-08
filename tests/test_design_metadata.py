"""test_design_metadata.py — every downloaded file carries the WHOLE design for Import.

A file used to save only its own request's parameters: an SVG or DXF had no Belt
Height (a flat drawing doesn't need it), a single belt's files only its profile, a
flange's STL its own renamed subset (pulley 2's read back as pulley 1). A design
imported from one came back with the page's Belt Height, silently (an Imperial XL
print with 12 mm teeth for 14.5, 2026-10-07). The owner: "ensure that full metadata
is saved in all files".

The page now sends the whole design with every download (`design`, JSON:
designParams() — buildParams and the Belt Height, what a link carries) and every
file embeds it (app._design_of); without it a file embeds its request's own
parameters, as before. tests/browser/roundtrip_ui.js checks the page's side: every
file the Download window makes embeds the design, and an SVG, DXF and STL import it.
"""
import json
import re

import pytest

# The design the owner printed (from the downloaded SVG), with the Belt Height asked for.
DESIGN = {
    "family": "Imperial", "pitch": "XL", "teeth": "30", "bore": "8", "print_extra": "0.2",
    "clearance_preset": "STANDARD", "clearance_custom": "0", "backlash_preset": "STANDARD",
    "backlash_custom": "0", "hub_od": "25", "hub_height": "10", "hub_screw_size": "M5",
    "hub_screw_hold": "thread", "hub_screw_count": "2", "dual": "true", "center_distance": "94.47",
    "p2_teeth": "45", "p2_bore": "8", "p2_print_extra": "0", "p2_clearance_preset": "STANDARD",
    "p2_clearance_custom": "0", "p2_backlash_preset": "STANDARD", "p2_backlash_custom": "0",
    "p2_hub_od": "25", "p2_hub_height": "15", "p2_hub_screw_size": "M5", "p2_hub_screw_hold": "thread",
    "p2_hub_screw_count": "2", "p2_spokes_enabled": "1", "p2_spokes_hub_od": "25",
    "p2_spokes_rim_depth": "6", "p2_spokes_width": "8", "p2_spokes_fillet_tip": "3",
    "p2_spokes_fillet_base": "3", "p2_spokes_count": "5", "p2_spokes_height": "6",
    "flange_enabled": "1", "flange_3dprint": "1", "flange_angle": "15", "flange_rim_radius": "4",
    "flange_height": "3", "flange_plate_height": "1", "flange_bend_radius": "0",
    "flange_top_separate": "1", "hub_height_for_flange": "10",
    "p2_flange_enabled": "1", "p2_flange_3dprint": "1", "p2_flange_angle": "15",
    "p2_flange_rim_radius": "4", "p2_flange_height": "3", "p2_flange_plate_height": "1",
    "p2_flange_bend_radius": "0", "p2_flange_top_separate": "1", "p2_hub_height_for_flange": "15",
    "clearance_height": "2", "belt_height": "12.5", "sv": "1",
}


def _embedded(data: bytes) -> dict:
    """The design a file embeds, read the way the page's Import reads it."""
    t = data.decode("utf-8", errors="replace")
    m = (re.search(r"/\* CCT:(\{.+?\}) \*/", t) or re.search(r"999\r?\nCCT:(\{.+})", t)
         or re.search(r"<cct>([\s\S]+?)</cct>", t))
    assert m, "no design embedded"
    data = json.loads(m.group(1))
    return dict(data.get("cct", data))


# Each route with only ITS OWN request's parameters — what the page's Download window
# sends for that file — which name less than the design, or other names.
_P1 = {k: v for k, v in DESIGN.items() if k != "belt_height"}          # as buildParams
_FLANGE_P2 = {"family": "Imperial", "pitch": "XL", "teeth": "45", "bore": "8", "belt_height": "12.5",
              "flange_enabled": "1", "flange_3dprint": "1", "flange_angle": "15", "flange_rim_radius": "4",
              "flange_height": "3", "flange_top_separate": "1", "flange_which": "top", "pulley": "2"}
ROUTES = [
    ("/download/svg", dict(_P1, include_data="1")),
    ("/download/dxf", _P1),
    ("/download/svg", dict(_P1, include_data="1", pulley="2")),
    ("/download/stl", dict(_P1, belt_height="12.5")),
    ("/download/belt-svg", {"family": "Imperial", "pitch": "XL"}),          # a single belt: its profile only
    ("/download/belt-dxf", {"family": "Imperial", "pitch": "XL"}),
    ("/download/belt-svg", dict(_P1, n_belt="0")),
    ("/download/all-dxf", dict(_P1, n_belt="0")),
    ("/download/flange-stl", _FLANGE_P2),                                   # pulley 2's flange, flat names
]


@pytest.mark.parametrize("path, params", ROUTES,
                         ids=[f"{p.split('/')[-1]}-{i}" for i, (p, _) in enumerate(ROUTES)])
def test_every_file_embeds_the_whole_design(client, path, params):
    r = client.get(path, query_string=dict(params, design=json.dumps(DESIGN)))
    assert r.status_code == 200, r.data[:200]
    assert _embedded(r.data) == DESIGN


def test_the_flat_drawings_carry_the_belt_height(client):
    """The reported case: the SVG and DXF had no belt_height, so Import kept the page's."""
    for path in ("/download/svg", "/download/dxf"):
        r = client.get(path, query_string=dict(_P1, include_data="1", design=json.dumps(DESIGN)))
        meta = _embedded(r.data)
        assert meta["belt_height"] == "12.5" and meta["clearance_height"] == "2"


def test_without_the_design_a_file_embeds_its_own_request(client):
    """A link or an older page: the request's own parameters, as before (less the
    reserved key)."""
    r = client.get("/download/svg", query_string=dict(_P1, include_data="1"))
    assert _embedded(r.data) == dict(_P1, include_data="1")
    r = client.get("/download/svg", query_string=dict(_P1, include_data="1", design="{not json"))
    assert _embedded(r.data) == dict(_P1, include_data="1")


def test_the_agent_sends_the_whole_design_with_every_file():
    """The agent API's files (the MCP gateway, CAD plugins): each request carries the
    caller's whole design, so a single belt's files aren't only its profile."""
    from agent import files
    q = {"family": "HTD", "pitch": "5M", "teeth": "20", "bore": "8", "belt_height": "15",
         "hub_od": "22", "hub_height": "8"}
    out = files(q, ["pulley1", "belt"], ["stl", "svg", "dxf"])
    assert out and all(json.loads(params["design"]) == q for _pt, _fmt, _path, params, _name in out)


def test_the_page_sends_the_design_with_every_download():
    """Every way the page downloads goes through one of these, and each adds `design`:
    the Download window's requests (_dlFiles), a direct link (_dlUrl) and the async
    jobs. roundtrip_ui.js runs it; this keeps the hooks from being dropped unnoticed."""
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
    assert "function designParams()" in page
    dl_files = page[page.index("function _dlFiles(part, fmt)"):page.index("function _dlFileRequests(")]
    assert "design" in dl_files
    dl_url = page[page.index("function _dlUrl(url)"):]
    assert "'design=' + encodeURIComponent(_designJson())" in dl_url[:1200]
    assert page.count("params.design = _designJson();") == 2      # _startAsyncJob, startAsyncDownload
