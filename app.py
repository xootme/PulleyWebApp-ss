"""
app.py — Sketch Timing Pulley web app (Flask)
Serves the pulley generator UI and returns SVG downloads.
"""
import math
import io
from flask import Flask, render_template, request, Response, jsonify

from geometry.pulley_geometry import (
    PULLEY_SPECS, PROFILE_KEY_PREFIX, PROFILE_PITCHES,
    getPitchDiameter, getOuterDiameter, getTeethFromOD,
    BELT_FAMILIES,
    correct_center_distance, center_dist_from_belt_teeth,
)
from exporters.svg_exporter import generate_svg, generate_svg_dual
from exporters.png_exporter import generate_png, generate_png_dual
from exporters.belt_svg_exporter import generate_belt_svg, generate_belt_png
from exporters.dxf_exporter import generate_dxf

app = Flask(__name__)

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
    )


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
            n  = int(request.args.get('value', spec['min_teeth']))
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
            png = _build_png_dual_from_request(request.args, size_px=480)
        else:
            png = _build_png_from_request(request.args, size_px=480)
        return Response(png, mimetype='image/png')
    except Exception as e:
        from PIL import Image, ImageDraw
        import io
        img = Image.new('RGB', (480, 480), (250, 251, 252))
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
            bore_mm   = float(request.args.get('p2_bore', 8.0))
            pr_ex     = float(request.args.get('p2_print_extra', 0.0))
            cl_preset = request.args.get('p2_clearance_preset', 'STANDARD')
            bl_preset = request.args.get('p2_backlash_preset',  'STANDARD')
            cl_mm = _get_preset_value(spec, 'clearances', cl_preset, request.args.get('p2_clearance_custom', 0.0))
            bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, request.args.get('p2_backlash_custom',  0.0))
            filename = f'{family}-{pitch}-{num_teeth}T-P2.dxf'
        else:
            num_teeth = max(spec['min_teeth'], int(request.args.get('teeth', spec['min_teeth'])))
            bore_mm   = float(request.args.get('bore', 8.0))
            pr_ex     = float(request.args.get('print_extra', 0.0))
            cl_preset = request.args.get('clearance_preset', 'STANDARD')
            bl_preset = request.args.get('backlash_preset',  'STANDARD')
            cl_mm = _get_preset_value(spec, 'clearances', cl_preset, request.args.get('clearance_custom', 0.0))
            bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, request.args.get('backlash_custom',  0.0))
            filename = f'{family}-{pitch}-{num_teeth}T.dxf'

        dxf = generate_dxf(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
            print_extra_mm=pr_ex,
        )
        return Response(
            dxf,
            mimetype='application/dxf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return f'Error generating DXF: {e}', 400


def _build_png_from_request(args, size_px=480):
    family  = args.get('family', 'HTD')
    pitch   = args.get('pitch', '5M')
    key     = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec       = PULLEY_SPECS[key]
    num_teeth  = max(spec['min_teeth'], int(args.get('teeth', spec['min_teeth'])))
    bore_mm    = float(args.get('bore', 8.0))
    pr_ex      = float(args.get('print_extra', 0.0))
    cl_preset  = args.get('clearance_preset', 'STANDARD')
    bl_preset  = args.get('backlash_preset', 'STANDARD')
    cl_mm = _get_preset_value(spec, 'clearances', cl_preset, args.get('clearance_custom', 0.0))
    bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, args.get('backlash_custom',  0.0))
    return generate_png(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
        print_extra_mm=pr_ex, size_px=size_px,
    )


def _build_svg_from_request(args):
    family  = args.get('family', 'HTD')
    pitch   = args.get('pitch', '5M')
    key     = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec    = PULLEY_SPECS[key]

    num_teeth  = max(spec['min_teeth'], int(args.get('teeth', spec['min_teeth'])))
    bore_mm    = float(args.get('bore', 8.0))
    pr_ex      = float(args.get('print_extra', 0.0))

    cl_preset  = args.get('clearance_preset', 'STANDARD')
    bl_preset  = args.get('backlash_preset', 'STANDARD')
    cl_custom  = args.get('clearance_custom', 0.0)
    bl_custom  = args.get('backlash_custom', 0.0)

    cl_mm = _get_preset_value(spec, 'clearances', cl_preset, cl_custom)
    bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, bl_custom)

    return generate_svg(
        family=family,
        pitch=pitch,
        num_teeth=num_teeth,
        bore_mm=bore_mm,
        clearance_mm=cl_mm,
        backlash_mm=bl_mm,
        print_extra_mm=pr_ex,
        clearance_preset=cl_preset,
        backlash_preset=bl_preset,
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
    bore_mm    = float(args.get('p2_bore', 8.0))
    pr_ex      = float(args.get('p2_print_extra', 0.0))

    cl_preset  = args.get('p2_clearance_preset', 'STANDARD')
    bl_preset  = args.get('p2_backlash_preset', 'STANDARD')
    cl_custom  = args.get('p2_clearance_custom', 0.0)
    bl_custom  = args.get('p2_backlash_custom', 0.0)

    cl_mm = _get_preset_value(spec, 'clearances', cl_preset, cl_custom)
    bl_mm = _get_preset_value(spec, 'backlash',   bl_preset, bl_custom)

    return generate_svg(
        family=family,
        pitch=pitch,
        num_teeth=num_teeth,
        bore_mm=bore_mm,
        clearance_mm=cl_mm,
        backlash_mm=bl_mm,
        print_extra_mm=pr_ex,
        clearance_preset=cl_preset,
        backlash_preset=bl_preset,
    )


def _build_png_dual_from_request(args, size_px=480):
    family  = args.get('family', 'HTD')
    pitch   = args.get('pitch', '5M')
    key     = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec = PULLEY_SPECS[key]

    num_teeth1 = max(spec['min_teeth'], int(args.get('teeth', spec['min_teeth'])))
    bore1      = float(args.get('bore', 8.0))
    pr_ex1     = float(args.get('print_extra', 0.0))
    cl1 = _get_preset_value(spec, 'clearances', args.get('clearance_preset', 'STANDARD'), args.get('clearance_custom', 0.0))
    bl1 = _get_preset_value(spec, 'backlash',   args.get('backlash_preset',  'STANDARD'), args.get('backlash_custom',  0.0))

    num_teeth2 = max(spec['min_teeth'], int(args.get('p2_teeth', spec['min_teeth'])))
    bore2      = float(args.get('p2_bore', 8.0))
    pr_ex2     = float(args.get('p2_print_extra', 0.0))
    cl2 = _get_preset_value(spec, 'clearances', args.get('p2_clearance_preset', 'STANDARD'), args.get('p2_clearance_custom', 0.0))
    bl2 = _get_preset_value(spec, 'backlash',   args.get('p2_backlash_preset',  'STANDARD'), args.get('p2_backlash_custom',  0.0))

    import math as _math
    _default_c = (num_teeth1 + num_teeth2) * spec['pitch'] / (2.0 * _math.pi)
    center_dist = float(args.get('center_distance', _default_c))

    return generate_png_dual(
        family=family, pitch=pitch,
        num_teeth1=num_teeth1, bore_mm1=bore1, clearance_mm1=cl1, backlash_mm1=bl1, print_extra_mm1=pr_ex1,
        num_teeth2=num_teeth2, bore_mm2=bore2, clearance_mm2=cl2, backlash_mm2=bl2, print_extra_mm2=pr_ex2,
        center_dist_mm=center_dist, size_px=size_px,
    )


@app.route('/api/belt-preview')
def api_belt_preview():
    """Return PNG of belt tooth cross-section for live preview."""
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        if family not in BELT_FAMILIES:
            return Response(b'', mimetype='image/png')
        png = generate_belt_png(family, pitch, n_teeth=3, size_px=480)
        return Response(png, mimetype='image/png')
    except Exception as e:
        from PIL import Image, ImageDraw
        import io as _io
        img = Image.new('RGB', (480, 200), (250, 251, 252))
        ImageDraw.Draw(img).text((10, 10), f'Error: {e}', fill=(200, 0, 0))
        buf = _io.BytesIO();  img.save(buf, 'PNG');  buf.seek(0)
        return Response(buf.read(), mimetype='image/png')


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
            bore1      = float(request.args.get('bore',    8.0))
            bore2      = float(request.args.get('p2_bore', 8.0))
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

            svg      = generate_svg_dual(
                family=family, pitch=pitch,
                num_teeth1=num_teeth1, bore_mm1=bore1,
                clearance_mm1=cl1, backlash_mm1=bl1, print_extra_mm1=pe1,
                clearance_preset1=cl1_preset, backlash_preset1=bl1_preset,
                num_teeth2=num_teeth2, bore_mm2=bore2,
                clearance_mm2=cl2, backlash_mm2=bl2, print_extra_mm2=pe2,
                clearance_preset2=cl2_preset, backlash_preset2=bl2_preset,
                center_dist_mm=center_dist, n_belt_teeth=n_belt,
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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
