"""
app.py — Timing Pulley Generator web app (Flask)
Serves the pulley generator UI and returns SVG downloads.
"""
import math
import io
import os
import json
from datetime import datetime
from flask import Flask, render_template, request, Response, jsonify, send_from_directory

# ── App version ───────────────────────────────────────────────────────────────
APP_VERSION  = '0.1'
BUILD_TIME   = datetime.now().strftime('%Y-%m-%d %H:%M')

# ── Bug-report log ────────────────────────────────────────────────────────────
_LOG_DIR  = os.path.join(os.path.dirname(__file__), 'logs')
_LOG_FILE = os.path.join(_LOG_DIR, 'bug_reports.log')

from geometry.pulley_geometry import (
    PULLEY_SPECS, PROFILE_KEY_PREFIX, PROFILE_PITCHES,
    getPitchDiameter, getOuterDiameter, getTeethFromOD,
    BELT_FAMILIES,
    correct_center_distance, center_dist_from_belt_teeth,
)
from exporters.svg_exporter import generate_svg, generate_svg_dual, generate_rim_layer_svg
from exporters.png_exporter import generate_png, generate_png_dual
from exporters.belt_svg_exporter import generate_belt_svg, generate_belt_png
from exporters.dxf_exporter import generate_dxf, generate_belt_dxf, generate_belt_dxf_dual, generate_rim_layer_dxf
from exporters.step_exporter import (
    generate_pulley_stl, generate_pulley_stl_preview,
    generate_drive_stl_preview,
)
from exporters.flange_exporter import (
    generate_3dprint_flange_stl,
    generate_metal_flange_stl,
)

app = Flask(__name__)
# ─── Cloudflare Worker proxy support ──────
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_host=1)
# ──────────────────────────────────────────


# u2500u2500 Reverse-proxy / subfolder support u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500
# Use ProxyFix so Flask knows it is behind Cloudflare and handles the path correctly.

# This tells Flask to prepend this path to all url_for() calls (like static assets)

# ── Reverse-proxy / subfolder support ────────────────────────────────────────
# When running on GreenGeeks under /tst_pulleys/, index.cgi sets SCRIPT_NAME
# so Flask generates correct URLs for static assets and redirects.
# In local dev this env var is absent, so nothing changes.

# ── Profile catalogue for the UI ─────────────────────────────────────────────
# Use PROFILE_PITCHES (short names) + PROFILE_KEY_PREFIX to resolve full spec keys,
# matching the same logic as the Fusion add-in.
FAMILIES = PROFILE_PITCHES   # short pitch names per family

CLEARANCE_PRESETS = {
    'TIGHT':    'Tight',
    'STANDARD': 'Standard',
    'LOOSE':    'Loose',
    'CUSTOM':   'Custom',
}
BACKLASH_PRESETS = {
    'NONE':     'None (0 mm)',
    'TIGHT':    'Tight',
    'STANDARD': 'Standard',
    'LOOSE':    'Loose',
    'CUSTOM':   'Custom',
}


def _resolve_key(family, pitch):
    if family not in PROFILE_KEY_PREFIX:
        return None   # unknown family — caller must check for None
    if pitch not in PROFILE_PITCHES.get(family, []):
        return None   # pitch not valid for this family
    prefix = PROFILE_KEY_PREFIX[family]
    return prefix + pitch


def _get_bore(args, key='bore', default=8.0):
    """Parse bore diameter from request args, clamped to minimum 1 mm."""
    try:
        return max(1.0, float(args.get(key, default)))
    except (ValueError, TypeError):
        return default


def _get_preset_value(spec, preset_type, preset_key, custom_val):
    """Resolve a clearance or backlash preset to a mm float."""
    if preset_key == 'CUSTOM':
        return float(custom_val or 0)
    if preset_key == 'NONE':
        return 0.0
    return spec[preset_type].get(preset_key, 0.0) if preset_type == 'backlash' \
        else spec['clearances'].get(preset_key, 0.0)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template(
        'index.html',
        families=FAMILIES,
        clearance_presets=CLEARANCE_PRESETS,
        backlash_presets=BACKLASH_PRESETS,
        belt_families=sorted(BELT_FAMILIES),
        app_version=APP_VERSION,
        build_time=BUILD_TIME,
    )


@app.route('/help/<path:filename>')
def help_page(filename):
    return send_from_directory('static', filename)


@app.route('/api/spec')
def api_spec():
    """Return spec data for a given family+pitch: min_teeth, default OD."""
    family = request.args.get('family', 'HTD')
    pitch  = request.args.get('pitch', '5M')
    key    = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        return jsonify({'error': f'Unknown profile {family}/{pitch}'}), 400
    spec      = PULLEY_SPECS[key]
    min_teeth = spec['min_teeth']
    od        = round(getOuterDiameter(min_teeth, spec['pitch'], spec['pitch_line_diff']), 3)
    presets   = {
        'clearance': {k: round(v, 4) for k, v in spec['clearances'].items()},
        'backlash':  {k: round(v, 4) for k, v in spec['backlash'].items()},
    }
    return jsonify({
        'min_teeth':  min_teeth,
        'pitch_mm':   spec['pitch'],
        'pld_mm':     spec['pitch_line_diff'],
        'default_od': od,
        'presets':    presets,
    })


@app.route('/api/belt')
def api_belt():
    """
    Belt-length / centre-distance correction.

    mode=from_center  (default):
        Given center_distance → returns n_belt (ceil) and C_corrected.
    mode=from_teeth:
        Given n_belt → returns C_corrected.
    """
    family = request.args.get('family', 'HTD')
    pitch  = request.args.get('pitch', '5M')
    key    = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        return jsonify({'error': f'Unknown profile {family}/{pitch}'}), 400
    spec      = PULLEY_SPECS[key]
    pitch_mm  = spec['pitch']
    mode      = request.args.get('mode', 'from_center')

    try:
        teeth1 = max(spec['min_teeth'], int(request.args.get('teeth1', spec['min_teeth'])))
        teeth2 = max(spec['min_teeth'], int(request.args.get('teeth2', spec['min_teeth'])))
    except (ValueError, TypeError) as e:
        return jsonify({'error': f'Invalid teeth value: {e}'}), 400

    if mode == 'from_teeth':
        try:
            n_belt = int(request.args.get('n_belt', 0))
        except (ValueError, TypeError) as e:
            return jsonify({'error': f'Invalid n_belt: {e}'}), 400
        if n_belt <= 0:
            return jsonify({'error': 'n_belt must be > 0'}), 400
        C = center_dist_from_belt_teeth(pitch_mm, teeth1, teeth2, n_belt)
        if C is None:
            return jsonify({'error': 'Belt too short to span both pulleys'}), 400
        return jsonify({'n_belt': n_belt, 'center_dist_mm': round(C, 4)})
    else:
        try:
            center_dist = float(request.args.get('center_distance', 100.0))
        except (ValueError, TypeError) as e:
            return jsonify({'error': f'Invalid center_distance: {e}'}), 400
        _L, n_belt, C_corr = correct_center_distance(pitch_mm, teeth1, teeth2, center_dist)
        return jsonify({'n_belt': n_belt, 'center_dist_mm': round(C_corr, 4)})


@app.route('/api/od')
def api_od():
    """Convert teeth ↔ OD for live preview."""
    family    = request.args.get('family', 'HTD')
    pitch     = request.args.get('pitch', '5M')
    key       = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        return jsonify({'error': f'Unknown profile {family}/{pitch}'}), 400
    spec      = PULLEY_SPECS[key]
    mode      = request.args.get('mode', 'teeth')   # 'teeth' or 'od'
    try:
        if mode == 'teeth':
            n  = max(spec['min_teeth'], int(request.args.get('value', spec['min_teeth'])))
            od = round(getOuterDiameter(n, spec['pitch'], spec['pitch_line_diff']), 3)
            return jsonify({'teeth': n, 'od': od})
        else:
            od = float(request.args.get('value', 0))
            n  = getTeethFromOD(od, spec['pitch'], spec['pitch_line_diff'])
            od2 = round(getOuterDiameter(n, spec['pitch'], spec['pitch_line_diff']), 3)
            return jsonify({'teeth': n, 'od': od2})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/preview')
def api_preview():
    """Return PNG for live preview — raster only, not usable as vector."""
    try:
        dual = request.args.get('dual') == 'true'
        if dual:
            png = _build_png_dual_from_request(request.args, size_px=1000)
        else:
            png = _build_png_from_request(request.args, size_px=1000)
        return Response(png, mimetype='image/png')
    except Exception as e:
        from PIL import Image, ImageDraw
        import io
        img = Image.new('RGB', (1000, 1000), (250, 251, 252))
        d = ImageDraw.Draw(img)
        d.text((10, 10), f'Error: {e}', fill=(200, 0, 0))
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        buf.seek(0)
        return Response(buf.read(), mimetype='image/png')


@app.route('/download/svg')
def download_svg():
    """Return SVG file download."""
    try:
        family  = request.args.get('family', 'HTD')
        pitch   = request.args.get('pitch', '5M')
        pulley  = request.args.get('pulley', '1')
        if pulley == '2':
            teeth = request.args.get('p2_teeth', '20')
            svg   = _build_svg_from_request_p2(request.args)
            filename = f'{family}-{pitch}-{teeth}T-P2.svg'
        else:
            teeth = request.args.get('teeth', '20')
            svg   = _build_svg_from_request(request.args)
            filename = f'{family}-{pitch}-{teeth}T.svg'
        return Response(
            svg,
            mimetype='image/svg+xml',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return f'Error generating SVG: {e}', 400


@app.route('/download/dxf')
def download_dxf():
    """Return DXF file download for pulley 1 or pulley 2."""
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        pulley = request.args.get('pulley', '1')
        key    = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return f'Unknown profile {family}/{pitch}', 400
        spec = PULLEY_SPECS[key]

        if pulley == '2':
            num_teeth = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
            bore_mm   = _get_bore(request.args, 'p2_bore')
            pr_ex     = float(request.args.get('p2_print_extra', 0.0))
            cl_preset = request.args.get('p2_clearance_preset', 'STANDARD')
            bl_preset = request.args.get('p2_backlash_preset',  'STANDARD')
            cl_mm = _get_preset_value(spec, 'clearances', cl_preset, request.args.get('p2_clearance_custom', 0.0))
            bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, request.args.get('p2_backlash_custom',  0.0))
            sp_en, sp_hub_od, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, _, _ = _parse_spoke_params(request.args, 'p2_')
            filename = f'{family}-{pitch}-{num_teeth}T-P2.dxf'
        else:
            num_teeth = max(spec['min_teeth'], int(request.args.get('teeth', spec['min_teeth'])))
            bore_mm   = _get_bore(request.args, 'bore')
            pr_ex     = float(request.args.get('print_extra', 0.0))
            cl_preset = request.args.get('clearance_preset', 'STANDARD')
            bl_preset = request.args.get('backlash_preset',  'STANDARD')
            cl_mm = _get_preset_value(spec, 'clearances', cl_preset, request.args.get('clearance_custom', 0.0))
            bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, request.args.get('backlash_custom',  0.0))
            sp_en, sp_hub_od, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, _, _ = _parse_spoke_params(request.args, '')
            filename = f'{family}-{pitch}-{num_teeth}T.dxf'

        dxf = generate_dxf(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
            print_extra_mm=pr_ex,
            spoke_count=sp_cnt if sp_en else 0,
            spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub_od,
            rim_depth_mm=sp_rim, fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
        )
        return Response(
            dxf,
            mimetype='application/dxf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return f'Error generating DXF: {e}', 400


@app.route('/download/svg-rim')
def download_svg_rim():
    """Return rim-layer SVG: toothed profile + inner-rim, hub, bore circles."""
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        pulley = request.args.get('pulley', '1')
        pfx    = 'p2_' if pulley == '2' else ''
        key    = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return f'Unknown profile {family}/{pitch}', 400
        spec = PULLEY_SPECS[key]

        if pulley == '2':
            teeth    = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
            bore_mm  = _get_bore(request.args, 'p2_bore')
            pr_ex    = float(request.args.get('p2_print_extra', 0.0))
            cl_preset = request.args.get('p2_clearance_preset', 'STANDARD')
            bl_preset = request.args.get('p2_backlash_preset',  'STANDARD')
            cl_mm = _get_preset_value(spec, 'clearances', cl_preset, request.args.get('p2_clearance_custom', 0.0))
            bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, request.args.get('p2_backlash_custom',  0.0))
            _, sp_hub_od, sp_rim, *_ = _parse_spoke_params(request.args, 'p2_')
            filename = f'{family}-{pitch}-{teeth}T-P2-Rim.svg'
        else:
            teeth    = max(spec['min_teeth'], int(request.args.get('teeth', spec['min_teeth'])))
            bore_mm  = _get_bore(request.args, 'bore')
            pr_ex    = float(request.args.get('print_extra', 0.0))
            cl_preset = request.args.get('clearance_preset', 'STANDARD')
            bl_preset = request.args.get('backlash_preset',  'STANDARD')
            cl_mm = _get_preset_value(spec, 'clearances', cl_preset, request.args.get('clearance_custom', 0.0))
            bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, request.args.get('backlash_custom',  0.0))
            _, sp_hub_od, sp_rim, *_ = _parse_spoke_params(request.args, '')
            filename = f'{family}-{pitch}-{teeth}T-Rim.svg'

        svg = generate_rim_layer_svg(
            family=family, pitch=pitch, num_teeth=teeth,
            bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
            print_extra_mm=pr_ex, spoke_hub_od_mm=sp_hub_od, rim_depth_mm=sp_rim,
        )
        return Response(svg, mimetype='image/svg+xml',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except Exception as e:
        return f'Error generating rim SVG: {e}', 400


@app.route('/download/dxf-rim')
def download_dxf_rim():
    """Return rim-layer DXF: toothed profile + inner-rim, hub, bore circles."""
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        pulley = request.args.get('pulley', '1')
        key    = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return f'Unknown profile {family}/{pitch}', 400
        spec = PULLEY_SPECS[key]

        if pulley == '2':
            teeth    = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
            bore_mm  = _get_bore(request.args, 'p2_bore')
            pr_ex    = float(request.args.get('p2_print_extra', 0.0))
            cl_preset = request.args.get('p2_clearance_preset', 'STANDARD')
            bl_preset = request.args.get('p2_backlash_preset',  'STANDARD')
            cl_mm = _get_preset_value(spec, 'clearances', cl_preset, request.args.get('p2_clearance_custom', 0.0))
            bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, request.args.get('p2_backlash_custom',  0.0))
            _, sp_hub_od, sp_rim, *_ = _parse_spoke_params(request.args, 'p2_')
            filename = f'{family}-{pitch}-{teeth}T-P2-Rim.dxf'
        else:
            teeth    = max(spec['min_teeth'], int(request.args.get('teeth', spec['min_teeth'])))
            bore_mm  = _get_bore(request.args, 'bore')
            pr_ex    = float(request.args.get('print_extra', 0.0))
            cl_preset = request.args.get('clearance_preset', 'STANDARD')
            bl_preset = request.args.get('backlash_preset',  'STANDARD')
            cl_mm = _get_preset_value(spec, 'clearances', cl_preset, request.args.get('clearance_custom', 0.0))
            bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, request.args.get('backlash_custom',  0.0))
            _, sp_hub_od, sp_rim, *_ = _parse_spoke_params(request.args, '')
            filename = f'{family}-{pitch}-{teeth}T-Rim.dxf'

        dxf = generate_rim_layer_dxf(
            family=family, pitch=pitch, num_teeth=teeth,
            bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
            print_extra_mm=pr_ex, spoke_hub_od_mm=sp_hub_od, rim_depth_mm=sp_rim,
        )
        return Response(dxf, mimetype='application/dxf',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except Exception as e:
        return f'Error generating rim DXF: {e}', 400


def _build_png_from_request(args, size_px=480):
    family  = args.get('family', 'HTD')
    pitch   = args.get('pitch', '5M')
    key     = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec       = PULLEY_SPECS[key]
    num_teeth  = max(spec['min_teeth'], int(args.get('teeth', spec['min_teeth'])))
    bore_mm    = _get_bore(args, 'bore')
    pr_ex      = float(args.get('print_extra', 0.0))
    cl_preset  = args.get('clearance_preset', 'STANDARD')
    bl_preset  = args.get('backlash_preset', 'STANDARD')
    cl_mm = _get_preset_value(spec, 'clearances', cl_preset, args.get('clearance_custom', 0.0))
    bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, args.get('backlash_custom',  0.0))
    sp_en, sp_hub_od, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, sp_split = \
        _parse_spoke_params(args, '')
    return generate_png(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
        print_extra_mm=pr_ex, size_px=size_px,
        spoke_count=sp_cnt if sp_en else 0,
        spoke_width_mm=sp_w,
        spoke_hub_od_mm=sp_hub_od,
        rim_depth_mm=sp_rim,
        fillet_tip_mm=sp_ft,
        fillet_base_mm=sp_fb,
    )


def _build_svg_from_request(args):
    family  = args.get('family', 'HTD')
    pitch   = args.get('pitch', '5M')
    key     = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec    = PULLEY_SPECS[key]

    num_teeth  = max(spec['min_teeth'], int(args.get('teeth', spec['min_teeth'])))
    bore_mm    = _get_bore(args, 'bore')
    pr_ex      = float(args.get('print_extra', 0.0))

    cl_preset  = args.get('clearance_preset', 'STANDARD')
    bl_preset  = args.get('backlash_preset', 'STANDARD')
    cl_custom  = args.get('clearance_custom', 0.0)
    bl_custom  = args.get('backlash_custom', 0.0)

    cl_mm = _get_preset_value(spec, 'clearances', cl_preset, cl_custom)
    bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, bl_custom)
    sp_en, sp_hub_od, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, sp_split = \
        _parse_spoke_params(args, '')

    include_data = args.get('include_data', '1') != '0'
    return generate_svg(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
        print_extra_mm=pr_ex, clearance_preset=cl_preset, backlash_preset=bl_preset,
        spoke_count=sp_cnt if sp_en else 0,
        spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub_od, rim_depth_mm=sp_rim,
        fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
        include_data=include_data,
    )


def _build_svg_from_request_p2(args):
    """Build SVG for Pulley 2 (uses p2_* params, same family/pitch as P1)."""
    family  = args.get('family', 'HTD')
    pitch   = args.get('pitch', '5M')
    key     = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec    = PULLEY_SPECS[key]

    num_teeth  = max(spec['min_teeth'], int(args.get('p2_teeth', spec['min_teeth'])))
    bore_mm    = _get_bore(args, 'p2_bore')
    pr_ex      = float(args.get('p2_print_extra', 0.0))

    cl_preset  = args.get('p2_clearance_preset', 'STANDARD')
    bl_preset  = args.get('p2_backlash_preset', 'STANDARD')
    cl_custom  = args.get('p2_clearance_custom', 0.0)
    bl_custom  = args.get('p2_backlash_custom', 0.0)

    cl_mm = _get_preset_value(spec, 'clearances', cl_preset, cl_custom)
    bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, bl_custom)
    sp_en, sp_hub_od, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, sp_split = \
        _parse_spoke_params(args, 'p2_')

    include_data = args.get('include_data', '1') != '0'
    return generate_svg(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
        print_extra_mm=pr_ex, clearance_preset=cl_preset, backlash_preset=bl_preset,
        spoke_count=sp_cnt if sp_en else 0,
        spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub_od, rim_depth_mm=sp_rim,
        fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
        include_data=include_data,
    )


def _build_png_dual_from_request(args, size_px=480):
    family  = args.get('family', 'HTD')
    pitch   = args.get('pitch', '5M')
    key     = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec = PULLEY_SPECS[key]

    num_teeth1 = max(spec['min_teeth'], int(args.get('teeth', spec['min_teeth'])))
    bore1      = _get_bore(args, 'bore')
    pr_ex1     = float(args.get('print_extra', 0.0))
    cl1 = _get_preset_value(spec, 'clearances', args.get('clearance_preset', 'STANDARD'), args.get('clearance_custom', 0.0))
    bl1 = _get_preset_value(spec, 'backlash',   args.get('backlash_preset',  'STANDARD'), args.get('backlash_custom',  0.0))

    num_teeth2 = max(spec['min_teeth'], int(args.get('p2_teeth', spec['min_teeth'])))
    bore2      = _get_bore(args, 'p2_bore')
    pr_ex2     = float(args.get('p2_print_extra', 0.0))
    cl2 = _get_preset_value(spec, 'clearances', args.get('p2_clearance_preset', 'STANDARD'), args.get('p2_clearance_custom', 0.0))
    bl2 = _get_preset_value(spec, 'backlash',   args.get('p2_backlash_preset',  'STANDARD'), args.get('p2_backlash_custom',  0.0))

    import math as _math
    _default_c = (num_teeth1 + num_teeth2) * spec['pitch'] / (2.0 * _math.pi)
    center_dist = float(args.get('center_distance', _default_c))

    sp1_en, sp1_hub_od, sp1_rim, sp1_w, sp1_ft, sp1_fb, sp1_cnt, sp1_h, sp1_split = \
        _parse_spoke_params(args, '')
    sp2_en, sp2_hub_od, sp2_rim, sp2_w, sp2_ft, sp2_fb, sp2_cnt, sp2_h, sp2_split = \
        _parse_spoke_params(args, 'p2_')
    return generate_png_dual(
        family=family, pitch=pitch,
        num_teeth1=num_teeth1, bore_mm1=bore1, clearance_mm1=cl1, backlash_mm1=bl1, print_extra_mm1=pr_ex1,
        num_teeth2=num_teeth2, bore_mm2=bore2, clearance_mm2=cl2, backlash_mm2=bl2, print_extra_mm2=pr_ex2,
        center_dist_mm=center_dist, size_px=size_px,
        spoke_count1=sp1_cnt if sp1_en else 0,
        spoke_width_mm1=sp1_w, spoke_hub_od_mm1=sp1_hub_od, rim_depth_mm1=sp1_rim,
        fillet_tip_mm1=sp1_ft, fillet_base_mm1=sp1_fb,
        spoke_count2=sp2_cnt if sp2_en else 0,
        spoke_width_mm2=sp2_w, spoke_hub_od_mm2=sp2_hub_od, rim_depth_mm2=sp2_rim,
        fillet_tip_mm2=sp2_ft, fillet_base_mm2=sp2_fb,
    )


@app.route('/api/belt-preview')
def api_belt_preview():
    """Return SVG of belt tooth cross-section for live preview."""
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        if family not in BELT_FAMILIES:
            return Response('', mimetype='image/svg+xml')
        svg = generate_belt_svg(family, pitch, n_teeth=3)
        return Response(svg, mimetype='image/svg+xml')
    except Exception as e:
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100"><text x="10" y="20" fill="red">Error: {e}</text></svg>'
        return Response(svg, mimetype='image/svg+xml')


@app.route('/download/belt-svg')
def download_belt_svg():
    """Return belt SVG download.
    In dual mode: two-pulley belt layout SVG.
    In single mode: belt tooth cross-section SVG.
    """
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        dual   = request.args.get('dual') == 'true'

        if dual:
            key  = _resolve_key(family, pitch)
            if key is None or key not in PULLEY_SPECS:
                return f'Unknown profile {family}/{pitch}', 400
            spec = PULLEY_SPECS[key]

            num_teeth1 = max(spec['min_teeth'], int(request.args.get('teeth',    spec['min_teeth'])))
            num_teeth2 = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
            bore1      = _get_bore(request.args, 'bore')
            bore2      = _get_bore(request.args, 'p2_bore')
            pe1        = float(request.args.get('print_extra',    0.0))
            pe2        = float(request.args.get('p2_print_extra', 0.0))
            cl1_preset = request.args.get('clearance_preset',    'STANDARD')
            bl1_preset = request.args.get('backlash_preset',     'STANDARD')
            cl2_preset = request.args.get('p2_clearance_preset', 'STANDARD')
            bl2_preset = request.args.get('p2_backlash_preset',  'STANDARD')
            cl1 = _get_preset_value(spec, 'clearances', cl1_preset, request.args.get('clearance_custom',    0.0))
            bl1 = _get_preset_value(spec, 'backlash',   bl1_preset, request.args.get('backlash_custom',     0.0))
            cl2 = _get_preset_value(spec, 'clearances', cl2_preset, request.args.get('p2_clearance_custom', 0.0))
            bl2 = _get_preset_value(spec, 'backlash',   bl2_preset, request.args.get('p2_backlash_custom',  0.0))
            import math as _math
            _default_c = (num_teeth1 + num_teeth2) * spec['pitch'] / (2.0 * _math.pi)
            center_dist = float(request.args.get('center_distance', _default_c))
            n_belt      = int(request.args.get('n_belt', 0))

            sp1_en, sp1_hub_od, sp1_rim, sp1_w, sp1_ft, sp1_fb, sp1_cnt, _, _ = \
                _parse_spoke_params(request.args, '')
            sp2_en, sp2_hub_od, sp2_rim, sp2_w, sp2_ft, sp2_fb, sp2_cnt, _, _ = \
                _parse_spoke_params(request.args, 'p2_')
            svg      = generate_svg_dual(
                family=family, pitch=pitch,
                num_teeth1=num_teeth1, bore_mm1=bore1,
                clearance_mm1=cl1, backlash_mm1=bl1, print_extra_mm1=pe1,
                clearance_preset1=cl1_preset, backlash_preset1=bl1_preset,
                num_teeth2=num_teeth2, bore_mm2=bore2,
                clearance_mm2=cl2, backlash_mm2=bl2, print_extra_mm2=pe2,
                clearance_preset2=cl2_preset, backlash_preset2=bl2_preset,
                center_dist_mm=center_dist, n_belt_teeth=n_belt,
                spoke_count1=sp1_cnt if sp1_en else 0,
                spoke_width_mm1=sp1_w, spoke_hub_od_mm1=sp1_hub_od,
                rim_depth_mm1=sp1_rim, fillet_tip_mm1=sp1_ft, fillet_base_mm1=sp1_fb,
                spoke_count2=sp2_cnt if sp2_en else 0,
                spoke_width_mm2=sp2_w, spoke_hub_od_mm2=sp2_hub_od,
                rim_depth_mm2=sp2_rim, fillet_tip_mm2=sp2_ft, fillet_base_mm2=sp2_fb,
            )
            filename = f'{family}-{pitch}-{num_teeth1}T-{num_teeth2}T-belt.svg'
        else:
            if family not in BELT_FAMILIES:
                return f'Belt SVG not available for family {family}', 400
            svg      = generate_belt_svg(family, pitch, n_teeth=3)
            filename = f'{family}-{pitch}-belt-profile.svg'

        return Response(
            svg,
            mimetype='image/svg+xml',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return f'Error generating belt SVG: {e}', 400


def _parse_stl_params(args, pulley='1'):
    """Extract and validate STL export parameters for one pulley."""
    family = args.get('family', 'HTD')
    pitch  = args.get('pitch',  '5M')
    key    = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec = PULLEY_SPECS[key]

    if pulley == '2':
        num_teeth = max(spec['min_teeth'], int(args.get('p2_teeth', spec['min_teeth'])))
        bore_mm   = _get_bore(args, 'p2_bore')
        pr_ex     = float(args.get('p2_print_extra', 0.0))
        cl_mm = _get_preset_value(spec, 'clearances',
                                  args.get('p2_clearance_preset', 'STANDARD'),
                                  args.get('p2_clearance_custom', 0.0))
        bl_mm = _get_preset_value(spec, 'backlash',
                                  args.get('p2_backlash_preset', 'STANDARD'),
                                  args.get('p2_backlash_custom', 0.0))
    else:
        num_teeth = max(spec['min_teeth'], int(args.get('teeth', spec['min_teeth'])))
        bore_mm   = _get_bore(args, 'bore')
        pr_ex     = float(args.get('print_extra', 0.0))
        cl_mm = _get_preset_value(spec, 'clearances',
                                  args.get('clearance_preset', 'STANDARD'),
                                  args.get('clearance_custom', 0.0))
        bl_mm = _get_preset_value(spec, 'backlash',
                                  args.get('backlash_preset', 'STANDARD'),
                                  args.get('backlash_custom', 0.0))

    belt_height = max(1.0, float(args.get('belt_height', 10.0)))
    return family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex


def _parse_hub_params(args, prefix=''):
    """Return (hub_od_mm, hub_height_mm, screw_dia_mm, screw_count, captured_nut, flat_depth_mm, keyway_w_mm, keyway_h_mm) from request args."""
    hub_od       = max(0.0, float(args.get(f'{prefix}hub_od',           0.0)))
    hub_height   = max(0.0, float(args.get(f'{prefix}hub_height',       0.0)))
    screw_dia    = max(0.0, float(args.get(f'{prefix}hub_screw_dia',    0.0)))
    screw_count  = max(0,   int(float(args.get(f'{prefix}hub_screw_count', 0))))
    captured_nut = args.get(f'{prefix}hub_captured_nut', '0') == '1'
    flat_depth   = max(0.0, float(args.get(f'{prefix}hub_flat_depth',   0.0)))
    keyway_w     = max(0.0, float(args.get(f'{prefix}hub_keyway_w',     0.0)))
    keyway_h     = max(0.0, float(args.get(f'{prefix}hub_keyway_h',     0.0)))
    return hub_od, hub_height, screw_dia, screw_count, captured_nut, flat_depth, keyway_w, keyway_h


def _parse_spoke_params(args, prefix=''):
    """Return spoke params tuple from request args.
    Returns (enabled, hub_od, rim_depth, width, fillet_tip, fillet_base, count, height, split).
    """
    enabled    = args.get(f'{prefix}spokes_enabled', '0') == '1'
    hub_od     = max(0.0, float(args.get(f'{prefix}spokes_hub_od',     0.0)))
    rim_depth  = max(0.0, float(args.get(f'{prefix}spokes_rim_depth',  2.0)))
    width      = max(0.0, float(args.get(f'{prefix}spokes_width',      4.0)))
    fillet_tip = max(0.0, float(args.get(f'{prefix}spokes_fillet_tip', 1.0)))
    fillet_base= max(0.0, float(args.get(f'{prefix}spokes_fillet_base',1.5)))
    count      = max(0,   int(float(args.get(f'{prefix}spokes_count',   4))))
    height     = max(0.0, float(args.get(f'{prefix}spokes_height',     0.0) or 0.0))
    split      = args.get(f'{prefix}spokes_split', '0') == '1'
    return enabled, hub_od, rim_depth, width, fillet_tip, fillet_base, count, height, split


@app.route('/api/preview-stl')
def api_preview_stl():
    """Return binary STL for the Three.js 3D viewer (centred at origin)."""
    try:
        dual = request.args.get('dual') == 'true'
        if dual:
            family, pitch, num_teeth1, bore1, belt_height, cl1, bl1, pe1 = \
                _parse_stl_params(request.args, '1')
            key  = _resolve_key(family, pitch)
            spec = PULLEY_SPECS[key]
            num_teeth2 = max(spec['min_teeth'],
                             int(request.args.get('p2_teeth', spec['min_teeth'])))
            bore2 = _get_bore(request.args, 'p2_bore')
            pe2   = float(request.args.get('p2_print_extra', 0.0))
            cl2   = _get_preset_value(spec, 'clearances',
                                      request.args.get('p2_clearance_preset', 'STANDARD'),
                                      request.args.get('p2_clearance_custom', 0.0))
            bl2   = _get_preset_value(spec, 'backlash',
                                      request.args.get('p2_backlash_preset', 'STANDARD'),
                                      request.args.get('p2_backlash_custom', 0.0))
            center_dist = float(request.args.get('center_distance', 100.0))
            part = request.args.get('part', 'all')
            hub_od1, hub_h1, sd1, sc1, cn1, fd1, kw_w1, kw_h1 = _parse_hub_params(request.args, '')
            hub_od2, hub_h2, sd2, sc2, cn2, fd2, kw_w2, kw_h2 = _parse_hub_params(request.args, 'p2_')
            sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, sp_split = \
                _parse_spoke_params(request.args, '')
            sp_en2, sp_hub2, sp_rim2, sp_w2, sp_ft2, sp_fb2, sp_cnt2, sp_h2, sp_split2 = \
                _parse_spoke_params(request.args, 'p2_')
            fl1 = _parse_flange_params(request.args, '') \
                  if request.args.get('flange_enabled') == '1' else None
            fl2 = _parse_flange_params(request.args, 'p2_') \
                  if request.args.get('p2_flange_enabled') == '1' else None
            stl = generate_drive_stl_preview(
                family, pitch,
                num_teeth1, bore1, num_teeth2, bore2,
                center_dist, belt_height,
                cl1, bl1, pe1, cl2, bl2, pe2,
                hub_od_mm1=hub_od1, hub_height_mm1=hub_h1,
                hub_od_mm2=hub_od2, hub_height_mm2=hub_h2,
                screw_dia_mm1=sd1, screw_count1=sc1, captured_nut1=cn1,
                screw_dia_mm2=sd2, screw_count2=sc2, captured_nut2=cn2,
                flat_depth_mm1=fd1, flat_depth_mm2=fd2,
                keyway_w_mm1=kw_w1, keyway_h_mm1=kw_h1,
                keyway_w_mm2=kw_w2, keyway_h_mm2=kw_h2,
                spoke_count1=sp_cnt if sp_en else 0,
                spoke_width_mm1=sp_w, spoke_hub_od_mm1=sp_hub,
                fillet_tip_mm1=sp_ft, fillet_base_mm1=sp_fb, rim_depth_mm1=sp_rim,
                spoke_height_mm1=sp_h if sp_en else 0.0,
                spoke_count2=sp_cnt2 if sp_en2 else 0,
                spoke_width_mm2=sp_w2, spoke_hub_od_mm2=sp_hub2,
                fillet_tip_mm2=sp_ft2, fillet_base_mm2=sp_fb2, rim_depth_mm2=sp_rim2,
                spoke_height_mm2=sp_h2 if sp_en2 else 0.0,
                part=part,
                flange1=fl1, flange2=fl2,
            )
        else:
            pulley = request.args.get('pulley', '1')
            family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
                _parse_stl_params(request.args, pulley)
            hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(request.args, '')
            sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, sp_split = \
                _parse_spoke_params(request.args, '')
            sp_count = sp_cnt if sp_en else 0
            # Build socket meshes before generating pulley STL (avoids STL round-trip)
            _socket_meshes = None
            _fl_meshes = []
            if request.args.get('flange_enabled') == '1':
                import trimesh
                from exporters.flange_exporter import build_flange_meshes, build_socket_meshes
                fp = _parse_flange_params(request.args)
                _fl_meshes = build_flange_meshes(
                    fp, family, pitch, num_teeth, bore_mm, belt_height,
                    clearance_mm=cl_mm, print_extra_mm=pr_ex,
                    hub_od_mm=hub_od, hub_height_mm=hub_h,
                    spokes_enabled=sp_en, spoke_hub_od_mm=sp_hub,
                    rim_depth_mm=sp_rim,
                )
                if fp.get('nubs_enabled') and fp.get('flange_3dprint') and fp.get('top_separate'):
                    _socket_meshes = build_socket_meshes(
                        fp, family, pitch, num_teeth, bore_mm, belt_height,
                        clearance_mm=cl_mm, print_extra_mm=pr_ex,
                        hub_od_mm=hub_od, spokes_enabled=sp_en, spoke_hub_od_mm=sp_hub,
                        rim_depth_mm=sp_rim,
                    ) or None
            stl = generate_pulley_stl_preview(
                family, pitch, num_teeth, bore_mm, belt_height,
                cl_mm, bl_mm, pr_ex, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
                spoke_count=sp_count, spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
                fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb, rim_depth_mm=sp_rim,
                spoke_height_mm=sp_h if sp_en else 0.0,
                socket_meshes=_socket_meshes,
            )
            if _fl_meshes:
                import io as _io
                pulley_mesh = trimesh.load(_io.BytesIO(stl), file_type='stl')
                combined = trimesh.util.concatenate([pulley_mesh] + _fl_meshes)
                combined.apply_translation(-combined.centroid)
                stl = combined.export(file_type='stl')
        return Response(stl, mimetype='model/stl',
                        headers={'Cache-Control': 'no-store'})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb, flush=True)
        return f'Error generating STL preview: {e}\n\n{tb}', 400


@app.route('/download/stl')
def download_stl():
    """Return binary STL file download."""
    try:
        pulley = request.args.get('pulley', '1')
        family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
            _parse_stl_params(request.args, pulley)
        pfx = 'p2_' if pulley == '2' else ''
        hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(request.args, pfx)
        sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, _ = \
            _parse_spoke_params(request.args, pfx)
        suffix   = '-P2' if pulley == '2' else ''
        sp_count = sp_cnt if sp_en else 0
        stl = generate_pulley_stl(
            family, pitch, num_teeth, bore_mm, belt_height,
            cl_mm, bl_mm, pr_ex, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
            spoke_count=sp_count, spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
            fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb, rim_depth_mm=sp_rim,
            spoke_height_mm=sp_h if sp_en else 0.0,
        )
        fname = f'{family}-{pitch}-{num_teeth}T{suffix}.stl'
        return Response(stl, mimetype='model/stl',
                        headers={'Content-Disposition': f'attachment; filename="{fname}"'})
    except Exception as e:
        import traceback
        return f'Error generating STL: {e}\n{traceback.format_exc()}', 400


@app.route('/download/step')
def download_step():
    try:
        import json, os
        pulley = request.args.get('pulley', '1')
        family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
            _parse_stl_params(request.args, pulley)
        pfx = 'p2_' if pulley == '2' else ''
        hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(request.args, pfx)
        sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_c, sp_h, sp_split = _parse_spoke_params(request.args, pfx)

        # When spokes are enabled, use spoke hub OD as hub boss OD if no
        # explicit hub OD was set — matches the STL download behaviour.
        eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od

        kw = dict(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, belt_height_mm=belt_height,
            clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex,
            hub_od_mm=eff_hub_od, hub_height_mm=hub_h,
            screw_dia_mm=sd, screw_count=sc,
            captured_nut=cn, flat_depth_mm=fd,
            keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            spoke_count=sp_c if sp_en else 0,
            spoke_width_mm=sp_w,
            spoke_hub_od_mm=sp_hub,
            rim_depth_mm=sp_rim,
            fillet_tip_mm=sp_ft,
            fillet_base_mm=sp_fb,
            spoke_height_mm=sp_h,
        )

        # Try direct import first (cadquery available — Render / Python 3.12 venv).
        # Fall back to subprocess when Flask is running on Python 3.14 (local dev)
        # and cadquery lives in a separate .venv312 on Windows.
        try:
            from exporters.step_exporter import generate_pulley_step
            step_bytes = generate_pulley_step(**kw)
        except ImportError:
            import subprocess, sys
            root    = os.path.dirname(os.path.abspath(__file__))
            venv_py = os.path.join(root, '.venv312', 'Scripts', 'python.exe')
            worker  = os.path.join(root, 'exporters', 'step_worker.py')
            result  = subprocess.run(
                [venv_py, worker, json.dumps(kw)],
                capture_output=True, cwd=root,
            )
            if result.returncode != 0:
                return f'STEP error: {result.stderr.decode()}', 400
            step_bytes = result.stdout

        suffix = '-P2' if pulley == '2' else ''
        fname  = f'{family}-{pitch}-{num_teeth}T{suffix}.step'
        return Response(step_bytes, mimetype='application/step',
                        headers={'Content-Disposition': f'attachment; filename="{fname}"'})
    except Exception as e:
        return f'Error generating STEP: {e}', 400


@app.route('/download/belt-step')
def download_belt_step():
    """Two-pulley belt STEP export (cadquery, B-rep with true arcs + B-spline teeth)."""
    try:
        family  = request.args.get('family', 'HTD')
        pitch   = request.args.get('pitch',  '5M')
        dual    = request.args.get('dual') == 'true'

        if not dual:
            return Response(
                'Belt STEP export requires Two Pulley Drive mode.',
                status=400, mimetype='text/plain')

        key = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return f'Unknown profile {family}/{pitch}', 400
        spec = PULLEY_SPECS[key]

        num_teeth1   = max(spec['min_teeth'], int(request.args.get('teeth',    spec['min_teeth'])))
        num_teeth2   = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
        belt_h       = float(request.args.get('belt_height', 10.0))
        _default_c   = (num_teeth1 + num_teeth2) * spec['pitch'] / (2.0 * math.pi)
        center_dist  = float(request.args.get('center_distance', _default_c))

        from exporters.step_exporter import generate_belt_step
        step_bytes = generate_belt_step(
            family=family, pitch=pitch,
            num_teeth_left=num_teeth1, num_teeth_right=num_teeth2,
            center_dist_mm=center_dist,
            belt_height_mm=belt_h,
        )
        filename = f'{family}-{pitch}-{num_teeth1}T-{num_teeth2}T-belt.step'
        return Response(
            step_bytes,
            mimetype='application/step',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        import traceback
        return Response(
            f'Belt STEP export failed:\n{traceback.format_exc()}',
            status=500, mimetype='text/plain'
        )


@app.route('/download/belt-stl')
def download_belt_stl():
    return Response('Belt STL export — coming soon', status=501, mimetype='text/plain')

@app.route('/download/belt-dxf')
def download_belt_dxf():
    """Return belt DXF download.
    In dual mode: two-pulley belt layout DXF.
    In single mode: belt tooth cross-section DXF.
    """
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        dual   = request.args.get('dual') == 'true'

        if dual:
            key = _resolve_key(family, pitch)
            if key is None or key not in PULLEY_SPECS:
                return f'Unknown profile {family}/{pitch}', 400
            spec = PULLEY_SPECS[key]

            num_teeth1 = max(spec['min_teeth'], int(request.args.get('teeth',    spec['min_teeth'])))
            num_teeth2 = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
            bore1      = _get_bore(request.args, 'bore')
            bore2      = _get_bore(request.args, 'p2_bore')
            pe1        = float(request.args.get('print_extra',    0.0))
            pe2        = float(request.args.get('p2_print_extra', 0.0))
            cl1_preset = request.args.get('clearance_preset',    'STANDARD')
            bl1_preset = request.args.get('backlash_preset',     'STANDARD')
            cl2_preset = request.args.get('p2_clearance_preset', 'STANDARD')
            bl2_preset = request.args.get('p2_backlash_preset',  'STANDARD')
            cl1 = _get_preset_value(spec, 'clearances', cl1_preset, request.args.get('clearance_custom',    0.0))
            bl1 = _get_preset_value(spec, 'backlash',   bl1_preset, request.args.get('backlash_custom',     0.0))
            cl2 = _get_preset_value(spec, 'clearances', cl2_preset, request.args.get('p2_clearance_custom', 0.0))
            bl2 = _get_preset_value(spec, 'backlash',   bl2_preset, request.args.get('p2_backlash_custom',  0.0))
            _default_c = (num_teeth1 + num_teeth2) * spec['pitch'] / (2.0 * math.pi)
            center_dist = float(request.args.get('center_distance', _default_c))

            dxf_bytes = generate_belt_dxf_dual(
                family=family, pitch=pitch,
                num_teeth1=num_teeth1, num_teeth2=num_teeth2,
                bore_mm1=bore1, bore_mm2=bore2,
                clearance_mm1=cl1, backlash_mm1=bl1, print_extra_mm1=pe1,
                clearance_mm2=cl2, backlash_mm2=bl2, print_extra_mm2=pe2,
                center_dist_mm=center_dist,
            )
            filename = f'{family}-{pitch}-{num_teeth1}T-{num_teeth2}T-belt.dxf'
        else:
            if family not in BELT_FAMILIES:
                return f'Belt DXF not available for family {family}', 400
            dxf_bytes = generate_belt_dxf(family, pitch, n_teeth=3)
            filename  = f'{family}-{pitch}-belt-profile.dxf'

        return Response(
            dxf_bytes,
            mimetype='application/dxf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return f'Error generating belt DXF: {e}', 400


@app.route('/api/validate-spoke-fillets')
def api_validate_spoke_fillets():
    """Check if tip/base fillet tangent points conflict on the spoke wall.
    Returns corrected {tip, base} values (clamping the one that was just changed).
    """
    import math as _m
    from exporters.svg_exporter import (
        _sv2_line_circle_fillet, _sv2_line_line_fillet,
        _sv2_unit, _sv2_dot, _sv2_project,
    )
    from geometry.pulley_geometry import generate_profile_groove, _build_groove_points, wrap_groove_to_pulley
    try:
        family   = request.args.get('family', 'HTD')
        pitch    = request.args.get('pitch',  '5M')
        key      = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            raise ValueError(f'Unknown profile {family}/{pitch}')
        spec      = PULLEY_SPECS[key]
        num_teeth = max(spec['min_teeth'], int(request.args.get('teeth', spec['min_teeth'])))
        hub_od    = float(request.args.get('spokes_hub_od',    16.0))
        rim_depth = float(request.args.get('spokes_rim_depth',  2.0))
        spoke_count = max(2, int(request.args.get('spokes_count', 4)))
        spoke_width = float(request.args.get('spokes_width',  4.0))
        fillet_tip  = float(request.args.get('spokes_fillet_tip',  0.0))
        fillet_base = float(request.args.get('spokes_fillet_base', 0.0))
        changed     = request.args.get('changed', 'tip')  # 'tip' or 'base'

        # Compute r_tooth_root from groove profile (clearance=0 for geometry check)
        container  = generate_profile_groove(family, key, num_teeth, 0.0, 0.0, 0.0)
        groove_pts = _build_groove_points(container.primitives[1:-1], family)
        wrapped, _, _ = wrap_groove_to_pulley(groove_pts, spec, num_teeth, 0.0)
        r_tooth_root = (min(_m.hypot(x, y) for x, y in wrapped)
                        if wrapped else num_teeth * spec['pitch'] / (2.0 * _m.pi))

        r_hub = hub_od / 2.0
        r_rim = max(r_hub + 0.5, r_tooth_root - rim_depth)

        half_w     = spoke_width / 2.0
        half_a_hub = _m.asin(min(1.0, half_w / r_hub))
        half_a_rim = _m.asin(min(1.0, half_w / r_rim))
        theta_step = 2.0 * _m.pi / spoke_count
        void_mid   = theta_step / 2.0

        # Right and left wall corner points for void 0
        p_rh = (r_hub * _m.cos(half_a_hub), r_hub * _m.sin(half_a_hub))
        p_rr = (r_rim * _m.cos(half_a_rim), r_rim * _m.sin(half_a_rim))
        p_lh = (r_hub * _m.cos(theta_step - half_a_hub), r_hub * _m.sin(theta_step - half_a_hub))
        p_lr = (r_rim * _m.cos(theta_step - half_a_rim), r_rim * _m.sin(theta_step - half_a_rim))

        rdx, rdy = p_rr[0]-p_rh[0], p_rr[1]-p_rh[1]
        ldx, ldy = p_lr[0]-p_lh[0], p_lr[1]-p_lh[1]
        probe_x  = (r_hub + r_rim) * 0.5 * _m.cos(void_mid)
        probe_y  = (r_hub + r_rim) * 0.5 * _m.sin(void_mid)

        rux, ruy = _sv2_unit(rdx, rdy); rnx, rny = -ruy, rux
        rdp = _sv2_dot(probe_x-p_rh[0], probe_y-p_rh[1], rnx, rny)
        in_rx, in_ry = (rnx, rny) if rdp > 0 else (-rnx, -rny)

        lux, luy = _sv2_unit(ldx, ldy); lnx, lny = -luy, lux
        ldp = _sv2_dot(probe_x-p_lh[0], probe_y-p_lh[1], lnx, lny)
        in_lx, in_ly = (lnx, lny) if ldp > 0 else (-lnx, -lny)

        def tip_rim_angle_for(ft):
            """Angle (radians) of the rim tangent point for tip fillet radius ft.
            The rim tangent point is in the direction of the fillet center from the
            origin (since rim circle is centred at origin). Returns None if no solution.
            """
            if ft <= 0:
                return _m.atan2(p_rr[1], p_rr[0])
            r = _sv2_line_circle_fillet(
                p_rh[0], p_rh[1], rdx, rdy, 0.0, 0.0, r_rim, ft,
                False, in_rx, in_ry, True)
            return _m.atan2(r[1], r[0]) if r else None

        def s_tip_for(ft):
            r = _sv2_line_circle_fillet(
                p_rh[0], p_rh[1], rdx, rdy, 0.0, 0.0, r_rim, ft,
                False, in_rx, in_ry, True)
            return r[6] if r else None

        def s_base_for(fb):
            ll = _sv2_line_line_fillet(
                p_rh[0], p_rh[1], rdx, rdy,
                p_lh[0], p_lh[1], ldx, ldy,
                in_rx, in_ry, in_lx, in_ly, fb)
            if ll is None:
                return None
            _, _, s = _sv2_project(ll[2], ll[3], p_rh[0], p_rh[1], rdx, rdy)
            return s

        corrected = False

        if changed == 'tip':
            # ── Rim-arc cap: rim tangent point must not pass the void midpoint angle.
            rim_angle = tip_rim_angle_for(fillet_tip)
            if rim_angle is None or rim_angle > void_mid:
                lo, hi = 0.0, fillet_tip
                for _ in range(60):
                    mid = (lo + hi) / 2.0
                    a = tip_rim_angle_for(mid)
                    if a is not None and a <= void_mid:
                        lo = mid
                    else:
                        hi = mid
                fillet_tip = lo
                corrected = True

            # ── Spoke-wall conflict: tip tangent must remain above base tangent.
            s_tip  = s_tip_for(fillet_tip)
            s_base = s_base_for(fillet_base)
            if s_tip is not None and s_base is not None and s_tip < s_base:
                target = s_base
                lo, hi = 0.0, fillet_tip
                for _ in range(60):
                    mid = (lo + hi) / 2.0
                    s = s_tip_for(mid)
                    if s is not None and s >= target:
                        lo = mid
                    else:
                        hi = mid
                fillet_tip = lo
                corrected = True

            return jsonify({'tip': round(fillet_tip, 2), 'base': round(fillet_base, 2), 'corrected': corrected})

        else:  # changed == 'base'
            # ── Spoke-wall conflict: base tangent must remain below tip tangent.
            s_tip  = s_tip_for(fillet_tip)
            s_base = s_base_for(fillet_base)
            if s_tip is None or s_base is None or s_tip >= s_base:
                return jsonify({'tip': round(fillet_tip, 2), 'base': round(fillet_base, 2), 'corrected': False})
            target = s_tip
            lo, hi = 0.0, fillet_base
            for _ in range(60):
                mid = (lo + hi) / 2.0
                s = s_base_for(mid)
                if s is not None and s <= target:
                    lo = mid
                else:
                    hi = mid
            return jsonify({'tip': round(fillet_tip, 2), 'base': round(lo, 2), 'corrected': True})

    except Exception as e:
        return jsonify({'error': str(e)}), 400


def _safe_float(val, default):
    """Convert val to float, falling back to default on empty string or None."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return float(default)


def _parse_flange_params(args, prefix=''):
    """Return flange parameter dict from request args (shared by both routes)."""
    return dict(
        flange_3dprint    = args.get(f'{prefix}flange_3dprint', '0') == '1',
        top_separate      = args.get(f'{prefix}flange_top_separate', '1') == '1',
        flange_angle_deg  = max(8.0,  min(25.0, _safe_float(args.get(f'{prefix}flange_angle'),   15.0))),
        rim_radius_mm     = max(0.5,  _safe_float(args.get(f'{prefix}flange_rim_radius'),  3.0)),
        flange_height_mm  = max(0.1,  _safe_float(args.get(f'{prefix}flange_height'),      1.5)),
        plate_height_mm   = max(0.3,  _safe_float(args.get(f'{prefix}flange_plate_height'), 1.0)),
        bend_radius_mm    = max(0.0,  _safe_float(args.get(f'{prefix}flange_bend_radius'),  0.0)),
        # Nub/socket params (3D-print top-separate only)
        nubs_enabled      = args.get(f'{prefix}flange_nubs_enabled', '0') == '1',
        nub_count         = max(1, int(_safe_float(args.get(f'{prefix}flange_nub_count'),     4))),
        nub_dia_mm        = max(1.0, _safe_float(args.get(f'{prefix}flange_nub_dia'),         3.0)),
        nub_height_mm     = max(0.5, _safe_float(args.get(f'{prefix}flange_nub_height'),      2.0)),
        nub_allowance_mm  = max(0.0, _safe_float(args.get(f'{prefix}flange_nub_allowance'),   0.2)),
    )


@app.route('/download/flange-stl')
def download_flange_stl():
    """Return STL of a single flange plate.

    Required query params: family, pitch, teeth, bore, belt_height,
                           flange_which ('top'|'bottom'),
                           flange_3dprint ('1'|'0'),
                           flange_angle, flange_rim_radius, flange_height (3D print),
                           flange_plate_height, flange_bend_radius (metal).
    Optional: hub_od, spokes_enabled, spokes_hub_od, clearance_preset, backlash_preset.
    """
    try:
        args = request.args

        family = args.get('family', 'HTD')
        pitch  = args.get('pitch',  '5M')
        key    = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return jsonify({'error': f'Unknown profile {family}/{pitch}'}), 400

        spec      = PULLEY_SPECS[key]
        num_teeth = max(spec['min_teeth'], int(args.get('teeth', spec['min_teeth'])))
        bore_mm   = _get_bore(args)
        belt_h    = max(1.0, float(args.get('belt_height', 10.0)))
        cl_mm     = _get_preset_value(spec, 'clearances',
                                      args.get('clearance_preset', 'STANDARD'),
                                      args.get('clearance_custom', 0.0))
        pe_mm     = float(args.get('print_extra', 0.0))

        hub_od         = max(0.0, float(args.get('hub_od', 0.0)))
        spokes_enabled = args.get('spokes_enabled', '0') == '1'
        spoke_hub_od   = max(0.0, float(args.get('spokes_hub_od', 0.0)))
        spoke_rim_depth = max(0.0, float(args.get('spokes_rim_depth', 0.0)))

        fp    = _parse_flange_params(args)
        which = args.get('flange_which', 'top')   # 'top' or 'bottom'

        if fp['flange_3dprint']:
            stl_bytes = generate_3dprint_flange_stl(
                family, pitch, num_teeth, bore_mm, belt_h,
                clearance_mm=cl_mm, print_extra_mm=pe_mm,
                flange_angle_deg=fp['flange_angle_deg'],
                rim_radius_mm=fp['rim_radius_mm'],
                flange_height_mm=fp['flange_height_mm'],
                which=which,
                hub_od_mm=hub_od,
                spokes_enabled=spokes_enabled,
                spoke_hub_od_mm=spoke_hub_od,
                rim_depth_mm=spoke_rim_depth,
            )
        else:
            stl_bytes = generate_metal_flange_stl(
                family, pitch, num_teeth, bore_mm, belt_h,
                clearance_mm=cl_mm, print_extra_mm=pe_mm,
                flange_angle_deg=fp['flange_angle_deg'],
                rim_radius_mm=fp['rim_radius_mm'],
                plate_height_mm=fp['plate_height_mm'],
                bend_radius_mm=fp['bend_radius_mm'],
                which=which,
                hub_od_mm=hub_od,
                spokes_enabled=spokes_enabled,
                spoke_hub_od_mm=spoke_hub_od,
                rim_depth_mm=spoke_rim_depth,
            )

        suffix    = '-upper-flange' if which == 'top' else '-lower-flange'
        type_tag  = '3DP' if fp['flange_3dprint'] else 'Metal'
        filename  = f'{family}{pitch}-{num_teeth}T-{type_tag}{suffix}.stl'

        return Response(
            stl_bytes,
            mimetype='model/stl',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        import traceback
        return Response(f'Flange STL export failed:\n{traceback.format_exc()}',
                        status=500, mimetype='text/plain')


@app.route('/api/report-bug', methods=['POST'])
def api_report_bug():
    """Save a bug report to logs/bug_reports.log."""
    try:
        data = request.get_json(force=True) or {}
        seeing      = str(data.get('seeing',      '')).strip()
        should_see  = str(data.get('should_see',  '')).strip()
        email       = str(data.get('email',       '')).strip()
        state       = data.get('state', {})          # dict of current app params
        report_type = str(data.get('report_type', 'bug')).strip()

        if not seeing and not should_see:
            return jsonify({'error': 'At least one description field is required.'}), 400

        is_feature   = report_type == 'feature'
        report_label = 'Feature Request' if is_feature else 'Bug Report'
        label_seeing = 'Would like to do' if is_feature else 'Currently seeing'
        label_should = "Why it's useful"  if is_feature else 'Should be seeing'

        os.makedirs(_LOG_DIR, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = (
            f'\n{"="*60}\n'
            f'{report_label} — {timestamp}\n'
            f'App Version: {APP_VERSION}   Build: {BUILD_TIME}\n'
            f'{"="*60}\n'
            f'{label_seeing}:\n  {seeing or "(not provided)"}\n\n'
            f'{label_should}:\n  {should_see or "(not provided)"}\n\n'
            f'Contact email:\n  {email or "(not provided)"}\n\n'
            f'App state:\n{json.dumps(state, indent=2)}\n'
        )
        with open(_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(entry)

        return jsonify({'ok': True})
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
