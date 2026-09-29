"""
test_center_distance.py — correct_center_distance snaps a centre to a whole
belt, and snapping the snapped centre again (as the page shows it, to 0.01
mm) leaves it alone. Without the slack, a rounded centre read a hair over its
belt and re-snapping jumped a tooth (49.07 -> 51.61), which Undo/Redo and a
reload both do.
"""
import math
import random

import pytest

from geometry.pulley_geometry import correct_center_distance, open_belt_length


def _radii(pitch, t1, t2):
    return t1 * pitch / (2 * math.pi), t2 * pitch / (2 * math.pi)


def test_snapped_centre_gives_a_whole_belt():
    pitch, t1, t2 = 5.0, 20, 60
    L, n, C = correct_center_distance(pitch, t1, t2, 100.0)
    assert L == n * pitch
    assert open_belt_length(*_radii(pitch, t1, t2), C) == pytest.approx(L, abs=1e-6)
    assert C >= 100.0 - 0.02


def test_resnapping_a_rounded_centre_keeps_the_belt():
    rng = random.Random(20260928)
    for _ in range(2000):
        pitch = rng.choice([2.0, 2.032, 3.0, 5.0, 5.08, 8.0, 14.0])
        t1, t2 = rng.randint(10, 80), rng.randint(10, 120)
        R1, R2 = _radii(pitch, t1, t2)
        C0 = rng.uniform(R1 + R2, (R1 + R2) * 6)
        _, n, C = correct_center_distance(pitch, t1, t2, C0)
        _, n2, C2 = correct_center_distance(pitch, t1, t2, round(C, 2))
        assert n2 == n, (pitch, t1, t2, C0, C)
        assert C2 == pytest.approx(C, abs=0.01)


def test_the_regression_case():
    # HTD 5M 20T:60T-ish min-centre case from the undo harness: whatever the
    # pair, re-snapping the 2-decimal centre must not add a tooth.
    for t1, t2 in ((20, 30), (20, 60), (24, 36)):
        _, n, C = correct_center_distance(5.0, t1, t2, 0.0)
        _, n2, _ = correct_center_distance(5.0, t1, t2, round(C, 2))
        assert n2 == n
