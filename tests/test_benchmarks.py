"""
test_benchmarks.py — pytest-benchmark suite for the Pulley Generator.

Run benchmarks:
    pytest tests/test_benchmarks.py --benchmark-only

Compare against a saved baseline:
    pytest tests/test_benchmarks.py --benchmark-compare

Save the current run as a new baseline:
    pytest tests/test_benchmarks.py --benchmark-save=baseline

Each test uses a fixed, representative input so results are comparable
across versions.  Slow operations (STL, preview) use one iteration;
fast operations (SVG, DXF) run at pytest-benchmark's default iteration count.
"""
import pytest

from exporters.svg_exporter    import generate_svg
from exporters.dxf_exporter    import generate_dxf
from exporters.step_exporter   import generate_pulley_stl, generate_pulley_stl_preview
from exporters.flange_exporter import generate_3dprint_flange_stl, generate_metal_flange_stl

# ---------------------------------------------------------------------------
# Representative fixed inputs
# ---------------------------------------------------------------------------

# 2D base params (SVG, DXF)
SMALL_2D  = dict(family='HTD', pitch='5M', num_teeth=20, bore_mm=8.0)
MEDIUM_2D = dict(family='HTD', pitch='5M', num_teeth=40, bore_mm=10.0)

# 3D base params (STL, preview, flange)
SMALL  = dict(**SMALL_2D,  belt_height_mm=10.0)
MEDIUM = dict(**MEDIUM_2D, belt_height_mm=15.0)
LARGE  = dict(family='HTD', pitch='5M', num_teeth=80, bore_mm=12.0,
              belt_height_mm=15.0)

# Hub params added on top of MEDIUM
HUB = dict(hub_od_mm=25.0, hub_height_mm=10.0, screw_dia_mm=3.0, screw_count=2)

# Spoke params added on top of MEDIUM
SPOKES = dict(spoke_count=5, spoke_width_mm=8.0, spoke_hub_od_mm=25.0,
              fillet_tip_mm=3.0, fillet_base_mm=2.0,
              rim_depth_mm=5.0, spoke_height_mm=10.0)

# Flange params (3D-print)
FLANGE_3DP = dict(flange_angle_deg=15.0, rim_radius_mm=3.0, flange_height_mm=1.5)

# Flange params (metal)
FLANGE_METAL = dict(flange_angle_deg=10.0, plate_height_mm=1.5, bend_radius_mm=3.0)


# ---------------------------------------------------------------------------
# SVG — pure Python, should be <50 ms
# ---------------------------------------------------------------------------

def test_svg_small(benchmark):
    benchmark(generate_svg, **SMALL_2D)


def test_svg_medium(benchmark):
    benchmark(generate_svg, **MEDIUM_2D)


def test_svg_medium_with_spokes(benchmark):
    benchmark(generate_svg, spoke_count=5, spoke_width_mm=8.0,
              spoke_hub_od_mm=25.0, rim_depth_mm=5.0, **MEDIUM_2D)


# ---------------------------------------------------------------------------
# DXF — ezdxf, should be <100 ms
# ---------------------------------------------------------------------------

def test_dxf_small(benchmark):
    benchmark(generate_dxf, **SMALL_2D)


def test_dxf_medium(benchmark):
    benchmark(generate_dxf, **MEDIUM_2D)


def test_dxf_medium_with_spokes(benchmark):
    benchmark(generate_dxf, spoke_count=5, spoke_width_mm=8.0,
              spoke_hub_od_mm=25.0, rim_depth_mm=5.0, **MEDIUM_2D)


# ---------------------------------------------------------------------------
# STL — trimesh + manifold3d booleans; slowest server operation
# ---------------------------------------------------------------------------

def test_stl_small(benchmark):
    benchmark.pedantic(generate_pulley_stl, kwargs=SMALL,
                       rounds=3, warmup_rounds=1)


def test_stl_medium(benchmark):
    benchmark.pedantic(generate_pulley_stl, kwargs=MEDIUM,
                       rounds=3, warmup_rounds=1)


def test_stl_large(benchmark):
    benchmark.pedantic(generate_pulley_stl, kwargs=LARGE,
                       rounds=3, warmup_rounds=1)


def test_stl_medium_with_hub(benchmark):
    kwargs = {**MEDIUM, **HUB}
    benchmark.pedantic(generate_pulley_stl, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)


def test_stl_medium_with_spokes(benchmark):
    kwargs = {**MEDIUM, **SPOKES}
    benchmark.pedantic(generate_pulley_stl, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)


def test_stl_medium_hub_and_spokes(benchmark):
    kwargs = {**MEDIUM, **HUB, **SPOKES}
    benchmark.pedantic(generate_pulley_stl, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)


# ---------------------------------------------------------------------------
# STL preview — same pipeline, used by the 3D viewer
# ---------------------------------------------------------------------------

def test_preview_small(benchmark):
    benchmark.pedantic(generate_pulley_stl_preview, kwargs=SMALL,
                       rounds=3, warmup_rounds=1)


def test_preview_medium(benchmark):
    benchmark.pedantic(generate_pulley_stl_preview, kwargs=MEDIUM,
                       rounds=3, warmup_rounds=1)


def test_preview_medium_with_spokes(benchmark):
    kwargs = {**MEDIUM, **SPOKES}
    benchmark.pedantic(generate_pulley_stl_preview, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)


# ---------------------------------------------------------------------------
# Flange STL — revolution + boolean ops
# ---------------------------------------------------------------------------

def test_flange_3dprint_top(benchmark):
    kwargs = dict(**MEDIUM, **FLANGE_3DP, which='top')
    benchmark.pedantic(generate_3dprint_flange_stl, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)


def test_flange_3dprint_bottom(benchmark):
    kwargs = dict(**MEDIUM, **FLANGE_3DP, which='bottom')
    benchmark.pedantic(generate_3dprint_flange_stl, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)


def test_flange_3dprint_with_spokes(benchmark):
    kwargs = dict(**MEDIUM, **FLANGE_3DP, which='top',
                  spokes_enabled=True, spoke_hub_od_mm=25.0, rim_depth_mm=5.0)
    benchmark.pedantic(generate_3dprint_flange_stl, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)


def test_flange_metal_top(benchmark):
    kwargs = dict(**MEDIUM, **FLANGE_METAL, which='top')
    benchmark.pedantic(generate_metal_flange_stl, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)


def test_flange_metal_bottom(benchmark):
    kwargs = dict(**MEDIUM, **FLANGE_METAL, which='bottom')
    benchmark.pedantic(generate_metal_flange_stl, kwargs=kwargs,
                       rounds=3, warmup_rounds=1)
