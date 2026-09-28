"""
test_step_stl_parity.py — the cadquery STEP and the STL download are the same
pulley, measured the way fuzz_pulley.py measures them (tessellated STEP volume
vs the STL's volume, metal plates taken off the STL). One case per mismatch
the cadquery fuzz found:

  * captured-nut lobes over spokes: the STL had no 45° support cones under
    the lobes, though the STEP and the preview did;
  * a set screw with no hub height: the STEP invented a nut-sized hub the
    STL and the preview never showed;
  * a metal flange under a hub wider than the pulley: the STEP raised the
    hub by flange_height, the STL by the plate it actually sits on;
  * a keyway wider than the bore: the STEP's key box started just inside
    the bore wall and left two slivers the STL cut away;
  * a big pulley's spoke voids: the STL drew the rim arc with 16 points,
    sagging ~0.2 mm inside the STEP's true arc;
  * two captured nuts over spokes: the STEP's pocket face sat exactly on
    the bore (tangent) and OCCT made an invalid solid;
  * metal plates over spokes the app had to fit: /download/flange-stl cut
    the plates for the spokes as typed, not as built;
  * nub sockets on a pulley small enough that the nub circle overlaps the
    hub: the STL's socket cutters reached up through the hub and carved it.

Skipped without cadquery (the .venv312 track has it).
"""
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("cadquery")

from fuzz_pulley import VOLUME_REL_TOL, _fetch, _load_stl, _step_mesh_volume  # noqa: E402

NUT_SPOKES = {
    'family': 'GT', 'pitch': '5M', 'teeth': 52, 'bore': 28.8, 'print_extra': 0.15,
    'clearance_preset': 'STANDARD', 'backlash_preset': 'NONE', 'belt_height': 19.0,
    'clearance_height': 0.36, 'hub_flat_depth': 1.04, 'hub_od': 36.6, 'hub_height': 13.0,
    'hub_screw_size': '#6-32', 'hub_screw_hold': 'nut', 'hub_screw_count': 1,
    'hub_screw_dia': 3.505, 'hub_captured_nut': '1',
    'spokes_enabled': '1', 'spokes_hub_od': 36.6, 'spokes_rim_depth': 5.3, 'spokes_width': 3.2,
    'spokes_fillet_tip': 1.9, 'spokes_fillet_base': 1.2, 'spokes_count': 7, 'spokes_height': 3.9,
}
NO_HUB_HEIGHT = {k: v for k, v in NUT_SPOKES.items() if k not in ('hub_od', 'hub_height')}
METAL_WIDE_HUB = {
    'family': 'HTD', 'pitch': '5M', 'teeth': 16, 'bore': 8, 'print_extra': 0,
    'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD', 'belt_height': 10,
    'clearance_height': 0.5, 'hub_od': 32, 'hub_height': 8,
    'flange_enabled': '1', 'flange_3dprint': '0', 'flange_angle': 15, 'flange_rim_radius': 2,
    'flange_height': 2.5, 'flange_plate_height': 1.2, 'flange_bend_radius': 0.4,
}

WIDE_KEY = {
    'family': 'GT', 'pitch': '2M', 'teeth': 33, 'bore': 4.1, 'print_extra': 0.33,
    'clearance_preset': 'STANDARD', 'backlash_preset': 'LOOSE', 'belt_height': 15.7,
    'clearance_height': 0.1, 'hub_keyway_w': 6.0, 'hub_keyway_h': 3.0, 'hub_od': 14.9, 'hub_height': 8.1,
}
BIG_SPOKED = {
    'family': 'T', 'pitch': 'T20', 'teeth': 31, 'bore': 27.6, 'print_extra': 0.27,
    'clearance_preset': 'LOOSE', 'backlash_preset': 'LOOSE', 'belt_height': 11.8,
    'clearance_height': 0.48, 'spokes_enabled': '1', 'spokes_hub_od': 30.9, 'spokes_rim_depth': 2.1,
    'spokes_width': 6.2, 'spokes_fillet_tip': 0.7, 'spokes_fillet_base': 1.1, 'spokes_count': 3,
    'spokes_height': 4.1,
}
TWO_NUTS_SPOKES = {
    'family': 'HTD', 'pitch': '14M', 'teeth': 33, 'bore': 6.6, 'print_extra': 0.29,
    'clearance_preset': 'TIGHT', 'backlash_preset': 'STANDARD', 'belt_height': 16.2,
    'clearance_height': 0.25, 'hub_od': 21.4, 'hub_height': 4.1, 'hub_screw_size': '#4-40',
    'hub_screw_hold': 'nut', 'hub_screw_count': 2, 'hub_screw_dia': 2.845, 'hub_captured_nut': '1',
    'spokes_enabled': '1', 'spokes_hub_od': 21.4, 'spokes_rim_depth': 6.9, 'spokes_width': 6.0,
    'spokes_fillet_tip': 1.5, 'spokes_fillet_base': 3.6, 'spokes_count': 3, 'spokes_height': 2.5,
}

METAL_FITTED_SPOKES = {
    'family': 'STD', 'pitch': '3M', 'teeth': 28, 'bore': 8.0, 'print_extra': 0.06,
    'clearance_preset': 'LOOSE', 'backlash_preset': 'TIGHT', 'belt_height': 8.9, 'clearance_height': 0.76,
    'spokes_enabled': '1', 'spokes_hub_od': 12.7, 'spokes_rim_depth': 6.3, 'spokes_width': 5.0,
    'spokes_fillet_tip': 0.6, 'spokes_fillet_base': 3.4, 'spokes_count': 7, 'spokes_height': 0.9,
    'flange_enabled': '1', 'flange_3dprint': '0', 'flange_angle': 13.9, 'flange_rim_radius': 5.5,
    'flange_height': 1.8, 'flange_plate_height': 1.0, 'flange_bend_radius': 2.9, 'flange_top_separate': '1',
}
NUBS_OVER_HUB = {
    'family': 'T', 'pitch': 'T5', 'teeth': 13, 'bore': 4.4, 'print_extra': 0.03,
    'clearance_preset': 'STANDARD', 'backlash_preset': 'LOOSE', 'belt_height': 7.7, 'clearance_height': 0.22,
    'hub_od': 13.2, 'hub_height': 12.8,
    'flange_enabled': '1', 'flange_3dprint': '1', 'flange_angle': 8.4, 'flange_rim_radius': 4.9,
    'flange_height': 2.4, 'flange_top_separate': '1', 'flange_nubs_enabled': '1', 'flange_nub_count': 6,
    'flange_nub_dia': 5.8, 'flange_nub_height': 3.6, 'flange_nub_allowance': 0.35,
}


@pytest.fixture
def cadquery_backend(monkeypatch):
    monkeypatch.setenv('PULLEY_STEP_BACKEND', 'cadquery')
    monkeypatch.setenv('QUEUE_DISABLED', '1')


@pytest.mark.parametrize('cfg', [NUT_SPOKES, NO_HUB_HEIGHT, METAL_WIDE_HUB, WIDE_KEY, BIG_SPOKED,
                                 TWO_NUTS_SPOKES, METAL_FITTED_SPOKES, NUBS_OVER_HUB],
                         ids=['nut-lobes-over-spokes', 'screw-without-hub-height', 'metal-flange-wide-hub',
                              'keyway-wider-than-bore', 'big-spoked-rim-arc', 'two-nuts-over-spokes',
                              'metal-plates-fitted-spokes', 'nub-sockets-over-hub'])
def test_step_and_stl_are_the_same_pulley(cadquery_backend, cfg):
    stl = _load_stl(_fetch('/download/stl', cfg))
    stl_volume = stl.volume
    if cfg.get('flange_enabled') == '1' and cfg.get('flange_3dprint') == '0':
        # pulley + plates in one file, touching where the plates sit: not one
        # closed surface, but the volumes still add up
        stl_volume -= _load_stl(_fetch('/download/flange-stl', cfg)).volume
    else:
        assert stl.is_watertight
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(_fetch('/download/step', cfg))
    step_volume = _step_mesh_volume(Path(f.name))
    assert abs(step_volume - stl_volume) / stl_volume < VOLUME_REL_TOL, (step_volume, stl_volume)


def test_two_nuts_over_spokes_is_a_valid_solid(cadquery_backend):
    import cadquery as cq
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(_fetch('/download/step', TWO_NUTS_SPOKES))
    solids = [so for sh in cq.importers.importStep(f.name).vals() for so in sh.Solids()]
    assert solids and all(so.isValid() for so in solids)
