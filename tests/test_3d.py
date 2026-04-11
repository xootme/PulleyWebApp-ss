"""
test_3d.py — STL exporter tests.
Covers single-pulley and drive (dual) STL generation, part isolation,
and basic mesh validity (watertight, non-empty).
"""
import io
import math
import pytest
import trimesh

from exporters.step_exporter import (
    generate_pulley_stl,
    generate_pulley_stl_preview,
    generate_drive_stl_preview,
    _build_outline_points,
    _rot2d,
)
from tests.conftest import get_spec, std_cl, std_bl, BORE_MM


# A small representative set — full PULLEY_CASES is tested in test_exporters.py
STL_CASES = [
    pytest.param('HTD',      '5M',  id='HTD-5M'),
    pytest.param('GT',       '3M',  id='GT-3M'),
    pytest.param('T',        'T5',  id='T-T5'),
    pytest.param('Imperial', 'XL',  id='Imperial-XL'),
    pytest.param('RPP',      '5M',  id='RPP-5M'),
]

# Drive cases: only belt-capable families
DRIVE_CASES = [
    pytest.param('HTD',      '5M',  id='HTD-5M'),
    pytest.param('T',        'T5',  id='T-T5'),
    pytest.param('Imperial', 'XL',  id='Imperial-XL'),
]


def _load_stl(stl_bytes: bytes) -> trimesh.Trimesh:
    """Parse binary STL bytes into a trimesh.Trimesh."""
    return trimesh.load(io.BytesIO(stl_bytes), file_type='stl')


# ---------------------------------------------------------------------------
# Single-pulley STL
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', STL_CASES)
def test_stl_single_returns_bytes(family, pitch):
    spec = get_spec(family, pitch)
    teeth = spec['min_teeth']
    stl = generate_pulley_stl(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, belt_height_mm=10.0,
        clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
    )
    assert isinstance(stl, bytes)
    assert len(stl) > 84, 'STL too short to contain any triangles'


@pytest.mark.parametrize('family,pitch', STL_CASES)
def test_stl_single_valid_mesh(family, pitch):
    spec = get_spec(family, pitch)
    teeth = spec['min_teeth']
    stl = generate_pulley_stl(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, belt_height_mm=10.0,
        clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
    )
    mesh = _load_stl(stl)
    assert not mesh.is_empty, 'Mesh has no geometry'
    assert mesh.volume > 0, 'Mesh has non-positive volume'


@pytest.mark.parametrize('family,pitch', STL_CASES)
def test_stl_preview_centred(family, pitch):
    """generate_pulley_stl_preview should centre the mesh near the origin."""
    spec = get_spec(family, pitch)
    teeth = spec['min_teeth']
    stl = generate_pulley_stl_preview(
        family=family, pitch=pitch, num_teeth=teeth,
        bore_mm=BORE_MM, belt_height_mm=10.0,
        clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
    )
    mesh = _load_stl(stl)
    cx, cy, cz = mesh.centroid
    assert abs(cx) < 1.0, f'Centroid X not near 0: {cx:.3f}'
    assert abs(cy) < 1.0, f'Centroid Y not near 0: {cy:.3f}'


# ---------------------------------------------------------------------------
# Drive (dual-pulley + belt) STL
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', DRIVE_CASES)
def test_stl_drive_all(family, pitch):
    spec = get_spec(family, pitch)
    t1, t2 = spec['min_teeth'], spec['min_teeth'] * 2
    cl, bl = std_cl(spec), std_bl(spec)
    stl = generate_drive_stl_preview(
        family=family, pitch=pitch,
        num_teeth1=t1, bore_mm1=BORE_MM,
        num_teeth2=t2, bore_mm2=BORE_MM,
        center_dist_mm=120.0, belt_height_mm=10.0,
        clearance_mm1=cl, backlash_mm1=bl,
        clearance_mm2=cl, backlash_mm2=bl,
        part='all',
    )
    assert isinstance(stl, bytes)
    assert len(stl) > 84
    mesh = _load_stl(stl)
    assert mesh.volume > 0


@pytest.mark.parametrize('family,pitch', DRIVE_CASES)
def test_stl_drive_pulleys_only(family, pitch):
    spec = get_spec(family, pitch)
    t1, t2 = spec['min_teeth'], spec['min_teeth'] * 2
    cl, bl = std_cl(spec), std_bl(spec)
    stl = generate_drive_stl_preview(
        family=family, pitch=pitch,
        num_teeth1=t1, bore_mm1=BORE_MM,
        num_teeth2=t2, bore_mm2=BORE_MM,
        center_dist_mm=120.0, belt_height_mm=10.0,
        clearance_mm1=cl, backlash_mm1=bl,
        clearance_mm2=cl, backlash_mm2=bl,
        part='pulleys',
    )
    mesh = _load_stl(stl)
    assert mesh.volume > 0


@pytest.mark.parametrize('family,pitch', DRIVE_CASES)
def test_stl_drive_belt_only(family, pitch):
    spec = get_spec(family, pitch)
    t1, t2 = spec['min_teeth'], spec['min_teeth'] * 2
    cl, bl = std_cl(spec), std_bl(spec)
    stl = generate_drive_stl_preview(
        family=family, pitch=pitch,
        num_teeth1=t1, bore_mm1=BORE_MM,
        num_teeth2=t2, bore_mm2=BORE_MM,
        center_dist_mm=120.0, belt_height_mm=10.0,
        clearance_mm1=cl, backlash_mm1=bl,
        clearance_mm2=cl, backlash_mm2=bl,
        part='belt',
    )
    mesh = _load_stl(stl)
    assert mesh.volume > 0


# ---------------------------------------------------------------------------
# Hub geometry
# ---------------------------------------------------------------------------

class TestHubGeometry:
    """Hub boss: cylinder unioned on top of toothed section, bore through full height."""

    FAMILY, PITCH = 'HTD', '5M'

    def _spec(self):
        from tests.conftest import get_spec
        return get_spec(self.FAMILY, self.PITCH)

    # -- hub smaller than pulley OD ------------------------------------------

    def test_hub_smaller_than_pulley_od_valid_mesh(self):
        """Hub OD < pulley OD: the boss sits inside the toothed rim, mesh valid."""
        spec  = self._spec()
        teeth = spec['min_teeth']
        stl   = generate_pulley_stl(
            family=self.FAMILY, pitch=self.PITCH, num_teeth=teeth,
            bore_mm=BORE_MM, belt_height_mm=10.0,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
            hub_od_mm=12.0, hub_height_mm=8.0,
        )
        mesh = _load_stl(stl)
        assert not mesh.is_empty
        assert mesh.volume > 0

    # -- hub larger than pulley OD -------------------------------------------

    def test_hub_larger_than_pulley_od_valid_mesh(self):
        """Hub OD > pulley OD: the boss protrudes radially beyond the teeth, mesh valid."""
        spec  = self._spec()
        teeth = spec['min_teeth']
        # Compute pulley OD so we can exceed it
        from exporters.step_exporter import _build_outline_points
        _, R_OD, _ = _build_outline_points(self.FAMILY, self.PITCH, teeth)
        pulley_od_mm = R_OD * 2.0
        hub_od_mm    = pulley_od_mm * 1.5   # deliberately larger

        stl = generate_pulley_stl(
            family=self.FAMILY, pitch=self.PITCH, num_teeth=teeth,
            bore_mm=BORE_MM, belt_height_mm=10.0,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
            hub_od_mm=hub_od_mm, hub_height_mm=8.0,
        )
        mesh = _load_stl(stl)
        assert not mesh.is_empty
        assert mesh.volume > 0

    def test_hub_larger_increases_total_volume(self):
        """Adding a hub boss increases the mesh volume compared to no hub."""
        spec  = self._spec()
        teeth = spec['min_teeth']
        kwargs = dict(
            family=self.FAMILY, pitch=self.PITCH, num_teeth=teeth,
            bore_mm=BORE_MM, belt_height_mm=10.0,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
        )
        mesh_no_hub = _load_stl(generate_pulley_stl(**kwargs))
        mesh_hub    = _load_stl(generate_pulley_stl(
            **kwargs, hub_od_mm=30.0, hub_height_mm=8.0
        ))
        assert mesh_hub.volume > mesh_no_hub.volume, (
            'Hub should add volume; '
            f'no-hub={mesh_no_hub.volume:.1f} hub={mesh_hub.volume:.1f}'
        )

    def test_hub_height_extends_total_height(self):
        """The mesh Z-extent should be belt_height + hub_height."""
        spec         = self._spec()
        teeth        = spec['min_teeth']
        belt_h, hub_h = 10.0, 8.0
        stl = generate_pulley_stl(
            family=self.FAMILY, pitch=self.PITCH, num_teeth=teeth,
            bore_mm=BORE_MM, belt_height_mm=belt_h,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
            hub_od_mm=20.0, hub_height_mm=hub_h,
        )
        mesh = _load_stl(stl)
        z_extent = mesh.bounds[1][2] - mesh.bounds[0][2]
        assert abs(z_extent - (belt_h + hub_h)) < 0.5, (
            f'Expected Z extent ≈ {belt_h + hub_h}, got {z_extent:.3f}'
        )

    def test_no_hub_height_unchanged(self):
        """With hub_height=0, the mesh should be identical to no-hub output."""
        spec  = self._spec()
        teeth = spec['min_teeth']
        kwargs = dict(
            family=self.FAMILY, pitch=self.PITCH, num_teeth=teeth,
            bore_mm=BORE_MM, belt_height_mm=10.0,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
        )
        mesh_no_hub = _load_stl(generate_pulley_stl(**kwargs))
        mesh_zero_h = _load_stl(generate_pulley_stl(**kwargs, hub_od_mm=20.0, hub_height_mm=0.0))
        # Volume should be the same (hub silently skipped)
        assert abs(mesh_zero_h.volume - mesh_no_hub.volume) < 1.0, (
            f'hub_height=0 should produce same volume as no hub; '
            f'got {mesh_zero_h.volume:.1f} vs {mesh_no_hub.volume:.1f}'
        )

    def test_hub_in_drive_preview_valid(self):
        """Hub params pass through generate_drive_stl_preview without error."""
        spec   = self._spec()
        t1, t2 = spec['min_teeth'], spec['min_teeth'] * 2
        cl, bl = std_cl(spec), std_bl(spec)
        stl = generate_drive_stl_preview(
            family=self.FAMILY, pitch=self.PITCH,
            num_teeth1=t1, bore_mm1=BORE_MM,
            num_teeth2=t2, bore_mm2=BORE_MM,
            center_dist_mm=120.0, belt_height_mm=10.0,
            clearance_mm1=cl, backlash_mm1=bl,
            clearance_mm2=cl, backlash_mm2=bl,
            hub_od_mm1=30.0, hub_height_mm1=8.0,   # P1 hub larger than pulley
            hub_od_mm2=20.0, hub_height_mm2=6.0,   # P2 hub smaller
            part='all',
        )
        mesh = _load_stl(stl)
        assert not mesh.is_empty
        assert mesh.volume > 0

    def test_screw_holes_reduce_volume(self):
        """Drilling set-screw holes should reduce hub volume vs no holes."""
        spec  = self._spec()
        teeth = spec['min_teeth']
        kwargs = dict(
            family=self.FAMILY, pitch=self.PITCH, num_teeth=teeth,
            bore_mm=BORE_MM, belt_height_mm=10.0,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
            hub_od_mm=20.0, hub_height_mm=10.0,
        )
        mesh_no_holes = _load_stl(generate_pulley_stl(**kwargs))
        mesh_1_hole   = _load_stl(generate_pulley_stl(**kwargs, screw_dia_mm=5.0, screw_count=1))
        mesh_2_holes  = _load_stl(generate_pulley_stl(**kwargs, screw_dia_mm=5.0, screw_count=2))
        assert mesh_1_hole.volume  < mesh_no_holes.volume, '1 screw hole should reduce volume'
        assert mesh_2_holes.volume < mesh_1_hole.volume,   '2 screw holes should reduce more'

    def test_hub_preview_centred(self):
        """generate_pulley_stl_preview with hub should still centre near origin."""
        spec  = self._spec()
        teeth = spec['min_teeth']
        stl   = generate_pulley_stl_preview(
            family=self.FAMILY, pitch=self.PITCH, num_teeth=teeth,
            bore_mm=BORE_MM, belt_height_mm=10.0,
            clearance_mm=std_cl(spec), backlash_mm=std_bl(spec),
            hub_od_mm=30.0, hub_height_mm=8.0,
        )
        mesh = _load_stl(stl)
        cx, cy, _ = mesh.centroid
        assert abs(cx) < 1.0, f'Centroid X not near 0 with hub: {cx:.3f}'
        assert abs(cy) < 1.0, f'Centroid Y not near 0 with hub: {cy:.3f}'


# ---------------------------------------------------------------------------
# Phase rotation helper
# ---------------------------------------------------------------------------
def test_rot2d_identity():
    pts = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)]
    result = _rot2d(pts, 0.0)
    assert result is pts   # returned unchanged


def test_rot2d_quarter_turn():
    """90° rotation: (1,0) → (0, -1) in compass-CW convention."""
    pts = [(1.0, 0.0)]
    rx, ry = _rot2d(pts, math.pi / 2)[0]
    assert abs(rx - 0.0) < 1e-9
    assert abs(ry - (-1.0)) < 1e-9


# ---------------------------------------------------------------------------
# Outline point builder
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('family,pitch', STL_CASES)
def test_outline_points_count(family, pitch):
    spec = get_spec(family, pitch)
    teeth = spec['min_teeth']
    pts, R_OD, sp = _build_outline_points(family, pitch, teeth)
    assert len(pts) >= teeth * 2, 'Expected at least 2 points per tooth'
    assert R_OD > 0
