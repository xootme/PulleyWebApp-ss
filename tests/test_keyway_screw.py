"""
test_keyway_screw.py — regression guard for the keyway ↔ set-screw merge.

A set screw drilled along the keyway axis used to leave a free-standing tube of
material where the round screw cylinder crossed the rectangular keyway slot (the
keyway bore face never cut the screw's inner rim). small_step now merges the
keyway-aligned screw into the slot as a blind hole that breaks into the back wall
(build_keyway_screw_merge); a non-aligned 2nd screw's rim is cut into the bore arc.

This test asserts, for each keyway + screw config, that every solid is:
  * OCCT BRepCheck-valid,
  * a SINGLE shell (a free-standing tube would be a 2nd shell / disconnected),
  * free of dangling edges (every edge used by 2 faces).

Run:  .venv314\\Scripts\\python.exe tests\\test_keyway_screw.py
Env:  SMALL_STEP_BIN may point at the binary under test (else auto-detected).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from exporters.dxf_exporter import generate_dxf           # noqa: E402
from exporters.step_worker_ss import _build_pulley_cmd     # noqa: E402


def _find_binary() -> str | None:
    env = os.environ.get("SMALL_STEP_BIN")
    if env and os.path.isfile(env):
        return env
    exe = "small_step.exe" if os.name == "nt" else "small_step"
    for c in [
        _ROOT / "small_step" / "target" / "release" / exe,
        Path.home() / "Documents" / "small_step" / "target" / "release" / exe,
        Path.home() / "Documents" / "small_step" / "target" / "x86_64-pc-windows-gnu" / "release" / exe,
    ]:
        if c.is_file():
            return str(c)
    return None


def _find_occ_python() -> str | None:
    try:
        import OCP  # noqa: F401
        return sys.executable
    except Exception:
        pass
    p = Path.home() / "Documents" / "PulleyWebApp" / ".venv312" / "Scripts" / "python.exe"
    if p.is_file() and subprocess.run([str(p), "-c", "import OCP"], capture_output=True).returncode == 0:
        return str(p)
    return None


# Per-solid: BRepCheck valid + exactly one shell + zero free (dangling) edges.
_OCC_SCRIPT = r"""
import sys
from OCP.STEPControl import STEPControl_Reader
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
r = STEPControl_Reader(); r.ReadFile(sys.argv[1]); r.TransferRoots()
exp = TopExp_Explorer(r.OneShape(), TopAbs_SOLID)
n = bad = 0
while exp.More():
    s = exp.Current(); n += 1
    nsh = 0; she = TopExp_Explorer(s, TopAbs_SHELL)
    while she.More(): nsh += 1; she.Next()
    valid = BRepCheck_Analyzer(s).IsValid()
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(s, TopAbs_EDGE, TopAbs_FACE, m)
    free = sum(1 for i in range(1, m.Extent() + 1) if m.FindFromIndex(i).Extent() == 1)
    if not valid or nsh != 1 or free != 0:
        bad += 1
        print(f"solid{n-1}: valid={valid} shells={nsh} free_edges={free}")
    exp.Next()
print(f"solids={n} bad={bad}")
sys.exit(1 if bad or n == 0 else 0)
"""


# (name, keyway_w, keyway_h, screw_dia, screw_count, captured)
CASES = [
    ("wide_1screw",     3.0, 1.4, 4.0, 1, False),   # screw wider than keyway, merged
    ("wide_2screw",     3.0, 1.4, 4.0, 2, False),   # + a 90° screw cut into the bore arc
    ("wide_big_screw",  3.0, 1.4, 5.0, 1, False),
    ("narrow_screw",    6.0, 3.0, 6.0, 1, False),   # screw == keyway width -> narrow branch
    ("narrow_2screw",   6.0, 3.0, 5.0, 2, False),   # screw narrower than keyway + 90°
    ("captured_keyway", 3.0, 1.4, 4.0, 1, True),    # captured nut pocket + keyway
]


def _gen(binary: str, kw, kh, sd, sc, cap) -> tuple[int, bytes, str]:
    dxf = generate_dxf(
        family="HTD", pitch="8M", num_teeth=30, bore_mm=10.0,
        clearance_mm=0, backlash_mm=0, print_extra_mm=0,
        spoke_count=0, spoke_width_mm=0, spoke_hub_od_mm=0, rim_depth_mm=0,
        fillet_tip_mm=0, fillet_base_mm=0, flat_depth_mm=0, keyway_w_mm=0, keyway_h_mm=0,
    )
    if isinstance(dxf, str):
        dxf = dxf.encode()
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False, mode="wb") as f:
        dxf_tmp = f.name
        f.write(dxf)
    params = dict(
        family="HTD", pitch="8M", num_teeth=30, bore_mm=10.0, belt_height_mm=12.0,
        clearance_mm=0.3, backlash_mm=0.3, print_extra_mm=0.5, spoke_count=0,
        hub_od_mm=22.0, hub_height_mm=16.0, flat_depth_mm=0,
        keyway_w_mm=kw, keyway_h_mm=kh, screw_dia_mm=sd, screw_count=sc, captured_nut=cap,
        flange_enabled=False, nubs_enabled=False,
    )
    try:
        cmd = _build_pulley_cmd(params, binary, dxf_tmp)
        cmd[0] = binary
        r = subprocess.run(cmd, capture_output=True, env=dict(os.environ, SMALL_STEP_DIAG="1"))
        return r.returncode, r.stdout, r.stderr.decode(errors="replace")
    finally:
        os.unlink(dxf_tmp)


def _check_case(binary: str, occ_py: str | None, kw, kh, sd, sc, cap) -> tuple[bool, str]:
    """Generate one keyway+screw config and validate it. Returns (ok, note)."""
    rc, out, err = _gen(binary, kw, kh, sd, sc, cap)
    if rc != 0 or b"ISO-10303-21" not in out:
        return False, f"generation failed rc={rc} {err[:160]}"
    if not occ_py:
        return True, "OCC skipped (no interpreter)"
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False, mode="wb") as sf:
        step_tmp = sf.name
        sf.write(out)
    try:
        v = subprocess.run([occ_py, "-c", _OCC_SCRIPT, step_tmp], capture_output=True, text=True)
        if v.returncode != 0:
            return False, f"{v.stdout.strip()} {v.stderr[:160]}"
        return True, v.stdout.strip().splitlines()[-1]
    finally:
        os.unlink(step_tmp)


# One pytest item per config so coverage is visible in the test count.
import pytest  # noqa: E402

_BINARY = _find_binary()
_OCC_PY = _find_occ_python()


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_keyway_screw(case):
    if not _BINARY:
        pytest.skip("small_step binary not found (set SMALL_STEP_BIN)")
    name, kw, kh, sd, sc, cap = case
    ok, note = _check_case(_BINARY, _OCC_PY, kw, kh, sd, sc, cap)
    assert ok, f"{name}: {note}"


def run() -> int:
    binary = _find_binary()
    if not binary:
        print("SKIP: small_step binary not found (set SMALL_STEP_BIN)")
        return 0
    occ_py = _find_occ_python()
    print(f"binary: {binary}")
    print(f"OCC:    {occ_py or '(none — validity checks skipped)'}\n")
    failures = 0
    for name, kw, kh, sd, sc, cap in CASES:
        ok, note = _check_case(binary, occ_py, kw, kh, sd, sc, cap)
        print(f"[{'PASS' if ok else 'FAIL'}] {name:16s} {note}")
        failures += 0 if ok else 1
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
