"""
step_worker_ss.py
Subprocess worker that generates STEP using the small_step Rust binary.

Usage:
    python step_worker_ss.py <json-params>

Accepts the same JSON parameter format as step_worker.py.
export_type controls the operation:

  'pulley' (default):
    small_step combined <input.dxf> <height_mm> [spoke_height_mm]
               [--top-3d|--top-metal ...]  [--nubs ...]
               [--bot-3d|--bot-metal ...]
               [--hub <od> <h> [--flat <d>] [--keyway <w> <h>]]

  'flange':
    small_step flange-3d  <r_inner> <r_od> <rim_r> <angle_deg> <height_mm> [top|bottom]
    small_step flange-metal <r_inner> <r_od> <rim_r> <angle_deg> <thick> <bend> [top|bottom]

  'belt':
    Generates belt DXF via generate_belt_dxf_for_step, then:
    small_step <belt.dxf> <height_mm>
    Required params: family, pitch, num_teeth_left, num_teeth_right,
                     center_dist_mm, belt_height_mm

  'all':
    Generates each part (P1, P2, belt) via small_step, merges into one STEP file.
    Required params: same as 'pulley' for P1, plus optional kw2 (P2 dict),
                     belt_kw (belt dict).
    Parts are merged with entity renumbering; no positional offset applied
    (all parts at origin — experimental).

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
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Minimum compatible small_step binary version (MAJOR, MINOR, PATCH).
# Bump MINOR when a new subcommand or flag is required; MAJOR on breaking CLI changes.
SMALL_STEP_MIN_VERSION = (0, 2, 0)

_version_checked: dict = {}   # bin_path → True once verified


def _check_binary_version(ss_bin: str) -> None:
    """Raise RuntimeError if the binary is older than SMALL_STEP_MIN_VERSION."""
    if ss_bin in _version_checked:
        return
    try:
        r = subprocess.run([ss_bin, '--version'], capture_output=True, text=True, timeout=5)
        # expects stdout: "small_step X.Y.Z"
        parts = r.stdout.strip().split()
        if len(parts) != 2 or parts[0] != 'small_step':
            raise RuntimeError(f'unexpected --version output: {r.stdout.strip()!r}')
        ver = tuple(int(x) for x in parts[1].split('.'))
        if ver < SMALL_STEP_MIN_VERSION:
            raise RuntimeError(
                f'small_step binary is v{parts[1]}, need >={".".join(str(x) for x in SMALL_STEP_MIN_VERSION)}. '
                f'Rebuild from source and update bin/small_step_linux.'
            )
    except FileNotFoundError:
        raise RuntimeError(f'small_step binary not executable: {ss_bin!r}')
    _version_checked[ss_bin] = True


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
    # Rust NubParams.r_outer_mm = outer edge of nub circle; it subtracts dia/2 for centre.
    r_groove_bottom = r_tooth_od - tooth_ht
    margin = min(tooth_ht, 3.0)
    return r_groove_bottom - margin


def _run_cmd(cmd) -> bytes:
    """Run cmd, return stdout bytes.  On failure write stderr and exit(1)."""
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode(errors='replace'))
        sys.exit(1)
    return result.stdout


def _translate_step_x(step_bytes: bytes, dx: float) -> bytes:
    """Translate a STEP B-rep solid by dx along X.

    Only CARTESIAN_POINT coordinates are positions; DIRECTION/VECTOR entities are
    orientation vectors and must NOT be shifted. Adding dx to the X of every
    CARTESIAN_POINT cleanly translates the solid in place.
    """
    if not dx or not step_bytes:
        return step_bytes
    text = step_bytes.decode('utf-8', errors='replace')

    # Match STEP number formats: 1.5  0.  1.  .5  1  1.5E3
    # The bare-trailing-dot form (0. 1.) is common in small_step output and
    # must be matched or axis-center points at integer X coords are skipped.
    _num = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
    pat = re.compile(
        r"(CARTESIAN_POINT\(\s*(?:'[^']*'|\$)\s*,\s*\(\s*)"
        rf"({_num})(\s*,\s*)({_num})(\s*,\s*)({_num})(\s*\)\s*\))"
    )

    def _shift(mo):
        x = float(mo.group(2)) + dx
        # STEP-friendly number: trim trailing zeros but keep a trailing dot.
        xs = repr(x)
        return f"{mo.group(1)}{xs}{mo.group(3)}{mo.group(4)}{mo.group(5)}{mo.group(6)}{mo.group(7)}"

    text = pat.sub(_shift, text)
    return text.encode('utf-8')


def _rotate_step_z(step_bytes: bytes, phi: float) -> bytes:
    """Rotate a STEP B-rep solid around the Z-axis by phi radians (compass CW).

    CW convention (matches _rot2d / SVG phase): x' = x·cos+y·sin, y' = -x·sin+y·cos.
    Both CARTESIAN_POINT (positions) and DIRECTION (surface normals / axis orientations)
    must be rotated — directions are orientation vectors and transform the same way as
    positions under a pure rotation (unlike translation, which leaves directions unchanged).
    """
    import math
    if abs(phi) < 1e-9 or not step_bytes:
        return step_bytes
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    text = step_bytes.decode('utf-8', errors='replace')

    _num = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'

    def _make_rot_fn(cos_p, sin_p, z_pass_through=True):
        def _rot(mo):
            x, y = float(mo.group(2)), float(mo.group(4))
            xr = x * cos_p + y * sin_p
            yr = -x * sin_p + y * cos_p
            z_part = mo.group(6)  # preserve Z text exactly (no change for Z-rotation)
            return f"{mo.group(1)}{repr(xr)}{mo.group(3)}{repr(yr)}{mo.group(5)}{z_part}{mo.group(7)}"
        return _rot

    rot_fn = _make_rot_fn(cos_phi, sin_phi)

    for entity in ('CARTESIAN_POINT', 'DIRECTION'):
        pat = re.compile(
            rf"({entity}\(\s*(?:'[^']*'|\$)\s*,\s*\(\s*)"
            rf"({_num})(\s*,\s*)({_num})(\s*,\s*)({_num})(\s*\)\s*\))"
        )
        text = pat.sub(rot_fn, text)

    return text.encode('utf-8')


_STEP_PART_NAMES = {
    'pulley':        'Pulley',
    'top_flange':    'Top Flange',
    'bottom_flange': 'Bottom Flange',
    'hub':           'Hub',
    'belt':          'Belt',
    'flange':        'Flange',
}


def _rename_step_products(step_bytes: bytes, suffix: str = '', prefix: str = '') -> bytes:
    """Rename PRODUCT name fields in a STEP DATA section.

    Builds display names like "HTD-5M-40T Pulley 1" from snake_case STEP names.
    prefix  — pulley spec e.g. 'HTD-5M-40T' or belt spec e.g. 'HTD-5M-63T'
    suffix  — index suffix e.g. ' 1' or ' 2' (empty for single-pulley exports)
    """
    text = step_bytes.decode('utf-8', errors='replace')

    def _replace(m):
        old  = m.group(2)
        base = _STEP_PART_NAMES.get(old, old)
        if prefix:
            new = f'{prefix} {base}{suffix}'
        else:
            new = f'{base}{suffix}'
        return f"{m.group(1)}'{new}','{new}'"

    # Match only bare PRODUCT( lines (not PRODUCT_DEFINITION, PRODUCT_CONTEXT, etc.)
    text = re.sub(
        r'(^#\d+=PRODUCT\()\'([^\']+)\',\'[^\']*\'',
        _replace,
        text,
        flags=re.MULTILINE,
    )
    return text.encode('utf-8')


def _merge_steps(step_parts: list, label: str = 'assembly') -> bytes:
    """Concatenate multiple STEP DATA sections, renumbering entity IDs.

    Each part's entities are offset so IDs don't collide.  No positional
    transforms are applied — all parts share the same coordinate origin.
    """
    all_data = []
    offset = 0

    for part_bytes in step_parts:
        if not part_bytes:
            continue
        text = part_bytes.decode('utf-8', errors='replace')
        m = re.search(r'\bDATA;\s*(.*?)\s*ENDSEC;', text, re.DOTALL | re.IGNORECASE)
        if not m:
            continue
        data = m.group(1).strip()

        nums = [int(n) for n in re.findall(r'^#(\d+)\s*=', data, re.MULTILINE)]
        if not nums:
            continue
        max_num = max(nums)

        if offset > 0:
            data = re.sub(r'#(\d+)', lambda mo: f'#{int(mo.group(1)) + offset}', data)

        all_data.append(data)
        offset += max_num

    header = (
        'ISO-10303-21;\n'
        'HEADER;\n'
        f"FILE_DESCRIPTION(('{label}'),'2;1');\n"
        f"FILE_NAME('{label}.step','',(''),(''),'',$,$);\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
        'ENDSEC;\n'
        'DATA;\n'
    )
    return (header + '\n'.join(all_data) + '\nENDSEC;\nEND-ISO-10303-21;\n').encode('utf-8')


def _build_pulley_cmd(params, ss_bin, dxf_tmp):
    """Build the small_step combined command for a pulley."""
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
            # prof_r_tooth = tooth-root OD for spokes (flat face stops at rim ring),
            # full OD otherwise. Pass the same value as r_tooth_OD so flange_inner_r
            # computes the correct spoke-void OD (R_tr - rim_depth_mm).
            prof_r_tooth = _R_tr if spokes_on else R_OD
            r_inner_top = flange_inner_r_3dprint(
                bore_mm, hub_od_mm, spokes_on, spoke_hub_od_mm,
                r_tooth_OD=prof_r_tooth, rim_depth_mm=rim_depth_mm)
            r_inner_bot = flange_inner_r_3dprint_bottom(
                bore_mm, spokes_on, spoke_hub_od_mm,
                r_tooth_OD=prof_r_tooth, rim_depth_mm=rim_depth_mm)
            cmd += ['--top-3d',
                    str(r_inner_top), str(prof_r_tooth),
                    str(flange_rim_r_mm), str(flange_angle_deg), str(flange_height_mm)]
            if nubs_enabled:
                import math as _math
                r_nub = _nub_circle_r(R_OD, tooth_ht, nub_dia_mm)
                r_nub_centre = r_nub - nub_dia_mm / 2.0
                if r_nub_centre > 0:
                    max_safe = max(1, int(_math.pi * r_nub_centre / (nub_dia_mm / 2.0)))
                    if nub_count > max_safe:
                        sys.stderr.write(
                            f'warning: nub_count {nub_count} clamped to {max_safe}'
                            f' (nubs overlap at r={r_nub_centre:.1f} mm)\n'
                        )
                        nub_count = max_safe
                cmd += ['--nubs',
                        str(nub_count), str(nub_dia_mm),
                        str(nub_height_mm), str(nub_allowance_mm), str(r_nub)]
            cmd += ['--bot-3d',
                    str(r_inner_bot), str(prof_r_tooth),
                    str(flange_rim_r_mm), str(flange_angle_deg), str(flange_height_mm)]
        else:
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
        screw_dia_mm = float(params.get('screw_dia_mm', 0.0))
        screw_count  = int(params.get('screw_count', 0))
        captured_nut = bool(params.get('captured_nut', False))
        if screw_dia_mm > 0.0 and screw_count > 0:
            cmd += ['--screws', str(screw_dia_mm), str(screw_count)]
            if captured_nut:
                cmd += ['--captured-nut']

    return cmd


def _generate_pulley_bytes(params, ss_bin) -> bytes:
    """Generate STEP for one pulley via small_step combined; return bytes."""
    family         = params['family']
    pitch          = params['pitch']
    num_teeth      = int(params['num_teeth'])
    bore_mm        = float(params['bore_mm'])
    clearance_mm   = float(params.get('clearance_mm', 0.0))
    backlash_mm    = float(params.get('backlash_mm', 0.0))
    print_extra_mm = float(params.get('print_extra_mm', 0.0))
    spoke_count    = int(params.get('spoke_count', 0))
    spoke_width_mm = float(params.get('spoke_width_mm', 0.0))
    spoke_hub_od_mm= float(params.get('spoke_hub_od_mm', 0.0))
    rim_depth_mm   = float(params.get('rim_depth_mm', 0.0))
    fillet_tip_mm  = float(params.get('fillet_tip_mm', 0.0))
    fillet_base_mm = float(params.get('fillet_base_mm', 0.0))

    from exporters.dxf_exporter import generate_dxf
    dxf_bytes = generate_dxf(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=clearance_mm, backlash_mm=backlash_mm,
        print_extra_mm=print_extra_mm,
        spoke_count=spoke_count,
        spoke_width_mm=spoke_width_mm, spoke_hub_od_mm=spoke_hub_od_mm,
        rim_depth_mm=rim_depth_mm, fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm,
        flat_depth_mm=0.0, keyway_w_mm=0.0, keyway_h_mm=0.0,
    )
    if isinstance(dxf_bytes, str):
        dxf_bytes = dxf_bytes.encode()

    with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False, mode='wb') as f:
        dxf_tmp = f.name
        f.write(dxf_bytes)

    try:
        cmd = _build_pulley_cmd(params, ss_bin, dxf_tmp)
        return _run_cmd(cmd)
    finally:
        try:
            os.unlink(dxf_tmp)
        except OSError:
            pass



def _generate_belt_bytes(belt_params, ss_bin) -> bytes:
    """Generate belt STEP via belt DXF + small_step basic command; return bytes."""
    from exporters.dxf_exporter import generate_belt_dxf_for_step
    family          = belt_params['family']
    pitch           = belt_params['pitch']
    num_teeth_left  = int(belt_params['num_teeth_left'])
    num_teeth_right = int(belt_params['num_teeth_right'])
    center_dist_mm  = float(belt_params['center_dist_mm'])
    belt_height_mm  = float(belt_params['belt_height_mm'])

    dxf_bytes = generate_belt_dxf_for_step(
        family=family, pitch=pitch,
        num_teeth1=num_teeth_left, num_teeth2=num_teeth_right,
        center_dist_mm=center_dist_mm,
    )
    if isinstance(dxf_bytes, str):
        dxf_bytes = dxf_bytes.encode()

    with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False, mode='wb') as f:
        dxf_tmp = f.name
        f.write(dxf_bytes)

    try:
        return _run_cmd([ss_bin, 'belt', dxf_tmp, str(belt_height_mm)])
    finally:
        try:
            os.unlink(dxf_tmp)
        except OSError:
            pass


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
                r_tooth_OD=prof_r_tooth, rim_depth_mm=rim_depth_mm)
        else:
            r_inner = flange_inner_r_3dprint(
                bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                r_tooth_OD=prof_r_tooth, rim_depth_mm=rim_depth_mm)
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

    sys.stdout.buffer.write(_run_cmd(cmd))


def run(params: dict, ss_bin: str) -> bytes:
    """Generate STEP bytes in-process (used by the PyInstaller frozen build).

    Same logic as main() but accepts params directly and returns bytes instead
    of writing to stdout.  Raises RuntimeError on failure.
    """
    params = dict(params)   # don't mutate caller's dict
    export_type = params.pop('export_type', 'pulley')

    if export_type == 'flange':
        import io as _io2
        buf = _io2.BytesIO()
        old_buf = sys.stdout
        sys.stdout = type(sys.stdout)(buf)  # won't work cleanly — call helper
        # _export_flange writes to sys.stdout.buffer; capture via monkey-patch
        class _CaptureBuf:
            def __init__(self): self.data = b''
            def write(self, b): self.data += b
            def flush(self): pass
        cap = _CaptureBuf()
        class _FakeStdout:
            buffer = cap
        old_stdout = sys.stdout
        sys.stdout = _FakeStdout()
        try:
            _export_flange(params, ss_bin)
        finally:
            sys.stdout = old_stdout
        return cap.data

    if export_type == 'belt':
        return _generate_belt_bytes(params, ss_bin)

    if export_type == 'all':
        kw2      = params.pop('kw2', None)
        belt_kw  = params.pop('belt_kw', None)
        family   = params.get('family', 'HTD')
        pitch    = params.get('pitch', '5M')
        nt1      = int(params.get('num_teeth', 0))
        prefix1  = f'{family}-{pitch}-{nt1}T'

        phi_left = phi_right = 0.0
        if belt_kw:
            from geometry.pulley_geometry import build_two_pulley_belt, BELT_FAMILIES
            if family in BELT_FAMILIES:
                num_t1 = int(belt_kw.get('num_teeth_left',  params.get('num_teeth', 20)))
                num_t2 = int(belt_kw.get('num_teeth_right', kw2.get('num_teeth', num_t1) if kw2 else num_t1))
                cdist  = float(belt_kw.get('center_dist_mm', 0.0))
                try:
                    _, _, phi_left, phi_right = build_two_pulley_belt(
                        family, pitch, num_t1, num_t2, cdist, x_offset=0.0
                    )
                except Exception:
                    phi_left = phi_right = 0.0

        p1_bytes = _generate_pulley_bytes(params, ss_bin)
        p1_bytes = _rotate_step_z(p1_bytes, phi_left)
        suffix1  = ' 1' if kw2 else ''
        p1_bytes = _rename_step_products(p1_bytes, suffix1, prefix1)
        parts    = [p1_bytes]

        if kw2:
            fam2    = kw2.get('family', family)
            pit2    = kw2.get('pitch', pitch)
            nt2     = int(kw2.get('num_teeth', 0))
            prefix2 = f'{fam2}-{pit2}-{nt2}T'
            p2_bytes = _generate_pulley_bytes(kw2, ss_bin)
            p2_bytes = _rotate_step_z(p2_bytes, phi_right)
            cdist = float(belt_kw.get('center_dist_mm', 0.0)) if belt_kw else 0.0
            if cdist:
                p2_bytes = _translate_step_x(p2_bytes, cdist)
            p2_bytes = _rename_step_products(p2_bytes, ' 2', prefix2)
            parts.append(p2_bytes)

        if belt_kw:
            n_belt = int(belt_kw.get('n_belt_teeth', 0))
            belt_prefix = f'{family}-{pitch}-{n_belt}T' if n_belt else f'{family}-{pitch}'
            parts.append(_rename_step_products(_generate_belt_bytes(belt_kw, ss_bin), prefix=belt_prefix))

        label = f'{family}-{pitch}-assembly'
        return _merge_steps(parts, label)

    # Default: 'pulley'
    return _generate_pulley_bytes(params, ss_bin)


def main():
    params = json.loads(sys.argv[1])

    ss_bin = os.environ.get('SMALL_STEP_BIN', '')
    if not ss_bin or not os.path.isfile(ss_bin):
        _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _exe = 'small_step.exe' if os.name == 'nt' else 'small_step'
        _candidates = [
            # Pre-compiled Linux binary committed to the repo (Render path)
            os.path.join(_app_root, 'bin', 'small_step_linux'),
            # Local dev: built from source in the small_step submodule/sibling dir
            os.path.join(_app_root, 'small_step', 'target', 'release', _exe),
        ]
        for _c in _candidates:
            if os.path.isfile(_c):
                ss_bin = _c
                break
        if not ss_bin:
            sys.stderr.write(
                f'small_step binary not found. Set SMALL_STEP_BIN, or build via render_build.sh.\n'
                f'Looked at: {_candidates}\n'
            )
            sys.exit(1)

    try:
        _check_binary_version(ss_bin)
    except RuntimeError as e:
        sys.stderr.write(f'small_step version error: {e}\n')
        sys.exit(1)

    export_type = params.pop('export_type', 'pulley')

    if export_type == 'flange':
        _export_flange(params, ss_bin)
        return

    if export_type == 'belt':
        sys.stdout.buffer.write(_generate_belt_bytes(params, ss_bin))
        return

    if export_type == 'all':
        kw2      = params.pop('kw2', None)
        belt_kw  = params.pop('belt_kw', None)
        family   = params.get('family', 'HTD')
        pitch    = params.get('pitch', '5M')
        nt1      = int(params.get('num_teeth', 0))
        prefix1  = f'{family}-{pitch}-{nt1}T'

        # Compute tooth-phase offsets so pulley grooves mesh with belt teeth.
        # build_two_pulley_belt returns phi in compass-CW radians (same convention
        # as _rot2d / SVG / STL exporters). x_offset=0 matches the belt DXF origin
        # (left pulley centre at 0,0).
        phi_left = phi_right = 0.0
        if belt_kw:
            from geometry.pulley_geometry import build_two_pulley_belt, BELT_FAMILIES
            if family in BELT_FAMILIES:
                num_t1 = int(belt_kw.get('num_teeth_left',  params.get('num_teeth', 20)))
                num_t2 = int(belt_kw.get('num_teeth_right', kw2.get('num_teeth', num_t1) if kw2 else num_t1))
                cdist  = float(belt_kw.get('center_dist_mm', 0.0))
                try:
                    _, _, phi_left, phi_right = build_two_pulley_belt(
                        family, pitch, num_t1, num_t2, cdist, x_offset=0.0
                    )
                except Exception:
                    phi_left = phi_right = 0.0

        p1_bytes = _generate_pulley_bytes(params, ss_bin)
        p1_bytes = _rotate_step_z(p1_bytes, phi_left)
        suffix1  = ' 1' if kw2 else ''
        p1_bytes = _rename_step_products(p1_bytes, suffix1, prefix1)
        parts    = [p1_bytes]

        if kw2:
            fam2    = kw2.get('family', family)
            pit2    = kw2.get('pitch', pitch)
            nt2     = int(kw2.get('num_teeth', 0))
            prefix2 = f'{fam2}-{pit2}-{nt2}T'
            p2_bytes = _generate_pulley_bytes(kw2, ss_bin)
            p2_bytes = _rotate_step_z(p2_bytes, phi_right)
            cdist = float(belt_kw.get('center_dist_mm', 0.0)) if belt_kw else 0.0
            if cdist:
                p2_bytes = _translate_step_x(p2_bytes, cdist)
            p2_bytes = _rename_step_products(p2_bytes, ' 2', prefix2)
            parts.append(p2_bytes)

        if belt_kw:
            n_belt = int(belt_kw.get('n_belt_teeth', 0))
            belt_prefix = f'{family}-{pitch}-{n_belt}T' if n_belt else f'{family}-{pitch}'
            parts.append(_rename_step_products(_generate_belt_bytes(belt_kw, ss_bin), prefix=belt_prefix))

        label = f'{family}-{pitch}-assembly'
        sys.stdout.buffer.write(_merge_steps(parts, label))
        return

    # Default: 'pulley'
    sys.stdout.buffer.write(_generate_pulley_bytes(params, ss_bin))


if __name__ == '__main__':
    main()
