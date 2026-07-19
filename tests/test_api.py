"""
test_api.py — HTTP endpoint tests via Flask test client.
Covers /api/spec, /api/od, /api/belt, /api/preview, /api/belt-preview,
/download/svg, and /download/belt-svg (single and dual).
"""
import pytest

from tests.conftest import PULLEY_CASES, BELT_CASES, get_spec


# ---------------------------------------------------------------------------
# /api/spec
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_api_spec(client, family, pitch):
    r = client.get(f'/api/spec?family={family}&pitch={pitch}')
    assert r.status_code == 200
    data = r.get_json()
    assert 'min_teeth' in data
    assert 'presets' in data
    assert 'default_od' in data
    assert data['default_od'] > 0


# ---------------------------------------------------------------------------
# /api/od
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_api_od_from_teeth(client, family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    r = client.get(f'/api/od?family={family}&pitch={pitch}&mode=teeth&value={teeth}')
    assert r.status_code == 200
    data = r.get_json()
    assert 'od' in data
    assert data['od'] > 0


# ---------------------------------------------------------------------------
# /api/belt
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', [
    pytest.param('HTD', '5M', id='HTD-5M'),
    pytest.param('T',   'T5', id='T-T5'),
    pytest.param('Imperial', 'XL', id='Imperial-XL'),
])
def test_api_belt_from_center(client, family, pitch):
    r = client.get(
        f'/api/belt?mode=from_center&family={family}&pitch={pitch}'
        '&teeth1=20&teeth2=30&center_distance=100'
    )
    assert r.status_code == 200
    data = r.get_json()
    assert 'n_belt' in data
    assert 'center_dist_mm' in data
    assert data['n_belt'] > 0


@pytest.mark.parametrize('family,pitch', [
    pytest.param('HTD', '5M', id='HTD-5M'),
    pytest.param('T',   'T5', id='T-T5'),
])
def test_api_belt_from_teeth(client, family, pitch):
    r = client.get(
        f'/api/belt?mode=from_teeth&family={family}&pitch={pitch}'
        '&teeth1=20&teeth2=30&n_belt=80'
    )
    assert r.status_code == 200
    data = r.get_json()
    assert 'center_dist_mm' in data


# ---------------------------------------------------------------------------
# /api/preview
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', [
    pytest.param('HTD',      '5M',  id='HTD-5M'),
    pytest.param('GT',       '3M',  id='GT-3M'),
    pytest.param('T',        'T5',  id='T-T5'),
    pytest.param('AT',       'AT5', id='AT-AT5'),
    pytest.param('Imperial', 'XL',  id='Imperial-XL'),
    pytest.param('RPP',      '5M',  id='RPP-5M'),
])
def test_preview_single(client, family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    r = client.get(
        f'/api/preview?family={family}&pitch={pitch}&teeth={teeth}'
        '&bore=8&print_extra=0'
        '&clearance_preset=STANDARD&backlash_preset=STANDARD'
    )
    assert r.status_code == 200
    assert r.data[:4] == b'\x89PNG'


@pytest.mark.parametrize('family,pitch', [
    pytest.param('HTD',      '5M',  id='HTD-5M'),
    pytest.param('Imperial', 'XL',  id='Imperial-XL'),
    pytest.param('T',        'T5',  id='T-T5'),
])
def test_preview_dual(client, family, pitch):
    spec  = get_spec(family, pitch)
    t1, t2 = spec['min_teeth'], spec['min_teeth'] * 2
    r = client.get(
        f'/api/preview?family={family}&pitch={pitch}'
        f'&teeth={t1}&bore=8&print_extra=0'
        '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        f'&dual=true&center_distance=120'
        f'&p2_teeth={t2}&p2_bore=8&p2_print_extra=0'
        '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
    )
    assert r.status_code == 200
    assert r.data[:4] == b'\x89PNG'


# ---------------------------------------------------------------------------
# /api/belt-preview
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', BELT_CASES)
def test_belt_preview(client, family, pitch):
    """belt-preview returns SVG (belt tooth cross-section), not PNG."""
    r = client.get(f'/api/belt-preview?family={family}&pitch={pitch}')
    assert r.status_code == 200
    assert b'<svg' in r.data


# ---------------------------------------------------------------------------
# /download/svg
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_download_svg_single(client, family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    r = client.get(
        f'/download/svg?family={family}&pitch={pitch}&teeth={teeth}'
        '&bore=8&print_extra=0'
        '&clearance_preset=STANDARD&backlash_preset=STANDARD'
    )
    assert r.status_code == 200
    assert b'<?xml' in r.data
    assert b'<svg' in r.data


# ---------------------------------------------------------------------------
# /download/belt-svg
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', BELT_CASES)
def test_download_belt_svg_single(client, family, pitch):
    r = client.get(f'/download/belt-svg?family={family}&pitch={pitch}')
    assert r.status_code == 200
    assert b'<?xml' in r.data
    assert b'<svg' in r.data


@pytest.mark.parametrize('family,pitch', [
    pytest.param('HTD',      '5M',  id='HTD-5M'),
    pytest.param('GT',       '3M',  id='GT-3M'),
    pytest.param('Imperial', 'XL',  id='Imperial-XL'),
    pytest.param('T',        'T5',  id='T-T5'),
    pytest.param('AT',       'AT5', id='AT-AT5'),
])
def test_download_belt_svg_dual(client, family, pitch):

    spec  = get_spec(family, pitch)
    t1, t2 = spec['min_teeth'], spec['min_teeth'] * 2
    r = client.get(
        f'/download/belt-svg?family={family}&pitch={pitch}&dual=true'
        f'&teeth={t1}&p2_teeth={t2}&bore=8&p2_bore=8&center_distance=120'
        '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        '&print_extra=0&p2_print_extra=0'
    )
    assert r.status_code == 200
    assert b'<?xml' in r.data
    assert b'<path' in r.data   # belt ring path present


# ---------------------------------------------------------------------------
# /api/preview-stl
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', [
    pytest.param('HTD',      '5M', id='HTD-5M'),
    pytest.param('T',        'T5', id='T-T5'),
    pytest.param('Imperial', 'XL', id='Imperial-XL'),
])
def test_api_preview_stl_single(client, family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    r = client.get(
        f'/api/preview-stl?family={family}&pitch={pitch}&teeth={teeth}'
        '&bore=8&belt_height=10&print_extra=0'
        '&clearance_preset=STANDARD&backlash_preset=STANDARD'
    )
    assert r.status_code == 200
    assert r.content_type.startswith('model/stl') or r.content_type.startswith('application/octet-stream')
    assert len(r.data) > 84, 'STL response too short'


@pytest.mark.parametrize('part', ['all', 'pulleys', 'belt'])
def test_api_preview_stl_dual_parts(client, part):
    r = client.get(
        '/api/preview-stl?family=HTD&pitch=5M&teeth=20&bore=8&belt_height=10'
        '&print_extra=0&clearance_preset=STANDARD&backlash_preset=STANDARD'
        f'&dual=true&center_distance=120&p2_teeth=30&p2_bore=8'
        '&p2_print_extra=0&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        f'&part={part}'
    )
    assert r.status_code == 200
    assert len(r.data) > 84


# ---------------------------------------------------------------------------
# /download/stl
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', [
    pytest.param('HTD',      '5M', id='HTD-5M'),
    pytest.param('Imperial', 'XL', id='Imperial-XL'),
])
def test_download_stl_single(client, family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    r = client.get(
        f'/download/stl?family={family}&pitch={pitch}&teeth={teeth}'
        '&bore=8&belt_height=10&print_extra=0'
        '&clearance_preset=STANDARD&backlash_preset=STANDARD'
    )
    assert r.status_code == 200
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    assert len(r.data) > 84


# ---------------------------------------------------------------------------
# /download/step  — small_step Rust binary path, expect 200
# ---------------------------------------------------------------------------
def test_download_step_returns_200(client):
    r = client.get(
        '/download/step?family=HTD&pitch=5M&teeth=20'
        '&bore=8&belt_height=10&print_extra=0'
        '&clearance_preset=STANDARD&backlash_preset=STANDARD'
    )
    assert r.status_code == 200
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    assert len(r.data) > 1000


# ---------------------------------------------------------------------------
# /download/svg-rim and /download/dxf-rim  (added for Gen. Rim Layer feature)
# ---------------------------------------------------------------------------
_RIM_BASE = (
    'family=HTD&pitch=5M&teeth=20&bore=8'
    '&clearance_preset=STANDARD&backlash_preset=STANDARD'
    '&spokes_enabled=1&spokes_hub_od=20&spokes_rim_depth=2'
    '&spokes_width=4&spokes_count=4'
)

def test_download_svg_rim_returns_200(client):
    r = client.get(f'/download/svg-rim?{_RIM_BASE}')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '-Rim.svg' in cd
    assert r.data.strip().startswith(b'<?xml')

def test_download_dxf_rim_returns_200(client):
    r = client.get(f'/download/dxf-rim?{_RIM_BASE}')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '-Rim.dxf' in cd
    assert len(r.data) > 500

def test_download_svg_rim_p2_filename(client):
    r = client.get(f'/download/svg-rim?{_RIM_BASE}&pulley=2&p2_teeth=20&p2_bore=8')
    assert r.status_code == 200
    assert '-P2-Rim.svg' in r.headers.get('Content-Disposition', '')

def test_download_dxf_rim_p2_filename(client):
    r = client.get(f'/download/dxf-rim?{_RIM_BASE}&pulley=2&p2_teeth=20&p2_bore=8')
    assert r.status_code == 200
    assert '-P2-Rim.dxf' in r.headers.get('Content-Disposition', '')

def test_download_svg_rim_bad_family_returns_400(client):
    r = client.get('/download/svg-rim?family=BOGUS&pitch=5M&teeth=20&bore=8')
    assert r.status_code == 400

def test_download_dxf_rim_bad_family_returns_400(client):
    r = client.get('/download/dxf-rim?family=BOGUS&pitch=5M&teeth=20&bore=8')
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /download/flange-stl  (3D-print and metal, with and without spokes)
# ---------------------------------------------------------------------------
_FLANGE_BASE = (
    'family=HTD&pitch=5M&teeth=20&bore=8&belt_height=10'
    '&clearance_preset=STANDARD'
    '&flange_angle=15&flange_rim_radius=3'
)
_FLANGE_3DP   = _FLANGE_BASE + '&flange_3dprint=1&flange_height=1.5'
_FLANGE_METAL = _FLANGE_BASE + '&flange_3dprint=0&flange_plate_height=1.0&flange_bend_radius=1.5'
_FLANGE_SPOKE = '&spokes_enabled=1&spokes_hub_od=20&spokes_rim_depth=3'


def test_download_flange_stl_3dprint_upper(client):
    r = client.get(f'/download/flange-stl?{_FLANGE_3DP}&flange_which=top')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'upper-flange' in cd
    assert '3DP' in cd
    assert len(r.data) > 84


def test_download_flange_stl_3dprint_lower(client):
    r = client.get(f'/download/flange-stl?{_FLANGE_3DP}&flange_which=bottom')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'lower-flange' in cd
    assert len(r.data) > 84


def test_download_flange_stl_metal_upper(client):
    # Metal flanges are always returned as a combined both-sides file
    r = client.get(f'/download/flange-stl?{_FLANGE_METAL}&flange_which=top')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'Metal' in cd
    assert 'flanges' in cd
    assert len(r.data) > 84


def test_download_flange_stl_metal_lower(client):
    # Metal flanges are always returned as a combined both-sides file
    r = client.get(f'/download/flange-stl?{_FLANGE_METAL}&flange_which=bottom')
    assert r.status_code == 200
    cd = r.headers.get('Content-Disposition', '')
    assert 'Metal' in cd
    assert 'flanges' in cd
    assert len(r.data) > 84


def test_download_flange_stl_with_spokes(client):
    """Flange with spokes active must return 200 and valid STL."""
    r = client.get(f'/download/flange-stl?{_FLANGE_3DP}&flange_which=top{_FLANGE_SPOKE}')
    assert r.status_code == 200
    assert len(r.data) > 84


def test_download_flange_stl_bad_family_returns_400(client):
    r = client.get('/download/flange-stl?family=BOGUS&pitch=5M&teeth=20&bore=8&belt_height=10')
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/report-bug — report_type logged correctly
# ---------------------------------------------------------------------------
import json as _json
import os as _os

def _last_log_entry(tmp_log_path):
    """Read the last block from the bug report log."""
    with open(tmp_log_path, encoding='utf-8') as f:
        return f.read()


def test_report_bug_type_bug(client, tmp_path):
    """Submitting report_type='bug' should log 'Bug Report'."""
    from app import _LOG_FILE
    r = client.post(
        '/api/report-bug',
        data=_json.dumps({'seeing': 'x', 'should_see': 'y', 'report_type': 'bug'}),
        content_type='application/json',
    )
    assert r.status_code == 200
    assert r.get_json().get('ok') is True
    content = open(_LOG_FILE, encoding='utf-8').read()
    assert 'Bug Report' in content


def test_report_bug_type_feature(client):
    """Submitting report_type='feature' should log 'Feature Request'."""
    from app import _LOG_FILE
    r = client.post(
        '/api/report-bug',
        data=_json.dumps({'seeing': 'add dark mode', 'should_see': 'useful', 'report_type': 'feature'}),
        content_type='application/json',
    )
    assert r.status_code == 200
    content = open(_LOG_FILE, encoding='utf-8').read()
    assert 'Feature Request' in content


def test_report_bug_missing_body_returns_400(client):
    r = client.post(
        '/api/report-bug',
        data=_json.dumps({'seeing': '', 'should_see': ''}),
        content_type='application/json',
    )
    assert r.status_code == 400


def test_report_bug_default_type_is_bug(client):
    """Omitting report_type should default to 'Bug Report'."""
    from app import _LOG_FILE
    initial = open(_LOG_FILE, encoding='utf-8').read() if _os.path.exists(_LOG_FILE) else ''
    client.post(
        '/api/report-bug',
        data=_json.dumps({'seeing': 'something', 'should_see': 'something else'}),
        content_type='application/json',
    )
    content = open(_LOG_FILE, encoding='utf-8').read()
    new_content = content[len(initial):]
    assert 'Bug Report' in new_content


# ---------------------------------------------------------------------------
# /api/shutdown (cct_common.flask_shutdown)
# ---------------------------------------------------------------------------
def test_shutdown_route_wired_to_shared_helper(client, monkeypatch):
    calls = []
    monkeypatch.setattr('cct_common.flask_shutdown.os.kill',
                        lambda pid, sig: calls.append((pid, sig)))
    r = client.post('/api/shutdown')
    assert r.status_code == 200
    assert r.get_json() == {'ok': True}
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# live reload (cct_common.live_reload)
# ---------------------------------------------------------------------------
def test_live_reload_route_wired_to_shared_helper(client):
    r = client.get('/api/_boot_id')
    assert r.status_code == 200
    assert isinstance(r.get_json()['boot_id'], str)
    r = client.get('/_cct_live_reload.js')
    assert r.status_code == 200
    assert r.mimetype == 'application/javascript'
