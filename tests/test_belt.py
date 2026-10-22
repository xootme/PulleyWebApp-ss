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


# The toothed loop of a two-pulley belt closes exactly on its own start: the
# teeth sat on the pitch line's chords (shorter than its arcs) at exact pitch,
# so STD 5M 20/100 at cd 183.86 ended 0.03 mm past its start, the DXF's
# closing line doubled back and small_step's belt STEP was invalid.
_LOOP_CENTRES = [   # (left teeth, right teeth, centre): shown-to-0.01 whole-tooth and arbitrary
    (20, 100, 183.86), (20, 30, 102.19), (20, 30, 100.0), (15, 40, 90.0), (30, 30, 80.0),
]


@pytest.mark.parametrize('family,pitch', BELT_CASES)
def test_the_belt_loop_closes_on_its_start(family, pitch):
    import ezdxf
    import io
    from geometry.pulley_geometry import belt_outline_segments
    from exporters.dxf_exporter import generate_belt_dxf_for_step

    for t1, t2, cd in _LOOP_CENTRES:
        _outer, inner, n, _spec, _C = belt_outline_segments(family, pitch, t1, t2, cd)
        assert len(inner) == n
        for a, b in zip(inner, inner[1:]):
            assert a[1][-1] == b[1][0], f'{t1}/{t2} cd {cd}: teeth do not meet'
        assert inner[-1][1][-1] == inner[0][1][0], f'{t1}/{t2} cd {cd}: loop not closed'

        dxf = generate_belt_dxf_for_step(family, pitch, t1, t2, cd)
        doc = ezdxf.read(io.StringIO(dxf.decode() if isinstance(dxf, bytes) else dxf))
        teeth = doc.modelspace().query('*[layer=="BELT_TEETH"]')
        assert len(teeth.query('LINE')) == 0, f'{t1}/{t2} cd {cd}: a closing line'
        assert len(teeth.query('SPLINE')) == n


# Imperial belts are ISO 5296-1:1989 Table 4a's overall height h_s (teeth included);
# Gates LPP Table 15 gives MXL 1.14 and XL 2.29. Until 2026-10-21 the belt files were
# 1.65 / 3.84 / 6.48 / 8.64 / 17.53 / 25.41 mm thick (owner's fix 1 of DriveTrainWebApp's check).
_ISO5296_HS = {'MXL': 1.14, 'XL': 2.3, 'L': 3.6, 'H': 4.3, 'XH': 11.2, 'XXH': 15.7}


@pytest.mark.parametrize('pitch,hs', sorted(_ISO5296_HS.items()))
def test_imperial_belt_is_iso_5296_high(pitch, hs):
    """The belt's cross-section (the belt SVG/DXF's shape, and the loop the STEP and
    STL sweep) is h_s from tooth tip to back, and its pitch line lies inside the body."""
    from geometry.pulley_geometry import generate_imperial_belt_profile
    pts, geo = generate_imperial_belt_profile(pitch, n_teeth=3)
    ys = [y for _x, y in pts]
    assert max(ys) - min(ys) == pytest.approx(hs, abs=1e-9)
    assert geo['hs'] - geo['ht'] > geo['aa']
