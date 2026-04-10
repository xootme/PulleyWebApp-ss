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
# /download/step  (expected 501 — cadquery-ocp unavailable on Python 3.14)
# ---------------------------------------------------------------------------
def test_download_step_returns_501(client):
    r = client.get(
        '/download/step?family=HTD&pitch=5M&teeth=20'
        '&bore=8&belt_height=10&print_extra=0'
        '&clearance_preset=STANDARD&backlash_preset=STANDARD'
    )
    assert r.status_code == 501
