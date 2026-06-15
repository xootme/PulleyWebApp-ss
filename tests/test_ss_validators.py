"""
test_ss_validators.py
Generate one random pulley STEP via PulleyWebApp-ss (small_step path)
and validate it with all three tools: NIST SFA, eDrawings, pythonocc.

Usage:
    .venv312\\Scripts\\python.exe tests\\test_ss_validators.py [--seed SEED] [--count N]

SMALL_STEP_BIN env var must point to small_step.exe, or set it via the
default path constant below.

Exit code: 0 = all validators passed on all configs, 1 = any failure.
"""
import sys
import os
import random
import json
import tempfile
import subprocess
import time
import argparse
from pathlib import Path
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE     = Path(__file__).parent
_ROOT     = _HERE.parent
_SFA_EXE  = _ROOT / "tools" / "sfa" / "sfa-cl.exe"
_SS_BIN_DEFAULT = Path(r"C:\Users\cmyer\Documents\small_step\target\x86_64-pc-windows-gnu\debug\small_step.exe")

# Add repo root to path so we can import app and tests
sys.path.insert(0, str(_ROOT))


def _get_ss_bin():
    p = os.environ.get("SMALL_STEP_BIN", str(_SS_BIN_DEFAULT))
    if not os.path.isfile(p):
        print(f"[error] small_step binary not found: {p}")
        print("        Set SMALL_STEP_BIN env var or build small_step first.")
        sys.exit(1)
    return p


# ── Config generation (mirrors test_nightly_random.py logic) ──────────────────

_FAMILIES = {
    'HTD': {'3M': (10, 80), '5M': (12, 80), '8M': (15, 60), '14M': (18, 50)},
    'GT':  {'2M': (10, 80), '3M': (10, 80), '5M': (12, 70), '8M':  (20, 50)},
    'STD': {'2M': (10, 80), '3M': (10, 80), '5M': (12, 70), '8M':  (22, 50)},
}
_CLEARANCE = ['TIGHT', 'STANDARD', 'LOOSE']
_BACKLASH  = ['NONE', 'TIGHT', 'STANDARD', 'LOOSE']


def _raw_config(r):
    family = r.choice(list(_FAMILIES))
    pitch  = r.choice(list(_FAMILIES[family]))
    t_min, t_max = _FAMILIES[family][pitch]
    cfg = {
        'family':           family,
        'pitch':            pitch,
        'teeth':            r.randint(t_min, t_max),
        'bore':             round(r.uniform(4.0, 30.0), 1),
        'print_extra':      round(r.uniform(0.0, 0.4), 2),
        'clearance_preset': r.choice(_CLEARANCE),
        'backlash_preset':  r.choice(_BACKLASH),
        'belt_height':      round(r.uniform(6.0, 25.0), 1),
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
        cfg['hub_od']     = hub_od
        cfg['hub_height'] = round(r.uniform(3.0, 18.0), 1)
    else:
        hub_od = 0.0
    if int(cfg['teeth']) >= 20 and r.random() > 0.4:
        sp_hub = hub_od if hub_od > float(cfg['bore']) else round(float(cfg['bore']) + r.uniform(2.0, 6.0), 1)
        cfg['spokes_enabled']     = '1'
        cfg['spokes_hub_od']      = sp_hub
        cfg['spokes_rim_depth']   = round(r.uniform(2.0, 7.0), 1)
        cfg['spokes_width']       = round(r.uniform(3.0, 8.0), 1)
        cfg['spokes_fillet_tip']  = round(r.uniform(0.5, 2.5), 1)
        cfg['spokes_fillet_base'] = round(r.uniform(1.0, 4.0), 1)
        cfg['spokes_count']       = r.choice([3, 4, 5, 6, 7])
        cfg['spokes_height']      = round(r.uniform(0.0, 5.0), 1)
    if r.random() > 0.3:
        is_3dp = r.random() > 0.4
        cfg['flange_enabled']      = '1'
        cfg['flange_3dprint']      = '1' if is_3dp else '0'
        cfg['flange_angle']        = round(r.uniform(8.0, 25.0), 1)
        cfg['flange_rim_radius']   = round(r.uniform(1.0, 8.0), 1)
        cfg['flange_height']       = round(r.uniform(0.5, 5.0), 1)
        cfg['flange_plate_height'] = round(r.uniform(0.5, 3.0), 1)
        cfg['flange_bend_radius']  = round(r.uniform(0.0, 3.0), 1)
        top_sep = r.random() > 0.3
        cfg['flange_top_separate'] = '1' if top_sep else '0'
        if is_3dp and top_sep and r.random() > 0.4:
            cfg['flange_nubs_enabled']  = '1'
            cfg['flange_nub_count']     = r.choice([2, 3, 4, 6])
            cfg['flange_nub_dia']       = round(r.uniform(2.0, 6.0), 1)
            cfg['flange_nub_height']    = round(r.uniform(1.0, 6.0), 1)
            cfg['flange_nub_allowance'] = round(r.uniform(0.1, 0.4), 2)
    return cfg


def _make_config(r, max_attempts=30):
    from app import _parse_stl_params, _parse_hub_params, _parse_spoke_params
    from geometry.pulley_geometry import getOuterDiameter, PULLEY_SPECS, PROFILE_KEY_PREFIX
    last_err = None
    for _ in range(max_attempts):
        raw = _raw_config(r)
        qs  = {k: str(v) for k, v in raw.items()}
        try:
            family, pitch, num_teeth, bore_mm, belt_h, cl_mm, bl_mm, pr_ex = \
                _parse_stl_params(qs, '1')
            key  = PROFILE_KEY_PREFIX.get(family, '') + pitch
            spec = PULLEY_SPECS[key]
            pld  = spec.get('pitch_line_diff', spec.get('pitchLineDiff', 0.0))
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
            sp_en, sp_hub, rim_d, sp_w, ft, fb, sp_c, sp_h, _ = _parse_spoke_params(qs, '')
            if sp_en:
                R_rim   = R_tr - rim_d
                R_hub_s = sp_hub / 2.0 if sp_hub > 0 else bore_mm / 2.0 + 1.0
                if R_hub_s >= R_rim - 1.5:
                    raise ValueError('spoke void too narrow')
                if ft + fb >= sp_w:
                    raise ValueError(f'fillets {ft}+{fb} >= spoke width {sp_w}')
            return raw
        except ValueError as e:
            last_err = e
    raise RuntimeError(f'Could not generate valid config after {max_attempts} attempts: {last_err}')


# ── STEP generation ───────────────────────────────────────────────────────────

def generate_step(cfg, ss_bin):
    """Call PulleyWebApp-ss Flask test client with SMALL_STEP_BIN set."""
    os.environ['SMALL_STEP_BIN'] = ss_bin
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    qs = {k: str(v) for k, v in cfg.items()}
    with flask_app.test_client() as c:
        resp = c.get('/download/step', query_string=qs)
    return resp.status_code, resp.data


# ── Validators ────────────────────────────────────────────────────────────────

def validate_occ(step_bytes):
    """pythonocc BRepCheck: topology validity + positive volume."""
    try:
        from OCC.Core.STEPControl import STEPControl_Reader
        from OCC.Core.BRepCheck import BRepCheck_Analyzer
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.GProp import GProp_GProps
        from OCC.Core.IFSelect import IFSelect_RetDone

        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
            f.write(step_bytes)
            tmp = f.name
        try:
            reader = STEPControl_Reader()
            status = reader.ReadFile(tmp)
            if status != IFSelect_RetDone:
                return False, "STEPControl_Reader failed to read file"
            reader.TransferRoots()
            shape = reader.OneShape()
            if shape.IsNull():
                return False, "shape is null after transfer"

            analyzer = BRepCheck_Analyzer(shape, True)
            if not analyzer.IsValid():
                return False, "BRepCheck_Analyzer: shape invalid"

            props = GProp_GProps()
            brepgprop.VolumeProperties(shape, props)
            vol = props.Mass()
            if vol <= 0:
                return False, f"volume = {vol:.3f} mm³ (must be > 0)"

            return True, f"valid, volume = {vol:.1f} mm³"
        finally:
            os.unlink(tmp)
    except ImportError:
        return None, "pythonocc (OCC) not available in this environment"
    except Exception as e:
        return False, f"OCC error: {e}"


def validate_nist_sfa(step_bytes):
    """NIST SFA 5.45 syntax check."""
    if not _SFA_EXE.is_file():
        return None, f"sfa-cl.exe not found at {_SFA_EXE}"
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(step_bytes)
        tmp = f.name
    try:
        result = subprocess.run(
            [str(_SFA_EXE), tmp, "syntax", "noopen", "nolog"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        output = result.stdout + result.stderr
        lines  = output.splitlines()
        problems = [l.strip() for l in lines if '**' in l]
        ok_line  = any('No syntax errors or warnings' in l for l in lines)
        if ok_line and not problems:
            return True, "No syntax errors or warnings"
        if problems:
            return False, "; ".join(problems[:3])
        return False, "no clean result line in output"
    except subprocess.TimeoutExpired:
        return False, "timeout after 60 s"
    finally:
        os.unlink(tmp)


def validate_edrawings(step_bytes):
    """eDrawings 2026 COM: OnFinishedLoadingDocument / OnFailedLoadingDocument."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None, "pywin32 not available"

    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(step_bytes)
        tmp = f.name

    result = {}

    class _Events:
        def OnFinishedLoadingDocument(self, fileName):
            result['ok']  = True
            result['msg'] = "loaded"

        def OnFailedLoadingDocument(self, fileName, errorCode, errorString):
            _ = fileName
            result['ok']  = False
            result['msg'] = f"[err {errorCode}] {errorString}"

    pythoncom.CoInitialize()
    ctrl = None
    try:
        ctrl = win32com.client.DispatchWithEvents(
            "EModelView.EModelViewControl.26", _Events)
        ctrl.OpenDoc(tmp, False, False, False, "")
        deadline = time.time() + 20
        while time.time() < deadline:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.05)
            if 'ok' in result:
                break
        if 'ok' not in result:
            return False, "timeout after 20 s — no load event"
        return result['ok'], result['msg']
    except Exception as e:
        return False, f"COM error: {e}"
    finally:
        if ctrl is not None:
            try:
                ctrl.CloseActiveDoc("")
            except Exception:
                pass
        pythoncom.CoUninitialize()
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate random pulleys via small_step and validate with all tools")
    parser.add_argument('--seed', type=int, default=None,
                        help='Random seed (default: date-hour)')
    parser.add_argument('--count', type=int, default=1,
                        help='Number of random configs to generate (default: 1)')
    args = parser.parse_args()

    ss_bin = _get_ss_bin()
    seed   = args.seed if args.seed is not None else int(datetime.now().strftime('%Y%m%d%H'))
    rng    = random.Random(seed)

    print(f"small_step: {ss_bin}")
    print(f"Seed: {seed}  Count: {args.count}")
    print()

    overall_pass = True

    for i in range(args.count):
        print(f"{'='*60}")
        print(f"Config {i+1}/{args.count}")

        # Generate config
        try:
            cfg = _make_config(rng)
        except RuntimeError as e:
            print(f"  [SKIP] Could not generate valid config: {e}")
            continue

        tags = ' '.join(t for t in [
            f"{cfg['family']}-{cfg['pitch']}-{cfg['teeth']}T",
            f"bore={cfg['bore']}",
            'spokes'  if cfg.get('spokes_enabled') == '1' else '',
            'flange'  if cfg.get('flange_enabled') == '1' else '',
            'dflat'   if 'hub_flat_depth' in cfg else '',
            'keyway'  if 'hub_keyway_w' in cfg else '',
            f"hub" if cfg.get('hub_od') else '',
        ] if t)
        print(f"  {tags}")
        print(f"  Config: {json.dumps({k: cfg[k] for k in list(cfg)[:8]}, separators=(',', ':'))}")

        # Generate STEP
        t0 = time.time()
        status, step_bytes = generate_step(cfg, ss_bin)
        elapsed = time.time() - t0

        if status != 200 or not step_bytes.startswith(b'ISO-10303-21'):
            print(f"  [FAIL] STEP generation failed (HTTP {status}): {step_bytes[:200]}")
            overall_pass = False
            continue

        print(f"  STEP: {len(step_bytes):,} bytes in {elapsed:.2f}s")

        # Count solids in STEP for info
        solid_count = step_bytes.count(b'MANIFOLD_SOLID_BREP')
        print(f"  MANIFOLD_SOLID_BREP count: {solid_count}")

        # Validator 1: pythonocc
        ok, msg = validate_occ(step_bytes)
        tag = "[PASS]" if ok else ("[SKIP]" if ok is None else "[FAIL]")
        print(f"  {tag} pythonocc BRepCheck: {msg}")
        if ok is False:
            overall_pass = False

        # Validator 2: NIST SFA
        ok, msg = validate_nist_sfa(step_bytes)
        tag = "[PASS]" if ok else ("[SKIP]" if ok is None else "[FAIL]")
        print(f"  {tag} NIST SFA 5.45:       {msg}")
        if ok is False:
            overall_pass = False

        # Validator 3: eDrawings
        ok, msg = validate_edrawings(step_bytes)
        tag = "[PASS]" if ok else ("[SKIP]" if ok is None else "[FAIL]")
        print(f"  {tag} eDrawings 2026:      {msg}")
        if ok is False:
            overall_pass = False

        print()

    print('='*60)
    print("OVERALL:", "PASS" if overall_pass else "FAIL")
    sys.exit(0 if overall_pass else 1)


if __name__ == '__main__':
    main()
