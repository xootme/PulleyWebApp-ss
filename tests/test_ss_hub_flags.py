"""test_ss_hub_flags.py — what the small_step worker tells small_step about the
hub and its set screw (exporters/step_worker_ss.py::_build_pulley_cmd): the hole
and nut the app draws, the hub's skirt under a flange, and a captured nut's
screw height — each only when the binary lists the flag.

No binary needed: the flags are checked on the command line. That they make
the right solid is measured in test_set_screw_step.py (STEP against the STL),
which needs cadquery and a binary with the flags on one machine; by hand on
2026-10-06 (small_step 000ca92, ed8f1901 built in WSL, read on Windows) hub tops
and screw centres agreed with the STL to its 0.05 mm probe step.
"""
import math

import pytest

from exporters import step_worker_ss as W

NUT = (8.731, 3.175)                                   # an #8-32 nut
P = dict(family='HTD', pitch='5M', num_teeth=24, bore_mm=8.0, belt_height_mm=10.5,
         hub_od_mm=26.0, hub_height_mm=12.0, screw_dia_mm=4.166, screw_count=1,
         screw_hole={'shape': 'circle', 'diameter': 4.7})
CAPTURED = dict(P, captured_nut=True, screw_nut=list(NUT))
PRINTED = dict(flange_enabled=True, flange_3dprint=True, flange_height_mm=2.4, flange_top_separate=False)


def _cmd(params, flags=('--screw-hole', '--nut', '--hub-skirt', '--screw-z')):
    W._help.clear()
    orig = W._has_flag
    W._has_flag = lambda _bin, flag: flag in flags
    try:
        return W._build_pulley_cmd(dict(params), 'ss', 'x.dxf')
    finally:
        W._has_flag = orig


def _arg(cmd, flag, n=1):
    i = cmd.index(flag)
    return [float(a) if a.replace('.', '', 1).replace('-', '', 1).isdigit() else a for a in cmd[i + 1:i + 1 + n]]


def test_the_hole_and_nut_the_app_draws():
    assert _arg(_cmd(P), '--screw-hole', 2) == ['round', 4.7]
    assert _arg(_cmd(dict(P, screw_hole={'shape': 'hex', 'flats': 4.1})), '--screw-hole', 2) == ['hex', 4.1]
    assert _arg(_cmd(CAPTURED), '--nut', 2) == list(NUT)


def test_a_captured_nuts_screw_is_on_the_nut():
    """hub_top − af/√3: 10.5 + 12 − 8.731/√3."""
    assert _arg(_cmd(CAPTURED), '--screw-z')[0] == pytest.approx(22.5 - NUT[0] / math.sqrt(3))
    assert '--screw-z' not in _cmd(P)                                  # a threaded screw: small_step's mid-boss


def test_the_pocket_can_grow_the_hub():
    """A hub lower than the nut's pocket is grown to hold it, as the STL does."""
    low = _arg(_cmd(dict(CAPTURED, hub_height_mm=6.0)), '--screw-z')[0]
    pocket = 2.0 * (NUT[0] + 0.5) / math.sqrt(3)
    assert low == pytest.approx(10.5 + pocket - NUT[0] / math.sqrt(3))


def test_a_flanged_hub_has_its_skirt_and_the_screw_rides_on_it():
    cmd = _cmd(dict(CAPTURED, **PRINTED))
    assert _arg(cmd, '--hub-skirt')[0] == pytest.approx(2.4)
    assert _arg(cmd, '--screw-z')[0] == pytest.approx(22.5 + 2.4 - NUT[0] / math.sqrt(3))
    metal = _cmd(dict(P, flange_enabled=True, flange_3dprint=False, plate_height_mm=1.2))
    assert _arg(metal, '--hub-skirt')[0] == pytest.approx(1.2)
    assert '--hub-skirt' not in _cmd(dict(P, spoke_count=5, spoke_width_mm=6.0, spoke_hub_od_mm=22.0,
                                          rim_depth_mm=5.0, **PRINTED))   # spokes: no skirt, as the STL


def test_a_binary_without_the_flags_is_not_sent_them():
    """small_step fails the whole STEP on a flag it doesn't know."""
    cmd = _cmd(dict(CAPTURED, **PRINTED), flags=())
    assert not {'--screw-hole', '--nut', '--hub-skirt', '--screw-z'} & set(cmd)
    cmd = _cmd(dict(CAPTURED, **PRINTED), flags=('--screw-z',))       # no skirt: the screw rides lower
    assert _arg(cmd, '--screw-z')[0] == pytest.approx(22.5 - NUT[0] / math.sqrt(3))
