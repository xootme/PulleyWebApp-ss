"""test_captured_nut_hub.py — a captured nut in a hub too narrow for it, on a
D-flat, keyway or hex bore (small_step 0.8.1's gap 57 refusal; the small_step
session, 2026-10-07).

A hub narrower than bore + 6·t_nut is built with lobes, and small_step builds
a lobed hub's bore round: before 0.8.1 the D-flat was silently missing from the
hub, and a keyway's STEP was invalid. 0.8.1 refuses it by name. The Dimensions
panel warns first with an Auto-fix (the Hub OD the nut needs), and the STEP
worker refuses with the same words, so an older binary can't make the wrong part.
"""
import pytest

from geometry.captured_nut_hub import min_hub_od, problems

# test_3d's D-shaft case: Ø8 bore, Ø5 screw, its M5 nut (af 8, 4 thick): Ø32 hub
_DSHAFT = dict(bore_mm=8.0, hub_od_mm=24.0, hub_height_mm=10.0, screw_count=1, captured_nut=True,
               screw_dia_mm=5.0, flat_depth_mm=1.5)


def test_min_hub_od():
    assert min_hub_od(bore_mm=8.0, screw_dia_mm=5.0) == pytest.approx(32.0)
    assert min_hub_od(bore_mm=10.0, screw_dia_mm=4.0, nut=(7.0, 3.2)) == pytest.approx(29.2)


def test_a_d_flat_in_a_lobed_hub():
    [msg] = problems(**_DSHAFT)
    assert 'D-flat' in msg and 'Ø32.00' in msg and 'Ø24 mm' in msg
    assert problems(**dict(_DSHAFT, hub_od_mm=31.9))
    assert problems(**dict(_DSHAFT, hub_od_mm=32.0)) == []


@pytest.mark.parametrize('kw, what', [
    ({'flat_depth_mm': 0.0, 'keyway_w_mm': 5.0, 'keyway_h_mm': 2.0}, 'keyway'),
    ({'flat_depth_mm': 0.0, 'spline': {'kind': 'hex'}}, 'hex bore'),
], ids=['keyway', 'hex bore'])
def test_every_bore_that_is_not_round(kw, what):
    [msg] = problems(**dict(_DSHAFT, **kw))
    assert what in msg


@pytest.mark.parametrize('kw', [
    {'flat_depth_mm': 0.0},                                  # a round bore: the lobed hub is right
    {'captured_nut': False}, {'screw_count': 0}, {'hub_height_mm': 0.0},
    {'hub_od_mm': 11.0},                                     # past 2.9x: small_step grows it round
], ids=['round bore', 'thread', 'no screws', 'no hub', 'grown round'])
def test_what_small_step_builds_is_left_alone(kw):
    assert problems(**dict(_DSHAFT, **kw)) == []


def test_the_lobed_band_ends_at_2_9_times():
    assert problems(**dict(_DSHAFT, hub_od_mm=11.1))       # 32 < 2.9 x 11.1: lobed, refused


# ── the Dimensions panel ──────────────────────────────────────────────────────

DFLAT = {'family': 'HTD', 'pitch': '5M', 'teeth': '30', 'bore': '8', 'belt_height': '10', 'feature_build': '1',
         'hub_od': '24', 'hub_height': '10', 'hub_screw_size': 'M5', 'hub_screw_count': '1',
         'hub_screw_hold': 'nut', 'hub_flat_depth': '1.5'}
KEYWAY = {**DFLAT, 'bore': '10', 'hub_flat_depth': '0', 'hub_keyway_w': '4', 'hub_keyway_h': '1.8',
          'hub_screw_size': 'M4'}


def _dims(client, q):
    r = client.get('/api/dimensions', query_string=q)
    assert r.status_code == 200, r.data[:200]
    return r.get_json()


def _lobed(d):
    return [w for w in d['warnings'] if 'built with lobes' in w]


@pytest.mark.parametrize('q, what', [(DFLAT, 'D-flat'), (KEYWAY, 'keyway')], ids=['D-flat', 'keyway'])
def test_the_panel_warns_and_the_fix_widens_the_hub(client, q, what):
    d = _dims(client, q)
    [w] = _lobed(d)
    assert what in w
    hub = d['fix']['set']['hub1_od']
    assert hub > float(q['hub_od']) and hub * 2 == int(hub * 2)        # a 0.5 mm step, as the field takes
    from agent import _fix_params
    assert _fix_params(d['fix']['set'])['hub_od'] == hub
    assert not _lobed(_dims(client, {**q, 'hub_od': str(hub)}))       # the fix clears it
    assert _lobed(_dims(client, {**q, 'hub_od': str(hub - 0.5)}))     # and is the least that does


@pytest.mark.parametrize('extra', [{'hub_screw_hold': 'thread'}, {'hub_flat_depth': '0'}, {'feature_build': '0'},
                                   {'hub_od': '40'}], ids=['thread', 'round bore', '2D', 'wide hub'])
def test_the_panel_is_quiet_otherwise(client, extra):
    assert not _lobed(_dims(client, {**DFLAT, **extra}))


def test_no_fix_when_the_hub_cannot_grow(client):
    """A Ø20 bore with an M8 nut needs Ø59: past a 30-tooth 5M pulley's root."""
    d = _dims(client, {**DFLAT, 'bore': '20', 'hub_od': '30', 'hub_screw_size': 'M8'})
    assert _lobed(d)
    assert 'hub1_od' not in ((d.get('fix') or {}).get('set') or {})


# ── the STEP worker ───────────────────────────────────────────────────────────

def _cmd_params(hub_od, **kw):
    p = {'family': 'HTD', 'pitch': '5M', 'num_teeth': 20, 'bore_mm': 8.0, 'belt_height_mm': 10.0,
         'hub_od_mm': hub_od, 'hub_height_mm': 10.0, 'screw_dia_mm': 5.0, 'screw_count': 1,
         'captured_nut': True, 'flat_depth_mm': 1.5}
    p.update(kw)
    return p


def test_the_worker_refuses_by_name(monkeypatch, tmp_path):
    from exporters import step_worker_ss as w
    monkeypatch.setattr(w, '_binary_version', lambda _bin: (0, 8, 1))
    with pytest.raises(w.Refused, match=r'built with lobes.*D-flat'):
        w._build_pulley_cmd(_cmd_params(24.0), 'small_step', str(tmp_path / 'p.dxf'))
    cmd = w._build_pulley_cmd(_cmd_params(32.0), 'small_step', str(tmp_path / 'p.dxf'))
    assert '--captured-nut' in cmd and '--flat' in cmd
