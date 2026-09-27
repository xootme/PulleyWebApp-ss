"""
test_backlash_tight.py — Backlash "Tight" narrows the groove on every profile.

Negative backlash (the TIGHT presets) used to be clamped to 0 in
generate_imperial_groove, so Tight was the same as Standard on every
Imperial, T and AT pitch while the panel showed e.g. "Offset: −0.340 mm".
It now narrows the groove by backlash/2 per side, stopping before the groove
floor closes so a large negative Custom value can't cross the flanks.
"""
import pytest
from shapely.geometry import Polygon

from app import _get_preset_value, _resolve_key
from exporters.step_exporter import _build_outline_points
from geometry.pulley_geometry import PROFILE_PITCHES, PULLEY_SPECS

CASES = []
for _family, _pitches in PROFILE_PITCHES.items():
    for _pitch in _pitches:
        _key = _resolve_key(_family, _pitch)
        _spec = PULLEY_SPECS.get(_key) if _key else None
        if _spec and "backlash" in _spec and \
                _get_preset_value(_spec, "backlash", "TIGHT", 0.0) != _get_preset_value(_spec, "backlash", "STANDARD", 0.0):
            CASES.append((_family, _pitch))


def _outline(family, pitch, backlash, teeth=30):
    spec = PULLEY_SPECS[_resolve_key(family, pitch)]
    cl = _get_preset_value(spec, "clearances", "STANDARD", 0.0)
    return Polygon(_build_outline_points(family, pitch, teeth, cl, backlash, 0.0)[0])


def test_every_family_with_a_tight_preset_is_covered():
    families = {f for f, _ in CASES}
    assert {"T", "AT", "Imperial", "GT", "RPP"} <= families, families


@pytest.mark.parametrize("family,pitch", CASES)
def test_tight_narrows_the_groove(family, pitch):
    spec = PULLEY_SPECS[_resolve_key(family, pitch)]
    tight = _outline(family, pitch, _get_preset_value(spec, "backlash", "TIGHT", 0.0))
    std = _outline(family, pitch, _get_preset_value(spec, "backlash", "STANDARD", 0.0))
    assert tight.is_valid and std.is_valid
    assert tight.area > std.area + 1e-6          # narrower grooves: more pulley


@pytest.mark.parametrize("family,pitch", [("Imperial", "XL"), ("T", "T5"), ("AT", "AT10")])
def test_huge_negative_backlash_stays_a_valid_outline(family, pitch):
    big = _outline(family, pitch, -50.0)
    assert big.is_valid, f"{family} {pitch}: self-intersecting outline"
    assert big.area > _outline(family, pitch, 0.0).area
