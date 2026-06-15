"""
step_worker_ss.py
Subprocess worker that generates STEP using the small_step Rust binary.

Usage:
    python step_worker_ss.py <json-params>

Accepts the same JSON parameter format as step_worker.py.
export_type controls which small_step subcommand is used:

  'pulley' (default):
    small_step combined <dxf> <height_mm> [spoke_height_mm]
               [--top-3d|--top-metal ...]  [--nubs ...]
               [--bot-3d|--bot-metal ...]
               [--hub <od> <h> [--flat <d>] [--keyway <w> <h>]]

  'flange':
    small_step flange-3d  <r_inner> <r_od> <rim_r> <angle_deg> <height_mm> [top|bottom]
    small_step flange-metal <r_inner> <r_od> <rim_r> <angle_deg> <thick> <bend> [top|bottom]

Writes STEP bytes to stdout; errors to stderr.

Requires env var SMALL_STEP_BIN pointing to the small_step binary.
Features not yet supported by small_step (screw holes, captured nut) are
silently skipped — geometry still valid, just without those features.
"""
import sys
import json
import os
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _profile_key(family: str, pitch: str) -> str:
    from geometry.pulley_geometry import PROFILE_KEY_PREFIX
    return PROFILE_KEY_PREFIX.get(family, '') + pitch


def _compute_rod(family, pitch, num_teeth, print_extra_mm, clearance_mm):
    """Return (R_OD, tooth_ht) for the given pulley spec."""
    from geometry.pulley_geometry import PULLEY_SPECS, getOuterDiameter
    key  = _profile_key(family, pitch)
    spec = PULLEY_SPECS[key]
    pld  = spec.get('pitch_line_diff', spec.get('pitchLineDiff', 0.0))
    r_od = getOuterDiameter(num_teeth, spec['pitch'], pld + print_extra_mm - clearance_mm) / 2.0
    return r_od, spec['tooth_ht']


def _nub_circle_r(r_tooth_od, tooth_ht, nub_dia_mm):
    r_groove_bottom = r_tooth_od - tooth_ht
    margin = min(tooth_ht, 3.0)
    return r_groove_bottom - margin - nub_dia_mm / 2.0


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode(errors='replace'))
        sys.exit(1)
    sys.stdout.buffer.write(result.stdout)


def _export_flange(params, ss_bin):
    """Handle export_type='flange': calls small_step flange-3d or flange-metal."""
    from geometry.flange_geometry import (
        flange_inner_r_3dprint, flange_inner_r_3dprint_bottom,
        flange_inner_r_metal_top, flange_inner_r_metal_bottom,
    )
    family          = params['family']
    pitch           = params['pitch']
    num_teeth       = int(params['num_teeth'])
    bore_mm         = float(params['bore_mm'])
    clearance_mm    = float(params.get('clearance_mm', 0.0))
    print_extra_mm  = float(params.get('print_extra_mm', 0.0))
    hub_od_mm       = float(params.get('hub_od_mm', 0.0))
    spokes_enabled  = bool(params.get('spokes_enabled', False))
    spoke_hub_od_mm = float(params.get('spoke_hub_od_mm', 0.0))
    rim_depth_mm    = float(params.get('rim_depth_mm', 0.0))
    flange_3dprint  = bool(params.get('flange_3dprint', True))
    flange_angle_deg= float(params.get('flange_angle_deg', 15.0))
    rim_radius_mm   = float(params.get('rim_radius_mm', 3.0))
    flange_height_mm= float(params.get('flange_height_mm', 1.5))
    plate_height_mm = float(params.get('plate_height_mm', 1.0))
    bend_radius_mm  = float(params.get('bend_radius_mm', 0.0))
    which           = params.get('which', 'top')

    R_OD, tooth_ht = _compute_rod(family, pitch, num_teeth, print_extra_mm, clearance_mm)
    side_str = 'bottom' if which == 'bottom' else 'top'

    if flange_3dprint:
        _R_tr = R_OD - tooth_ht
        prof_r_tooth = _R_tr if spokes_enabled else R_OD
        if which == 'bottom':
            r_inner = flange_inner_r_3dprint_bottom(
                bore_mm, spokes_enabled, spoke_hub_od_mm,
                r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
        else:
            r_inner = flange_inner_r_3dprint(
                bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
        cmd = [ss_bin, 'flange-3d',
               str(r_inner), str(prof_r_tooth), str(rim_radius_mm),
               str(flange_angle_deg), str(flange_height_mm), side_str]
    else:
        _bend = bend_radius_mm if bend_radius_mm > 0.0 else 1.5 * plate_height_mm
        _bend = min(_bend, rim_radius_mm * 0.8)
        if which == 'bottom':
            r_inner = flange_inner_r_metal_bottom(
                bore_mm, spokes_enabled, spoke_hub_od_mm,
                r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
        else:
            r_inner = flange_inner_r_metal_top(
                bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
        cmd = [ss_bin, 'flange-metal',
               str(r_inner), str(R_OD), str(rim_radius_mm),
               str(flange_angle_deg), str(plate_height_mm), str(_bend), side_str]

    _run(cmd)


def main():
    params = json.loads(sys.argv[1])

    ss_bin = os.environ.get('SMALL_STEP_BIN', '')
    if not ss_bin or not os.path.isfile(ss_bin):
        sys.stderr.write(f'SMALL_STEP_BIN not set or binary not found: {ss_bin!r}\n')
        sys.exit(1)

    export_type = params.pop('export_type', 'pulley')
    if export_type == 'flange':
        _export_flange(params, ss_bin)
        return

    family          = params['family']
    pitch           = params['pitch']
    num_teeth       = int(params['num_teeth'])
    bore_mm         = float(params['bore_mm'])
    belt_height_mm  = float(params['belt_height_mm'])
    clearance_mm    = float(params.get('clearance_mm', 0.0))
    backlash_mm     = float(params.get('backlash_mm', 0.0))
    print_extra_mm  = float(params.get('print_extra_mm', 0.0))
    spoke_count     = int(params.get('spoke_count', 0))
    spoke_width_mm  = float(params.get('spoke_width_mm', 0.0))
    spoke_hub_od_mm = float(params.get('spoke_hub_od_mm', 0.0))
    rim_depth_mm    = float(params.get('rim_depth_mm', 0.0))
    fillet_tip_mm   = float(params.get('fillet_tip_mm', 0.0))
    fillet_base_mm  = float(params.get('fillet_base_mm', 0.0))
    spoke_height_mm = float(params.get('spoke_height_mm', 0.0))
    hub_od_mm       = float(params.get('hub_od_mm', 0.0))
    hub_height_mm   = float(params.get('hub_height_mm', 0.0))
    flat_depth_mm   = float(params.get('flat_depth_mm', 0.0))
    keyway_w_mm     = float(params.get('keyway_w_mm', 0.0))
    keyway_h_mm     = float(params.get('keyway_h_mm', 0.0))
    flange_enabled      = bool(params.get('flange_enabled', False))
    flange_3dprint      = bool(params.get('flange_3dprint', True))
    flange_angle_deg    = float(params.get('flange_angle_deg', 15.0))
    flange_rim_r_mm     = float(params.get('flange_rim_radius_mm', 3.0))
    flange_height_mm    = float(params.get('flange_height_mm', 1.5))
    plate_height_mm     = float(params.get('plate_height_mm', 1.0))
    bend_radius_mm      = float(params.get('bend_radius_mm', 0.0))
    nubs_enabled        = bool(params.get('nubs_enabled', False))
    nub_count           = int(params.get('nub_count', 4))
    nub_dia_mm          = float(params.get('nub_dia_mm', 3.0))
    nub_height_mm       = float(params.get('nub_height_mm', 2.0))
    nub_allowance_mm    = float(params.get('nub_allowance_mm', 0.2))

    # Generate DXF
    from exporters.dxf_exporter import generate_dxf
    dxf_bytes = generate_dxf(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=clearance_mm, backlash_mm=backlash_mm,
        print_extra_mm=print_extra_mm,
        spoke_count=spoke_count,
        spoke_width_mm=spoke_width_mm, spoke_hub_od_mm=spoke_hub_od_mm,
        rim_depth_mm=rim_depth_mm, fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm,
        flat_depth_mm=flat_depth_mm, keyway_w_mm=keyway_w_mm, keyway_h_mm=keyway_h_mm,
    )
    if isinstance(dxf_bytes, str):
        dxf_bytes = dxf_bytes.encode()

    with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False, mode='wb') as f:
        dxf_tmp = f.name
        f.write(dxf_bytes)

    try:
        cmd = [ss_bin, 'combined', dxf_tmp, str(belt_height_mm)]

        if spoke_height_mm > 0.0:
            cmd.append(str(spoke_height_mm))

        if flange_enabled:
            from geometry.flange_geometry import (
                flange_inner_r_3dprint, flange_inner_r_3dprint_bottom,
                flange_inner_r_metal_top, flange_inner_r_metal_bottom,
            )
            R_OD, tooth_ht = _compute_rod(family, pitch, num_teeth, print_extra_mm, clearance_mm)
            _R_tr = R_OD - tooth_ht
            spokes_on = spoke_count > 0

            if flange_3dprint:
                prof_r_tooth = _R_tr if spokes_on else R_OD
                r_inner_top = flange_inner_r_3dprint(
                    bore_mm, hub_od_mm, spokes_on, spoke_hub_od_mm,
                    r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
                r_inner_bot = flange_inner_r_3dprint_bottom(
                    bore_mm, spokes_on, spoke_hub_od_mm,
                    r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
                cmd += ['--top-3d',
                        str(r_inner_top), str(prof_r_tooth),
                        str(flange_rim_r_mm), str(flange_angle_deg), str(flange_height_mm)]
                if nubs_enabled:
                    r_nub = _nub_circle_r(R_OD, tooth_ht, nub_dia_mm)
                    cmd += ['--nubs',
                            str(nub_count), str(nub_dia_mm),
                            str(nub_height_mm), str(nub_allowance_mm), str(r_nub)]
                cmd += ['--bot-3d',
                        str(r_inner_bot), str(prof_r_tooth),
                        str(flange_rim_r_mm), str(flange_angle_deg), str(flange_height_mm)]
            else:
                # Metal flanges
                _bend = bend_radius_mm if bend_radius_mm > 0.0 else 1.5 * plate_height_mm
                _bend = min(_bend, flange_rim_r_mm * 0.8)
                r_inner_top = flange_inner_r_metal_top(
                    bore_mm, hub_od_mm, spokes_on, spoke_hub_od_mm,
                    r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
                r_inner_bot = flange_inner_r_metal_bottom(
                    bore_mm, spokes_on, spoke_hub_od_mm,
                    r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
                cmd += ['--top-metal',
                        str(r_inner_top), str(R_OD),
                        str(flange_rim_r_mm), str(flange_angle_deg),
                        str(plate_height_mm), str(_bend)]
                cmd += ['--bot-metal',
                        str(r_inner_bot), str(R_OD),
                        str(flange_rim_r_mm), str(flange_angle_deg),
                        str(plate_height_mm), str(_bend)]

        if hub_od_mm > bore_mm and hub_height_mm > 0.0:
            cmd += ['--hub', str(hub_od_mm), str(hub_height_mm)]
            if flat_depth_mm > 0.0:
                cmd += ['--flat', str(flat_depth_mm)]
            if keyway_w_mm > 0.0 and keyway_h_mm > 0.0:
                cmd += ['--keyway', str(keyway_w_mm), str(keyway_h_mm)]

        _run(cmd)
    finally:
        try:
            os.unlink(dxf_tmp)
        except OSError:
            pass


if __name__ == '__main__':
    main()
