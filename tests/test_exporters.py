"""
test_exporters.py — Direct exporter tests (no HTTP server needed).
Covers SVG single, PNG single, PNG dual, and clearance×backlash combos
for every supported family/pitch combination.
"""
import pytest

from exporters.svg_exporter import generate_svg, generate_svg_dual
from exporters.png_exporter import generate_png, generate_png_dual

from tests.conftest import (
    PULLEY_CASES, BORE_MM,
    get_spec, std_cl, std_bl, preset_val,
    CLEARANCE_PRESETS, BACKLASH_PRESETS, PRINT_EXTRA_VALS,
)


@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_svg_single(family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    svg = generate_svg(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
        print_extra_mm=0.0,
    )
    assert svg.strip().startswith('<?xml')
    assert '<svg' in svg
    assert len(svg) > 200


@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_png_single(family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    png = generate_png(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
        print_extra_mm=0.0, size_px=256,
    )
    assert png[:4] == b'\x89PNG'
    assert len(png) > 500


@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_png_dual(family, pitch):
    spec   = get_spec(family, pitch)
    teeth1 = spec['min_teeth']
    teeth2 = spec['min_teeth'] * 2
    cl, bl = std_cl(spec), std_bl(spec)
    png = generate_png_dual(
        family=family, pitch=pitch,
        num_teeth1=teeth1, bore_mm1=BORE_MM, clearance_mm1=cl, backlash_mm1=bl,
        num_teeth2=teeth2, bore_mm2=BORE_MM, clearance_mm2=cl, backlash_mm2=bl,
        center_dist_mm=120.0, size_px=256,
    )
    assert png[:4] == b'\x89PNG'
    assert len(png) > 100


@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_svg_dual(family, pitch):
    spec   = get_spec(family, pitch)
    teeth1 = spec['min_teeth']
    teeth2 = spec['min_teeth'] * 2
    cl, bl = std_cl(spec), std_bl(spec)
    svg = generate_svg_dual(
        family=family, pitch=pitch,
        num_teeth1=teeth1, bore_mm1=BORE_MM, clearance_mm1=cl, backlash_mm1=bl,
        num_teeth2=teeth2, bore_mm2=BORE_MM, clearance_mm2=cl, backlash_mm2=bl,
        center_dist_mm=120.0,
    )
    assert svg.strip().startswith('<?xml')
    assert '<svg' in svg


@pytest.mark.slow
@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_clearance_backlash_combos(family, pitch):
    """All clearance × backlash × print_extra combos produce valid PNGs."""
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    for cl_key in CLEARANCE_PRESETS:
        cl = preset_val(spec, 'clearance', cl_key)
        for bl_key in BACKLASH_PRESETS:
            bl = preset_val(spec, 'backlash', bl_key)
            for pe in PRINT_EXTRA_VALS:
                png = generate_png(
                    family=family, pitch=pitch, num_teeth=teeth,
                    bore_mm=BORE_MM, clearance_mm=cl, backlash_mm=bl,
                    print_extra_mm=pe, size_px=128,
                )
                assert png[:4] == b'\x89PNG', \
                    f'{family}-{pitch} CL={cl_key} BL={bl_key} PE={pe} — not a PNG'
