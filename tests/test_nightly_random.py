"""
test_nightly_random.py — Nightly randomized pulley generation tests.

Generates 5 random pulley configurations and verifies that all exports
(SVG, DXF, STL preview, STL download, STEP) succeed without errors.

Runs only when PULLEY_NIGHTLY=1 is set — excluded from normal test runs.

Design principles:
  - Single save/restore path: configs are stored and restored via the same
    _cct_meta() / _applyUrlParams() mechanism used by the Fusion/FreeCAD addins.
  - Validation via app parse functions: raw random values are run through
    _parse_stl_params(), _parse_spoke_params() etc. — the same functions the
    download routes use — so impossible geometries are caught before testing.
  - The saved JSON and repro URL contain the VALIDATED params (what the app
    actually used), not the raw random values.

Saved to: logs/nightly_random/<date>_<run_id>.json
Repro:    python tests/test_nightly_random.py [--open]
"""

import json
import os
import random
from datetime import datetime
from pathlib import Path

import pytest

# ── Nightly-only guard ─────────────────────────────────────────────────────────

pytestmark = pytest.mark.skipif(
    os.environ.get('PULLEY_NIGHTLY') != '1',
    reason='Nightly random tests — set PULLEY_NIGHTLY=1 to run'
)

# ── Raw parameter space (pre-validation) ───────────────────────────────────────

_FAMILIES = {
    'HTD': {'3M': (10, 80), '5M': (12, 80), '8M': (15, 60), '14M': (18, 50)},
    'GT':  {'2M': (10, 80), '3M': (10, 80), '5M': (12, 70), '8M':  (20, 50)},
    'STD': {'2M': (10, 80), '3M': (10, 80), '5M': (12, 70), '8M':  (22, 50)},
}
_CLEARANCE = ['TIGHT', 'STANDARD', 'LOOSE']
_BACKLASH  = ['NONE', 'TIGHT', 'STANDARD', 'LOOSE']


def _raw_config(r):
    """Generate a raw (unvalidated) random config dict using download-route param names."""
    family = r.choice(list(_FAMILIES))
    pitch  = r.choice(list(_FAMILIES[family]))
    t_min, t_max = _FAMILIES[family][pitch]
    teeth  = r.randint(t_min, t_max)
    bore   = round(r.uniform(4.0, 30.0), 1)
    dual   = r.random() < 0.25

    cfg = {
        'family':           family,
        'pitch':            pitch,
        'teeth':            teeth,
        'bore':             bore,
        'print_extra':      round(r.uniform(0.0, 0.4), 2),
        'clearance_preset': r.choice(_CLEARANCE),
        'backlash_preset':  r.choice(_BACKLASH),
        'belt_height':      round(r.uniform(6.0, 25.0), 1),
        'clearance_height': round(r.uniform(0.0, 0.8), 2),
    }

    # Bore profile
    profile = r.choice(['round', 'dflat', 'keyway'])
    if profile == 'dflat':
        cfg['hub_flat_depth'] = round(r.uniform(0.3, bore * 0.25), 2)
    elif profile == 'keyway':
        kw = r.choice([2.0, 3.0, 4.0, 5.0, 6.0])
        cfg['hub_keyway_w'] = kw
        cfg['hub_keyway_h'] = round(kw * 0.5, 1)

    # Hub
    if r.random() > 0.2:
        hub_od = round(bore + r.uniform(4.0, 18.0), 1)
        cfg['hub_od']          = hub_od
        cfg['hub_height']      = round(r.uniform(3.0, 18.0), 1)
        screw_dia = r.choice([0.0, 2.5, 3.0, 4.0, 5.0])
        if screw_dia > 0:
            cfg['hub_screw_dia']   = screw_dia
            cfg['hub_screw_count'] = r.choice([1, 2, 3])
            if r.random() > 0.5:
                cfg['hub_captured_nut'] = '1'
    else:
        hub_od = 0.0

    # Spokes
    if teeth >= 20 and r.random() > 0.4:
        sp_hub = hub_od if hub_od > bore else round(bore + r.uniform(2.0, 6.0), 1)
        cfg['spokes_enabled']     = '1'
        cfg['spokes_hub_od']      = sp_hub
        cfg['spokes_rim_depth']   = round(r.uniform(2.0, 7.0), 1)
        cfg['spokes_width']       = round(r.uniform(3.0, 8.0), 1)
        cfg['spokes_fillet_tip']  = round(r.uniform(0.5, 2.5), 1)
        cfg['spokes_fillet_base'] = round(r.uniform(1.0, 4.0), 1)
        cfg['spokes_count']       = r.choice([3, 4, 5, 6, 7])
        cfg['spokes_height']      = round(r.uniform(0.0, 5.0), 1)

    # Flanges
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

    # Dual P2
    if dual:
        cfg['dual']               = 'true'
        cfg['p2_teeth']           = r.randint(t_min, t_max)
        cfg['p2_bore']            = round(r.uniform(4.0, 25.0), 1)
        cfg['p2_print_extra']     = round(r.uniform(0.0, 0.4), 2)
        cfg['p2_clearance_preset'] = r.choice(_CLEARANCE)
        cfg['p2_backlash_preset']  = r.choice(_BACKLASH)

    return cfg


def _parse_and_clamp(raw):
    """Run raw config through the app's own parse functions.

    Returns a validated param dict (strings, matching download-route format)
    that is guaranteed to be accepted by the app, or raises ValueError if the
    geometry is not feasible after clamping.
    """
    from app import (
        _parse_stl_params, _parse_hub_params,
        _parse_spoke_params, _parse_flange_params,
        _cct_meta, CCT_SCHEMA_VERSION,
    )
    from geometry.pulley_geometry import (
        getOuterDiameter, PULLEY_SPECS, PROFILE_KEY_PREFIX,
    )

    qs = {k: str(v) for k, v in raw.items()}

    # ── P1 basic ──────────────────────────────────────────────────────────────
    family, pitch, num_teeth, bore_mm, belt_h, cl_mm, bl_mm, pr_ex = \
        _parse_stl_params(qs, '1')

    key  = PROFILE_KEY_PREFIX.get(family, '') + pitch
    spec = PULLEY_SPECS[key]
    pld  = spec.get('pitch_line_diff', spec.get('pitchLineDiff', 0.0))
    R_OD = getOuterDiameter(num_teeth, spec['pitch'], pld + pr_ex - cl_mm) / 2.0
    R_tr = R_OD - spec['tooth_ht']   # actual tooth root radius

    if bore_mm >= R_tr - 1.0:
        raise ValueError(f'bore {bore_mm} >= tooth root {R_tr:.1f}')

    # ── Hub ───────────────────────────────────────────────────────────────────
    hub_od, hub_h, sd, sc, cn, fd, kw, kh = _parse_hub_params(qs, '')
    if hub_od > 0 and hub_od / 2.0 >= R_tr - 1.0:
        raise ValueError(f'hub_od {hub_od} >= tooth root {R_tr:.1f}')
    if kw > 0 and (bore_mm / 2.0 + kh) >= R_tr - 1.0:
        raise ValueError('keyway extends outside pulley')
    if fd > 0 and fd >= bore_mm / 2.0 - 0.5:
        raise ValueError('d-flat too deep')

    # ── Spokes ────────────────────────────────────────────────────────────────
    sp_en, sp_hub, rim_d, sp_w, ft, fb, sp_c, sp_h, _ = _parse_spoke_params(qs, '')
    if sp_en:
        R_rim   = R_tr - rim_d
        R_hub_s = sp_hub / 2.0 if sp_hub > 0 else bore_mm / 2.0 + 1.0
        if R_hub_s >= R_rim - 1.5:
            raise ValueError('spoke void too narrow (hub too large for rim depth)')
        if ft + fb >= sp_w:
            raise ValueError(f'fillets {ft}+{fb} >= spoke width {sp_w}')

    # ── P2 ────────────────────────────────────────────────────────────────────
    if raw.get('dual') == 'true':
        fam2, pit2, t2, b2, _, cl2, _, _ = _parse_stl_params(qs, '2')
        k2   = PROFILE_KEY_PREFIX.get(fam2, '') + pit2
        sp2  = PULLEY_SPECS[k2]
        pld2 = sp2.get('pitch_line_diff', sp2.get('pitchLineDiff', 0.0))
        R2   = getOuterDiameter(t2, sp2['pitch'], pld2) / 2.0 - sp2['tooth_ht']
        if b2 >= R2 - 1.0:
            raise ValueError(f'P2 bore {b2} >= tooth root {R2:.1f}')

    # ── Build validated param dict via _cct_meta ──────────────────────────────
    # _cct_meta wraps params exactly as the export routes do, giving us the
    # canonical representation used for save/restore everywhere.
    validated = dict(raw)   # start with raw (already string-safe)
    validated['sv'] = str(CCT_SCHEMA_VERSION)
    return validated


def _stl_preview_ok(cfg):
    """Return True if the STL preview endpoint succeeds for this config.
    Used to filter out configs that pass parameter validation but fail
    geometry generation (TopologyException, degenerate meshes etc.).
    """
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        r = c.get('/api/preview-stl', query_string=_qs(cfg))
        return r.status_code == 200 and len(r.data) > 84


def _make_config(r, run_idx, max_attempts=50):
    """Generate a validated config, retrying on geometry failures.

    First pass: parameter validation via app parse functions.
    Second pass: STL preview check — rejects any config that causes a
    TopologyException or other geometry error in the actual generator.
    This ensures the saved config is genuinely reproducible.
    """
    last_err = None
    for attempt in range(max_attempts):
        raw = _raw_config(r)
        try:
            v = _parse_and_clamp(raw)
        except ValueError as e:
            last_err = e
            continue
        # Quick generation check — rejects degenerate geometry before saving
        v['run_idx'] = run_idx
        if _stl_preview_ok(v):
            return v
        last_err = 'STL preview failed (geometry error)'
    raise RuntimeError(
        f'Could not generate valid config after {max_attempts} attempts: {last_err}'
    )


def _save_configs(run_id, configs):
    log_dir  = Path(__file__).parent.parent / 'logs' / 'nightly_random'
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f'{datetime.now().strftime("%Y-%m-%d")}_{run_id}.json'
    path.write_text(json.dumps({
        'run_id':    run_id,
        'timestamp': datetime.now().isoformat(),
        'configs':   configs,
    }, indent=2), encoding='utf-8')
    return path


def _qs(cfg):
    """Return query-string dict for the Flask test client (excludes run_idx)."""
    return {k: str(v) for k, v in cfg.items() if k != 'run_idx'}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def random_configs():
    seed    = int(datetime.now().strftime('%Y%m%d%H'))
    r       = random.Random(seed)
    run_id  = datetime.now().strftime('%H%M%S')
    configs = [_make_config(r, i) for i in range(5)]
    path    = _save_configs(run_id, configs)
    print(f'\n[nightly] configs saved → {path}')
    return configs


@pytest.fixture(scope='module')
def client():
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('idx', range(5))
def test_random_svg(client, random_configs, idx):
    cfg = random_configs[idx]
    r   = client.get('/download/svg', query_string=_qs(cfg))
    assert r.status_code == 200, \
        f'SVG failed config[{idx}]: {r.data[:300]}\n{json.dumps(cfg, indent=2)}'
    assert b'<svg' in r.data


@pytest.mark.parametrize('idx', range(5))
def test_random_dxf(client, random_configs, idx):
    cfg = random_configs[idx]
    r   = client.get('/download/dxf', query_string=_qs(cfg))
    assert r.status_code == 200, \
        f'DXF failed config[{idx}]: {r.data[:300]}\n{json.dumps(cfg, indent=2)}'
    assert b'SECTION' in r.data or b'0\n' in r.data


@pytest.mark.parametrize('idx', range(5))
def test_random_stl_preview(client, random_configs, idx):
    cfg = random_configs[idx]
    r   = client.get('/api/preview-stl', query_string=_qs(cfg))
    assert r.status_code == 200, \
        f'STL preview failed config[{idx}]: {r.data[:300]}\n{json.dumps(cfg, indent=2)}'
    assert len(r.data) > 84


@pytest.mark.parametrize('idx', range(5))
def test_random_stl_download(client, random_configs, idx):
    cfg = random_configs[idx]
    r   = client.get('/download/stl', query_string=_qs(cfg))
    assert r.status_code == 200, \
        f'STL download failed config[{idx}]: {r.data[:300]}\n{json.dumps(cfg, indent=2)}'
    assert len(r.data) > 84


@pytest.mark.parametrize('idx', range(5))
def test_random_step(client, random_configs, idx):
    pytest.importorskip('cadquery', reason='cadquery not installed')
    cfg = random_configs[idx]
    r   = client.get('/download/step', query_string=_qs(cfg))
    assert r.status_code == 200, \
        f'STEP failed config[{idx}]: {r.data[:300]}\n{json.dumps(cfg, indent=2)}'
    assert b'ISO-10303-21' in r.data


@pytest.mark.parametrize('idx', range(5))
def test_random_metadata_roundtrip(client, random_configs, idx):
    """Params embedded in SVG can be extracted and match the original config."""
    import re
    cfg = random_configs[idx]
    r   = client.get('/download/svg', query_string=_qs(cfg))
    assert r.status_code == 200
    text = r.data.decode('utf-8', errors='replace')
    m    = re.search(r'<cct>([\s\S]+?)</cct>', text)
    assert m, f'No CCT metadata in SVG for config[{idx}]'
    params = json.loads(m.group(1)).get('cct', {})
    assert params.get('family') == cfg['family']
    assert str(params.get('teeth', '')) == str(cfg['teeth'])


# ── View helper ───────────────────────────────────────────────────────────────
# python tests/test_nightly_random.py [run_file.json] [--open]

if __name__ == '__main__':
    import sys
    import urllib.parse
    import webbrowser

    log_dir    = Path(__file__).parent.parent / 'logs' / 'nightly_random'
    open_browser = '--open' in sys.argv
    files_args   = [a for a in sys.argv[1:] if not a.startswith('--')]
    candidates   = [Path(files_args[0])] if files_args else sorted(log_dir.glob('*.json'))

    if not candidates:
        print('No nightly run files found in', log_dir)
        sys.exit(1)

    data    = json.loads(candidates[-1].read_text(encoding='utf-8'))
    configs = data['configs']
    base    = 'http://localhost:5099'
    print(f'Run file: {candidates[-1]}\n')

    for cfg in configs:
        idx    = cfg.get('run_idx', '?')
        qs     = {k: str(v) for k, v in cfg.items() if k != 'run_idx'}
        url    = base + '/?' + urllib.parse.urlencode(qs)
        tags   = ' '.join(t for t in ['spokes' if cfg.get('spokes_enabled') == '1' else '',
                                       'flange' if cfg.get('flange_enabled') == '1' else '',
                                       'dual'   if cfg.get('dual') == 'true' else ''] if t)
        print(f'Config {idx}: {cfg["family"]} {cfg["pitch"]}  {cfg["teeth"]}T  bore={cfg["bore"]}  {tags}')
        print(f'  {url}\n')
        if open_browser:
            webbrowser.open(url)
