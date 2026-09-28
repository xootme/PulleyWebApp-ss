"""
fuzz_pulley.py — randomized pulley-parameter fuzz tester (dev tool, not
pytest). Replaces tests/test_ss_validators.py.

Combines that file's retry-until-valid config generator (_raw_config /
_make_config below, ported verbatim — the domain constraints there are
proven and not worth rewriting into EBoxDesigner-ss's dependency-ordered
auto-correct style for a one-off replacement) with fuzz_ebox.py's
iterate-many / JSONL-log / persistent-worker architecture, and the shared
cct_common.step_check helpers both fuzz tools now use, so this file doesn't
duplicate its own copy of the OCCT worker protocol or wire-order parser.

Three independent per-STEP-file checks, same trio test_ss_validators.py
had minus eDrawings (see below):
  * check_wire_order — free, pure-stdlib EDGE_LOOP winding check.
  * OCCT validity + STEP-vs-STL volume agreement, via a persistent
    occt_server.py worker (PulleyWebApp .venv312 — cadquery/OCP has no
    wheel for this repo's main Python).
  * NIST SFA syntax check (sfa-cl.exe), a genuinely independent parser
    opinion from OCCT.

eDrawings dropped: test_ss_validators.py's validate_edrawings() was never
actually confirmed working — ported over unchanged here first, its
ctrl.OpenDoc(...) call was found to hang indefinitely (60s+, no exception,
no OnFinishedLoadingDocument/OnFailedLoadingDocument callback, no eDrawings
process spawned, no window). Most likely the ActiveX control needs to be
sited in a real parent window (in-place OLE activation) to do anything —
bare DispatchWithEvents with no window gives it nothing to activate
against. Left as a known gap rather than sunk further effort chasing COM
window-hosting plumbing for uncertain payoff.

Usage:
    .venv312\\Scripts\\python.exe fuzz_pulley.py --iterations 200
    .venv312\\Scripts\\python.exe fuzz_pulley.py --duration 300
    .venv312\\Scripts\\python.exe fuzz_pulley.py --seed 42

Writes a JSONL log (one record per iteration) to
fuzz_results/fuzz_log_<timestamp>.jsonl, prints a summary + first failure.
Exits 1 if any iteration failed.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

import trimesh

from cct_common.step_check import PersistentStepWorker, check_wire_order, validate_nist_sfa

_HERE = Path(__file__).parent
_SFA_EXE = _HERE / "tools" / "sfa" / "sfa-cl.exe"
# Matches app.py's own fallback default (release build) -- the debug build
# under this same target dir was found (by fuzzing this fuzz tool itself) to
# predate small_step's --version flag entirely, so it fails step_worker_ss.py's
# _check_binary_version on every call.
_SS_BIN_DEFAULT = Path(r"C:\Users\cmyer\Documents\small_step\target\x86_64-pc-windows-gnu\release\small_step.exe")

# OCCT worker (sibling repo's venv — see cct_common.step_check's own docstring)
_OCC_PY = Path(r"C:\Users\cmyer\Documents\PulleyWebApp\.venv312\Scripts\python.exe")
_OCCT_SERVER = Path(r"C:\Users\cmyer\Documents\small_step\archive\occt_server.py")
VOLUME_REL_TOL = 5e-3

sys.path.insert(0, str(_HERE))


def _get_ss_bin() -> str:
    p = os.environ.get("SMALL_STEP_BIN", str(_SS_BIN_DEFAULT))
    if not os.path.isfile(p):
        print(f"[error] small_step binary not found: {p}")
        print("        Set SMALL_STEP_BIN env var or build small_step first.")
        sys.exit(1)
    return p


# ── Config generation (ported verbatim from test_ss_validators.py) ────────

_FAMILIES = {
    'HTD': {'3M': (10, 80), '5M': (12, 80), '8M': (15, 60), '14M': (18, 50)},
    'GT':  {'2M': (10, 80), '3M': (10, 80), '5M': (12, 70), '8M':  (20, 50)},
    'STD': {'2M': (10, 80), '3M': (10, 80), '5M': (12, 70), '8M':  (22, 50)},
    # added 2026-09-27: the trapezoidal and parabolic families (Backlash Tight
    # now narrows their grooves), tooth ranges clamped to each profile's own
    # min_teeth in _raw_config
    'T':        {'T2.5': (12, 80), 'T5': (12, 70), 'T10': (14, 60), 'T20': (15, 50)},
    'AT':       {'AT3': (12, 80), 'AT5': (12, 70), 'AT10': (15, 60), 'AT20': (18, 50)},
    'Imperial': {'MXL': (12, 80), 'XL': (12, 70), 'L': (12, 60), 'H': (14, 50), 'XH': (18, 40)},
    'RPP':      {'3M': (12, 80), '5M': (12, 70), '8M': (15, 60), '14M': (18, 50)},
}
_CLEARANCE = ['TIGHT', 'STANDARD', 'LOOSE']
_BACKLASH = ['NONE', 'TIGHT', 'STANDARD', 'LOOSE']


def _raw_config(r: random.Random) -> dict:
    family = r.choice(list(_FAMILIES))
    pitch = r.choice(list(_FAMILIES[family]))
    t_min, t_max = _FAMILIES[family][pitch]
    from geometry.pulley_geometry import PROFILE_KEY_PREFIX, PULLEY_SPECS
    spec_min = PULLEY_SPECS[PROFILE_KEY_PREFIX.get(family, '') + pitch].get('min_teeth', t_min)
    t_min = max(t_min, spec_min)
    t_max = max(t_max, t_min)
    cfg = {
        'family': family,
        'pitch': pitch,
        'teeth': r.randint(t_min, t_max),
        'bore': round(r.uniform(4.0, 30.0), 1),
        'print_extra': round(r.uniform(0.0, 0.4), 2),
        'clearance_preset': r.choice(_CLEARANCE),
        'backlash_preset': r.choice(_BACKLASH),
        'belt_height': round(r.uniform(6.0, 25.0), 1),
        'clearance_height': round(r.uniform(0.0, 0.8), 2),
    }
    profile = r.choice(['round', 'dflat', 'keyway'])
    if profile == 'dflat':
        cfg['hub_flat_depth'] = round(r.uniform(0.3, float(cfg['bore']) * 0.25), 2)
    elif profile == 'keyway':
        kw = r.choice([2.0, 3.0, 4.0, 5.0, 6.0])
        cfg['hub_keyway_w'] = kw
        cfg['hub_keyway_h'] = round(kw * 0.5, 1)
    if r.random() > 0.2:
        hub_od = round(float(cfg['bore']) + r.uniform(4.0, 18.0), 1)
        cfg['hub_od'] = hub_od
        cfg['hub_height'] = round(r.uniform(3.0, 18.0), 1)
        if r.random() > 0.3:
            _add_set_screw(r, cfg)
    else:
        hub_od = 0.0
    if int(cfg['teeth']) >= 20 and r.random() > 0.4:
        sp_hub = hub_od if hub_od > float(cfg['bore']) else round(float(cfg['bore']) + r.uniform(2.0, 6.0), 1)
        cfg['spokes_enabled'] = '1'
        cfg['spokes_hub_od'] = sp_hub
        cfg['spokes_rim_depth'] = round(r.uniform(2.0, 7.0), 1)
        cfg['spokes_width'] = round(r.uniform(3.0, 8.0), 1)
        cfg['spokes_fillet_tip'] = round(r.uniform(0.5, 2.5), 1)
        cfg['spokes_fillet_base'] = round(r.uniform(1.0, 4.0), 1)
        cfg['spokes_count'] = r.choice([3, 4, 5, 6, 7])
        cfg['spokes_height'] = round(r.uniform(0.0, 5.0), 1)
    if r.random() > 0.3:
        is_3dp = r.random() > 0.4
        cfg['flange_enabled'] = '1'
        cfg['flange_3dprint'] = '1' if is_3dp else '0'
        cfg['flange_angle'] = round(r.uniform(8.0, 25.0), 1)
        cfg['flange_rim_radius'] = round(r.uniform(1.0, 8.0), 1)
        cfg['flange_height'] = round(r.uniform(0.5, 5.0), 1)
        cfg['flange_plate_height'] = round(r.uniform(0.5, 3.0), 1)
        cfg['flange_bend_radius'] = round(r.uniform(0.0, 3.0), 1)
        top_sep = r.random() > 0.3
        cfg['flange_top_separate'] = '1' if top_sep else '0'
        if is_3dp and top_sep and r.random() > 0.4:
            cfg['flange_nubs_enabled'] = '1'
            cfg['flange_nub_count'] = r.choice([2, 3, 4, 6])
            cfg['flange_nub_dia'] = round(r.uniform(2.0, 6.0), 1)
            cfg['flange_nub_height'] = round(r.uniform(1.0, 6.0), 1)
            cfg['flange_nub_allowance'] = round(r.uniform(0.1, 0.4), 2)
    return cfg


def _add_set_screw(r: random.Random, cfg: dict) -> None:
    """A hub set screw (ADR-013): any size or Custom, any hold, and the
    design's threaded-hole settings — the parameters the page sends."""
    from geometry.set_screw import INCH, METRIC
    from cct_common import screws
    size = r.choice(list(METRIC) + list(INCH) + ['Custom'])
    hold = r.choice(['thread', 'nut', 'insert'])
    major = round(r.uniform(2.0, 8.0), 2) if size == 'Custom' else screws.get(size).major_diameter
    cfg['hub_screw_size'] = size
    cfg['hub_screw_hold'] = hold
    cfg['hub_screw_count'] = r.choice([1, 2])
    cfg['hub_screw_dia'] = major
    cfg['hub_captured_nut'] = '1' if hold == 'nut' else '0'
    if size == 'Custom' or hold == 'insert':
        cfg['hub_screw_hole_dia'] = round(major * r.uniform(1.0, 1.45), 2)
    if hold == 'thread' and size != 'Custom':
        cfg['screw_hole_shape'] = r.choice(['round', 'hex'])
        cfg['thread_engagement'] = r.choice([0, 25, 50, 75, 100])
        cfg['hex_flat'] = r.choice([78, 82, 88, 92])


def _make_config(r: random.Random, max_attempts: int = 30) -> dict:
    from app import _parse_hub_params, _parse_spoke_params, _parse_stl_params
    from geometry.pulley_geometry import PROFILE_KEY_PREFIX, PULLEY_SPECS, getOuterDiameter
    last_err = None
    for _ in range(max_attempts):
        raw = _raw_config(r)
        qs = {k: str(v) for k, v in raw.items()}
        try:
            family, pitch, num_teeth, bore_mm, belt_h, cl_mm, bl_mm, pr_ex = \
                _parse_stl_params(qs, '1')
            key = PROFILE_KEY_PREFIX.get(family, '') + pitch
            spec = PULLEY_SPECS[key]
            pld = spec.get('pitch_line_diff', spec.get('pitchLineDiff', 0.0))
            R_OD = getOuterDiameter(num_teeth, spec['pitch'], pld + pr_ex - cl_mm) / 2.0
            R_tr = R_OD - spec['tooth_ht']
            if bore_mm >= R_tr - 1.0:
                raise ValueError(f'bore {bore_mm} >= tooth root {R_tr:.1f}')
            hub_od, hub_h, sd, sc, cn, fd, kw, kh = _parse_hub_params(qs, '')
            if hub_od > 0 and hub_od / 2.0 >= R_tr - 1.0:
                raise ValueError(f'hub_od {hub_od} >= tooth root {R_tr:.1f}')
            if kw > 0 and (bore_mm / 2.0 + kh) >= R_tr - 1.0:
                raise ValueError('keyway extends outside pulley')
            if fd > 0 and fd >= bore_mm / 2.0 - 0.5:
                raise ValueError('d-flat too deep')
            if sc > 0 and sd > 0 and hub_od > 0 and (hub_od - bore_mm) / 2.0 < sd:
                raise ValueError(f'hub wall {(hub_od - bore_mm) / 2:.1f} thinner than the screw {sd}')
            sp_en, sp_hub, rim_d, sp_w, ft, fb, sp_c, sp_h, _ = _parse_spoke_params(qs, '')
            if sp_en:
                R_rim = R_tr - rim_d
                R_hub_s = sp_hub / 2.0 if sp_hub > 0 else bore_mm / 2.0 + 1.0
                if R_hub_s >= R_rim - 1.5:
                    raise ValueError('spoke void too narrow')
                if ft + fb >= sp_w:
                    raise ValueError(f'fillets {ft}+{fb} >= spoke width {sp_w}')
            return raw
        except ValueError as e:
            last_err = e
    raise RuntimeError(f'Could not generate valid config after {max_attempts} attempts: {last_err}')


# ── STEP/STL generation via the real Flask routes (same query-string shape
# for both, so there's no hand-mapped-kwargs risk of the two diverging) ────

def generate_step_and_stl(cfg: dict, ss_bin: str) -> tuple[int, bytes, int, bytes]:
    os.environ['SMALL_STEP_BIN'] = ss_bin     # (ignored when PULLEY_STEP_BACKEND=cadquery)
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    qs = {k: str(v) for k, v in cfg.items()}
    with flask_app.test_client() as c:
        step_resp = c.get('/download/step', query_string=qs)
        stl_resp = c.get('/download/stl', query_string=qs)
    return step_resp.status_code, step_resp.data, stl_resp.status_code, stl_resp.data


def _fetch(route: str, cfg: dict) -> bytes:
    from app import app as flask_app
    with flask_app.test_client() as c:
        r = c.get(route, query_string={k: str(v) for k, v in cfg.items()})
    if r.status_code != 200:
        raise RuntimeError(f"{route} failed (HTTP {r.status_code}): {r.data[:200]!r}")
    return r.data


# ── Geometry checks ─────────────────────────────────────────────────────

def _load_stl(data: bytes) -> "trimesh.Trimesh":
    """The app's binary STL: exactly the declared triangles. Its design data is
    appended after them, which a plain trimesh.load reads as stray triangles
    (a non-watertight mesh and a wrong volume)."""
    import numpy as np
    n = int(np.frombuffer(data[80:84], "<u4")[0])
    rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    t = np.frombuffer(data[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t["v"].reshape(-1, 3),
                           faces=np.arange(3 * n).reshape(-1, 3), process=True)


def _occt_tools_available() -> bool:
    return _OCC_PY.exists() and _OCCT_SERVER.exists()


def _step_mesh_volume(step_path: Path) -> float | None:
    """Total volume of the STEP's solids, from a fine tessellation (0.005 mm
    deflection) — accurate to ~1e-4 where OCCT's integrator isn't. Needs
    cadquery, which this tool's .venv312 has; None without it."""
    try:
        import cadquery as cq
    except ImportError:
        return None
    total = 0.0
    for shape in cq.importers.importStep(str(step_path)).vals():
        for solid in shape.Solids():
            vs, ts = solid.tessellate(0.005, 0.05)
            m = trimesh.Trimesh(vertices=[(v.x, v.y, v.z) for v in vs], faces=ts, process=True)
            total += m.volume
    return total


def _geometry_check(step_bytes: bytes, stl_volume: float | None,
                    occt_worker: PersistentStepWorker | None,
                    step_path: Path | None, sfa_available: bool) -> list[str]:
    problems = []
    wire = check_wire_order(step_bytes)
    if wire["out_of_order"]:
        problems.append(
            f"wire-order: {wire['out_of_order']}/{wire['total']} EDGE_LOOPs "
            f"out of order (loops {wire['bad_loops']})")

    if occt_worker is not None:
        assert step_path is not None
        step_path.write_bytes(step_bytes)
        occt = occt_worker.check(str(step_path))
        if occt.get("error"):
            problems.append(f"occt: {occt['error']}")
        else:
            if occt["invalid"]:
                problems.append(f"occt: {occt['invalid']}/{occt['solids']} solids invalid")
            if occt["solids"] == 0:
                problems.append("occt: 0 solids in STEP output")
            # Volume from a fine mesh of the STEP, not OCCT's GProp integration:
            # that was found off by 0.6% (default) to 4% ("precise") on solids
            # with extruded spline faces - an RPP 14M whose cross-section was
            # right to 2e-4 - so it reported mismatches that weren't there.
            step_volume = _step_mesh_volume(step_path)
            if step_volume is not None and stl_volume is not None and stl_volume > 1e-9:
                rel = abs(step_volume - stl_volume) / stl_volume
                if rel > VOLUME_REL_TOL:
                    problems.append(
                        f"volume mismatch: STEP={step_volume:.3f} "
                        f"STL={stl_volume:.3f} rel_diff={rel:.2e} "
                        f"(> tol {VOLUME_REL_TOL:.0e})")

    if sfa_available:
        ok, msg = validate_nist_sfa(step_bytes, _SFA_EXE)
        if ok is False:
            problems.append(f"sfa: {msg}")

    return problems


def run(iterations: int | None, duration: float | None, seed: int | None,
       out_dir: str = "fuzz_results") -> list[dict]:
    ss_bin = _get_ss_bin()
    # Must happen before ANY "from app import ..." (including the one inside
    # _make_config, called first each iteration below) -- app.py resolves its
    # module-level _SS_BIN from this env var at first-import time only, and
    # every later `from app import ...` just returns the cached module. Set
    # after the fact, this line would silently do nothing: _make_config's own
    # import would have already locked _SS_BIN onto app.py's own fallback
    # default (a stale/different binary) before this function's caller ever
    # sees it -- found by fuzzing this fuzz tool itself, when a run's actual
    # binary turned out not to match the one this function's ss_bin/print
    # implied it was using.
    os.environ['SMALL_STEP_BIN'] = ss_bin
    seed = seed if seed is not None else int(datetime.now().strftime('%Y%m%d%H'))
    rng = random.Random(seed)

    geometry_check = _occt_tools_available()
    if not geometry_check:
        print(f"[setup] OCCT tools not available ({_OCC_PY} / {_OCCT_SERVER}) "
             f"— falling back to wire-order-only for this run.")
    sfa_available = _SFA_EXE.is_file()
    if not sfa_available:
        print(f"[setup] sfa-cl.exe not found at {_SFA_EXE} — NIST SFA check skipped.")

    occt_worker = PersistentStepWorker(_OCC_PY, _OCCT_SERVER) if geometry_check else None
    occt_tmp_dir = Path(tempfile.mkdtemp(prefix="fuzz_pulley_occt_")) if geometry_check else None

    out_path = Path(out_dir)
    out_path.mkdir(exist_ok=True)
    log_path = out_path / f"fuzz_log_{int(time.time())}.jsonl"

    start = time.time()
    n = 0
    skipped = 0
    failures: list[dict] = []

    def keep_going() -> bool:
        if duration is not None:
            return (time.time() - start) < duration
        return n < iterations

    backend = os.environ.get('PULLEY_STEP_BACKEND', '').strip().lower() or 'small_step'
    print(f"STEP backend: {backend}" + (f" ({ss_bin})" if backend == 'small_step' else ""))
    print(f"Fuzzing PulleyWebApp-ss (STEP + STL"
         f"{' + OCCT' if geometry_check else ''}{' + SFA' if sfa_available else ''}), "
         f"seed={seed} -> {log_path}")

    try:
        with open(log_path, "w", encoding="utf-8") as logf:
            while keep_going():
                try:
                    cfg = _make_config(rng)
                except RuntimeError as e:
                    skipped += 1
                    continue

                n += 1
                record = {"iter": n, "cfg": cfg, "ok": True, "error": None,
                          "geometry_problems": []}
                try:
                    step_status, step_bytes, stl_status, stl_bytes = \
                        generate_step_and_stl(cfg, ss_bin)
                    if step_status != 200 or not step_bytes.startswith(b'ISO-10303-21'):
                        raise RuntimeError(
                            f"STEP generation failed (HTTP {step_status}): {step_bytes[:200]!r}")
                    if stl_status != 200 or len(stl_bytes) == 0:
                        raise RuntimeError(f"STL generation failed (HTTP {stl_status})")

                    stl_mesh = _load_stl(stl_bytes)
                    stl_volume = stl_mesh.volume
                    metal = cfg.get('flange_enabled') == '1' and cfg.get('flange_3dprint') == '0'
                    stl_problems = [] if (metal or stl_mesh.is_watertight) else \
                        ["stl: not watertight"]   # (a metal-flange STL is three touching parts)
                    if metal:
                        # The STL download is the pulley AND its two sheet-metal
                        # plates; the STEP is the pulley. Compare like with like:
                        # take the plates (/download/flange-stl) away.
                        plates = _load_stl(_fetch('/download/flange-stl', cfg))
                        stl_volume -= plates.volume
                        if not plates.is_watertight:
                            stl_problems.append("stl: metal plates not watertight")

                    step_path = (occt_tmp_dir / f"{n}.step"
                                if occt_tmp_dir is not None else None)
                    problems = _geometry_check(
                        step_bytes, stl_volume, occt_worker, step_path, sfa_available)
                    record["geometry_problems"] = stl_problems + problems
                except Exception as e:
                    record["ok"] = False
                    record["error"] = f"{type(e).__name__}: {e}"
                    record["traceback"] = traceback.format_exc()
                    failures.append(record)
                    print(f"  [{n}] FAIL: {record['error']}")

                if record["ok"] and record["geometry_problems"]:
                    record["ok"] = False
                    record["error"] = "; ".join(record["geometry_problems"])
                    failures.append(record)
                    print(f"  [{n}] GEOMETRY FAIL: {record['error']}")

                logf.write(json.dumps(record) + "\n")
                logf.flush()
                if n % 25 == 0:
                    elapsed = time.time() - start
                    print(f"  {n} iterations, {len(failures)} failures, "
                         f"{skipped} skipped configs, {elapsed:.0f}s elapsed")
    finally:
        if occt_worker is not None:
            occt_worker.close()
        if occt_tmp_dir is not None:
            shutil.rmtree(occt_tmp_dir, ignore_errors=True)

    elapsed = time.time() - start
    print()
    print(f"=== Fuzz run complete: {n} iterations in {elapsed:.1f}s, "
         f"{len(failures)} failures, {skipped} configs skipped ===")
    print(f"Full log: {log_path}")
    if failures:
        print("\nFirst failure:")
        print(json.dumps(failures[0], indent=2)[:2500])
    return failures


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--iterations", type=int, default=None)
    p.add_argument("--duration", type=float, default=None, help="seconds")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--backend", choices=["small_step", "cadquery"], default="small_step",
                   help="which STEP builder to check against the STL")
    args = p.parse_args()
    # before anything imports app (it reads this per request, but be early)
    os.environ['PULLEY_STEP_BACKEND'] = '' if args.backend == 'small_step' else 'cadquery'
    if args.iterations is None and args.duration is None:
        args.iterations = 200

    failures = run(iterations=args.iterations, duration=args.duration, seed=args.seed)
    sys.exit(1 if failures else 0)
