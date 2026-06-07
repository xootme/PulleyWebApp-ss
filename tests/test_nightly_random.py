"""
test_nightly_random.py — Nightly randomized pulley generation tests.

Generates 5 random pulley configurations covering the full input space and
verifies that STL preview, SVG, DXF, and (if cadquery available) STEP exports
all succeed without errors.

Runs only when PULLEY_NIGHTLY=1 is set in the environment — excluded from the
normal test suite so it doesn't slow down pre-commit or regular CI runs.

Input variables for each run are saved to:
    logs/nightly_random/<date>_<run_id>.json

so any failure can be reproduced exactly.
"""

import json
import os
import random
import string
import uuid
from datetime import datetime
from pathlib import Path

import pytest

# ── Nightly-only guard ─────────────────────────────────────────────────────────

pytestmark = pytest.mark.skipif(
    os.environ.get('PULLEY_NIGHTLY') != '1',
    reason='Nightly random tests — set PULLEY_NIGHTLY=1 to run'
)

# ── Parameter space ────────────────────────────────────────────────────────────

FAMILIES = {
    'HTD': {
        '3M':  {'min': 10, 'max': 120, 'bore_max': 20},
        '5M':  {'min': 12, 'max': 120, 'bore_max': 30},
        '8M':  {'min': 15, 'max': 100, 'bore_max': 50},
        '14M': {'min': 18, 'max':  80, 'bore_max': 80},
    },
    'GT': {
        '2M': {'min': 10, 'max': 120, 'bore_max': 15},
        '3M': {'min': 10, 'max': 120, 'bore_max': 25},
        '5M': {'min': 12, 'max': 100, 'bore_max': 40},
        '8M': {'min': 20, 'max':  80, 'bore_max': 60},
    },
    'STD': {
        '2M': {'min': 10, 'max': 120, 'bore_max': 15},
        '3M': {'min': 10, 'max': 120, 'bore_max': 25},
        '5M': {'min': 12, 'max': 100, 'bore_max': 40},
        '8M': {'min': 22, 'max':  80, 'bore_max': 60},
    },
}

CLEARANCE_PRESETS = ['TIGHT', 'STANDARD', 'LOOSE']
BACKLASH_PRESETS  = ['NONE', 'TIGHT', 'STANDARD', 'LOOSE']
BORE_PROFILES     = ['round', 'dflat', 'keyway']


def _rng(seed=None):
    r = random.Random(seed)
    return r


def _rand_bore(r, bore_max, profile):
    """Return (bore_mm, flat_depth, keyway_w, keyway_h)."""
    bore_mm = round(r.uniform(4.0, min(bore_max, 40.0)), 1)
    if profile == 'dflat':
        flat = round(r.uniform(0.3, bore_mm * 0.3), 2)
        return bore_mm, flat, 0.0, 0.0
    if profile == 'keyway':
        kw = round(r.choice([2.0, 3.0, 4.0, 5.0, 6.0, 8.0]), 1)
        kh = round(kw * 0.5, 1)
        return bore_mm, 0.0, kw, kh
    return bore_mm, 0.0, 0.0, 0.0


def _rand_hub(r, bore_mm, teeth):
    """Return hub params or None (no hub)."""
    if r.random() < 0.25:
        return None
    hub_od     = round(bore_mm + r.uniform(4.0, 20.0), 1)
    hub_height = round(r.uniform(3.0, 20.0), 1)
    screw_dia  = r.choice([0.0, 2.5, 3.0, 4.0, 5.0])
    screw_count= r.choice([0, 1, 2, 3]) if screw_dia > 0 else 0
    captured   = r.choice([True, False]) if screw_count > 0 else False
    return {
        'hub_od': hub_od, 'hub_height': hub_height,
        'hub_screw_dia': screw_dia, 'hub_screw_count': screw_count,
        'hub_captured_nut': '1' if captured else '0',
        'hub_flat_depth': 0.0,
    }


def _rand_spokes(r, hub_od, bore_mm, teeth, pitch_mm):
    """Return spoke params or None."""
    # Need enough teeth for spokes to be geometrically valid
    min_teeth_for_spokes = max(20, int(40 / pitch_mm))
    if teeth < min_teeth_for_spokes or r.random() < 0.4:
        return None
    spoke_hub_od = hub_od if hub_od else round(bore_mm + r.uniform(3.0, 8.0), 1)
    rim_depth    = round(r.uniform(2.0, min(8.0, pitch_mm * 1.5)), 1)
    return {
        'spokes_enabled': '1',
        'spokes_hub_od':   spoke_hub_od,
        'spokes_rim_depth': rim_depth,
        'spokes_width':    round(r.uniform(3.0, 8.0), 1),
        'spokes_fillet_tip':  round(r.uniform(0.5, 3.0), 1),
        'spokes_fillet_base': round(r.uniform(1.0, 5.0), 1),
        'spokes_count':    r.choice([3, 4, 5, 6, 7]),
        'spokes_height':   round(r.uniform(0.0, 5.0), 1),
    }


def _rand_flange(r, is_3dprint, has_spokes, spoke_hub_od, teeth):
    """Return flange params or None."""
    if r.random() < 0.35:
        return None
    fp = {
        'flange_enabled':   '1',
        'flange_3dprint':   '1' if is_3dprint else '0',
        'flange_angle':     round(r.uniform(8.0, 25.0), 1),
        'flange_rim_radius': round(r.uniform(1.0, 8.0), 1),
        'flange_height':    round(r.uniform(0.5, 5.0), 1),
        'flange_plate_height': round(r.uniform(0.5, 3.0), 1),
        'flange_bend_radius':  round(r.uniform(0.0, 3.0), 1),
        'flange_top_separate': '1' if r.random() > 0.3 else '0',
    }
    if is_3dprint and fp['flange_top_separate'] == '1' and r.random() > 0.4:
        fp['flange_nubs_enabled'] = '1'
        fp['flange_nub_count']    = r.choice([2, 3, 4, 6])
        fp['flange_nub_dia']      = round(r.uniform(2.0, 6.0), 1)
        fp['flange_nub_height']   = round(r.uniform(1.0, 6.0), 1)
        fp['flange_nub_allowance'] = round(r.uniform(0.1, 0.4), 2)
    return fp


def _rand_config(r, run_idx):
    """Generate a complete random pulley configuration."""
    family = r.choice(list(FAMILIES.keys()))
    pitch  = r.choice(list(FAMILIES[family].keys()))
    spec   = FAMILIES[family][pitch]

    teeth     = r.randint(spec['min'], min(spec['max'], 80))
    bore_prof = r.choice(BORE_PROFILES)
    bore_mm, flat_depth, kw, kh = _rand_bore(r, spec['bore_max'], bore_prof)
    print_extra = round(r.uniform(0.0, 0.5), 2)
    cl_preset   = r.choice(CLEARANCE_PRESETS)
    bl_preset   = r.choice(BACKLASH_PRESETS)
    belt_height = round(r.uniform(6.0, 25.0), 1)
    dual        = r.random() < 0.3

    from geometry.pulley_geometry import PULLEY_SPECS, PROFILE_KEY_PREFIX
    pfx = PROFILE_KEY_PREFIX.get(family, '')
    key = pfx + pitch
    pitch_mm = PULLEY_SPECS[key]['pitch']

    hub   = _rand_hub(r, bore_mm, teeth)
    hub_od = hub['hub_od'] if hub else 0.0

    spokes = _rand_spokes(r, hub_od, bore_mm, teeth, pitch_mm)
    is_3dp = r.random() > 0.4
    flange = _rand_flange(r, is_3dp, spokes is not None,
                          spokes['spokes_hub_od'] if spokes else hub_od, teeth)

    cfg = {
        'run_idx':   run_idx,
        'family':    family,
        'pitch':     pitch,
        'teeth':     teeth,
        'bore':      bore_mm,
        'hub_flat_depth': flat_depth,
        'hub_keyway_w':   kw,
        'hub_keyway_h':   kh,
        'print_extra':    print_extra,
        'clearance_preset': cl_preset,
        'backlash_preset':  bl_preset,
        'belt_height': belt_height,
        'dual':        dual,
    }
    if hub:
        cfg.update(hub)
    if spokes:
        cfg.update(spokes)
    if flange:
        pfx2 = '' if not dual else ''
        cfg.update(flange)

    if dual:
        p2_teeth = r.randint(spec['min'], min(spec['max'], 80))
        cfg['p2_teeth']            = p2_teeth
        cfg['p2_bore']             = round(r.uniform(4.0, min(spec['bore_max'], 30.0)), 1)
        cfg['p2_print_extra']      = round(r.uniform(0.0, 0.5), 2)
        cfg['p2_clearance_preset'] = r.choice(CLEARANCE_PRESETS)
        cfg['p2_backlash_preset']  = r.choice(BACKLASH_PRESETS)

    return cfg


def _validate_config(cfg):
    """Run the config through the actual app parse functions.
    Returns True if the config is geometrically feasible, False otherwise.
    This uses the same code the download routes use, so it catches all the
    same edge cases a real request would hit.
    """
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from app import _parse_stl_params, _parse_hub_params, _parse_spoke_params, _parse_flange_params
        from geometry.pulley_geometry import getOuterDiameter, PULLEY_SPECS, PROFILE_KEY_PREFIX

        qs = _qs(cfg)

        # Parse pulley params the same way the download route does
        family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
            _parse_stl_params(qs, '1')

        key    = PROFILE_KEY_PREFIX.get(family, '') + pitch
        spec   = PULLEY_SPECS[key]
        pld    = spec.get('pitch_line_diff', spec.get('pitchLineDiff', 0.0))
        R_OD   = getOuterDiameter(num_teeth, spec['pitch'],
                                  pld + pr_ex - cl_mm) / 2.0

        # Bore must be smaller than pulley radius with room to spare
        if bore_mm >= R_OD - 1.0:
            return False

        # Hub OD must be smaller than pulley
        _, hub_h, _, _, _, _, _, _ = _parse_hub_params(qs, '')
        hub_od = float(qs.get('hub_od', 0))
        if hub_od > 0 and hub_od >= R_OD * 2 - 2.0:
            return False

        # Spoke validation
        if qs.get('spokes_enabled') == '1':
            sp_hub_od  = float(qs.get('spokes_hub_od', 0))
            rim_depth  = float(qs.get('spokes_rim_depth', 2.0))
            sp_width   = float(qs.get('spokes_width', 4.0))
            tip_f      = float(qs.get('spokes_fillet_tip', 1.0))
            base_f     = float(qs.get('spokes_fillet_base', 1.5))
            R_rim      = R_OD - rim_depth
            R_hub_s    = sp_hub_od / 2.0 if sp_hub_od > 0 else bore_mm / 2.0 + 1.0

            # Spoke void must have positive radial space
            if R_hub_s >= R_rim - 2.0:
                return False
            # Fillets can't exceed spoke width
            if tip_f + base_f >= sp_width:
                return False

        # P2 validation for dual
        if cfg.get('dual'):
            family2, pitch2, num_teeth2, bore2, _, cl2, _, _ = \
                _parse_stl_params(qs, '2')
            key2  = PROFILE_KEY_PREFIX.get(family2, '') + pitch2
            spec2 = PULLEY_SPECS[key2]
            pld2  = spec2.get('pitch_line_diff', spec2.get('pitchLineDiff', 0.0))
            R_OD2 = getOuterDiameter(num_teeth2, spec2['pitch'], pld2) / 2.0
            if bore2 >= R_OD2 - 1.0:
                return False

        return True

    except Exception:
        return False


def _rand_config_validated(r, run_idx, max_attempts=20):
    """Generate a random config, retrying until it passes app validation."""
    for _ in range(max_attempts):
        cfg = _rand_config(r, run_idx)
        if _validate_config(cfg):
            return cfg
    # Fall back to last attempt — better to test a marginal config than skip
    return cfg


def _save_inputs(run_id, configs):
    """Persist all configs for this run to logs/nightly_random/."""
    log_dir = Path(__file__).parent.parent / 'logs' / 'nightly_random'
    log_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime('%Y-%m-%d')
    path = log_dir / f'{date_str}_{run_id}.json'
    path.write_text(json.dumps({
        'run_id':    run_id,
        'timestamp': datetime.now().isoformat(),
        'configs':   configs,
    }, indent=2), encoding='utf-8')
    return path


def _qs(cfg):
    """Convert config dict to query-string dict for the Flask test client."""
    qs = {k: str(v) for k, v in cfg.items()
          if k not in ('run_idx',)}
    if cfg.get('dual'):
        qs['dual'] = 'true'
    return qs


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def random_configs():
    """Generate 5 random configs and save them to disk before any test runs."""
    seed    = int(datetime.now().strftime('%Y%m%d'))  # same seed for whole day
    r       = _rng(seed)
    run_id  = datetime.now().strftime('%H%M%S')
    configs = [_rand_config_validated(r, i) for i in range(5)]
    path    = _save_inputs(run_id, configs)
    print(f'\n[nightly] configs saved → {path}')
    return configs


@pytest.fixture(scope='module')
def client():
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('idx', range(5))
def test_random_svg(client, random_configs, idx):
    """SVG export succeeds for random config."""
    cfg = random_configs[idx]
    qs  = _qs(cfg)
    r   = client.get('/download/svg', query_string=qs)
    assert r.status_code == 200, \
        f'SVG failed for config[{idx}]: {r.data[:200]}\nConfig: {json.dumps(cfg, indent=2)}'
    assert b'<svg' in r.data, 'Response is not SVG'


@pytest.mark.parametrize('idx', range(5))
def test_random_dxf(client, random_configs, idx):
    """DXF export succeeds for random config."""
    cfg = random_configs[idx]
    qs  = _qs(cfg)
    r   = client.get('/download/dxf', query_string=qs)
    assert r.status_code == 200, \
        f'DXF failed for config[{idx}]: {r.data[:200]}\nConfig: {json.dumps(cfg, indent=2)}'
    assert b'SECTION' in r.data or b'0\n' in r.data, 'Response is not DXF'


@pytest.mark.parametrize('idx', range(5))
def test_random_stl_preview(client, random_configs, idx):
    """STL preview succeeds for random config."""
    cfg = random_configs[idx]
    qs  = _qs(cfg)
    r   = client.get('/api/preview-stl', query_string=qs)
    assert r.status_code == 200, \
        f'STL preview failed for config[{idx}]: {r.data[:200]}\nConfig: {json.dumps(cfg, indent=2)}'
    # STL binary starts with 80-byte header
    assert len(r.data) > 84, 'STL response too small'


@pytest.mark.parametrize('idx', range(5))
def test_random_stl_download(client, random_configs, idx):
    """STL download succeeds for random config."""
    cfg = random_configs[idx]
    qs  = _qs(cfg)
    r   = client.get('/download/stl', query_string=qs)
    assert r.status_code == 200, \
        f'STL download failed for config[{idx}]: {r.data[:200]}\nConfig: {json.dumps(cfg, indent=2)}'
    assert len(r.data) > 84, 'STL response too small'


@pytest.mark.parametrize('idx', range(5))
def test_random_step(client, random_configs, idx):
    """STEP export succeeds for random config (skipped if cadquery unavailable)."""
    pytest.importorskip('cadquery', reason='cadquery not installed')
    cfg = random_configs[idx]
    qs  = _qs(cfg)
    r   = client.get('/download/step', query_string=qs)
    assert r.status_code == 200, \
        f'STEP failed for config[{idx}]: {r.data[:200]}\nConfig: {json.dumps(cfg, indent=2)}'
    assert b'ISO-10303-21' in r.data, 'Response is not STEP'


@pytest.mark.parametrize('idx', range(5))
def test_random_metadata_roundtrip(client, random_configs, idx):
    """Embedded metadata can be extracted and matches original params."""
    import re
    cfg = random_configs[idx]
    qs  = _qs(cfg)
    r   = client.get('/download/svg', query_string=qs)
    assert r.status_code == 200
    text = r.data.decode('utf-8', errors='replace')
    m    = re.search(r'<cct>([\s\S]+?)</cct>', text)
    assert m, f'No CCT metadata in SVG for config[{idx}]'
    data   = json.loads(m.group(1))
    params = data.get('cct', data)
    assert params.get('family') == cfg['family'], \
        f'family mismatch: got {params.get("family")} expected {cfg["family"]}'
    assert str(params.get('teeth', params.get('p1_teeth', ''))) == str(cfg['teeth']) or \
           str(params.get('teeth', '')) == str(cfg['teeth']), \
        f'teeth mismatch in metadata for config[{idx}]'


# ── View helper ───────────────────────────────────────────────────────────────
# Run directly to print app URLs for any saved nightly run:
#
#   python tests/test_nightly_random.py
#   python tests/test_nightly_random.py logs/nightly_random/2026-06-07_134234.json
#
# Opens the URL in your browser if --open is passed.

if __name__ == '__main__':
    import sys
    import urllib.parse
    import webbrowser

    log_dir = Path(__file__).parent.parent / 'logs' / 'nightly_random'
    open_browser = '--open' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]

    if args:
        candidates = [Path(args[0])]
    else:
        candidates = sorted(log_dir.glob('*.json'))

    if not candidates:
        print('No nightly run files found in', log_dir)
        sys.exit(1)

    run_file = candidates[-1]
    print(f'Run file: {run_file}\n')
    data    = json.loads(run_file.read_text(encoding='utf-8'))
    configs = data['configs']
    base    = 'http://localhost:5000/'

    for cfg in configs:
        idx = cfg['run_idx']
        qs  = {k: str(v) for k, v in cfg.items() if k != 'run_idx'}
        if cfg.get('dual'):
            qs['dual'] = 'true'
        url = base + '?' + urllib.parse.urlencode(qs)
        print(f'Config {idx}:  {cfg["family"]} {cfg["pitch"]}  '
              f'{cfg["teeth"]}T  bore={cfg["bore"]}mm'
              + ('  spokes' if cfg.get('spokes_enabled') == '1' else '')
              + ('  flange' if cfg.get('flange_enabled') == '1' else '')
              + ('  dual'   if cfg.get('dual')           else ''))
        print(f'  {url}\n')
        if open_browser:
            webbrowser.open(url)
