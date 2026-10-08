"""test_captured_nut_hub.py — a captured nut in a hub too narrow for it
(small_step's gap 57; the small_step session, 2026-10-07).

A hub narrower than bore + 6·t_nut is built with lobes. Since small_step 0.8.2
the lobed hub carries the bore's shape, so a D-flat, keyway or hex bore builds
(343 designs measured on 0.9.0/0.9.1; the owner dropped the app's refusal), and
0.9.1 refuses by name the lobed hubs it can't make. What's left here is a pocket
through the wall behind a key slot: the Dimensions panel warns with an Auto-fix
(the Hub OD the nut needs), and the STEP worker refuses with the same words.
"""
import pytest

from geometry.captured_nut_hub import min_hub_od, problems

# test_3d's D-shaft case: Ø8 bore, Ø5 screw, its M5 nut (af 8, 4 thick): Ø32 hub
_DSHAFT = dict(bore_mm=8.0, hub_od_mm=24.0, hub_height_mm=10.0, screw_count=1, captured_nut=True,
               screw_dia_mm=5.0, flat_depth_mm=1.5)


def test_min_hub_od():
    assert min_hub_od(bore_mm=8.0, screw_dia_mm=5.0) == pytest.approx(32.0)
    assert min_hub_od(bore_mm=10.0, screw_dia_mm=4.0, nut=(7.0, 3.2)) == pytest.approx(29.2)


@pytest.mark.parametrize('kw', [
    {},                                                                  # D-flat
    {'flat_depth_mm': 0.0, 'keyway_w_mm': 5.0, 'keyway_h_mm': 2.0},      # keyway
    {'flat_depth_mm': 0.0, 'spline': {'kind': 'hex'}},                   # hex bar
    # the Ø5 hex bar, M5 nut, Ø12 hub that 0.9.0 made INVALID: 0.9.1 refuses it by name
    {'bore_mm': 5.0, 'hub_od_mm': 12.0, 'flat_depth_mm': 0.0, 'spline': {'kind': 'hex'}, 'nut': (8.0, 4.0),
     'hole': {'shape': 'circle', 'diameter': 5.5}},
], ids=['D-flat', 'keyway', 'hex', 'hex5 M5'])
def test_a_lobed_hub_on_a_shaped_bore_goes_to_small_step(kw):
    """small_step 0.8.2+ cuts the bore's shape through the lobed hub."""
    assert problems(**dict(_DSHAFT, **kw)) == []


@pytest.mark.parametrize('kw', [
    {'flat_depth_mm': 0.0},                                  # a round bore: the lobed hub is right
    {'captured_nut': False}, {'screw_count': 0}, {'hub_height_mm': 0.0},
    {'hub_od_mm': 11.0},                                     # past 2.9x: small_step grows it round
], ids=['round bore', 'thread', 'no screws', 'no hub', 'grown round'])
def test_what_small_step_builds_is_left_alone(kw):
    assert problems(**dict(_DSHAFT, **kw)) == []


# The small_step session's measured case: a deep key pushes the pocket out through
# the wall round the screw. Ø9.9 bore, 6 x 3 key, #2-56 nut, Ø2.6 hole: the pocket
# reaches r 10.048; round Ø19.6 and Ø20.2 INVALID, Ø21 valid (2026-10-07).
_DEEP_KEY = dict(bore_mm=9.9, hub_height_mm=10.0, screw_count=1, captured_nut=True, screw_dia_mm=2.184,
                 keyway_w_mm=6.0, keyway_h_mm=3.0, nut=(4.763, 1.588), hole={'shape': 'circle', 'diameter': 2.6})


@pytest.mark.parametrize('hub, bad', [(19.6, True), (20.2, True), (21.0, False), (22.0, False)])
def test_no_wall_behind_a_deep_key(hub, bad):
    out = problems(hub_od_mm=hub, **_DEEP_KEY)
    assert bool(out) == bad
    if bad:
        assert 'no hub wall' in out[0] and '10.05 mm' in out[0] and 'key slot' in out[0]


def test_the_fix_measures_the_wall_from_the_key_slot():
    """2·t_nut past the pocket's back face (the slot's outer face), not the bore."""
    need = min_hub_od(bore_mm=9.9, screw_dia_mm=2.184, nut=(4.763, 1.588), keyway_h_mm=3.0,
                      hole={'shape': 'circle', 'diameter': 2.6})
    assert need == pytest.approx(2 * (9.9 / 2 + 3.0 + 3 * 1.588))
    assert problems(hub_od_mm=need, **_DEEP_KEY) == []
    # and the lobed message names the same Hub OD
    assert 'Ø25.43' in problems(hub_od_mm=16.3, **_DEEP_KEY)[0]


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
    return [w for w in d['warnings'] if 'built with lobes' in w or 'no hub wall' in w]


# The measured case on the page: Ø9.9 bore, 6 x 3 key, #2-56 nut. Ø20.2 is round
# (19.43 needed) but the pocket goes through the wall behind the slot.
DEEP_KEY = {**KEYWAY, 'bore': '9.9', 'hub_od': '20.2', 'hub_keyway_w': '6', 'hub_keyway_h': '3',
            'hub_screw_size': '#2-56'}


def test_the_panel_warns_and_the_fix_widens_the_hub(client):
    d = _dims(client, DEEP_KEY)
    [w] = _lobed(d)
    assert 'no hub wall' in w
    hub = d['fix']['set']['hub1_od']
    assert hub > float(DEEP_KEY['hub_od']) and hub * 2 == int(hub * 2)   # a 0.5 mm step, as the field takes
    from agent import _fix_params
    assert _fix_params(d['fix']['set'])['hub_od'] == hub
    assert not _lobed(_dims(client, {**DEEP_KEY, 'hub_od': str(hub)}))  # the fix clears it


@pytest.mark.parametrize('q', [DFLAT, KEYWAY, {**DEEP_KEY, 'feature_build': '0'}, {**DEEP_KEY, 'hub_od': '30'},
                               {**DEEP_KEY, 'hub_screw_hold': 'thread'}],
                         ids=['lobed D-flat', 'lobed keyway', '2D', 'wide hub', 'thread'])
def test_the_panel_is_quiet_otherwise(client, q):
    assert not _lobed(_dims(client, q))


def test_no_fix_when_the_hub_cannot_grow(client):
    """The deep key's Ø25.5 hub is past a 16-tooth 5M pulley's root."""
    d = _dims(client, {**DEEP_KEY, 'teeth': '16'})
    assert _lobed(d)
    assert not any('captured nut' in c for c in (d.get('fix') or {}).get('changes') or [])


# ── the STEP worker ───────────────────────────────────────────────────────────

def _cmd_params(hub_od, **kw):
    p = {'family': 'HTD', 'pitch': '5M', 'num_teeth': 20, 'bore_mm': 8.0, 'belt_height_mm': 10.0,
         'hub_od_mm': hub_od, 'hub_height_mm': 10.0, 'screw_dia_mm': 5.0, 'screw_count': 1,
         'captured_nut': True, 'flat_depth_mm': 1.5}
    p.update(kw)
    return p


def test_the_worker_sends_a_lobed_hub_on_a_shaped_bore(monkeypatch, tmp_path):
    from exporters import step_worker_ss as w
    monkeypatch.setattr(w, '_binary_version', lambda _bin: (0, 9, 1))
    cmd = w._build_pulley_cmd(_cmd_params(24.0), 'small_step', str(tmp_path / 'p.dxf'))
    assert '--captured-nut' in cmd and '--flat' in cmd
    hexed = _cmd_params(12.0, bore_mm=5.0, flat_depth_mm=0.0, screw_nut=[8.0, 4.0],
                        spline={'kind': 'hex', 'n': 6, 'minor': 5.0, 'major': 5.0 * 2 / 3 ** 0.5,
                                'bore': 5.0, 'print': 0.0, 'retainer': None})
    assert '--captured-nut' in w._build_pulley_cmd(hexed, 'small_step', str(tmp_path / 'p.dxf'))


def test_the_worker_refuses_a_pocket_through_the_wall(monkeypatch, tmp_path):
    from exporters import step_worker_ss as w
    monkeypatch.setattr(w, '_binary_version', lambda _bin: (0, 8, 1))
    p = _cmd_params(20.2, bore_mm=9.9, flat_depth_mm=0.0, keyway_w_mm=6.0, keyway_h_mm=3.0,
                    screw_dia_mm=2.184, screw_nut=[4.763, 1.588])
    with pytest.raises(w.Refused, match='no hub wall'):
        w._build_pulley_cmd(p, 'small_step', str(tmp_path / 'p.dxf'))
    assert '--captured-nut' in w._build_pulley_cmd(dict(p, hub_od_mm=25.5), 'small_step',
                                                   str(tmp_path / 'p.dxf'))


@pytest.mark.parametrize('kw', [{'flat_depth_mm': 1.5}, {'flat_depth_mm': 0.0, 'keyway_w_mm': 5.0, 'keyway_h_mm': 2.0}],
                         ids=['D-flat', 'keyway'])
def test_one_screw_on_a_flat_or_key_as_the_stl(monkeypatch, tmp_path, kw):
    """The STL cuts one screw there (step_exporter: screw_angles = [0.0]); two sent
    made small_step cut a second pocket the model hasn't got."""
    from exporters import step_worker_ss as w
    monkeypatch.setattr(w, '_binary_version', lambda _bin: (0, 8, 1))
    cmd = w._build_pulley_cmd(_cmd_params(32.0, screw_count=2, screw_dia_mm=4.0, **kw), 'small_step',
                              str(tmp_path / 'p.dxf'))
    i = cmd.index('--screws')
    assert cmd[i + 2] == '1'
    round_bore = w._build_pulley_cmd(_cmd_params(32.0, screw_count=2, flat_depth_mm=0.0), 'small_step',
                                     str(tmp_path / 'p.dxf'))
    assert round_bore[round_bore.index('--screws') + 2] == '2'
