"""test_nubs_flange_hole.py — gluing nubs that stand in the printed top
flange's hole: STEP refuses them (small_step's nubs_in_flange_bore), and the
Dimensions panel says so first, with an Auto-fix that turns the nubs off (the
owner, 2026-10-30).

The design is CCT_WebSite's from the 10-30 download failures: a 20T XL with a
Ø25.4 hub, its nubs at r 12.6161, the flange's hole r 12.7. Three STEP tries
were refused on it and the page had said nothing."""
import os
import subprocess
import tempfile

import pytest

# pulley 1 of the failed tries, alone (all sizes mm)
DESIGN = {'family': 'Imperial', 'pitch': 'XL', 'teeth': 20, 'bore': 6.35, 'belt_height': 6.35,
          'clearance_height': 2.6, 'feature_build': 1,
          'hub_od': 25.4, 'hub_height': 12.7,
          'flange_enabled': 1, 'flange_3dprint': 1, 'flange_top_separate': 1, 'flange_angle': 15,
          'flange_height': 3.048, 'flange_rim_radius': 6.35,
          'flange_nubs_enabled': 1, 'flange_nub_count': 2, 'flange_nub_dia': 3,
          'flange_nub_height': 1, 'flange_nub_allowance': 0.2}


def _dims(client, **extra):
    r = client.get('/api/dimensions', query_string={**DESIGN, **extra})
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def _nub_warnings(d):
    return [w for w in d['warnings'] if 'gluing nubs' in w]


def test_the_panel_warns_and_the_fix_turns_the_nubs_off(client):
    d = _dims(client)
    assert _nub_warnings(d) == [
        "the gluing nubs sit within r 12.62 mm, inside the top flange's Ø25.40 mm hole (the hub), "
        "so they'd have no flange to hold — use a Hub OD under Ø25.2 mm, more teeth, or turn the nubs off."]
    assert d['fix']['set']['flange1_nubs_enabled'] is False
    assert 'gluing nubs off' in d['fix']['changes']


@pytest.mark.parametrize('q', [
    {'hub_od': 25.0},                       # the nubs clear the hub's hole
    {'flange_nubs_enabled': 0},             # the fix
    {'flange_3dprint': 0},                  # a metal flange has no nubs
    {'feature_build': 0},
], ids=['smaller hub', 'nubs off', 'metal flange', '2D'])
def test_the_panel_is_quiet_otherwise(client, q):
    assert _nub_warnings(_dims(client, **q)) == []


def test_pulley_2_is_named_and_fixed(client):
    p2 = {f'p2_{k}': v for k, v in DESIGN.items()
          if k not in ('family', 'pitch', 'belt_height', 'clearance_height', 'feature_build')}
    d = _dims(client, dual='true', center_distance=151.54, flange_nubs_enabled=0, **p2)
    [w] = _nub_warnings(d)
    assert w.startswith('Pulley 2: the gluing nubs sit within r 12.62 mm')
    assert d['fix']['set']['flange2_nubs_enabled'] is False


def _ss_binary():
    env = os.environ.get('SMALL_STEP_BIN')
    for p in (env, os.path.expanduser(
            r'~\Documents\small_step\target\x86_64-pc-windows-gnu\release\small_step.exe')):
        if p and os.path.isfile(p):
            return p
    return None


@pytest.mark.parametrize('hub_od, refused', [(25.4, True), (25.0, False)])
def test_the_panel_warns_exactly_when_step_refuses(client, hub_od, refused):
    """The panel's numbers are the STEP worker's (step_worker_ss), checked
    against small_step's own refusal."""
    from exporters.dxf_exporter import generate_dxf
    from exporters.step_worker_ss import _build_pulley_cmd
    binary = _ss_binary()
    if not binary:
        pytest.skip('small_step binary not found')
    assert bool(_nub_warnings(_dims(client, hub_od=hub_od))) == refused
    dxf = generate_dxf(family='Imperial', pitch='XL', num_teeth=20, bore_mm=6.35, clearance_mm=0.0,
                       backlash_mm=0.0, print_extra_mm=0.0, spoke_count=0, spoke_width_mm=0.0,
                       spoke_hub_od_mm=0.0, rim_depth_mm=0.0, fillet_tip_mm=0.0, fillet_base_mm=0.0,
                       flat_depth_mm=0.0, keyway_w_mm=0.0, keyway_h_mm=0.0)
    with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False, mode='wb') as f:
        f.write(dxf.encode() if isinstance(dxf, str) else dxf)
    try:
        cmd = _build_pulley_cmd(dict(
            family='Imperial', pitch='XL', num_teeth=20, bore_mm=6.35, belt_height_mm=8.95,
            hub_od_mm=hub_od, hub_height_mm=12.7, flange_enabled=True, flange_3dprint=True,
            flange_angle_deg=15.0, flange_rim_radius_mm=6.35, flange_height_mm=3.048,
            flange_top_separate=True, nubs_enabled=True, nub_count=2, nub_dia_mm=3.0,
            nub_height_mm=1.0, nub_allowance_mm=0.2), binary, f.name)
        r = subprocess.run(cmd, capture_output=True)
    finally:
        os.unlink(f.name)
    err = r.stderr.decode(errors='replace')
    assert ("nubs reach only r=12.6161 but the flange's inner radius is r=12.7000" in err) == refused, err[:400]
    if not refused:
        assert r.returncode == 0, err[:400]
