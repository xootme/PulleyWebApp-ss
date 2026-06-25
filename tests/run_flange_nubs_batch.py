"""
run_flange_nubs_batch.py
Generate 20 random pulleys, all with 3D-print top flange + nubs, and test STEP.

Usage:
    .venv312\\Scripts\\python.exe tests\\run_flange_nubs_batch.py [--seed N] [--count N]

Exit code: 0 = all passed, 1 = any failure.
"""
import sys
import os
import random
import json
import argparse
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

_FAMILIES = {
    'HTD': {'3M': (10, 80), '5M': (12, 80), '8M': (15, 60), '14M': (18, 50)},
    'GT':  {'2M': (10, 80), '3M': (10, 80), '5M': (12, 70), '8M':  (20, 50)},
    'STD': {'2M': (10, 80), '3M': (10, 80), '5M': (12, 70), '8M':  (22, 50)},
}
_CLEARANCE = ['TIGHT', 'STANDARD', 'LOOSE']
_BACKLASH  = ['NONE', 'TIGHT', 'STANDARD', 'LOOSE']


def _raw_config(r):
    """Random config with top flange + nubs forced on."""
    family = r.choice(list(_FAMILIES))
    pitch  = r.choice(list(_FAMILIES[family]))
    t_min, t_max = _FAMILIES[family][pitch]
    bore = round(r.uniform(4.0, 30.0), 1)
    cfg = {
        'family':           family,
        'pitch':            pitch,
        'teeth':            r.randint(t_min, t_max),
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
        cfg['hub_od']     = hub_od
        cfg['hub_height'] = round(r.uniform(3.0, 18.0), 1)
        screw_dia = r.choice([0.0, 2.5, 3.0, 4.0, 5.0])
        if screw_dia > 0:
            cfg['hub_screw_dia']   = screw_dia
            cfg['hub_screw_count'] = r.choice([1, 2, 3])
            if r.random() > 0.5:
                cfg['hub_captured_nut'] = '1'
    else:
        hub_od = 0.0

    # Spokes (optional)
    if int(cfg['teeth']) >= 20 and r.random() > 0.4:
        sp_hub = hub_od if hub_od > bore else round(bore + r.uniform(2.0, 6.0), 1)
        cfg['spokes_enabled']     = '1'
        cfg['spokes_hub_od']      = sp_hub
        cfg['spokes_rim_depth']   = round(r.uniform(2.0, 7.0), 1)
        cfg['spokes_width']       = round(r.uniform(3.0, 8.0), 1)
        cfg['spokes_fillet_tip']  = round(r.uniform(0.5, 2.5), 1)
        cfg['spokes_fillet_base'] = round(r.uniform(1.0, 4.0), 1)
        cfg['spokes_count']       = r.choice([3, 4, 5, 6, 7])
        cfg['spokes_height']      = round(r.uniform(0.0, 5.0), 1)

    # Flange: always 3D-print top with nubs
    cfg['flange_enabled']       = '1'
    cfg['flange_3dprint']       = '1'
    cfg['flange_top_separate']  = '1'
    cfg['flange_nubs_enabled']  = '1'
    cfg['flange_angle']         = round(r.uniform(8.0, 25.0), 1)
    cfg['flange_rim_radius']    = round(r.uniform(1.0, 8.0), 1)
    cfg['flange_height']        = round(r.uniform(0.5, 5.0), 1)
    cfg['flange_plate_height']  = round(r.uniform(0.5, 3.0), 1)
    cfg['flange_bend_radius']   = round(r.uniform(0.0, 3.0), 1)
    cfg['flange_nub_count']     = r.choice([2, 3, 4, 6])
    cfg['flange_nub_dia']       = round(r.uniform(2.0, 6.0), 1)
    cfg['flange_nub_height']    = round(r.uniform(1.0, 6.0), 1)
    cfg['flange_nub_allowance'] = round(r.uniform(0.1, 0.4), 2)

    return cfg


def _make_config(r, idx, max_attempts=30):
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
            raw['run_idx'] = idx
            return raw
        except ValueError as e:
            last_err = e
    raise RuntimeError(f'Config[{idx}]: could not generate valid config after {max_attempts} attempts: {last_err}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed',  type=int, default=None)
    ap.add_argument('--count', type=int, default=20)
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else int(datetime.now().strftime('%Y%m%d%H%M'))
    r    = random.Random(seed)
    n    = args.count
    print(f'Seed: {seed}  Count: {n}')
    print('Generating configs...')
    configs = [_make_config(r, i) for i in range(n)]

    # Save config JSON
    log_dir = _ROOT / 'logs' / 'nightly_random'
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id   = datetime.now().strftime('%H%M%S')
    out_path = log_dir / f'{datetime.now().strftime("%Y-%m-%d")}_{run_id}_flange_nubs.json'
    out_path.write_text(json.dumps({
        'run_id': run_id, 'seed': seed,
        'timestamp': datetime.now().isoformat(),
        'configs': configs,
    }, indent=2), encoding='utf-8')
    print(f'Configs saved -> {out_path}\n')

    # Test STEP for each config
    from app import app as flask_app
    flask_app.config['TESTING'] = True

    passed = 0
    failed = 0
    with flask_app.test_client() as c:
        for cfg in configs:
            idx = cfg['run_idx']
            qs  = {k: str(v) for k, v in cfg.items() if k != 'run_idx'}
            tags = ' '.join(t for t in [
                cfg['family'] + ' ' + cfg['pitch'],
                str(cfg['teeth']) + 'T',
                'spoke' if cfg.get('spokes_enabled') == '1' else '',
                cfg.get('hub_keyway_w') and 'keyway' or '',
                cfg.get('hub_flat_depth') and 'dflat' or '',
            ] if t)
            resp = c.get('/download/step', query_string=qs)
            if resp.status_code == 200 and b'ISO-10303-21' in resp.data:
                print(f'  [{idx:2d}] PASS  {tags}')
                passed += 1
            else:
                body = resp.data[:200].decode('utf-8', errors='replace')
                print(f'  [{idx:2d}] FAIL  {tags}')
                print(f'        status={resp.status_code}  body={body!r}')
                print(f'        config: {json.dumps({k: v for k, v in cfg.items() if k != "run_idx"})}')
                failed += 1

    print(f'\nResult: {passed}/{n} passed, {failed} failed')
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
