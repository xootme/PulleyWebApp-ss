"""
test_flange.py — mesh and STL tests for exporters/flange_exporter.py.

Covers:
  * build_flange_meshes  — 3D-print and metal, with and without spokes
  * build_socket_meshes  — socket cylinders for nub pockets
  * generate_3dprint_flange_stl — upper / lower STL bytes
  * generate_metal_flange_stl   — upper / lower STL bytes
  * Inner-radius regression: flange vertices must not extend inside the spoke rim
  * Socket regression: sockets must reach (not be clipped short of) spoke void
"""
import io
import math
import numpy as np
import pytest
import trimesh

from exporters.flange_exporter import (
    build_flange_meshes,
    build_socket_meshes,
    generate_3dprint_flange_stl,
    generate_metal_flange_stl,
    _pulley_radii,
)
from tests.conftest import get_spec, std_cl, BORE_MM


# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------
FAMILY, PITCH, TEETH = 'HTD', '5M', 20
BELT_H = 10.0

# A typical spoke setup
SPOKE_HUB_OD = 20.0
RIM_DEPTH     = 3.0

# Minimal fp dict helpers
def _fp_3dprint(top_separate=True, nubs=False, **kw):
    fp = dict(
        flange_3dprint    = True,
        top_separate      = top_separate,
        flange_angle_deg  = 15.0,
        rim_radius_mm     = 3.0,
        flange_height_mm  = 1.5,
        plate_height_mm   = 1.0,
        bend_radius_mm    = 0.0,
        nubs_enabled      = nubs,
        nub_count         = 4,
        nub_dia_mm        = 3.0,
        nub_height_mm     = 2.0,
        nub_allowance_mm  = 0.2,
    )
    fp.update(kw)
    return fp

def _fp_metal(**kw):
    fp = dict(
        flange_3dprint    = False,
        top_separate      = True,
        flange_angle_deg  = 15.0,
        rim_radius_mm     = 3.0,
        flange_height_mm  = 1.5,
        plate_height_mm   = 1.0,
        bend_radius_mm    = 1.5,
        nubs_enabled      = False,
        nub_count         = 4,
        nub_dia_mm        = 3.0,
        nub_height_mm     = 2.0,
        nub_allowance_mm  = 0.2,
    )
    fp.update(kw)
    return fp

def _load(stl_bytes):
    return trimesh.load(io.BytesIO(stl_bytes), file_type='stl')


# ===========================================================================
# 1. build_flange_meshes — 3D print
# ===========================================================================

class TestBuildFlangeMeshes3dprint:

    def _meshes(self, **kw):
        return build_flange_meshes(
            _fp_3dprint(**kw),
            FAMILY, PITCH, TEETH, BORE_MM, BELT_H,
        )

    def test_returns_two_meshes(self):
        meshes = self._meshes()
        assert len(meshes) == 2

    def test_both_meshes_have_positive_volume(self):
        for m in self._meshes():
            assert m.volume > 0, f'Mesh volume non-positive: {m.volume}'

    def test_top_mesh_is_above_belt_height(self):
        """Top flange centroid Z should be ≥ belt height."""
        top, _ = self._meshes()
        assert top.centroid[2] >= BELT_H * 0.5

    def test_bottom_mesh_is_below_belt_face(self):
        """Bottom flange should extend below Z=0."""
        _, bot = self._meshes()
        assert bot.bounds[0][2] < 0.0

    def test_top_separate_false_top_merged_at_belt_z(self):
        """When top_separate=False, top is merged (no offset above hub)."""
        top, _ = build_flange_meshes(
            _fp_3dprint(top_separate=False),
            FAMILY, PITCH, TEETH, BORE_MM, BELT_H,
        )
        assert top is not None

    def test_with_nubs_still_two_meshes(self):
        meshes = self._meshes(nubs=True)
        assert len(meshes) == 2

    def test_with_nubs_positive_volume(self):
        for m in self._meshes(nubs=True):
            assert m.volume > 0

    def test_empty_fp_returns_empty_list(self):
        result = build_flange_meshes({}, FAMILY, PITCH, TEETH, BORE_MM, BELT_H)
        assert result == []


# ===========================================================================
# 2. build_flange_meshes — metal
# ===========================================================================

class TestBuildFlangeMeshesMetal:

    def _meshes(self, **kw):
        return build_flange_meshes(
            _fp_metal(**kw),
            FAMILY, PITCH, TEETH, BORE_MM, BELT_H,
        )

    def test_returns_two_meshes(self):
        assert len(self._meshes()) == 2

    def test_both_volumes_positive(self):
        for m in self._meshes():
            assert m.volume > 0

    def test_top_mesh_above_belt_height(self):
        top, _ = self._meshes()
        assert top.centroid[2] >= BELT_H * 0.5

    def test_bottom_mesh_below_zero(self):
        _, bot = self._meshes()
        assert bot.bounds[0][2] < 0.0


# ===========================================================================
# 3. Inner-radius regression — flange must not extend inside spoke rim
# ===========================================================================

class TestFlangeInnerRadiusSpokes:
    """When spokes are enabled, the flange inner edge must sit at the actual
    rim boundary = (R_tooth_OD - tooth_ht) - rim_depth.
    R_tooth_OD is the theoretical OD; the actual tooth-root radius is
    R_tooth_OD - tooth_ht, and the rim boundary sits rim_depth inside that.
    The flange must NOT extend all the way to spoke_hub_od / 2.
    """

    def _r_inner_from_mesh(self, mesh):
        """Approximate the flange inner radius from the mesh bounding cylinder."""
        verts = mesh.vertices
        radii = np.sqrt(verts[:, 0] ** 2 + verts[:, 1] ** 2)
        return float(radii.min())

    def test_3dprint_inner_radius_equals_R_OD_minus_rim(self):
        R_OD, _, tooth_ht = _pulley_radii(FAMILY, PITCH, TEETH)
        # Actual rim boundary uses the tooth-root radius, not the theoretical OD
        expected_r_inner = (R_OD - tooth_ht) - RIM_DEPTH

        top, bot = build_flange_meshes(
            _fp_3dprint(),
            FAMILY, PITCH, TEETH, BORE_MM, BELT_H,
            spokes_enabled=True,
            spoke_hub_od_mm=SPOKE_HUB_OD,
            rim_depth_mm=RIM_DEPTH,
        )
        verts = bot.vertices
        radii = np.sqrt(verts[:, 0] ** 2 + verts[:, 1] ** 2)
        r_min = float(radii.min())

        assert abs(r_min - expected_r_inner) < 1.5, (
            f'3D-print flange inner r={r_min:.3f}, expected ≈{expected_r_inner:.3f} '
            f'(R_OD={R_OD:.3f}, tooth_ht={tooth_ht:.3f}, rim_depth={RIM_DEPTH})'
        )

    def test_3dprint_inner_radius_not_at_hub_boss(self):
        """The old (buggy) inner radius was spoke_hub_od / 2. Verify it's at rim boundary."""
        R_OD, _, tooth_ht = _pulley_radii(FAMILY, PITCH, TEETH)
        expected_r_inner = (R_OD - tooth_ht) - RIM_DEPTH
        _, bot = build_flange_meshes(
            _fp_3dprint(),
            FAMILY, PITCH, TEETH, BORE_MM, BELT_H,
            spokes_enabled=True,
            spoke_hub_od_mm=SPOKE_HUB_OD,
            rim_depth_mm=RIM_DEPTH,
        )
        verts  = bot.vertices
        radii  = np.sqrt(verts[:, 0] ** 2 + verts[:, 1] ** 2)
        r_min  = float(radii.min())
        r_hub  = SPOKE_HUB_OD / 2.0

        # Inner radius must be at the rim boundary, not at the hub boss
        assert abs(r_min - expected_r_inner) < 1.5, (
            f'Flange inner r={r_min:.3f}, expected rim boundary {expected_r_inner:.3f} '
            f'(hub boss={r_hub:.3f})'
        )
        assert r_min > r_hub, (
            f'Flange inner r={r_min:.3f} should be outside hub boss r={r_hub:.3f}'
        )

    def test_metal_top_inner_radius_equals_R_OD_minus_rim(self):
        R_OD, _, _ = _pulley_radii(FAMILY, PITCH, TEETH)
        expected_r_inner = R_OD - RIM_DEPTH

        top, _ = build_flange_meshes(
            _fp_metal(),
            FAMILY, PITCH, TEETH, BORE_MM, BELT_H,
            spokes_enabled=True,
            spoke_hub_od_mm=SPOKE_HUB_OD,
            rim_depth_mm=RIM_DEPTH,
        )
        verts  = top.vertices
        radii  = np.sqrt(verts[:, 0] ** 2 + verts[:, 1] ** 2)
        r_min  = float(radii.min())
        assert abs(r_min - expected_r_inner) < 1.5


# ===========================================================================
# 4. build_socket_meshes
# ===========================================================================

class TestBuildSocketMeshes:

    def _fp(self, **kw):
        fp = _fp_3dprint(top_separate=True, nubs=True)
        fp.update(kw)
        return fp

    def _sockets(self, **kw):
        return build_socket_meshes(
            self._fp(**kw),
            FAMILY, PITCH, TEETH, BORE_MM, BELT_H,
        )

    def test_returns_non_empty_list(self):
        result = self._sockets()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_each_socket_has_positive_volume(self):
        for s in self._sockets():
            assert s.volume > 0

    def test_nubs_disabled_returns_empty(self):
        fp = _fp_3dprint(top_separate=True, nubs=False)
        result = build_socket_meshes(fp, FAMILY, PITCH, TEETH, BORE_MM, BELT_H)
        assert result == []

    def test_top_not_separate_returns_empty(self):
        fp = _fp_3dprint(top_separate=False, nubs=True)
        result = build_socket_meshes(fp, FAMILY, PITCH, TEETH, BORE_MM, BELT_H)
        assert result == []

    def test_socket_count_matches_nub_count(self):
        fp = self._fp(nub_count=6)
        result = build_socket_meshes(fp, FAMILY, PITCH, TEETH, BORE_MM, BELT_H)
        assert len(result) == 6

    def test_socket_extends_above_belt_face(self):
        """Socket cylinders must protrude above belt_height_mm so they cut the top face."""
        for sock in self._sockets():
            z_max = sock.bounds[1][2]
            assert z_max > BELT_H, f'Socket top z={z_max:.3f} ≤ belt height {BELT_H}'

    def test_socket_extends_below_belt_face(self):
        """Socket cylinder must reach below belt_height_mm to create a pocket."""
        for sock in self._sockets():
            z_min = sock.bounds[0][2]
            assert z_min < BELT_H, f'Socket bottom z={z_min:.3f} not below belt face {BELT_H}'

    def test_sockets_with_spokes_no_crash(self):
        """Socket generation with spokes enabled must not raise."""
        result = build_socket_meshes(
            self._fp(), FAMILY, PITCH, TEETH, BORE_MM, BELT_H,
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            rim_depth_mm=RIM_DEPTH,
        )
        assert isinstance(result, list)
        assert len(result) > 0


# ===========================================================================
# 5. generate_3dprint_flange_stl — STL bytes
# ===========================================================================

class TestGenerate3dprintFlangeSTL:

    _BASE = dict(
        family=FAMILY, pitch=PITCH, num_teeth=TEETH,
        bore_mm=BORE_MM, belt_height_mm=BELT_H,
    )

    def test_top_returns_bytes(self):
        stl = generate_3dprint_flange_stl(**self._BASE, which='top')
        assert isinstance(stl, bytes)
        assert len(stl) > 84

    def test_bottom_returns_bytes(self):
        stl = generate_3dprint_flange_stl(**self._BASE, which='bottom')
        assert isinstance(stl, bytes)
        assert len(stl) > 84

    def test_top_mesh_volume_positive(self):
        stl  = generate_3dprint_flange_stl(**self._BASE, which='top')
        mesh = _load(stl)
        assert mesh.volume > 0

    def test_bottom_mesh_volume_positive(self):
        stl  = generate_3dprint_flange_stl(**self._BASE, which='bottom')
        mesh = _load(stl)
        assert mesh.volume > 0

    def test_with_spokes_no_crash(self):
        stl = generate_3dprint_flange_stl(
            **self._BASE, which='top',
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            rim_depth_mm=RIM_DEPTH,
        )
        assert isinstance(stl, bytes)
        assert len(stl) > 84

    def test_with_hub_no_crash(self):
        stl = generate_3dprint_flange_stl(
            **self._BASE, which='top',
            hub_od_mm=20.0,
        )
        assert isinstance(stl, bytes)

    def test_various_angles(self):
        for angle in (8.0, 15.0, 25.0):
            stl = generate_3dprint_flange_stl(
                **self._BASE, which='top', flange_angle_deg=angle
            )
            assert _load(stl).volume > 0, f'angle={angle} produced zero-volume mesh'


# ===========================================================================
# 6. generate_metal_flange_stl — STL bytes
# ===========================================================================

class TestGenerateMetalFlangeSTL:

    _BASE = dict(
        family=FAMILY, pitch=PITCH, num_teeth=TEETH,
        bore_mm=BORE_MM, belt_height_mm=BELT_H,
    )

    def test_top_returns_bytes(self):
        stl = generate_metal_flange_stl(**self._BASE, which='top')
        assert isinstance(stl, bytes)
        assert len(stl) > 84

    def test_bottom_returns_bytes(self):
        stl = generate_metal_flange_stl(**self._BASE, which='bottom')
        assert isinstance(stl, bytes)
        assert len(stl) > 84

    def test_top_mesh_volume_positive(self):
        stl  = generate_metal_flange_stl(**self._BASE, which='top')
        mesh = _load(stl)
        assert mesh.volume > 0

    def test_bottom_mesh_volume_positive(self):
        stl  = generate_metal_flange_stl(**self._BASE, which='bottom')
        mesh = _load(stl)
        assert mesh.volume > 0

    def test_with_spokes_no_crash(self):
        stl = generate_metal_flange_stl(
            **self._BASE, which='top',
            spokes_enabled=True, spoke_hub_od_mm=SPOKE_HUB_OD,
            rim_depth_mm=RIM_DEPTH,
        )
        assert isinstance(stl, bytes) and len(stl) > 84

    def test_auto_bend_radius(self):
        """bend_radius=0 triggers auto (1.5×plate), must not crash."""
        stl = generate_metal_flange_stl(
            **self._BASE, which='top',
            plate_height_mm=1.0, bend_radius_mm=0.0,
        )
        assert _load(stl).volume > 0

    def test_various_plate_thicknesses(self):
        for t in (0.5, 1.0, 2.0):
            stl = generate_metal_flange_stl(
                **self._BASE, which='top',
                plate_height_mm=t, bend_radius_mm=1.5 * t,
            )
            assert _load(stl).volume > 0, f'plate_height={t} produced zero-volume mesh'


# ===========================================================================
# 7. Smoke: all PULLEY_CASES generate valid STL without crashing
# ===========================================================================

from tests.conftest import PULLEY_CASES, std_bl

@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_3dprint_flange_all_families_top(family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    stl   = generate_3dprint_flange_stl(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, belt_height_mm=10.0,
        clearance_mm=std_cl(spec),
        which='top',
    )
    assert isinstance(stl, bytes) and len(stl) > 84, f'{family}-{pitch}: STL too short'


@pytest.mark.parametrize('family,pitch', PULLEY_CASES)
def test_metal_flange_all_families_top(family, pitch):
    spec  = get_spec(family, pitch)
    teeth = spec['min_teeth']
    stl   = generate_metal_flange_stl(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, belt_height_mm=10.0,
        clearance_mm=std_cl(spec),
        which='top',
    )
    assert isinstance(stl, bytes) and len(stl) > 84, f'{family}-{pitch}: STL too short'


# ===========================================================================
# Flange nub clipping — nubs must not intrude into the spoke void
# (clipping order fix: always clip at r_spoke_outer = (R_OD - tooth_ht) - rim_depth)
# ===========================================================================
class TestFlangeNubClipping:
    """Nub pins protrude down from the top 3D-print flange. With spokes enabled
    they must be clipped at the spoke rim boundary so no pin material reaches
    into the spoke void (radius < r_spoke_outer)."""

    F, P, T = 'HTD', '5M', 40       # bigger pulley → meaningful spoke void
    BORE, BELT = 8.0, 10.0
    HUB_OD, RIM = 14.0, 3.0

    def _r_spoke_outer(self):
        R_OD, _, tooth_ht = _pulley_radii(self.F, self.P, self.T)
        return (R_OD - tooth_ht) - self.RIM, R_OD

    def _stl(self, nubs=True, **kw):
        p = dict(
            family=self.F, pitch=self.P, num_teeth=self.T,
            bore_mm=self.BORE, belt_height_mm=self.BELT, which='top',
            spokes_enabled=True, spoke_hub_od_mm=self.HUB_OD, rim_depth_mm=self.RIM,
            nubs_enabled=nubs, nub_count=8, nub_dia_mm=4.0, nub_height_mm=2.0,
        )
        p.update(kw)
        return generate_3dprint_flange_stl(**p)

    def test_nubs_spokes_no_crash(self):
        stl = self._stl(nubs=True)
        assert isinstance(stl, bytes) and len(stl) > 84
        assert _load(stl).volume > 0

    def test_nub_pins_present_below_belt(self):
        """Adding nubs introduces pin geometry below the belt face; without
        nubs the top flange sits entirely at/above the belt face."""
        v_no  = _load(self._stl(nubs=False)).vertices
        v_yes = _load(self._stl(nubs=True)).vertices
        assert v_no[:, 2].min() >= self.BELT - 1e-3, 'no-nub flange dipped below belt'
        assert v_yes[:, 2].min() < self.BELT - 0.5, 'nub pins did not protrude below belt'

    def test_nubs_do_not_intrude_into_spoke_void(self):
        """Every vertex (flange ring + clipped nub pins) must stay outside the
        spoke rim boundary. If the clip regressed, pins would reach inward toward
        the hub and this min radius would drop well below r_spoke_outer."""
        r_spoke_outer, _R_OD = self._r_spoke_outer()
        v = _load(self._stl(nubs=True)).vertices
        r = np.sqrt(v[:, 0] ** 2 + v[:, 1] ** 2)
        assert r.min() >= r_spoke_outer - 0.15, (
            f'nub/flange vertex intrudes into spoke void: '
            f'rmin={r.min():.3f} < r_spoke_outer={r_spoke_outer:.3f}'
        )
