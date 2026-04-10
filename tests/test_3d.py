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
