"""
test_nub_socket_merge.py — regression guard for the socket<->void B-rep merge.

The small_step merge (build_merged_socket_void_solid_v2) fuses nub-socket pockets
into spoke voids so no thin "ribbon" of material is left between them. This test
guards three failure modes:

  1. Invalid solid    — assert OCCT BRepCheck validity (the kernel Fusion/FreeCAD
                        use) when an OCC-capable interpreter is available.
  2. Silent fallback  — the merge returning None falls back to the crescent path,
                        which silently re-introduces the ribbon. The binary emits
                        `merge: v2 applied|fallback` on stderr under SMALL_STEP_DIAG;
                        we assert the merge-relevant configs report *applied*.
  3. Generation fail  — assert a valid ISO-10303-21 STEP is produced.

Deterministic config matrix covers every merge branch: filleted / non-filleted
spokes, bridging sockets (over a web), sockets over voids, full-circle sockets
(no rim overlap -> no merge expected), clamped nub counts, and a non-HTD family.

Run:  .venv314\\Scripts\\python.exe tests\\test_nub_socket_merge.py
Env:  SMALL_STEP_BIN may point at the binary under test (else auto-detected).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from exporters.dxf_exporter import generate_dxf           # noqa: E402
from exporters.step_worker_ss import _build_pulley_cmd     # noqa: E402


# ── Binary + OCC interpreter discovery ─────────────────────────────────────────
def _find_binary() -> str | None:
    env = os.environ.get("SMALL_STEP_BIN")
    if env and os.path.isfile(env):
        return env
    exe = "small_step.exe" if os.name == "nt" else "small_step"
    candidates = [
        _ROOT / "small_step" / "target" / "release" / exe,
        Path.home() / "Documents" / "small_step" / "target" / "release" / exe,
        Path.home() / "Documents" / "small_step" / "target" / "x86_64-pc-windows-gnu" / "release" / exe,
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def _find_occ_python() -> str | None:
    """An interpreter that can `import OCP` (OCCT bindings)."""
    try:
        import OCP  # noqa: F401
        return sys.executable
    except Exception:
        pass
    for p in [
        Path.home() / "Documents" / "PulleyWebApp" / ".venv312" / "Scripts" / "python.exe",
    ]:
        if p.is_file():
            probe = subprocess.run([str(p), "-c", "import OCP"], capture_output=True)
            if probe.returncode == 0:
                return str(p)
    return None


_OCC_SCRIPT = r"""
import sys
from OCP.STEPControl import STEPControl_Reader
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID
r = STEPControl_Reader(); r.ReadFile(sys.argv[1]); r.TransferRoots()
exp = TopExp_Explorer(r.OneShape(), TopAbs_SOLID)
n = bad = 0
while exp.More():
    if not BRepCheck_Analyzer(exp.Current()).IsValid():
        bad += 1
    n += 1; exp.Next()
print(f"solids={n} invalid={bad}")
sys.exit(1 if bad or n == 0 else 0)
"""


# ── Config matrix ──────────────────────────────────────────────────────────────
def _base(**over) -> dict:
    d = dict(
        family="HTD", pitch="8M", num_teeth=45, bore_mm=18.0, belt_height_mm=7.2,
        clearance_mm=0.0, backlash_mm=0.0, print_extra_mm=0.0,
        spoke_count=5, spoke_width_mm=5.8, spoke_hub_od_mm=27.7, rim_depth_mm=7.0,
        fillet_tip_mm=1.0, fillet_base_mm=1.3, spoke_height_mm=0.0,
        hub_od_mm=27.7, hub_height_mm=10.5, keyway_w_mm=6.0, keyway_h_mm=3.1,
        screw_dia_mm=5.0, screw_count=1, captured_nut=False,
        flange_enabled=True, flange_3dprint=True, flange_angle_deg=19.2,
        flange_rim_radius_mm=3.5, flange_height_mm=1.3, plate_height_mm=3.0, bend_radius_mm=0.0,
        nubs_enabled=True, nub_count=20, nub_dia_mm=15.0, nub_height_mm=2.3, nub_allowance_mm=0.2,
    )
    d.update(over)
    return d


# (name, params, expect_merge, check_occ):
#   expect_merge=True  => sockets reach the voids and the v2 merge MUST engage
#                         (guards the ribbon returning).
#   expect_merge=False => sockets stay in the solid rim ring (full circle), no merge.
#   check_occ=False    => documented pre-existing issue: assert generation only.
CASES = [
    ("filleted_bridging",  _base(),                                              True,  True),
    ("non_filleted",       _base(fillet_tip_mm=0.0, fillet_base_mm=0.0),         True,  True),
    ("mid_nubs_d8_n16",    _base(nub_dia_mm=8.0, nub_count=16),                  True,  True),
    ("few_nubs_d6_n4",     _base(nub_dia_mm=6.0, nub_count=4),                   True,  True),
    ("gt5m_d10_n10",       _base(family="GT", pitch="5M", num_teeth=40, bore_mm=10.0,
                                 spoke_hub_od_mm=20.0, hub_od_mm=20.0,
                                 keyway_w_mm=0.0, keyway_h_mm=0.0,
                                 nub_dia_mm=10.0, nub_count=10),                 True,  True),
    ("tiny_nubs_d3_n12",   _base(nub_dia_mm=3.0, nub_count=12),                  False, True),
    ("tangent_d4_n10",     _base(nub_dia_mm=4.0, nub_count=10),                  False, True),
    # Partial-height (recessed) spokes go through build_partial_height_solid.
    # NARROW spokes (natural hub arc) are OCCT-valid WITH fillets (the cap's
    # tangent hub-arc↔fillet junction is split off onto a thin sub-face,
    # split_island_hub_arc) AND with nub sockets cut into the top rim ring. WIDE
    # spokes (fillet_base does not reach the hub) build the web as one connected
    # "star" cap with a hub hole; the rim-arc↔fillet tangency is split onto thin
    # rim slivers, and the notch is clipped off the hub so it never pinches the
    # hub hole — valid with or without fillets (see the wide cases below).
    #   - narrow + fillets + flange + nub sockets, fused (no ribbon) → OCCT-valid.
    #     nub_height < top-recess depth so the socket floor stays in the recess
    #     band (deeper sockets are guarded — see app/ToDo).
    ("partial_height_d15", _base(spoke_height_mm=5.0, nub_height_mm=0.8),        False, True),
    #   - narrow + no fillet / no flange / no nubs → OCCT-valid
    ("partial_height_plain", _base(spoke_height_mm=5.0,
                                   fillet_tip_mm=0.0, fillet_base_mm=0.0,
                                   flange_enabled=False, nubs_enabled=False),    False, True),
    #   - DEEP sockets: nub taller than the top recess so the socket floor drops
    #     into/through the web. The unified per-cell rim wall + web-level splits
    #     carve the notch and the socket↔web inner wall. → OCCT-valid.
    ("partial_height_deep", _base(spoke_height_mm=5.0, nub_height_mm=2.0),       False, True),
    # WIDE spokes (fillet_base does not reach the hub): connected star web cap.
    #   - filleted wide (the real "P2": 11 wide spokes on a hub_od 15) → valid
    ("partial_height_wide", _base(num_teeth=75, bore_mm=5.0, spoke_count=11,
                                  spoke_width_mm=7.0, spoke_hub_od_mm=15.0,
                                  hub_od_mm=15.0, rim_depth_mm=10.0,
                                  keyway_w_mm=0.0, keyway_h_mm=0.0, spoke_height_mm=5.0,
                                  flange_enabled=False, nubs_enabled=False),       False, True),
    #   - non-filleted wide: the flanks converge onto the hub; the notch is clipped
    #     off the hub so the cap outline never pinches its own hub hole → valid
    ("partial_height_wide_nofillet", _base(num_teeth=75, bore_mm=5.0, spoke_count=11,
                                  spoke_width_mm=7.0, spoke_hub_od_mm=15.0,
                                  hub_od_mm=15.0, rim_depth_mm=10.0,
                                  fillet_tip_mm=0.0, fillet_base_mm=0.0,
                                  keyway_w_mm=0.0, keyway_h_mm=0.0, spoke_height_mm=5.0,
                                  flange_enabled=False, nubs_enabled=False),       False, True),
    #   - wide spokes WITH flange + nub sockets (the recessed web carries the
    #     sockets too) → valid; exercises the wide path's socket handling.
    ("partial_height_wide_nubs", _base(num_teeth=75, bore_mm=5.0, spoke_count=11,
                                  spoke_width_mm=7.0, spoke_hub_od_mm=15.0,
                                  hub_od_mm=15.0, rim_depth_mm=10.0,
                                  keyway_w_mm=0.0, keyway_h_mm=0.0, spoke_height_mm=5.0,
                                  nub_dia_mm=6.0, nub_count=12, nub_height_mm=2.0),  False, True),
]


def _gen(binary: str, params: dict) -> tuple[int, bytes, str]:
    dxf = generate_dxf(
        family=params["family"], pitch=params["pitch"], num_teeth=params["num_teeth"],
        bore_mm=params["bore_mm"], clearance_mm=0, backlash_mm=0, print_extra_mm=0,
        spoke_count=params["spoke_count"], spoke_width_mm=params["spoke_width_mm"],
        spoke_hub_od_mm=params["spoke_hub_od_mm"], rim_depth_mm=params["rim_depth_mm"],
        fillet_tip_mm=params["fillet_tip_mm"], fillet_base_mm=params["fillet_base_mm"],
        flat_depth_mm=0.0, keyway_w_mm=0.0, keyway_h_mm=0.0,
    )
    if isinstance(dxf, str):
        dxf = dxf.encode()
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False, mode="wb") as f:
        dxf_tmp = f.name
        f.write(dxf)
    try:
        cmd = _build_pulley_cmd(params, binary, dxf_tmp)
        cmd[0] = binary
        env = dict(os.environ, SMALL_STEP_DIAG="1")
        r = subprocess.run(cmd, capture_output=True, env=env)
        return r.returncode, r.stdout, r.stderr.decode(errors="replace")
    finally:
        os.unlink(dxf_tmp)


def run() -> int:
    binary = _find_binary()
    if not binary:
        print("SKIP: small_step binary not found (set SMALL_STEP_BIN)")
        return 0
    occ_py = _find_occ_python()
    print(f"binary: {binary}")
    print(f"OCC:    {occ_py or '(none — validity checks skipped)'}\n")

    failures = 0
    for name, params, expect_merge, check_occ in CASES:
        rc, out, err = _gen(binary, params)
        if rc != 0 or b"ISO-10303-21" not in out:
            print(f"[FAIL] {name}: generation failed rc={rc} {err[:160]}")
            failures += 1
            continue

        diag = "applied" if "merge: v2 applied" in err else \
               "fallback" if "merge: v2 fallback" in err else "none"
        if expect_merge and diag != "applied":
            print(f"[FAIL] {name}: expected merge applied, got '{diag}' "
                  f"(ribbon may have returned)")
            failures += 1
            continue
        if not expect_merge and diag == "applied":
            print(f"[FAIL] {name}: merge unexpectedly engaged (expected full-circle path)")
            failures += 1
            continue

        nfaces = len(re.findall(rb"ADVANCED_FACE", out))
        occ_note = ""
        if occ_py and check_occ:
            with tempfile.NamedTemporaryFile(suffix=".step", delete=False, mode="wb") as sf:
                step_tmp = sf.name
                sf.write(out)
            try:
                v = subprocess.run([occ_py, "-c", _OCC_SCRIPT, step_tmp],
                                   capture_output=True, text=True)
                if v.returncode != 0:
                    print(f"[FAIL] {name}: OCC invalid — {v.stdout.strip()} {v.stderr[:160]}")
                    failures += 1
                    continue
                occ_note = f"OCC {v.stdout.strip()}"
            finally:
                os.unlink(step_tmp)
        elif not check_occ:
            occ_note = "OCC skipped (known pre-existing issue)"

        print(f"[PASS] {name:18s} merge={diag:8s} faces={nfaces:3d} {len(out):7d}B  {occ_note}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


def test_nub_socket_merge():
    """pytest entry point."""
    assert run() == 0


def test_wide_spoke_partial_height_valid():
    """Wide-spoke partial-height (fillet_base does not reach the hub) builds a
    connected star web cap and must emit a valid solid — not an open shell.
    Covered in the CASES matrix too; this is an explicit standalone guard.
    """
    binary = _find_binary()
    if not binary:
        import pytest
        pytest.skip("small_step binary not found")
    # HTD-8M-75T, hub_od 15, rim_depth 10, 11 wide spokes (the real "P2").
    params = _base(
        num_teeth=75, bore_mm=5.0, spoke_count=11, spoke_width_mm=7.0,
        spoke_hub_od_mm=15.0, hub_od_mm=15.0, rim_depth_mm=10.0,
        keyway_w_mm=0.0, keyway_h_mm=0.0, spoke_height_mm=5.0,
        flange_enabled=False, nubs_enabled=False,
    )
    rc, out, err = _gen(binary, params)
    assert rc == 0, f"wide-spoke partial-height should succeed: {err[:200]}"
    assert b"ISO-10303-21" in out


if __name__ == "__main__":
    sys.exit(run())
