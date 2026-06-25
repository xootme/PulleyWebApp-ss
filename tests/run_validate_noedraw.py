"""
Like run_quick_validate.py but without eDrawings (no COM/GUI needed).
Run from PulleyWebApp-ss root:
    .venv312/Scripts/python.exe tests/run_validate_noedraw.py [seed [count]]

Generated STEP files are saved to test_output/ (gitignored).
"""
import sys, os, tempfile, subprocess, random, time
from pathlib import Path

ROOT       = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "test_output"
OUTPUT_DIR.mkdir(exist_ok=True)

_OCP_SITE = Path(r"C:\Users\cmyer\AppData\Roaming\CheapCADTools\runtime\site-packages")
_SS_BIN   = ROOT.parent / "small_step" / "target" / "x86_64-pc-windows-gnu" / "debug" / "small_step.exe"
_SFA      = ROOT / "tools" / "sfa" / "sfa-cl.exe"
_FC_CMD   = Path(r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe")
_FC_SCRIPT= ROOT / "tests" / "_freecad_import.py"

sys.path.insert(0, str(ROOT))
os.environ['SMALL_STEP_BIN'] = str(_SS_BIN)
if str(_OCP_SITE) not in sys.path:
    sys.path.insert(0, str(_OCP_SITE))

from geometry.pulley_validation import groove_diameter
from app import app as flask_app
flask_app.config['TESTING'] = True

_FAMILIES = {
    'HTD': {'3M':(10,80),'5M':(12,80),'8M':(15,60),'14M':(18,50)},
    'GT':  {'2M':(10,80),'3M':(10,80),'5M':(12,70),'8M': (20,50)},
    'STD': {'2M':(10,80),'3M':(10,80),'5M':(12,70),'8M': (22,50)},
}


_SCREW_DIA = {'M3': 3, 'M4': 4, 'M5': 5, 'M6': 6}


def make_cfg(r):
    family = r.choice(list(_FAMILIES))
    pitch  = r.choice(list(_FAMILIES[family]))
    t_min, t_max = _FAMILIES[family][pitch]
    num_teeth = r.randint(t_min, t_max)

    groove_d, tooth_ht, od = groove_diameter(family, pitch, num_teeth)
    bore_max = groove_d - 1.0
    bore = round(r.uniform(2.0, min(25.0, bore_max - 0.1)), 1)

    cfg = {
        'family': family, 'pitch': pitch, 'teeth': num_teeth,
        'bore': bore, 'print_extra': 0.0,
        'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD',
        'belt_height': round(r.uniform(6.0, 25.0), 1), 'clearance_height': 0.0,
    }

    # ── Hub ──────────────────────────────────────────────────────────────────
    if r.random() > 0.3:
        hub_od     = round(bore + r.uniform(4.0, 16.0), 1)
        hub_height = round(r.uniform(3.0, 16.0), 1)
        cfg['hub_od']     = hub_od
        cfg['hub_height'] = hub_height

        retention = r.choice(['set_screw_std', 'set_screw_nut', 'd_shaft', 'keyway', 'none'])
        screw_size = r.choice(list(_SCREW_DIA))
        screw_dia  = _SCREW_DIA[screw_size]

        if retention in ('set_screw_std', 'set_screw_nut'):
            cfg['hub_screw_dia']    = screw_dia
            cfg['hub_screw_count']  = r.choice([1, 2])
            cfg['hub_captured_nut'] = '1' if retention == 'set_screw_nut' else '0'
        elif retention == 'd_shaft':
            cfg['hub_flat_depth'] = round(r.uniform(0.3, 1.5), 2)
            if r.random() > 0.5:  # optional set screw on D-shaft
                cfg['hub_screw_dia']    = screw_dia
                cfg['hub_screw_count']  = 1
                cfg['hub_captured_nut'] = r.choice(['0', '1'])
        elif retention == 'keyway':
            cfg['hub_keyway_w'] = round(r.uniform(3.0, 8.0), 1)
            cfg['hub_keyway_h'] = round(r.uniform(1.5, 4.0), 1)
            if r.random() > 0.5:  # optional set screw on keyway
                cfg['hub_screw_dia']    = screw_dia
                cfg['hub_screw_count']  = 1
                cfg['hub_captured_nut'] = r.choice(['0', '1'])
        # 'none' — no extra params

    # ── Spokes ───────────────────────────────────────────────────────────────
    if int(cfg['teeth']) >= 20 and r.random() > 0.4:
        rim_depth   = round(r.uniform(2.0, 5.0), 1)
        void_od     = groove_d - 2 * rim_depth
        sp_bore_max = void_od - 1.0
        if bore < sp_bore_max:
            sp_hub = float(cfg.get('hub_od', bore)) + 2
            cfg.update({
                'spokes_enabled':      '1',
                'spokes_hub_od':       sp_hub,
                'spokes_rim_depth':    rim_depth,
                'spokes_width':        round(r.uniform(4.0, 7.0), 1),
                'spokes_fillet_tip':   round(r.uniform(0.5, 1.5), 1),
                'spokes_fillet_base':  round(r.uniform(1.0, 2.5), 1),
                'spokes_count':        r.choice([3, 4, 5, 6]),
                'spokes_split':        r.choice(['0', '1']),
            })

    # ── Flange ───────────────────────────────────────────────────────────────
    if r.random() > 0.5:
        is_3dprint = r.random() > 0.3  # 70% 3D-print, 30% metal
        cfg['flange_enabled'] = '1'
        cfg['flange_3dprint'] = '1' if is_3dprint else '0'
        cfg['flange_angle']       = round(r.uniform(10.0, 20.0), 1)
        cfg['flange_rim_radius']  = round(r.uniform(2.0, 5.0), 1)
        cfg['flange_height']      = round(r.uniform(1.0, 3.0), 1)
        if is_3dprint:
            cfg['flange_top_separate'] = r.choice(['0', '1'])
            if r.random() > 0.5:  # gluing nubs
                cfg['flange_nubs_enabled'] = '1'
                cfg['flange_nub_count']    = r.choice([3, 4, 5, 6])
                cfg['flange_nub_dia']      = round(r.uniform(2.0, 4.0), 1)
                cfg['flange_nub_height']   = round(r.uniform(1.0, 2.5), 1)
        else:
            cfg['flange_plate_height'] = round(r.uniform(0.8, 2.0), 1)
            cfg['flange_bend_radius']  = round(r.uniform(0.0, 1.5), 1)

    return cfg


def validate_sfa(step_bytes):
    if not _SFA.is_file():
        return None, f"sfa-cl.exe not found"
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False, mode='wb') as f:
        f.write(step_bytes); tmp = f.name
    try:
        res = subprocess.run(
            [str(_SFA), tmp, 'syntax', 'noopen', 'nolog'],
            capture_output=True, text=True, timeout=60, check=False)
        out = res.stdout + res.stderr
        probs = [l.strip() for l in out.splitlines() if '**' in l]
        clean = any('No syntax errors or warnings' in l for l in out.splitlines())
        if clean and not probs:
            return True, 'No syntax errors or warnings'
        return False, '; '.join(probs[:3]) if probs else out[:200]
    except subprocess.TimeoutExpired:
        return False, 'timeout'
    finally:
        os.unlink(tmp)


def validate_occ(step_bytes):
    try:
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
    except ImportError as e:
        return None, f"OCP import failed: {e}"
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False, mode='wb') as f:
        f.write(step_bytes); tmp = f.name
    try:
        reader = STEPControl_Reader()
        if reader.ReadFile(tmp) != IFSelect_RetDone:
            return False, "ReadFile failed"
        reader.TransferRoots()
        shape = reader.OneShape()
        if shape.IsNull():
            return False, "shape is null"
        if not BRepCheck_Analyzer(shape, True).IsValid():
            return False, "BRepCheck: invalid"
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        vol = props.Mass()
        if vol <= 0:
            return False, f"volume={vol:.1f}"
        return True, f"valid, vol={vol:.1f} mm³"
    finally:
        os.unlink(tmp)


def validate_freecad(step_bytes):
    if not _FC_CMD.is_file():
        return None, "freecadcmd.exe not found"
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False, mode='wb') as f:
        f.write(step_bytes); tmp = f.name
    try:
        res = subprocess.run(
            [str(_FC_CMD), str(_FC_SCRIPT), tmp],
            capture_output=True, text=True, timeout=90, check=False)
        out = (res.stdout + res.stderr).strip()
        lines = [l for l in out.splitlines()
                 if l and not l.startswith('FreeCAD') and
                 not l.startswith('(C)') and not l.startswith('FreeCAD is free')]
        msg = lines[0] if lines else out[:200]
        if res.returncode == 0 and msg.startswith('OK:'):
            return True, msg
        return False, msg or f"exit={res.returncode}"
    except subprocess.TimeoutExpired:
        return False, 'timeout after 90s'
    finally:
        os.unlink(tmp)


seed  = int(sys.argv[1]) if len(sys.argv) > 1 else 42
count = int(sys.argv[2]) if len(sys.argv) > 2 else 5
rng   = random.Random(seed)

print(f"seed={seed}  count={count}")
print()

all_pass = True
with flask_app.test_client() as c:
    for i in range(count):
        cfg   = make_cfg(rng)
        qs    = {k: str(v) for k, v in cfg.items()}
        label = (f"{cfg['family']}-{cfg['pitch']}-{cfg['teeth']}T bore={cfg['bore']}"
                 + (' spokes' if cfg.get('spokes_enabled') == '1' else '')
                 + (' flange' if cfg.get('flange_enabled') == '1' else '')
                 + (f" hub={cfg['hub_od']}" if cfg.get('hub_od') else ''))
        print(f"--- Config {i+1}: {label}")

        t0   = time.time()
        resp = c.get('/download/step', query_string=qs)
        dt   = time.time() - t0

        if resp.status_code != 200 or not resp.data.startswith(b'ISO-10303-21'):
            print(f"  [FAIL] STEP gen: {resp.data[:250]}")
            all_pass = False
            print()
            continue

        safe_label = label.replace(' ', '_').replace('=', '').replace('/', '-')
        out_path = OUTPUT_DIR / f"s{seed}_{i+1:02d}_{safe_label}.step"
        out_path.write_bytes(resp.data)

        print(f"  STEP: {len(resp.data):,} bytes  {dt:.2f}s  "
              f"solids={resp.data.count(b'MANIFOLD_SOLID_BREP')}  -> {out_path.name}")

        for name, fn in [
            ('NIST SFA 5.45  ', validate_sfa),
            ('OCC/OpenCASCADE', validate_occ),
            ('FreeCAD 1.1    ', validate_freecad),
        ]:
            ok, msg = fn(resp.data)
            tag = '[PASS]' if ok else ('[SKIP]' if ok is None else '[FAIL]')
            print(f"  {tag} {name}: {msg}")
            if ok is False:
                all_pass = False
        print()

print('OVERALL:', 'PASS' if all_pass else 'FAIL')
sys.exit(0 if all_pass else 1)
