"""
test_dimensions.py — the Dimensions panel's figures and spec warnings
(geometry/belt_specs.py, /api/dimensions).

The published figures are checked against the tables they cite; the drive
formulas against Fenner's worked example (Precision Belting Tech Info p. 10:
26 and 156 grooves, 3 mm pitch).
"""
import math

import pytest

from geometry import belt_specs as bs
from geometry.pulley_geometry import PULLEY_SPECS


# ── Published figures ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('key,h', [
    ('5M', 2.652),      # ISO 13050 Table 9: ht 2.08 + a 0.572
    ('R8M', 3.936),     # Table 18: 3.25 + 0.686
    ('S14M', 6.697),    # Table 27: 5.30 + 1.397
    ('GT3M', 1.70),     # Gates Table 22
    ('XL', 1.0),        # ISO 5294 Annex A
])
def test_flange_height_from_the_tables(key, h):
    f = bs.min_flange_height(key, PULLEY_SPECS[key])
    assert f.value == pytest.approx(h, abs=1e-3) and not f.approximate


def test_flange_height_without_a_table_is_the_iso_rule_marked_approximate():
    spec = PULLEY_SPECS['AT5']
    f = bs.min_flange_height('AT5', spec)
    assert f.approximate
    assert f.value == pytest.approx(spec['tooth_ht'] + spec['pitch_line_diff'], abs=1e-3)


@pytest.mark.parametrize('key,belt,flanged,want', [
    ('5M', 15, True, 17), ('5M', 15, False, 21),          # ISO 13050 Table 17, a standard width
    ('8M', 85, False, 96),
    ('5M', 12, True, 14),                                   # between 9 (+2) and 15 (+2)
    ('8M', 40, True, 40 + 2.5),                             # between 30 (+2) and 50 (+3)
    ('GT3M', 6, True, 7.25), ('GT3M', 6, False, 10.0),     # Gates Table 23 allowances
    ('L', 0.75 * 25.4, True, 20.3),                         # ISO 5294 Table 4 (075)
])
def test_face_width(key, belt, flanged, want):
    assert bs.min_face_width(key, belt, flanged).value == pytest.approx(want, abs=0.01)


def test_face_width_has_no_figure_for_uncovered_profiles():
    assert bs.min_face_width('T5', 10, True) is None
    assert bs.min_face_width('S3M', 10, False) is None


# ── Drive figures ─────────────────────────────────────────────────────────────

def test_drive_matches_the_fenner_example():
    # 26 : 156 grooves at 3 mm. Fenner's arc is the linear approximation,
    # close to the exact one only on a long centre; teeth in mesh drop the
    # fraction (Gates p. 104).
    pitch, n1, n2 = 3.0, 26, 156
    C = 600.0
    d = bs.drive(pitch, n1, n2, C)
    D_, d_ = n2 * pitch / math.pi, n1 * pitch / math.pi
    fenner_arc = 180 - 180 * (D_ - d_) / (C * math.pi)
    assert d.wrap_small_deg == pytest.approx(fenner_arc, abs=0.2)
    assert d.teeth_in_mesh == math.floor(d.wrap_small_deg * n1 / 360)
    assert d.ratio == pytest.approx(6.0)


def test_equal_pulleys_wrap_half_and_span_is_the_centre():
    d = bs.drive(5.0, 30, 30, 120.0)
    assert d.wrap_small_deg == pytest.approx(180.0)
    assert d.teeth_in_mesh == 15
    assert d.span == pytest.approx(120.0)


# ── The route ─────────────────────────────────────────────────────────────────

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': 20, 'bore': 8, 'belt_height': 15,
        'clearance_height': 2, 'feature_build': 1}


def _dims(client, **extra):
    r = client.get('/api/dimensions', query_string={**BASE, **extra})
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def test_single_pulley_figures(client):
    d = _dims(client)
    p = d['pulleys'][0]
    assert p['pitch_diameter'] == pytest.approx(20 * 5 / math.pi)
    assert p['outside_diameter'] == pytest.approx(20 * 5 / math.pi - 2 * 0.572)
    assert p['face_width'] == 17 and p['face_width_min'] == 21   # unflanged 15 mm belt
    assert d['drive'] is None


def test_face_too_narrow_warns_and_auto_fix_widens_it(client):
    d = _dims(client, clearance_height=0.5)
    assert any('face is 15.5 mm' in w for w in d['warnings'])
    assert d['fix']['set']['clearance_height'] == 6.0
    assert not _dims(client, clearance_height=6.0)['warnings']


def test_flange_rules(client):
    d = _dims(client, clearance_height=2, flange_enabled=1, flange_3dprint=1,
              flange_rim_radius=1.5, flange_angle=30)
    w = ' | '.join(d['warnings'])
    assert 'reaches 1.50 mm past the OD' in w and '2.65 mm' in w
    assert 'flange angle 30°' in w
    assert d['fix']['set'] == {'flange1_rim_radius': 2.7, 'flange1_angle': 25.0}
    ok = _dims(client, clearance_height=2, flange_enabled=1, flange_3dprint=1,
               flange_rim_radius=2.7, flange_angle=15)
    assert not ok['warnings']


def test_flanges_only_count_with_3d_features_on(client):
    d = _dims(client, feature_build=0, flange_enabled=1, flange_rim_radius=3)
    assert d['pulleys'][0]['flanged'] is False and 'flange_od' not in d['pulleys'][0]


def test_printed_flange_on_spokes_reaches_from_the_root(client):
    spokes = dict(spokes_enabled=1, spokes_hub_od=14, spokes_rim_depth=2, spokes_width=4,
                  spokes_count=4, spokes_fillet_tip=0.5, spokes_fillet_base=0.5)
    d = _dims(client, teeth=40, flange_enabled=1, flange_3dprint=1, flange_rim_radius=3, **spokes)
    assert d['pulleys'][0]['flange_reach'] == pytest.approx(3 - 2.08, abs=1e-3)


def test_drive_warnings(client):
    dual = dict(dual='true', p2_teeth=60, p2_bore=8, clearance_height=6)
    few = _dims(client, center_distance=30, **{**dual, 'p2_teeth': 100})   # 5:1, short centre
    w = ' | '.join(few['warnings'])
    assert few['drive']['teeth_in_mesh'] < 6 and 'teeth in mesh' in w
    assert 'Neither pulley has flanges' in w
    flanged = dict(flange_enabled=1, flange_3dprint=1, flange_rim_radius=3)
    long = _dims(client, center_distance=500, **dual, **flanged)
    assert 'flanging both pulleys' in ' | '.join(long['warnings'])
    fine = _dims(client, center_distance=150, **dual, **flanged)
    assert not [w for w in fine['warnings'] if 'mesh' in w or 'flange' in w.lower()]


def test_unknown_profile_is_a_400(client):
    r = client.get('/api/dimensions', query_string={**BASE, 'pitch': 'nope'})
    assert r.status_code == 400
