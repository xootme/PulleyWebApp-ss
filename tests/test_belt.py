"""
test_belt.py — Belt tooth cross-section exporter tests.
Covers generate_belt_svg and generate_belt_png for every family/pitch
that has both a pulley spec and a belt tooth spec.
"""
import pytest

from exporters.belt_svg_exporter import generate_belt_svg, generate_belt_png

from tests.conftest import BELT_CASES


@pytest.mark.parametrize('family,pitch', BELT_CASES)
def test_belt_svg(family, pitch):
    svg = generate_belt_svg(family, pitch, n_teeth=3)
    assert svg.strip().startswith('<?xml')
    assert '<svg' in svg
    assert len(svg) > 200


@pytest.mark.parametrize('family,pitch', BELT_CASES)
def test_belt_png(family, pitch):
    png = generate_belt_png(family, pitch, n_teeth=3, size_px=256)
    assert png[:4] == b'\x89PNG'
    assert len(png) > 200
