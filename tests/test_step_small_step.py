"""test_step_small_step.py — the splined / hex STEP path through small_step.

`test_spline_retainer.py` and `test_hex_bore.py` assert the STEP is the STL,
but only on the cadquery track: they `importorskip('cadquery')` and force
`PULLEY_STEP_BACKEND=cadquery`. small_step gained the same ground in 0.5.0 —
a bore from any closed LINE/ARC loop, carried through the body, the hub and
the flanges, plus retaining-ring counterbores and set screws on a hex's flats
— so the assertions are repeated here against that backend, at the same
tolerance.

Skipped when the binary is absent or older than the app requires, because a
test that silently passes without exercising anything is worse than no test.

What small_step still refuses is asserted as a refusal, not skipped: a
refusal that quietly stops refusing is how an unsupported case starts
building wrong.
"""
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
import trimesh

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'belt_height': '12'}
HEX = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'hex',
       'spline_af': '17', 'spline_series': 'metric'}
STRAIGHT = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'straight',
            'spline_n': '6', 'spline_minor': '23', 'spline_major': '26',
            'spline_width': '6'}
INVOLUTE = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'involute',
            'spline_m': '1.5', 'spline_z': '16', 'spline_pa': '30',
            'spline_root': 'flat'}
SCREWS = {'hub_od': '44', 'hub_height': '12', 'hub_screw_size': 'M4',
          'hub_screw_hold': 'thread', 'hub_screw_count': '2'}


# The COMMITTED binary, used when SMALL_STEP_BIN says nothing.
#
# This file used to skip itself entirely without that variable, so nothing
# exercised the app against the binary it actually ships -- and a worker that
# sent a flag the committed binary rejects got as far as being pushed twice.
# Defaulting to bin/small_step_linux means the artifact under test is the
# artifact that deploys, unless someone deliberately points elsewhere.
_COMMITTED_BIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'bin', 'small_step_linux')


def _ss_bin():
    return os.environ.get('SMALL_STEP_BIN') or _COMMITTED_BIN


def _ss_ready():
    from exporters.step_worker_ss import SMALL_STEP_MIN_VERSION
    import subprocess
    binp = _ss_bin()
    if not binp or not os.path.isfile(binp):
        return False
    try:
        out = subprocess.run([binp, '--version'], capture_output=True,
                             text=True, timeout=20).stdout.split()
        got = tuple(int(x) for x in out[1].split('.'))
    except Exception:
        return False
    return got >= SMALL_STEP_MIN_VERSION


ss_only = pytest.mark.skipif(not _ss_ready(),
                             reason='no usable small_step: neither SMALL_STEP_BIN nor '
                                    'bin/small_step_linux is present and new enough')


def _load(data):
    n = int(np.frombuffer(data[80:84], '<u4')[0])
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    t = np.frombuffer(data[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t['v'].reshape(-1, 3),
                           faces=np.arange(3 * n).reshape(-1, 3), process=True)


def _step(client, monkeypatch, q):
    monkeypatch.setenv('PULLEY_STEP_BACKEND', '')
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    # Point the app at the same binary `_ss_ready` vetted. Without this the
    # app falls back to its Windows dev path and the test would check a
    # different binary from the one it decided to run against.
    monkeypatch.setenv('SMALL_STEP_BIN', _ss_bin())
    return client.get('/download/step', query_string=q)


def _solids(data):
    """(solids, ok, total volume): ok only when every solid is valid AND of
    positive volume — OCCT calls an inside-out solid valid — and no face is
    wound against its neighbours, which OCCT re-winds unseen (step_checks)."""
    from step_checks import solids, wrong_way_edges
    got = solids(data)
    ok = all(ok and v > 0 for v, ok in got) and not wrong_way_edges(data)
    return len(got), ok, sum(v for v, _ok in got)


@ss_only
@pytest.mark.parametrize('shape', [HEX, STRAIGHT, INVOLUTE],
                         ids=['hex', 'straight', 'involute'])
def test_small_step_step_is_the_stl(client, monkeypatch, shape):
    """The shaped bore's STEP is the STL, at the cadquery track's tolerance."""
    q = {**BASE, **shape}
    r = _step(client, monkeypatch, q)
    assert r.status_code == 200, r.data[:200]
    n, ok, vol = _solids(r.data)
    assert n == 1 and ok
    stl = _load(client.get('/download/stl', query_string=q).data).volume
    assert vol == pytest.approx(stl, rel=5e-3)


@ss_only
def test_small_step_hex_takes_set_screws_and_keeps_its_hub(client, monkeypatch):
    """Two solids, not one.

    This built ONE solid before small_step 0.5.0 -- the hub silently gone,
    while the file reported no unpaired edge -- so the count is the assertion
    that matters here, not just validity.
    """
    q = {**BASE, **HEX, **SCREWS}
    r = _step(client, monkeypatch, q)
    assert r.status_code == 200, r.data[:200]
    n, ok, vol = _solids(r.data)
    assert n == 2 and ok
    stl = _load(client.get('/download/stl', query_string=q).data).volume
    assert vol == pytest.approx(stl, rel=5e-3)


@ss_only
def test_small_step_captured_nut_in_a_shaped_bore_keeps_its_hub(client, monkeypatch):
    """A captured nut in a hex bore, which also used to lose the hub.

    Its stub takes the POCKET path rather than the radial-screw one, so the
    fix that made plain set screws work did not cover it: the same rim built
    cylinder-against-cylinder where the bore is a PLANE, and the same silent
    result -- one solid instead of two, OCCT calling it valid. It was
    refused by name for one commit and then routed through the same
    negative-`bore_arg` mechanism gap 57 already had for a D-flat.

    Two solids is the assertion that matters, as it is for plain screws.
    """
    q = {**BASE, **HEX, **SCREWS, 'hub_screw_hold': 'nut',
         'hub_captured_nut': '1'}
    r = _step(client, monkeypatch, q)
    assert r.status_code == 200, r.data[:200]
    n, ok, vol = _solids(r.data)
    assert n == 2 and ok
    stl = _load(client.get('/download/stl', query_string=q).data).volume
    assert vol == pytest.approx(stl, rel=5e-3)


@ss_only
@pytest.mark.parametrize('cb', [{'spline_cb_top': '0', 'spline_cb_bottom': '0'},
                                {'spline_cb_bottom': '0'}],
                         ids=['neither', 'top-only'])
def test_small_step_counterbore_is_optional(client, monkeypatch, cb):
    """"Make counterbore" off on a face: small_step gets no --counterbore for
    it, and the STEP is still the STL — which keeps the recess's material.

    The last assertion is the one that matters. Volume-against-STL alone
    would pass if the worker looped `faces` and cut BOTH recesses, because
    both exports would be wrong together; comparing against the
    counterbore-on volume is what catches a recess the user turned off.
    """
    q = {**BASE, **STRAIGHT, 'spline_ring_top': '1', 'spline_ring_bottom': '1',
         'spline_washer': '1'}
    r = _step(client, monkeypatch, {**q, **cb})
    assert r.status_code == 200, r.data[:200]
    n, ok, vol = _solids(r.data)
    assert n == 1 and ok
    stl = _load(client.get('/download/stl', query_string={**q, **cb}).data).volume
    assert vol == pytest.approx(stl, rel=5e-3)
    both = _load(client.get('/download/stl', query_string=q).data).volume
    assert vol > both + 100                     # the counterbores' material is there


FLANGE = {'flange_enabled': '1', 'flange_angle': '15', 'flange_rim_radius': '3',
          'flange_height': '1.5', 'flange_3dprint': '1'}


@ss_only
@pytest.mark.parametrize('sep,want', [('1', 2), ('0', 1)],
                         ids=['separate-top', 'joined-top'])
def test_small_step_separate_top_flange_is_its_own_product(client, monkeypatch,
                                                           sep, want):
    """A printed top flange set to print separately is its own STEP PRODUCT.

    Two things are asserted and both matter:

    The STEP must BUILD. The worker sends `--flange-separate top` for this
    case, and a binary older than it rejects the flag outright -- exit 2,
    "needs 6 args", because it falls into the metal-flange arm. That shipped
    briefly: the worker learned the flag while bin/small_step_linux was still
    0.5.0, so every design with a separate printed top flange (the DEFAULT,
    `top_separate=True`) would have failed to export. Nothing caught it,
    which is why this test exists.

    And the product COUNT must change with the setting, or the flag is being
    sent and ignored.
    """
    q = {**BASE, **FLANGE, 'flange_top_separate': sep}
    r = _step(client, monkeypatch, q)
    assert r.status_code == 200, r.data[:300]
    import re
    products = sorted(set(re.findall(r"PRODUCT\('([^']*)'",
                                     r.data.decode('utf8', 'replace'))))
    assert len(products) == want, products
    if want == 2:
        assert any('Flange' in p for p in products), products
    n, ok, _ = _solids(r.data)
    assert ok and n >= 2            # pulley + flange solids, both sound


@ss_only
@pytest.mark.parametrize('rim', ['2', '5'])
def test_small_step_metal_flanges_on_spokes(client, monkeypatch, rim):
    """Metal plates on a spoked pulley stop at the groove bottom less the rim
    depth (they used OD - rim, outside the groove at rim 2; the STL's plates
    are checked in test_flange_overlap.py). small_step's STEP carries the
    plates, so it equals the STL, plates and all."""
    q = {**BASE, 'bore': '8', 'flange_enabled': '1', 'flange_3dprint': '0', 'flange_angle': '15',
         'flange_rim_radius': '4.2', 'flange_plate_height': '1', 'spokes_enabled': '1',
         'spokes_rim_depth': rim, 'spokes_hub_od': '20', 'spokes_width': '6', 'spokes_count': '5'}
    r = _step(client, monkeypatch, q)
    assert r.status_code == 200, r.data[:200]
    n, ok, vol = _solids(r.data)
    assert ok
    stl = _load(client.get('/download/stl', query_string=q).data).volume
    assert vol == pytest.approx(stl, rel=5e-3)


# The owner's printed GT2 30T (site sample gt2-30t-45t-printed-three-spoke-pulleys,
# pulley 1): a 20 mm hub on a pulley 18.2 mm across, so the hub stands on the top
# flange. 2.0.22 sent the flange the hub's radius for its hole, past the tooth tips
# it flares from: small_step 0.12.1 built it inside out (-208 mm3, OCCT "valid").
WIDE_HUB = {'family': 'GT', 'pitch': '2M', 'teeth': '30', 'bore': '5', 'print_extra': '0.2',
            'belt_height': '5.5', 'clearance_height': '1', 'feature_build': '1',
            'hub_od': '20', 'hub_height': '6', 'hub_screw_size': 'M3', 'hub_screw_hold': 'nut',
            'hub_screw_count': '2', 'hub_captured_nut': '1',
            'flange_enabled': '1', 'flange_3dprint': '1', 'flange_angle': '15',
            'flange_rim_radius': '3', 'flange_height': '2', 'flange_top_separate': '0'}


@ss_only
def test_a_hub_wider_than_the_pulley_gets_a_full_top_flange():
    """The top flange's hole is the bore's, not the hub's, when the hub is
    wider than the tooth tips: a full disc under the hub, as the STL builds it."""
    from werkzeug.datastructures import MultiDict
    import app as A
    from exporters.step_worker_ss import _build_pulley_cmd
    cmd = _build_pulley_cmd(A._step_kw_of(MultiDict(WIDE_HUB), ''), _ss_bin(), 'x.dxf')
    i = cmd.index('--top-3d')
    assert float(cmd[i + 1]) == pytest.approx(2.5) and float(cmd[i + 2]) > 9.0   # bore r, tooth tips r
    q = dict(WIDE_HUB, hub_od='16')                                  # a hub inside the teeth keeps its hole
    cmd = _build_pulley_cmd(A._step_kw_of(MultiDict(q), ''), _ss_bin(), 'x.dxf')
    assert float(cmd[cmd.index('--top-3d') + 1]) == pytest.approx(8.0)


@ss_only
def test_small_step_wide_hub_top_flange_is_not_inside_out(client, monkeypatch):
    from exporters.step_worker_ss import _binary_version
    from step_checks import problems, solids, stl_volume
    r = _step(client, monkeypatch, WIDE_HUB)
    assert r.status_code == 200, r.data[:200]
    assert len(solids(r.data)) == 4                        # body, top flange, bottom flange, hub
    # small_step before 0.13.0 raises a captured nut's hub 0.5 mm above the
    # app's (0.13.0 drops that to 0.01, the owner 2026-10-12): +3 % here.
    rel = 0.01 if _binary_version(_ss_bin()) >= (0, 13, 0) else 0.04
    stl = stl_volume(client.get('/download/stl', query_string=WIDE_HUB).data)
    assert problems(r.data, stl, rel) == []


@ss_only
def test_a_face_wound_backwards_is_caught(client, monkeypatch):
    """eDrawings drew the GT2 30T's hub wall inside out: its end circles were
    wound as a bore's, which OCCT re-winds on reading (small_step 0.13.4 fixed
    it). The raw-entity check must pass the real file and catch one face's
    bound turned round."""
    import re
    from step_checks import problems, wrong_way_edges
    r = _step(client, monkeypatch, WIDE_HUB)
    assert r.status_code == 200, r.data[:200]
    assert wrong_way_edges(r.data) == {}
    bound = re.search(rb"(#\d+\s*=\s*FACE_OUTER_BOUND\('[^']*',#\d+,)\.T\.\)", r.data)
    flipped = r.data[:bound.start()] + bound.group(1) + b'.F.)' + r.data[bound.end():]
    assert wrong_way_edges(flipped)
    assert any('wound against' in p for p in problems(flipped))


# Nightly random designs (2026-10-23, 10-25) showed two faults: a key or flat in a pulley with
# no hub never reached small_step, so its STEP had a round bore (+1.5 %); and on a keyed hub
# the STL built a second captured-nut lobe, 180° round, with no screw in it (the STEP -5.3 %).
KEYED = {
    'no hub, keyway': {'family': 'STD', 'pitch': '2M', 'teeth': '59', 'bore': '10.4', 'print_extra': '0.14',
                       'belt_height': '11.8', 'clearance_height': '0.1', 'hub_keyway_w': '5', 'hub_keyway_h': '2.5'},
    'no hub, D-flat': {'family': 'STD', 'pitch': '2M', 'teeth': '59', 'bore': '10.4', 'belt_height': '11.8',
                       'hub_flat_depth': '1'},
    'captured nuts, keyway': {'family': 'HTD', 'pitch': '3M', 'teeth': '50', 'bore': '16.6', 'print_extra': '0.27',
                              'clearance_preset': 'LOOSE', 'backlash_preset': 'LOOSE', 'belt_height': '20.7',
                              'clearance_height': '0.29', 'hub_keyway_w': '6', 'hub_keyway_h': '3',
                              'hub_od': '28.4', 'hub_height': '11.1', 'hub_screw_dia': '5', 'hub_screw_count': '2',
                              'hub_captured_nut': '1'},
}


@ss_only
@pytest.mark.parametrize('q', KEYED.values(), ids=KEYED.keys())
def test_small_step_keyed_bore_is_the_stl(client, monkeypatch, q):
    from step_checks import problems, reference_volume
    r = _step(client, monkeypatch, {**q, 'sv': '1'})
    assert r.status_code == 200, r.data[:200]
    assert problems(r.data, reference_volume(client, q, 1)) == []
