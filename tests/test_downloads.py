"""
test_downloads.py — Smoke tests for download routes not covered elsewhere.

Covers:
  /download/belt-stl    (dual mode — was 501, now fixed)
  /download/belt-step   (dual mode — teeth-gap fix)
  /download/belt-dxf    (single and dual modes)
  /download/all-dxf     (combined layout DXF)
  /download/all-step    (multipart STEP — single and dual)
  /download/flange-step (3D-print and metal flange STEP)
  a field left empty on any of them (its default)

Also verifies that CCT metadata is embedded in every response:
  STEP / STL  → b'/* CCT:'
  DXF         → b'CCT:'
  SVG         → b'CCT:'  (checked in test_api.py for belt-svg; included here for all-dxf)
"""

import re

import pytest

# ---------------------------------------------------------------------------
# Shared query-string fragments reused across tests
# ---------------------------------------------------------------------------

_SINGLE = (
    'family=HTD&pitch=5M&teeth=20&bore=8&belt_height=10'
    '&clearance_preset=STANDARD&backlash_preset=STANDARD'
    '&print_extra=0'
)

_DUAL = (
    'family=HTD&pitch=5M'
    '&teeth=20&bore=8&clearance_preset=STANDARD&backlash_preset=STANDARD&print_extra=0'
    '&p2_teeth=30&p2_bore=8&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD&p2_print_extra=0'
    '&center_distance=120&belt_height=10'
    '&dual=true'
)

_FLANGE_3DP = (
    _SINGLE
    + '&flange_3dprint=1&flange_angle=15&flange_rim_radius=3&flange_height=1.5'
)


# ---------------------------------------------------------------------------
# /download/belt-stl
# ---------------------------------------------------------------------------

def test_belt_stl_single_returns_400(client):
    """belt-stl without dual=true must return 400."""
    r = client.get(f'/download/belt-stl?{_SINGLE}')
    assert r.status_code == 400


def test_belt_stl_dual_returns_200(client):
    r = client.get(f'/download/belt-stl?{_DUAL}')
    assert r.status_code == 200


def test_belt_stl_dual_content_disposition(client):
    r = client.get(f'/download/belt-stl?{_DUAL}')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '-belt.stl' in cd


def test_belt_stl_dual_min_size(client):
    r = client.get(f'/download/belt-stl?{_DUAL}')
    assert r.status_code == 200
    assert len(r.data) > 84, 'STL body smaller than STL header — likely empty'


def test_belt_stl_dual_cct_metadata(client):
    r = client.get(f'/download/belt-stl?{_DUAL}')
    assert r.status_code == 200
    assert b'/* CCT:' in r.data, 'CCT metadata trailer missing from belt STL'


# ---------------------------------------------------------------------------
# /download/belt-step
# ---------------------------------------------------------------------------

def test_belt_step_single_returns_400(client):
    """belt-step without dual=true must return 400."""
    r = client.get(f'/download/belt-step?{_SINGLE}')
    assert r.status_code == 400


def test_belt_step_dual_returns_200(client):
    r = client.get(f'/download/belt-step?{_DUAL}')
    assert r.status_code == 200


def test_belt_step_dual_content_disposition(client):
    r = client.get(f'/download/belt-step?{_DUAL}')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '-belt.step' in cd


def test_belt_step_dual_is_step(client):
    r = client.get(f'/download/belt-step?{_DUAL}')
    assert r.status_code == 200
    assert b'ISO-10303' in r.data or b'STEP' in r.data, 'Response does not look like STEP'
    assert len(r.data) > 1000


def test_belt_step_dual_cct_metadata(client):
    r = client.get(f'/download/belt-step?{_DUAL}')
    assert r.status_code == 200
    assert b'/* CCT:' in r.data, 'CCT metadata comment missing from belt STEP'


# ---------------------------------------------------------------------------
# /download/belt-dxf
# ---------------------------------------------------------------------------

def test_belt_dxf_single_returns_200(client):
    r = client.get('/download/belt-dxf?family=HTD&pitch=5M')
    assert r.status_code == 200


def test_belt_dxf_single_content_disposition(client):
    r = client.get('/download/belt-dxf?family=HTD&pitch=5M')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '.dxf' in cd


def test_belt_dxf_single_cct_metadata(client):
    r = client.get('/download/belt-dxf?family=HTD&pitch=5M')
    assert r.status_code == 200
    assert b'CCT:' in r.data, 'CCT metadata missing from single belt DXF'


def test_belt_dxf_dual_returns_200(client):
    r = client.get(f'/download/belt-dxf?{_DUAL}')
    assert r.status_code == 200


def test_belt_dxf_dual_content_disposition(client):
    r = client.get(f'/download/belt-dxf?{_DUAL}')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '-belt.dxf' in cd


def test_belt_dxf_dual_cct_metadata(client):
    r = client.get(f'/download/belt-dxf?{_DUAL}')
    assert r.status_code == 200
    assert b'CCT:' in r.data, 'CCT metadata missing from dual belt DXF'


# ---------------------------------------------------------------------------
# /download/all-dxf
# ---------------------------------------------------------------------------

def test_all_dxf_returns_200(client):
    r = client.get(f'/download/all-dxf?{_DUAL}')
    assert r.status_code == 200


def test_all_dxf_content_disposition(client):
    r = client.get(f'/download/all-dxf?{_DUAL}')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '.dxf' in cd


def test_all_dxf_min_size(client):
    r = client.get(f'/download/all-dxf?{_DUAL}')
    assert r.status_code == 200
    assert len(r.data) > 500


def test_all_dxf_cct_metadata(client):
    r = client.get(f'/download/all-dxf?{_DUAL}')
    assert r.status_code == 200
    assert b'CCT:' in r.data, 'CCT metadata missing from combined layout DXF'


# ---------------------------------------------------------------------------
# /download/all-step  (single and dual)
# ---------------------------------------------------------------------------

def test_all_step_single_returns_200(client):
    r = client.get(f'/download/all-step?{_SINGLE}')
    assert r.status_code == 200


def test_all_step_single_is_step(client):
    r = client.get(f'/download/all-step?{_SINGLE}')
    assert r.status_code == 200
    assert b'ISO-10303' in r.data or b'STEP' in r.data
    assert len(r.data) > 1000


def test_all_step_single_cct_metadata(client):
    r = client.get(f'/download/all-step?{_SINGLE}')
    assert r.status_code == 200
    assert b'/* CCT:' in r.data, 'CCT metadata missing from all-step (single)'


def test_all_step_dual_returns_200(client):
    r = client.get(f'/download/all-step?{_DUAL}')
    assert r.status_code == 200


def test_all_step_dual_is_step(client):
    r = client.get(f'/download/all-step?{_DUAL}')
    assert r.status_code == 200
    assert len(r.data) > 1000


def test_all_step_dual_cct_metadata(client):
    r = client.get(f'/download/all-step?{_DUAL}')
    assert r.status_code == 200
    assert b'/* CCT:' in r.data, 'CCT metadata missing from all-step (dual)'


# ---------------------------------------------------------------------------
# /download/flange-step
# ---------------------------------------------------------------------------

def test_flange_step_3dp_top_returns_200(client):
    r = client.get(f'/download/flange-step?{_FLANGE_3DP}&flange_which=top')
    assert r.status_code == 200


def test_flange_step_3dp_top_content_disposition(client):
    r = client.get(f'/download/flange-step?{_FLANGE_3DP}&flange_which=top')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '.step' in cd


def test_flange_step_3dp_top_is_step(client):
    r = client.get(f'/download/flange-step?{_FLANGE_3DP}&flange_which=top')
    assert r.status_code == 200
    assert b'ISO-10303' in r.data or b'STEP' in r.data
    assert len(r.data) > 1000


def test_flange_step_3dp_top_cct_metadata(client):
    r = client.get(f'/download/flange-step?{_FLANGE_3DP}&flange_which=top')
    assert r.status_code == 200
    assert b'/* CCT:' in r.data, 'CCT metadata missing from flange STEP'


# ---------------------------------------------------------------------------
# A field left empty: its default, as when it's missing
# ---------------------------------------------------------------------------
_BLANKS = '&'.join(f'{k}=' for k in (
    'teeth', 'p2_teeth', 'belt_height', 'clearance_height', 'print_extra', 'p2_print_extra',
    'center_distance', 'hub_flat_depth', 'hub_keyway_w', 'hub_keyway_h', 'clearance_custom',
    'backlash_custom', 'flange_angle', 'flange_height', 'spokes_rim_depth', 'spokes_height'))
_ONE_DESIGN = ('family=HTD&pitch=5M&bore=8&clearance_preset=STANDARD&backlash_preset=STANDARD&dual=true'
               '&p2_bore=8&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD&flange_enabled=1'
               '&flange_3dprint=1&spokes_enabled=1&spokes_hub_od=20&spokes_width=4&spokes_count=4')


def _same_file(data):
    """A download without what differs each time: a STEP header's time, a DXF's handles and time."""
    return re.sub(rb'FILE_NAME\([^;]*;|\{[0-9A-F-]{36}\}|\d{4}-\d\d-\d\dT[0-9:.+-]+', b'', data)


@pytest.mark.parametrize('route', ['svg', 'dxf', 'all-dxf', 'belt-svg', 'stl', 'belt-stl', 'flange-stl',
                                   'step', 'all-step', 'belt-step', 'flange-step', 'flange-assembly'])
def test_a_field_left_empty_downloads_as_its_default(client, route):
    """The page sends a cleared field blank: a download reads it as its default, as when it's
    missing (a 400 or 500 on every download route before, 2026-10-28)."""
    blank = client.get(f'/download/{route}?{_ONE_DESIGN}&{_BLANKS}')
    missing = client.get(f'/download/{route}?{_ONE_DESIGN}')
    assert missing.status_code == 200
    assert blank.status_code == 200, blank.get_data(as_text=True)[:300]
    assert _same_file(blank.data) == _same_file(missing.data)


@pytest.mark.parametrize('which', ['top', 'bottom'])
def test_flange_step_metal(client, which):
    """A metal flange's STEP: the worker read the groove radius only the printed one set
    (an UnboundLocalError and a 400 on every one, until 2026-10-28)."""
    r = client.get(f'/download/flange-step?{_SINGLE}&flange_3dprint=0&flange_angle=15&flange_rim_radius=3'
                   f'&flange_plate_height=1.0&flange_bend_radius=1.5&flange_which={which}')
    assert r.status_code == 200, r.get_data(as_text=True)[-300:]
    assert b'ISO-10303' in r.data and len(r.data) > 1000


def test_flange_step_bad_family_returns_400(client):
    r = client.get('/download/flange-step?family=BOGUS&pitch=5M&teeth=20&bore=8&belt_height=10')
    assert r.status_code == 400
