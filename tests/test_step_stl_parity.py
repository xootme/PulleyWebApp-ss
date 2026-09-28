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
    hub by flange_height, the STL by the plate it actually sits on.

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


@pytest.fixture
def cadquery_backend(monkeypatch):
    monkeypatch.setenv('PULLEY_STEP_BACKEND', 'cadquery')
    monkeypatch.setenv('QUEUE_DISABLED', '1')


@pytest.mark.parametrize('cfg', [NUT_SPOKES, NO_HUB_HEIGHT, METAL_WIDE_HUB],
                         ids=['nut-lobes-over-spokes', 'screw-without-hub-height', 'metal-flange-wide-hub'])
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
