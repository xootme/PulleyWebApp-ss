"""
test_set_screw_holes.py — hub set-screw holes sized from cct_common.screws
(geometry/set_screw.py, ADR-013).

How the screw holds decides the hole: threading into the plastic gets the
self-tapping bore from the design's threaded-hole settings; a captured nut
gets a plain clearance hole (it's never threaded) and that size's nut; a
heat-set insert gets the hole entered. A design from before sizes had names
(hub_screw_dia only) keeps the nominal-diameter hole it was made with.

Holes are measured on the app's own STL download, sliced through the screw
axis: the gap in the hub wall across the hole is its width.
"""
import math

import pytest
from shapely.geometry import LineString

from cct_common import screws
from geometry import set_screw
from test_hub_setscrew import BELT, BORE, CLEAR, HUB_H, R_MID, Z_SCREW, _material_at, _stl

HUB_TOP = BELT + CLEAR + HUB_H


def _hole_width(mesh, z, x):
    """Width of the gap in the hub wall at radius x on the +X axis (the first
    screw's hole), measured across the hole at height z."""
    mat = _material_at(mesh, z)
    cut = LineString([(x, -6.0), (x, 6.0)]).difference(mat)
    gaps = [g for g in getattr(cut, 'geoms', [cut]) if g.length > 0 and g.bounds[1] < 0 < g.bounds[3]]
    assert len(gaps) == 1, f'expected one gap across the hole, got {gaps}'
    return gaps[0].length


# ── the settings → hole rules ─────────────────────────────────────────────

def test_old_designs_have_no_named_screw():
    assert set_screw.parse({'hub_screw_dia': '5', 'hub_screw_count': '2'}) is None
    assert set_screw.parse({'hub_screw_size': 'M5', 'hub_screw_count': '0'}) is None


def test_threaded_hole_follows_the_design_settings():
    ss = set_screw.parse({'hub_screw_size': 'M5', 'hub_screw_count': '1'})
    assert ss.hold == 'thread' and ss.nut is None and ss.major == 5.0
    assert ss.hole == {'shape': 'circle', 'diameter': pytest.approx(5.0 - 0.5 * (5.0 - 4.134))}
    ss = set_screw.parse({'hub_screw_size': '#6-32', 'hub_screw_count': '1',
                          'screw_hole_shape': 'hex', 'hex_flat': '85'})
    assert ss.hole == {'shape': 'hex', 'flats': pytest.approx(3.505 * 0.85)}


def test_settings_are_defaulted_and_clamped():
    assert set_screw.thread_settings({}) == {'shape': 'round', 'engagement_percent': 50.0,
                                             'hex_flat_percent': 82.0}
    t = set_screw.thread_settings({'screw_hole_shape': 'oval', 'thread_engagement': '140',
                                   'hex_flat': 'x'})
    assert t == {'shape': 'round', 'engagement_percent': 100.0, 'hex_flat_percent': 82.0}


def test_captured_nut_hole_is_a_clearance_hole_never_threaded():
    args = {'hub_screw_size': 'M5', 'hub_screw_count': '2', 'hub_screw_hold': 'nut',
            'screw_hole_shape': 'hex', 'thread_engagement': '90'}     # threaded settings don't apply
    ss = set_screw.parse(args)
    assert ss.hole == {'shape': 'circle', 'diameter': 5.5} and ss.nut == (8.0, 4.0)
    # the old flag alone means a nut too
    assert set_screw.parse({'hub_screw_size': '#8-32', 'hub_screw_count': '1',
                            'hub_captured_nut': '1'}).nut == screws.nut('#8-32')


def test_insert_and_custom_use_the_hole_entered():
    ss = set_screw.parse({'hub_screw_size': 'M4', 'hub_screw_count': '1',
                          'hub_screw_hold': 'insert', 'hub_screw_hole_dia': '5.6'})
    assert ss.hole == {'shape': 'circle', 'diameter': 5.6} and ss.major == 4.0
    ss = set_screw.parse({'hub_screw_size': 'Custom', 'hub_screw_count': '1',
                          'hub_screw_hole_dia': '3.9'})
    assert ss.hole == {'shape': 'circle', 'diameter': 3.9} and ss.major == 3.9
    # Custom in a captured nut: the nearest metric nut to the hole, as before names
    ss = set_screw.parse({'hub_screw_size': 'Custom', 'hub_screw_count': '1',
                          'hub_screw_hold': 'nut', 'hub_screw_hole_dia': '4.2'})
    assert ss.nut == screws.nut('M4')


@pytest.mark.parametrize('args', [
    {'hub_screw_size': 'M7', 'hub_screw_count': '1'},
    {'hub_screw_size': 'Custom', 'hub_screw_count': '1'},
    {'hub_screw_size': 'M4', 'hub_screw_count': '1', 'hub_screw_hold': 'insert'},
    {'hub_screw_size': 'M4', 'hub_screw_count': '1', 'hub_screw_hold': 'glue'},
])
def test_settings_no_hole_can_be_cut_from_are_refused(args):
    with pytest.raises(ValueError):
        set_screw.parse(args)


def test_p2_reads_its_own_screw_and_the_shared_settings():
    args = {'p2_hub_screw_size': 'M3', 'p2_hub_screw_count': '1', 'thread_engagement': '100'}
    assert set_screw.parse(args) is None
    assert set_screw.parse(args, 'p2_').hole['diameter'] == pytest.approx(2.459)


# ── the holes actually cut (STL download) ─────────────────────────────────

def test_old_design_keeps_its_nominal_hole(client):
    m = _stl(client, hub_screw_dia=5, hub_screw_count=1, hub_captured_nut=0)
    assert _hole_width(m, Z_SCREW, R_MID) == pytest.approx(5.0, abs=0.05)


@pytest.mark.parametrize('engagement,expected', [(0, 5.0), (50, 4.567), (100, 4.134)])
def test_threaded_hole_is_the_self_tapping_bore(client, engagement, expected):
    m = _stl(client, hub_screw_dia=5, hub_screw_size='M5', hub_screw_count=1,
             hub_screw_hold='thread', thread_engagement=engagement)
    assert m.is_watertight
    assert _hole_width(m, Z_SCREW, R_MID) == pytest.approx(expected, abs=0.05)


def test_hex_threaded_hole_is_measured_across_its_flats(client):
    m = _stl(client, hub_screw_size='M5', hub_screw_count=1, screw_hole_shape='hex', hex_flat=82)
    assert m.is_watertight
    assert _hole_width(m, Z_SCREW, R_MID) == pytest.approx(5.0 * 0.82, abs=0.05)
    # a corner up: just under the corner the hole is narrower than across the flats
    r = 5.0 * 0.82 / math.sqrt(3)
    assert _hole_width(m, Z_SCREW + 0.8 * r, R_MID) < 5.0 * 0.82 * 0.6


def test_captured_nut_gets_the_clearance_hole_and_its_pocket(client):
    m = _stl(client, hub_screw_size='M5', hub_screw_count=1, hub_screw_hold='nut',
             hub_captured_nut=1)
    assert m.is_watertight
    z = HUB_TOP - 8.0 / math.sqrt(3)           # screw at the nut's centre
    # beyond the pocket (bore + nut + 0.5), before the hub's outer wall
    assert _hole_width(m, z, BORE / 2 + 4.0 + 0.5 + 3.0) == pytest.approx(5.5, abs=0.05)


@pytest.mark.parametrize('extra', [
    {'hub_screw_count': 2},
    {'hub_flat_depth': 1},
    {'hub_keyway_w': 4, 'hub_keyway_h': 2.6},
    {'hub_screw_size': None, 'hub_screw_dia': 5},        # an old link
])
def test_captured_nut_stls_are_watertight(client, extra):
    """The nut pocket's inner face used to sit exactly on the bore (or D-flat,
    or key slot face): a flat face on a round bore touches it along a line,
    which left non-manifold edges. It now overlaps by _POCKET_OVERLAP."""
    q = {'hub_screw_size': 'M5', 'hub_screw_count': 1, 'hub_screw_hold': 'nut', 'hub_captured_nut': 1}
    q.update(extra)
    q = {k: v for k, v in q.items() if v is not None}
    if 'hub_screw_size' not in q:
        q.pop('hub_screw_hold')
    assert _stl(client, **q).is_watertight


def test_heat_set_insert_gets_the_hole_entered(client):
    m = _stl(client, hub_screw_size='M4', hub_screw_count=1, hub_screw_hold='insert',
             hub_screw_hole_dia=5.6)
    assert _hole_width(m, Z_SCREW, R_MID) == pytest.approx(5.6, abs=0.05)


def test_preview_cuts_the_same_hole(client):
    q = {'family': 'HTD', 'pitch': '5M', 'teeth': '24', 'bore': str(BORE), 'print_extra': '0',
         'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD',
         'belt_height': str(BELT), 'clearance_height': str(CLEAR), 'hub_od': '26',
         'hub_height': str(HUB_H), 'hub_screw_size': 'M5', 'hub_screw_count': '1'}
    import io
    import trimesh
    r = client.get('/api/preview-stl', query_string=q)
    assert r.status_code == 200
    m = trimesh.load(io.BytesIO(r.data), file_type='stl')
    # the preview is centred: put its bottom back at z = 0 before slicing
    m.apply_translation([-(m.bounds[0][0] + m.bounds[1][0]) / 2,
                         -(m.bounds[0][1] + m.bounds[1][1]) / 2, -m.bounds[0][2]])
    assert _hole_width(m, Z_SCREW, R_MID) == pytest.approx(4.567, abs=0.05)


# ── what the page gets ────────────────────────────────────────────────────

def test_api_screws_gives_every_sizes_holes(client):
    d = client.get('/api/screws?thread_engagement=100').get_json()
    assert d['settings']['engagement_percent'] == 100.0
    by = {s['name']: s for s in d['sizes']}
    assert list(by) == list(set_screw.METRIC + set_screw.INCH)
    assert by['M3']['thread'] == {'shape': 'circle', 'diameter': 2.459}
    assert by['M5']['clearance'] == 5.5 and by['M5']['nut'] == [8.0, 4.0]
    assert by['#6-32']['group'] == 'Inch'


def test_page_offers_every_size_and_custom(client):
    html = client.get('/').get_data(as_text=True)
    for name in ('M2.5', 'M10', '#4-40', '1/4-20'):
        assert f'>{name}</option>' in html, name
    assert '>Custom…</option>' in html


def test_dialog_pictures_state_what_the_rules_give():
    """The Threaded screw holes pictures (tools/help_illustrations/
    build_thread_holes.py) print sizes; they must be the ones the rules give
    at the dialog's defaults — rebuild the pictures if a rule changes."""
    from pathlib import Path
    help_dir = Path(__file__).resolve().parent.parent / 'static' / 'help'
    rnd = (help_dir / 'thread_round.svg').read_text(encoding='utf-8')
    hx = (help_dir / 'thread_hex.svg').read_text(encoding='utf-8')
    assert (set_screw.DEFAULT_ENGAGEMENT, set_screw.DEFAULT_HEX_FLAT) == (50.0, 82.0)
    assert f'The hole at 50%: Ø {screws.self_tap_diameter("M5", 50):.2f}' in rnd
    assert f'Thread root Ø {screws.get("M5").minor_diameter:.2f}' in rnd
    flats = screws.hex_flats('M5', 82)
    assert f'Across the flats: {flats:.2f}' in hx
    assert f'Thread root Ø {screws.get("M5").minor_diameter:.2f}' in hx
    assert f'Corners (Ø {2 * flats / math.sqrt(3):.2f})' in hx
