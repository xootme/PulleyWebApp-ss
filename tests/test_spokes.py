"""
test_spokes.py — 2-D spoke void tests.

Covers:
  * _spoke_void_polygons geometry (counts, bounds, degenerates, fillet variants)
  * generate_png  with spokes (smoke + content checks)
  * generate_svg  with spokes (spoke paths + hub circle)
  * generate_dxf  with spokes (layer presence)
  * Single-pulley across several families/pitches to guard against regressions
"""
import math
import re
import pytest

from exporters.png_exporter  import _spoke_void_polygons, generate_png
from exporters.svg_exporter  import generate_svg
from exporters.dxf_exporter  import generate_dxf

# ---------------------------------------------------------------------------
# Shared geometry under test
# ---------------------------------------------------------------------------

R_HUB      = 10.0   # mm
R_RIM      = 30.0   # mm
SPOKE_W    = 5.0    # mm
N_SPOKES   = 6


# ============================================================================
# 1.  _spoke_void_polygons — geometry unit tests
# ============================================================================

class TestSpokeVoidPolygonsGeometry:
    """Unit-level checks on the shared geometry function."""

    def test_returns_one_polygon_per_spoke(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W)
        assert len(polys) == N_SPOKES

    def test_polygon_has_minimum_points(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W)
        for poly in polys:
            assert len(poly) >= 4, "Each void should have at least 4 vertices"

    def test_all_points_within_radial_bounds(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W)
        for poly in polys:
            for x, y in poly:
                r = math.hypot(x, y)
                assert r >= R_HUB * 0.99, f"Point at r={r:.3f} is inside hub (R_hub={R_HUB})"
                assert r <= R_RIM * 1.01, f"Point at r={r:.3f} is outside rim (R_rim={R_RIM})"

    def test_returns_empty_for_zero_spokes(self):
        assert _spoke_void_polygons(R_HUB, R_RIM, 0, SPOKE_W) == []

    def test_returns_empty_for_zero_width(self):
        assert _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, 0.0) == []

    def test_returns_empty_when_rim_too_close_to_hub(self):
        # R_rim must be > R_hub + 0.5
        assert _spoke_void_polygons(10.0, 10.3, N_SPOKES, SPOKE_W) == []

    def test_one_spoke_returns_one_polygon(self):
        # 1 spoke → 1 void gap wrapping almost the full circumference
        result = _spoke_void_polygons(R_HUB, R_RIM, 1, SPOKE_W)
        assert len(result) == 1

    @pytest.mark.parametrize('n', [2, 3, 4, 6, 8, 10])
    def test_various_spoke_counts(self, n):
        polys = _spoke_void_polygons(R_HUB, R_RIM, n, SPOKE_W)
        assert len(polys) == n

    def test_no_fillets(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W,
                                     fillet_tip_mm=0.0, fillet_base_mm=0.0)
        assert len(polys) == N_SPOKES
        for poly in polys:
            assert len(poly) >= 4

    def test_tip_fillet_only(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W,
                                     fillet_tip_mm=3.0, fillet_base_mm=0.0)
        assert len(polys) == N_SPOKES
        for poly in polys:
            # Tip fillet adds arc points, so expect more vertices than plain 4
            assert len(poly) >= 4

    def test_base_fillet_only(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W,
                                     fillet_tip_mm=0.0, fillet_base_mm=3.0)
        assert len(polys) == N_SPOKES

    def test_both_fillets(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W,
                                     fillet_tip_mm=3.0, fillet_base_mm=3.0)
        assert len(polys) == N_SPOKES
        for poly in polys:
            assert len(poly) >= 4

    def test_fillet_points_within_bounds(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W,
                                     fillet_tip_mm=3.0, fillet_base_mm=3.0)
        for poly in polys:
            for x, y in poly:
                r = math.hypot(x, y)
                assert r >= R_HUB * 0.95, f"Fillet point inside hub: r={r:.3f}"
                assert r <= R_RIM * 1.05, f"Fillet point outside rim: r={r:.3f}"

    def test_hub_overlap_fallback_does_not_crash(self):
        # Spoke width wider than hub circumference → hub_overlap path
        wide = R_HUB * math.pi  # definitely wider than the hub gap
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, wide)
        # May return empty or reduced polygons — must not raise
        assert isinstance(polys, list)

    def test_base_fillet_fallback_to_line_line(self):
        # Small hub, large spoke_count → base touches adjacent spoke, not hub arc
        polys = _spoke_void_polygons(
            R_hub=10.0, R_rim_inner=40.0,
            spoke_count=10, spoke_width_mm=4.0,
            fillet_tip_mm=2.0, fillet_base_mm=4.0,
        )
        # Should still produce one polygon per gap without crashing
        assert len(polys) == 10

    def test_polygon_symmetry(self):
        """All void polygons should have the same number of points (rotational symmetry)."""
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W,
                                     fillet_tip_mm=2.0, fillet_base_mm=2.0)
        assert len(polys) == N_SPOKES
        sizes = {len(p) for p in polys}
        assert len(sizes) == 1, f"Polygons should all be the same size; got {sizes}"

    def test_two_spokes_minimum(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, 2, SPOKE_W)
        assert len(polys) == 2
        for poly in polys:
            assert len(poly) >= 4

    def test_large_fillets_clamped_gracefully(self):
        # Fillets larger than the available gap must not crash
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, SPOKE_W,
                                     fillet_tip_mm=50.0, fillet_base_mm=50.0)
        assert isinstance(polys, list)

    def test_narrow_spokes(self):
        polys = _spoke_void_polygons(R_HUB, R_RIM, N_SPOKES, spoke_width_mm=0.5)
        assert len(polys) == N_SPOKES


# ============================================================================
# 2.  generate_png — image output with spokes
# ============================================================================

# Use a single representative family/pitch for speed
_FAMILY, _PITCH, _TEETH = 'HTD', '5M', 20
_BORE = 8.0
_SPOKE_PARAMS = dict(
    spoke_count=6, spoke_width_mm=5.0, spoke_hub_od_mm=18.0,
    rim_depth_mm=2.0, fillet_tip_mm=1.5, fillet_base_mm=1.5,
)


class TestPngWithSpokes:

    def _png(self, **kw):
        params = dict(family=_FAMILY, pitch=_PITCH, num_teeth=_TEETH,
                      bore_mm=_BORE, size_px=256)
        params.update(kw)
        return generate_png(**params)

    def test_spoke_png_is_valid(self):
        png = self._png(**_SPOKE_PARAMS)
        assert png[:4] == b'\x89PNG'
        assert len(png) > 500

    def test_no_spokes_still_valid(self):
        png = self._png(spoke_count=0)
        assert png[:4] == b'\x89PNG'

    @pytest.mark.parametrize('n', [2, 4, 6, 10])
    def test_various_spoke_counts_png(self, n):
        png = self._png(spoke_count=n, spoke_width_mm=4.0,
                        spoke_hub_od_mm=18.0, rim_depth_mm=2.0)
        assert png[:4] == b'\x89PNG'

    def test_spoke_png_no_fillets(self):
        png = self._png(spoke_count=6, spoke_width_mm=5.0, spoke_hub_od_mm=18.0,
                        rim_depth_mm=2.0, fillet_tip_mm=0.0, fillet_base_mm=0.0)
        assert png[:4] == b'\x89PNG'

    def test_spoke_png_tip_fillet_only(self):
        png = self._png(spoke_count=6, spoke_width_mm=5.0, spoke_hub_od_mm=18.0,
                        rim_depth_mm=2.0, fillet_tip_mm=2.0, fillet_base_mm=0.0)
        assert png[:4] == b'\x89PNG'

    def test_spoke_png_base_fillet_only(self):
        png = self._png(spoke_count=6, spoke_width_mm=5.0, spoke_hub_od_mm=18.0,
                        rim_depth_mm=2.0, fillet_tip_mm=0.0, fillet_base_mm=2.0)
        assert png[:4] == b'\x89PNG'

    def test_spoke_png_large_fillets(self):
        png = self._png(spoke_count=4, spoke_width_mm=5.0, spoke_hub_od_mm=18.0,
                        rim_depth_mm=2.0, fillet_tip_mm=10.0, fillet_base_mm=10.0)
        assert png[:4] == b'\x89PNG'

    def test_spoke_png_many_teeth(self):
        png = self._png(num_teeth=60, spoke_count=6, spoke_width_mm=5.0,
                        spoke_hub_od_mm=20.0, rim_depth_mm=3.0)
        assert png[:4] == b'\x89PNG'


# ============================================================================
# 3.  generate_svg — spoke paths and hub circle in output
# ============================================================================

class TestSvgWithSpokes:

    def _svg(self, **kw):
        params = dict(family=_FAMILY, pitch=_PITCH, num_teeth=_TEETH,
                      bore_mm=_BORE)
        params.update(kw)
        return generate_svg(**params)

    def test_spoke_svg_is_valid_xml(self):
        svg = self._svg(**_SPOKE_PARAMS)
        assert svg.strip().startswith('<?xml')
        assert '<svg' in svg

    def test_spoke_paths_present(self):
        svg = self._svg(**_SPOKE_PARAMS)
        # The spoke voids are emitted as <path> elements
        assert svg.count('<path') >= 2  # at least the profile + one spoke void

    def test_hub_circle_present_when_spokes_enabled(self):
        svg = self._svg(**_SPOKE_PARAMS)
        # Hub circle is a <circle> element; bore is also a circle → expect ≥2
        circles = re.findall(r'<circle', svg)
        assert len(circles) >= 2, "Expected bore + hub circles"

    def test_no_spoke_paths_when_disabled(self):
        svg_no   = self._svg(spoke_count=0)
        svg_yes  = self._svg(**_SPOKE_PARAMS)
        # Spoke SVG should have more content than no-spoke SVG
        assert len(svg_yes) > len(svg_no)

    def test_hub_circle_absent_when_no_spokes(self):
        svg = self._svg(spoke_count=0, spoke_width_mm=0.0)
        # Only one circle element (the bore), not two
        circles = re.findall(r'<circle', svg)
        assert len(circles) == 1

    @pytest.mark.parametrize('n', [2, 4, 6])
    def test_spoke_count_affects_output(self, n):
        svg = self._svg(spoke_count=n, spoke_width_mm=5.0,
                        spoke_hub_od_mm=18.0, rim_depth_mm=2.0)
        assert svg.strip().startswith('<?xml')
        assert 'path' in svg

    def test_svg_spoke_with_no_fillets(self):
        svg = self._svg(spoke_count=6, spoke_width_mm=5.0, spoke_hub_od_mm=18.0,
                        rim_depth_mm=2.0, fillet_tip_mm=0.0, fillet_base_mm=0.0)
        assert '<svg' in svg

    def test_svg_spoke_hub_od_defaults_when_zero(self):
        # spoke_hub_od_mm=0 → defaults to bore + 1 mm; must not crash
        svg = self._svg(spoke_count=4, spoke_width_mm=4.0, spoke_hub_od_mm=0.0,
                        rim_depth_mm=2.0)
        assert '<svg' in svg


# ============================================================================
# 4.  generate_dxf — spoke layer in output
# ============================================================================

class TestDxfWithSpokes:

    def _dxf(self, **kw):
        params = dict(family=_FAMILY, pitch=_PITCH, num_teeth=_TEETH,
                      bore_mm=_BORE)
        params.update(kw)
        return generate_dxf(**params)

    def test_spoke_dxf_returns_bytes(self):
        dxf = self._dxf(**_SPOKE_PARAMS)
        assert isinstance(dxf, bytes)
        assert len(dxf) > 200

    def test_dxf_contains_spokes_layer(self):
        dxf = self._dxf(**_SPOKE_PARAMS)
        text = dxf.decode('utf-8', errors='replace')
        assert 'SPOKES' in text

    def test_dxf_no_spokes_layer_when_disabled(self):
        dxf = self._dxf(spoke_count=0)
        text = dxf.decode('utf-8', errors='replace')
        # Layer table always defines SPOKES, but no LWPOLYLINE on SPOKES layer
        assert 'LWPOLYLINE' not in text or text.count('SPOKES') <= 2

    def test_dxf_bore_and_hub_circles(self):
        dxf = self._dxf(**_SPOKE_PARAMS)
        text = dxf.decode('utf-8', errors='replace')
        # Two circles on BORE layer (bore + hub)
        assert text.count('BORE') >= 2

    def test_dxf_various_spoke_counts(self):
        for n in (2, 4, 6):
            dxf = self._dxf(spoke_count=n, spoke_width_mm=5.0,
                            spoke_hub_od_mm=18.0, rim_depth_mm=2.0)
            assert isinstance(dxf, bytes)
            assert len(dxf) > 200

    def test_dxf_with_fillets(self):
        dxf = self._dxf(spoke_count=6, spoke_width_mm=5.0, spoke_hub_od_mm=18.0,
                        rim_depth_mm=2.0, fillet_tip_mm=2.0, fillet_base_mm=2.0)
        assert isinstance(dxf, bytes)


# ============================================================================
# 5.  Cross-family smoke tests — spokes work for every family/pitch
# ============================================================================

from tests.conftest import PULLEY_CASES, get_spec, std_cl, std_bl, BORE_MM

@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_png_spokes_all_families(family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    png = generate_png(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
        print_extra_mm=0.0, size_px=128,
        spoke_count=4, spoke_width_mm=3.0,
        spoke_hub_od_mm=BORE_MM + 4.0,
        rim_depth_mm=2.0,
    )
    assert png[:4] == b'\x89PNG', f'{family}-{pitch}: PNG header invalid'


@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_svg_spokes_all_families(family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    svg = generate_svg(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
        print_extra_mm=0.0,
        spoke_count=4, spoke_width_mm=3.0,
        spoke_hub_od_mm=BORE_MM + 4.0,
        rim_depth_mm=2.0,
    )
    assert svg.strip().startswith('<?xml'), f'{family}-{pitch}: SVG header invalid'


# ============================================================================
# 6.  Rim layer SVG / DXF (2D laser/waterjet output, added in fe47ef1 batch)
# ============================================================================

from exporters.svg_exporter import generate_rim_layer_svg
from exporters.dxf_exporter import generate_rim_layer_dxf

_RIM_SPOKE_HUB_OD = BORE_MM + 8.0
_RIM_DEPTH        = 2.0

class TestRimLayerSVG:
    """generate_rim_layer_svg returns valid SVG with the expected circle elements."""

    def _svg(self, **kw):
        params = dict(family='HTD', pitch='5M', num_teeth=20, bore_mm=BORE_MM,
                      spoke_hub_od_mm=_RIM_SPOKE_HUB_OD, rim_depth_mm=_RIM_DEPTH)
        params.update(kw)
        return generate_rim_layer_svg(**params)

    def test_returns_string(self):
        assert isinstance(self._svg(), str)

    def test_valid_svg_header(self):
        svg = self._svg()
        assert svg.strip().startswith('<?xml')
        assert '<svg' in svg
        assert '</svg>' in svg

    def test_contains_outer_profile_path(self):
        assert '<path' in self._svg()

    def test_contains_rim_inner_circle(self):
        # blue (#0055cc) inner rim circle must be present
        svg = self._svg()
        assert '#0055cc' in svg

    def test_contains_hub_circle_when_hub_od_set(self):
        # green (#007a00) hub circle when spoke_hub_od_mm > bore
        svg = self._svg(spoke_hub_od_mm=_RIM_SPOKE_HUB_OD)
        assert '#007a00' in svg

    def test_no_hub_circle_when_hub_od_equals_bore(self):
        # Hub circle suppressed when spoke_hub_od_mm == bore_mm (R_hub ≈ R_bore)
        svg = self._svg(spoke_hub_od_mm=BORE_MM)
        assert '#007a00' not in svg

    def test_contains_bore_circle(self):
        # red (#cc0000) bore circle
        assert '#cc0000' in self._svg()

    def test_no_bore_circle_when_bore_zero(self):
        svg = self._svg(bore_mm=0.0)
        assert '#cc0000' not in svg

    @pytest.mark.parametrize('family,pitch', PULLEY_CASES)
    def test_all_families(self, family, pitch):
        spec  = get_spec(family, pitch)
        teeth = spec['min_teeth']
        svg   = generate_rim_layer_svg(
            family=family, pitch=pitch, num_teeth=teeth, bore_mm=BORE_MM,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
            spoke_hub_od_mm=BORE_MM + 6.0, rim_depth_mm=_RIM_DEPTH,
        )
        assert '<svg' in svg, f'{family}-{pitch}: no <svg> element'


class TestRimLayerDXF:
    """generate_rim_layer_dxf returns bytes with the correct DXF layers."""

    def _dxf(self, **kw):
        params = dict(family='HTD', pitch='5M', num_teeth=20, bore_mm=BORE_MM,
                      spoke_hub_od_mm=_RIM_SPOKE_HUB_OD, rim_depth_mm=_RIM_DEPTH)
        params.update(kw)
        return generate_rim_layer_dxf(**params)

    def test_returns_bytes(self):
        dxf = self._dxf()
        assert isinstance(dxf, bytes)
        assert len(dxf) > 500

    def test_contains_profile_layer(self):
        assert b'PROFILE' in self._dxf()

    def test_contains_rim_inner_layer(self):
        assert b'RIM_INNER' in self._dxf()

    def test_contains_hub_layer_when_hub_set(self):
        assert b'HUB' in self._dxf(spoke_hub_od_mm=_RIM_SPOKE_HUB_OD)

    def test_hub_circle_suppressed_when_hub_equals_bore(self):
        # When spoke_hub_od_mm == bore_mm, no circle on HUB layer
        dxf  = self._dxf(spoke_hub_od_mm=BORE_MM)
        text = dxf.decode('utf-8', errors='replace')
        # Layer definition for HUB will exist, but no entity should reference it
        hub_entities = [l for l in text.split('\n') if 'HUB' in l]
        # Only the layer definition line(s), not an entity dxfattrib
        assert len(hub_entities) <= 4  # definition lines only, no circle entity

    def test_contains_bore_layer(self):
        assert b'BORE' in self._dxf()

    @pytest.mark.parametrize('family,pitch', PULLEY_CASES)
    def test_all_families(self, family, pitch):
        spec  = get_spec(family, pitch)
        teeth = spec['min_teeth']
        dxf   = generate_rim_layer_dxf(
            family=family, pitch=pitch, num_teeth=teeth, bore_mm=BORE_MM,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
            spoke_hub_od_mm=BORE_MM + 6.0, rim_depth_mm=_RIM_DEPTH,
        )
        assert isinstance(dxf, bytes), f'{family}-{pitch}: expected bytes'
        assert len(dxf) > 500, f'{family}-{pitch}: DXF suspiciously small'
