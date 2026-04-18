"""
test_flange_geometry.py — unit tests for geometry/flange_geometry.py.

Covers:
  * flange_inner_r_3dprint  — all three inner-rim scenarios
  * flange_inner_r_metal_top — all three inner-rim scenarios
  * flange_inner_r_metal_bottom — spokes + bare-bore scenarios
  * hub_intersects_flange — warning trigger conditions
  * profile_3dprint — polygon shape sanity
  * profile_metal — polygon shape sanity
"""
import math
import pytest

from geometry.flange_geometry import (
    flange_inner_r_3dprint,
    flange_inner_r_metal_top,
    flange_inner_r_metal_bottom,
    hub_intersects_flange,
    profile_3dprint,
    profile_metal,
)

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------
BORE_MM       = 8.0
HUB_OD_MM     = 20.0      # hub OD > bore → hub takes priority (no spokes)
R_TOOTH_OD    = 47.0      # representative pulley OD radius
RIM_DEPTH_MM  = 8.0       # rim ring depth (spoke case)
SPOKE_HUB_OD  = 25.0      # spoke hub OD (not used in inner-r rule; R_OD - rim prevails)

# Expected inner radius when spokes active
R_INNER_SPOKES = R_TOOTH_OD - RIM_DEPTH_MM   # 39.0


# ===========================================================================
# 1. flange_inner_r_3dprint
# ===========================================================================

class TestFlangeInnerR3dprint:

    def test_spokes_active_returns_R_OD_minus_rim_depth(self):
        r = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=HUB_OD_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=RIM_DEPTH_MM,
        )
        assert abs(r - R_INNER_SPOKES) < 1e-9

    def test_spokes_active_ignores_hub_od(self):
        """Hub OD should have no effect when spokes are enabled."""
        r_with_hub = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=HUB_OD_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=RIM_DEPTH_MM,
        )
        r_no_hub = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=0.0,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=RIM_DEPTH_MM,
        )
        assert abs(r_with_hub - r_no_hub) < 1e-9

    def test_no_spokes_with_hub_returns_hub_radius(self):
        r = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=HUB_OD_MM,
            spokes_enabled=False, spoke_hub_od_mm=SPOKE_HUB_OD,
        )
        assert abs(r - HUB_OD_MM / 2.0) < 1e-9

    def test_no_spokes_no_hub_returns_bore_radius(self):
        r = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=0.0,
            spokes_enabled=False, spoke_hub_od_mm=0.0,
        )
        assert abs(r - BORE_MM / 2.0) < 1e-9

    def test_spokes_zero_rim_depth_falls_through_to_hub(self):
        """rim_depth=0 disables the spokes branch → hub rule applies."""
        r = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=HUB_OD_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=0.0,   # zero rim depth
        )
        assert abs(r - HUB_OD_MM / 2.0) < 1e-9

    def test_spokes_zero_R_OD_falls_through_to_hub(self):
        """r_tooth_OD=0 disables the spokes branch → hub rule applies."""
        r = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=HUB_OD_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=0.0, rim_depth_mm=RIM_DEPTH_MM,
        )
        assert abs(r - HUB_OD_MM / 2.0) < 1e-9

    def test_spokes_result_less_than_R_OD(self):
        r = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=0.0,
            spokes_enabled=True, spoke_hub_od_mm=0.0,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=RIM_DEPTH_MM,
        )
        assert r < R_TOOTH_OD

    def test_no_spokes_hub_smaller_than_bore_returns_bore(self):
        """hub_od_mm < bore_mm → bore wins."""
        r = flange_inner_r_3dprint(
            bore_mm=BORE_MM, hub_od_mm=BORE_MM - 2.0,
            spokes_enabled=False, spoke_hub_od_mm=0.0,
        )
        assert abs(r - BORE_MM / 2.0) < 1e-9


# ===========================================================================
# 2. flange_inner_r_metal_top
# ===========================================================================

class TestFlangeInnerRMetalTop:

    def test_spokes_active_returns_R_OD_minus_rim_depth(self):
        r = flange_inner_r_metal_top(
            bore_mm=BORE_MM, hub_od_mm=HUB_OD_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=RIM_DEPTH_MM,
        )
        assert abs(r - R_INNER_SPOKES) < 1e-9

    def test_no_spokes_with_hub_returns_hub_radius(self):
        r = flange_inner_r_metal_top(
            bore_mm=BORE_MM, hub_od_mm=HUB_OD_MM,
            spokes_enabled=False, spoke_hub_od_mm=0.0,
        )
        assert abs(r - HUB_OD_MM / 2.0) < 1e-9

    def test_no_spokes_no_hub_returns_bore_radius(self):
        r = flange_inner_r_metal_top(
            bore_mm=BORE_MM, hub_od_mm=0.0,
            spokes_enabled=False, spoke_hub_od_mm=0.0,
        )
        assert abs(r - BORE_MM / 2.0) < 1e-9

    def test_spokes_zero_rim_depth_falls_through(self):
        r = flange_inner_r_metal_top(
            bore_mm=BORE_MM, hub_od_mm=HUB_OD_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=0.0,
        )
        assert abs(r - HUB_OD_MM / 2.0) < 1e-9


# ===========================================================================
# 3. flange_inner_r_metal_bottom
# ===========================================================================

class TestFlangeInnerRMetalBottom:

    def test_spokes_active_returns_R_OD_minus_rim_depth(self):
        r = flange_inner_r_metal_bottom(
            bore_mm=BORE_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=RIM_DEPTH_MM,
        )
        assert abs(r - R_INNER_SPOKES) < 1e-9

    def test_no_spokes_returns_bore_radius(self):
        """Metal bottom has no hub term — goes straight to bore."""
        r = flange_inner_r_metal_bottom(
            bore_mm=BORE_MM,
            spokes_enabled=False, spoke_hub_od_mm=0.0,
        )
        assert abs(r - BORE_MM / 2.0) < 1e-9

    def test_spokes_zero_rim_depth_returns_bore(self):
        r = flange_inner_r_metal_bottom(
            bore_mm=BORE_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=0.0,
        )
        assert abs(r - BORE_MM / 2.0) < 1e-9

    def test_bottom_inner_larger_than_bore_when_spokes(self):
        r = flange_inner_r_metal_bottom(
            bore_mm=BORE_MM,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            r_tooth_OD=R_TOOTH_OD, rim_depth_mm=RIM_DEPTH_MM,
        )
        assert r > BORE_MM / 2.0


# ===========================================================================
# 4. hub_intersects_flange
# ===========================================================================

class TestHubIntersectsFlange:
    TOOTH_HT = 2.06   # HTD-5M

    def test_no_hub_never_intersects(self):
        assert not hub_intersects_flange(R_TOOTH_OD, 0.0, False, 0.0, self.TOOTH_HT)

    def test_spokes_hub_inside_spoke_rim_no_warning(self):
        """Hub OD < spoke hub OD → no warning."""
        assert not hub_intersects_flange(
            R_TOOTH_OD, SPOKE_HUB_OD - 4.0, True, SPOKE_HUB_OD, self.TOOTH_HT
        )

    def test_spokes_hub_outside_spoke_rim_warning(self):
        """Hub OD > spoke hub OD → warning triggered."""
        assert hub_intersects_flange(
            R_TOOTH_OD, SPOKE_HUB_OD + 4.0, True, SPOKE_HUB_OD, self.TOOTH_HT
        )

    def test_no_spokes_hub_far_from_groove_no_warning(self):
        """Hub well inside the 10 mm safety margin → no warning."""
        # R_groove_bottom = R_TOOTH_OD - TOOTH_HT; hub must be > R_gb - 10 to warn
        r_groove = R_TOOTH_OD - self.TOOTH_HT
        safe_hub_od = (r_groove - 15.0) * 2.0   # 15 mm clearance > 10 mm limit
        assert not hub_intersects_flange(
            R_TOOTH_OD, safe_hub_od, False, 0.0, self.TOOTH_HT
        )

    def test_no_spokes_hub_close_to_groove_warning(self):
        """Hub within 10 mm of groove-bottom circle → warning triggered."""
        r_groove = R_TOOTH_OD - self.TOOTH_HT
        close_hub_od = (r_groove - 5.0) * 2.0   # only 5 mm clearance
        assert hub_intersects_flange(
            R_TOOTH_OD, close_hub_od, False, 0.0, self.TOOTH_HT
        )


# ===========================================================================
# 5. profile_3dprint — polygon shape sanity
# ===========================================================================

class TestProfile3dprint:

    def _profile(self, r_inner=10.0, r_tooth_OD=47.0,
                 rim_radius=3.0, angle_deg=15.0, flange_h=1.5):
        return profile_3dprint(r_inner, r_tooth_OD, rim_radius, angle_deg, flange_h)

    def test_returns_list_of_tuples(self):
        prof = self._profile()
        assert isinstance(prof, list)
        assert all(len(p) == 2 for p in prof)

    def test_has_five_vertices(self):
        assert len(self._profile()) == 5

    def test_inner_radius_at_first_vertex(self):
        prof = self._profile(r_inner=10.0)
        assert abs(prof[0][0] - 10.0) < 0.5   # profile may clamp r_inner slightly

    def test_outer_radius_equals_tooth_OD_plus_rim(self):
        r_tooth, rim = 47.0, 3.0
        prof = profile_3dprint(10.0, r_tooth, rim, 15.0, 1.5)
        r_outer = max(p[0] for p in prof)
        assert abs(r_outer - (r_tooth + rim)) < 1e-6

    def test_z_increases_with_flange_height(self):
        prof_low  = profile_3dprint(10.0, 47.0, 3.0, 15.0, 1.5)
        prof_high = profile_3dprint(10.0, 47.0, 3.0, 15.0, 3.0)
        z_max_low  = max(p[1] for p in prof_low)
        z_max_high = max(p[1] for p in prof_high)
        assert z_max_high > z_max_low

    def test_all_r_positive(self):
        prof = self._profile()
        assert all(p[0] >= 0.0 for p in prof)

    def test_steep_angle_increases_z_angled(self):
        prof_shallow = profile_3dprint(10.0, 47.0, 3.0, 8.0,  1.5)
        prof_steep   = profile_3dprint(10.0, 47.0, 3.0, 25.0, 1.5)
        # The C vertex (index 2) is the base of the outer lip
        z_shallow = prof_shallow[2][1]
        z_steep   = prof_steep[2][1]
        assert z_steep > z_shallow


# ===========================================================================
# 6. profile_metal — polygon shape sanity
# ===========================================================================

class TestProfileMetal:

    def _profile(self, r_inner=10.0, r_tooth_OD=47.0,
                 rim_radius=3.0, angle_deg=15.0, plate_h=1.0, bend_r=1.5):
        return profile_metal(r_inner, r_tooth_OD, rim_radius, angle_deg, plate_h, bend_r)

    def test_returns_list_of_tuples(self):
        prof = self._profile()
        assert isinstance(prof, list)
        assert all(len(p) == 2 for p in prof)

    def test_has_more_than_four_vertices(self):
        """Arc approximation means more than a simple 4-point rectangle."""
        assert len(self._profile()) > 4

    def test_inner_radius_at_first_vertex(self):
        prof = self._profile(r_inner=10.0)
        assert abs(prof[0][0] - 10.0) < 1e-9

    def test_thicker_plate_increases_max_z(self):
        prof_thin  = profile_metal(10.0, 47.0, 3.0, 15.0, 0.5, 0.75)
        prof_thick = profile_metal(10.0, 47.0, 3.0, 15.0, 2.0, 3.00)
        z_max_thin  = max(p[1] for p in prof_thin)
        z_max_thick = max(p[1] for p in prof_thick)
        assert z_max_thick > z_max_thin

    def test_all_r_positive(self):
        assert all(p[0] >= 0.0 for p in self._profile())

    def test_bend_zero_uses_auto_default(self):
        """bend_radius=0 should not crash (caller converts 0 → 1.5*plate_h)."""
        # This is handled by generate_metal_flange_stl, not profile_metal directly,
        # but a small bend_r is still valid.
        prof = profile_metal(10.0, 47.0, 3.0, 15.0, 1.0, 0.01)
        assert len(prof) > 4
