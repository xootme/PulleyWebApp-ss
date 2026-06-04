"""
app.py — Timing Pulley Generator web app (Flask)
Serves the pulley generator UI and returns SVG downloads.
"""
import hashlib
import math
import io
import os
import json
import re
import time
import threading
try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False          # Windows dev environment
try:
    import psutil
    _HAVE_PSUTIL = True
except ImportError:
    _HAVE_PSUTIL = False
from datetime import datetime, timedelta
from flask import Flask, render_template, request, Response, jsonify, send_from_directory, send_file
from exporters.job_queue import (
    create_job, get_job, start_job, update_progress, finish_job, get_queue_status
)

# ── App version ───────────────────────────────────────────────────────────────
APP_VERSION        = '1.0'
# Increment when a param is renamed, split, or its meaning changes.
# New optional params never need a bump — missing keys just use form defaults.
CCT_SCHEMA_VERSION = 1
BUILD_TIME         = datetime.now().strftime('%Y-%m-%d %H:%M')

# ── Logs ─────────────────────────────────────────────────────────────────────
# PULLEY_LOG_DIR is set by the packaged launcher so logs go to AppData, not the install folder.
_LOG_DIR             = os.environ.get('PULLEY_LOG_DIR',
                           os.path.join(os.path.dirname(__file__), 'logs'))
_LOG_FILE            = os.path.join(_LOG_DIR, 'bug_reports.log')
_DOWNLOAD_COUNT_FILE = os.path.join(_LOG_DIR, 'download_count.json')
_METRICS_FILE        = os.path.join(_LOG_DIR, 'metrics.jsonl')
_CONSTRAINTS_FILE    = os.path.join(_LOG_DIR, 'constraint_events.jsonl')
_BUG_COMMENTS_FILE   = os.path.join(_LOG_DIR, 'bug_comments.json')
_BUG_ISSUE_URLS_FILE = os.path.join(_LOG_DIR, 'bug_issue_urls.json')
_METRICS_RETENTION_DAYS  = 30
_CPU_CONSTRAINT_THRESHOLD = 80.0
_MEM_CONSTRAINT_THRESHOLD = 85.0
_download_lock       = threading.Lock()   # in-process guard (dev / single-worker)
_metrics_lock        = threading.Lock()

# Per-worker request rate counter (resets each metrics sample)
_request_count      = 0
_request_count_lock = threading.Lock()


def _increment_request_count():
    global _request_count
    with _request_count_lock:
        _request_count += 1


def _sample_and_reset_request_count():
    global _request_count
    with _request_count_lock:
        val = _request_count
        _request_count = 0
    return val


def _trim_jsonl(path, retention_days):
    """Drop entries older than retention_days from a .jsonl file."""
    cutoff = time.time() - retention_days * 86400
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        kept = []
        for l in lines:
            try:
                if json.loads(l).get('ts', 0) >= cutoff:
                    kept.append(l)
            except Exception:
                pass
        if len(kept) < len(lines):
            with open(path, 'w', encoding='utf-8') as f:
                f.writelines(kept)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _append_jsonl(path, obj):
    with _metrics_lock:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(obj) + '\n')


def _metrics_sampler():
    """Background thread: sample CPU/memory/requests every 60 s."""
    time.sleep(10)   # let gunicorn finish initialising before first sample
    while True:
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
            ts      = int(time.time())
            req_cnt = _sample_and_reset_request_count()

            if _HAVE_PSUTIL:
                cpu     = psutil.cpu_percent(interval=None)
                mem     = psutil.virtual_memory()
                mem_mb  = mem.used // (1024 * 1024)
                mem_pct = mem.percent
                try:
                    disk     = psutil.disk_usage(_LOG_DIR)
                    disk_pct = disk.percent
                except Exception:
                    disk_pct = 0
            else:
                cpu = mem_mb = mem_pct = disk_pct = 0

            _append_jsonl(_METRICS_FILE, {
                'ts': ts, 'req_per_min': req_cnt,
                'cpu': cpu, 'mem_mb': mem_mb, 'mem_pct': mem_pct,
                'disk_pct': disk_pct,
            })

            # Constraint events
            if cpu > _CPU_CONSTRAINT_THRESHOLD:
                _append_jsonl(_CONSTRAINTS_FILE, {
                    'ts': ts, 'type': 'cpu',
                    'value': cpu, 'detail': f'CPU {cpu:.1f}%',
                })
            if mem_pct > _MEM_CONSTRAINT_THRESHOLD:
                _append_jsonl(_CONSTRAINTS_FILE, {
                    'ts': ts, 'type': 'memory',
                    'value': mem_pct, 'detail': f'Memory {mem_pct:.1f}% ({mem_mb} MB)',
                })
            if req_cnt > 120:   # >2 req/s sustained over the sample window
                _append_jsonl(_CONSTRAINTS_FILE, {
                    'ts': ts, 'type': 'request_rate',
                    'value': req_cnt, 'detail': f'{req_cnt} req/min',
                })

            # Trim old data once per sample
            _trim_jsonl(_METRICS_FILE,     _METRICS_RETENTION_DAYS)
            _trim_jsonl(_CONSTRAINTS_FILE, _METRICS_RETENTION_DAYS)

        except Exception:
            pass

        time.sleep(60)


_metrics_thread = threading.Thread(target=_metrics_sampler, daemon=True)
_metrics_thread.start()


def _increment_download_count(fmt=None):
    """Increment the persistent download counter; email at each multiple of 100.

    Uses fcntl.flock (exclusive file lock) on Linux so concurrent gunicorn
    workers don't corrupt the counter file.  Falls back to a threading.Lock
    on Windows (dev environment, single worker).
    """
    os.makedirs(_LOG_DIR, exist_ok=True)
    count = 0
    if _HAVE_FCNTL:
        # Open for read+write, create if missing; flock blocks until exclusive.
        fd = os.open(_DOWNLOAD_COUNT_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.read(fd, 4096).decode('utf-8').strip()
            try:
                data = json.loads(raw) if raw else {}
            except (ValueError, json.JSONDecodeError):
                data = {}
            count = int(data.get('count', 0)) + 1
            data['count'] = count
            if fmt:
                by_fmt = data.get('by_format', {})
                by_fmt[fmt] = int(by_fmt.get(fmt, 0)) + 1
                data['by_format'] = by_fmt
            payload = json.dumps(data).encode('utf-8')
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, payload)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    else:
        with _download_lock:
            try:
                with open(_DOWNLOAD_COUNT_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError):
                data = {}
            count = int(data.get('count', 0)) + 1
            data['count'] = count
            if fmt:
                by_fmt = data.get('by_format', {})
                by_fmt[fmt] = int(by_fmt.get(fmt, 0)) + 1
                data['by_format'] = by_fmt
            with open(_DOWNLOAD_COUNT_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
    if count % 100 == 0:
        _send_milestone_email(count)


def _send_milestone_email(count):
    """Send a download-milestone notification via SendGrid."""
    api_key = os.environ.get('SENDGRID_API_KEY', '').strip()
    if not api_key:
        return
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        body = (
            f'The Timing Pulley Generator has reached {count:,} total downloads.\n\n'
            f'Timestamp: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
        )
        message = Mail(
            from_email='noreply@cheapcadtools.com',
            to_emails='info@cheapcadtools.com',
            subject=f'[Pulley Generator] {count:,} downloads milestone!',
            plain_text_content=body,
        )
        SendGridAPIClient(api_key).send(message)
    except Exception:
        pass

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
    build_support_ribs,
)

# PULLEY_BASE_DIR is set by the packaged launcher to sys._MEIPASS so Flask
# finds templates and static inside the PyInstaller bundle.
_base_dir = os.environ.get('PULLEY_BASE_DIR', os.path.dirname(os.path.abspath(__file__)))

# ── Fusion 360 addin integration ──────────────────────────────────────────────
_FUSION_CONFIG = os.path.join(
    os.environ.get('APPDATA', os.path.expanduser('~')),
    'CheapCADTools', 'config.json')

def _mirror_to_fusion(content: bytes, filename: str) -> None:
    """Copy a download to the Fusion watch folder when the addin is connected."""
    try:
        if not os.path.exists(_FUSION_CONFIG):
            return
        with open(_FUSION_CONFIG) as f:
            cfg = json.load(f)
        watch_dir = cfg.get('fusion_watch_dir')
        if not (cfg.get('fusion_connected') and watch_dir):
            return
        os.makedirs(watch_dir, exist_ok=True)
        dest = os.path.join(watch_dir, filename)
        with open(dest, 'wb') as f:
            f.write(content)
    except Exception as _mirror_err:
        import traceback as _tb
        import logging as _lg
        _lg.getLogger(__name__).error(
            '_mirror_to_fusion failed for %s: %s\n%s',
            filename, _mirror_err, _tb.format_exc()
        )

# ── SolidWorks listener integration ───────────────────────────────────────────
# Config key is shared with Fusion (same file); SolidWorks listener writes
# solidworks_connected + solidworks_watch_dir when it starts up.
def _mirror_to_solidworks(content: bytes, filename: str) -> None:
    """Copy a download to the SolidWorks watch folder when the listener is running."""
    try:
        if not os.path.exists(_FUSION_CONFIG):
            return
        with open(_FUSION_CONFIG) as f:
            cfg = json.load(f)
        watch_dir = cfg.get('solidworks_watch_dir')
        if not (cfg.get('solidworks_connected') and watch_dir):
            return
        os.makedirs(watch_dir, exist_ok=True)
        dest = os.path.join(watch_dir, filename)
        with open(dest, 'wb') as f:
            f.write(content)
    except Exception as _mirror_err:
        import traceback as _tb
        import logging as _lg
        _lg.getLogger(__name__).error(
            '_mirror_to_solidworks failed for %s: %s\n%s',
            filename, _mirror_err, _tb.format_exc()
        )

app = Flask(__name__,
            template_folder=os.path.join(_base_dir, 'templates'),
            static_folder=os.path.join(_base_dir, 'static'))
# ─── Cloudflare Worker proxy support ──────
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_host=1)
# ──────────────────────────────────────────

# ─── Gzip compression ─────────────────────
from flask_compress import Compress
app.config['COMPRESS_MIMETYPES'] = [
    'text/html', 'text/css', 'application/javascript',
    'image/svg+xml',                    # SVG downloads
    'application/dxf', 'text/plain',    # DXF downloads
    'application/octet-stream',         # STL / STEP binary
]
app.config['COMPRESS_LEVEL']   = 6     # balanced speed vs ratio
app.config['COMPRESS_MIN_SIZE'] = 512  # don't compress tiny responses
Compress(app)
# ──────────────────────────────────────────

# ─── HTTP caching ─────────────────────────
# Routes whose output is fully determined by query parameters.
_CACHEABLE_PREFIXES = (
    '/download/',
    '/api/belt', '/api/od', '/api/spec',
    '/api/preview-stl', '/api/preview', '/api/belt-preview',
)
_CACHE_MAX_AGE = 3600   # 1 hour; Cloudflare + browser cache


def _params_etag():
    """Stable ETag derived from query-string parameters and server build time."""
    raw = BUILD_TIME + '|' + '|'.join(f'{k}={v}' for k, v in sorted(request.args.items()))
    return '"' + hashlib.md5(raw.encode()).hexdigest() + '"'


@app.before_request
def _count_request():
    _increment_request_count()


@app.before_request
def _check_client_cache():
    """Return 304 Not Modified when the client already has the current version."""
    if request.method != 'GET':
        return
    if not any(request.path.startswith(p) for p in _CACHEABLE_PREFIXES):
        return
    etag = _params_etag()
    incoming = request.headers.get('If-None-Match', '')
    # flask-compress appends ':gzip' inside the ETag quotes on compressed responses
    # e.g. "abc123" → "abc123:gzip".  Strip it before comparing.
    incoming_norm = incoming[:-6] + '"' if incoming.endswith(':gzip"') else incoming
    if incoming_norm == etag:
        return Response(
            status=304,
            headers={'ETag': etag,
                     'Cache-Control': f'public, max-age={_CACHE_MAX_AGE}'},
        )
# ──────────────────────────────────────────


@app.after_request
def _admin_cors(response):
    """Allow the local admin dashboard HTML file to call admin API endpoints."""
    if request.path.startswith('/api/admin/') or request.path.startswith('/api/subscribers/'):
        response.headers['Access-Control-Allow-Origin']  = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
    return response


@app.route('/api/admin/<path:_>', methods=['OPTIONS'])
def _admin_cors_preflight(_):
    """Handle CORS preflight for all /api/admin/* routes."""
    r = Response('', 204)
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
    return r


@app.route('/api/subscribers/<path:_>', methods=['OPTIONS'])
def _subscribers_cors_preflight(_):
    r = Response('', 204)
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return r


@app.after_request
def _track_download(response):
    """Count every successful /download/* response and stamp cache headers."""
    if response.status_code == 200 and request.method == 'GET':
        if request.path.startswith('/download/'):
            _path = request.path.lower()
            if 'step' in _path:
                _fmt = 'step'
            elif 'stl' in _path:
                _fmt = 'stl'
            elif 'dxf' in _path:
                _fmt = 'dxf'
            elif 'svg' in _path:
                _fmt = 'svg'
            else:
                _fmt = 'other'
            _increment_download_count(_fmt)
        if any(request.path.startswith(p) for p in _CACHEABLE_PREFIXES):
            response.headers['ETag'] = _params_etag()
            # Downloads: no-store prevents all browser caching (Edge on 127.0.0.1
            # ignores max-age=0 for downloads; no-store is the only reliable option).
            # API/preview routes: full max-age for performance.
            if request.path.startswith('/download/'):
                response.headers['Cache-Control'] = 'no-store'
            else:
                response.headers['Cache-Control'] = f'public, max-age={_CACHE_MAX_AGE}'
    return response


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
        cct_schema_version=CCT_SCHEMA_VERSION,
    )


@app.route('/onshape')
def onshape_panel():
    """OnShape Application Extension panel."""
    return render_template('onshape_panel.html')


@app.route('/api/onshape/import', methods=['POST'])
def api_onshape_import():
    """Generate STEP for the given pulley params and upload to an OnShape document.

    Body (JSON):
        documentId, workspaceId, server (default cad.onshape.com),
        accessKey, secretKey  — OnShape API key credentials
        qs                    — URL query string identical to /download/step params
    """
    import hmac as _hmac, hashlib as _hs, base64 as _b64, uuid as _uid
    import requests as _rq
    from io import BytesIO
    from datetime import datetime, timezone as _tz
    from urllib.parse import parse_qs as _pqs

    try:
        body       = request.get_json(force=True)
        doc_id     = body['documentId'].strip()
        ws_id      = body['workspaceId'].strip()
        server     = body.get('server', 'cad.onshape.com').strip().rstrip('/')
        if not server.startswith('http'):
            server = 'https://' + server
        ak         = body['accessKey'].strip()
        sk         = body['secretKey'].strip()
        qs         = body.get('qs', '')

        # Parse query string → plain dict for existing param helpers
        raw  = _pqs(qs, keep_blank_values=True)
        args = {k: v[0] for k, v in raw.items()}

        pulley = args.get('pulley', '1')
        family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
            _parse_stl_params(args, pulley)
        pfx = 'p2_' if pulley == '2' else ''
        hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(args, pfx)
        sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_c, sp_h, sp_split = \
            _parse_spoke_params(args, pfx)
        eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od
        _fl_enabled = args.get(f'{pfx}flange_enabled') == '1'
        fp = _parse_flange_params(args, pfx) if _fl_enabled else {}

        kw = dict(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, belt_height_mm=belt_height,
            clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex,
            hub_od_mm=eff_hub_od, hub_height_mm=hub_h,
            screw_dia_mm=sd, screw_count=sc,
            captured_nut=cn, flat_depth_mm=fd,
            keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            spoke_count=sp_c if sp_en else 0,
            spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
            rim_depth_mm=sp_rim, fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
            spoke_height_mm=sp_h,
            flange_enabled=_fl_enabled,
            flange_3dprint=fp.get('flange_3dprint', True),
            flange_angle_deg=fp.get('flange_angle_deg', 15.0),
            flange_rim_radius_mm=fp.get('rim_radius_mm', 3.0),
            flange_height_mm=fp.get('flange_height_mm', 1.5),
            flange_top_separate=fp.get('top_separate', True),
            nubs_enabled=fp.get('nubs_enabled', False),
            nub_count=fp.get('nub_count', 4),
            nub_dia_mm=fp.get('nub_dia_mm', 3.0),
            nub_height_mm=fp.get('nub_height_mm', 2.0),
            nub_allowance_mm=fp.get('nub_allowance_mm', 0.2),
            plate_height_mm=fp.get('plate_height_mm', 1.0),
            bend_radius_mm=fp.get('bend_radius_mm', 0.0),
        )
        fname = f'{family}-{pitch}-{num_teeth}T.step'

        # ── Generate STEP ─────────────────────────────────────────────────────
        try:
            from exporters.step_exporter import generate_pulley_step
            step_bytes = generate_pulley_step(
                **{k: v for k, v in kw.items() if k not in ('plate_height_mm', 'bend_radius_mm')}
            )
        except ImportError:
            import subprocess
            root      = os.path.dirname(os.path.abspath(__file__))
            venv_py   = os.path.join(root, '.venv312', 'Scripts', 'python.exe')
            worker    = os.path.join(root, 'exporters', 'step_worker.py')
            result    = subprocess.run(
                [venv_py, worker, json.dumps(dict(kw, export_type='pulley'))],
                capture_output=True, cwd=root,
            )
            if result.returncode != 0:
                return jsonify({'ok': False, 'error': result.stderr.decode()}), 400
            step_bytes = result.stdout

        step_bytes = _rename_step_product(step_bytes, fname[:-5])

        # ── Upload to OnShape via translations API ────────────────────────────
        path  = f'/api/v6/translations/d/{doc_id}/w/{ws_id}'
        date  = datetime.now(_tz.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        nonce = _uid.uuid4().hex[:25]

        # OnShape HMAC-SHA256: sign with empty content-type for multipart uploads
        string_to_sign = '\n'.join(['post', nonce, date, '', path.lower(), '', ''])
        sig = _b64.b64encode(
            _hmac.new(sk.encode(), string_to_sign.encode(), _hs.sha256).digest()
        ).decode()

        headers = {
            'Date':          date,
            'On-Nonce':      nonce,
            'Authorization': f'On {ak}:HmacSHA256:{sig}',
            'Accept':        'application/json',
        }

        resp = _rq.post(
            server + path,
            files={'file': (fname, BytesIO(step_bytes), 'application/octet-stream')},
            headers=headers,
            timeout=30,
        )

        if resp.ok:
            tid = resp.json().get('id', '')
            return jsonify({'ok': True, 'translationId': tid, 'filename': fname})
        else:
            return jsonify({'ok': False,
                            'error': f'OnShape {resp.status_code}: {resp.text[:300]}'}), 400

    except KeyError as e:
        return jsonify({'ok': False, 'error': f'Missing field: {e}'}), 400
    except Exception as e:
        import traceback
        return jsonify({'ok': False, 'error': str(e)}), 500


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
        svg = _embed_svg(svg, request.args)
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
            flat_depth_mm = max(0.0, float(request.args.get('p2_hub_flat_depth', 0.0)))
            keyway_w_mm   = max(0.0, float(request.args.get('p2_hub_keyway_w',   0.0)))
            keyway_h_mm   = max(0.0, float(request.args.get('p2_hub_keyway_h',   0.0)))
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
            flat_depth_mm = max(0.0, float(request.args.get('hub_flat_depth', 0.0)))
            keyway_w_mm   = max(0.0, float(request.args.get('hub_keyway_w',   0.0)))
            keyway_h_mm   = max(0.0, float(request.args.get('hub_keyway_h',   0.0)))
            filename = f'{family}-{pitch}-{num_teeth}T.dxf'

        dxf = generate_dxf(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
            print_extra_mm=pr_ex,
            spoke_count=sp_cnt if sp_en else 0,
            spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub_od,
            rim_depth_mm=sp_rim, fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
            flat_depth_mm=flat_depth_mm, keyway_w_mm=keyway_w_mm, keyway_h_mm=keyway_h_mm,
        )
        dxf = _embed_dxf(dxf if isinstance(dxf, bytes) else dxf.encode(), request.args)
        _mirror_to_fusion(dxf, filename)
        _mirror_to_solidworks(dxf, filename)
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
    flat_depth_mm = max(0.0, float(args.get('hub_flat_depth', 0.0)))
    keyway_w_mm   = max(0.0, float(args.get('hub_keyway_w',   0.0)))
    keyway_h_mm   = max(0.0, float(args.get('hub_keyway_h',   0.0)))
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
        flat_depth_mm=flat_depth_mm,
        keyway_w_mm=keyway_w_mm,
        keyway_h_mm=keyway_h_mm,
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
    include_callouts = args.get('include_callouts', '0') == '1'
    flat_depth_mm = max(0.0, float(args.get('hub_flat_depth', 0.0)))
    keyway_w_mm   = max(0.0, float(args.get('hub_keyway_w',   0.0)))
    keyway_h_mm   = max(0.0, float(args.get('hub_keyway_h',   0.0)))
    return generate_svg(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
        print_extra_mm=pr_ex, clearance_preset=cl_preset, backlash_preset=bl_preset,
        spoke_count=sp_cnt if sp_en else 0,
        spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub_od, rim_depth_mm=sp_rim,
        fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
        include_data=include_data,
        include_callouts=include_callouts,
        flat_depth_mm=flat_depth_mm, keyway_w_mm=keyway_w_mm, keyway_h_mm=keyway_h_mm,
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
    include_callouts = args.get('include_callouts', '0') == '1'
    flat_depth_mm = max(0.0, float(args.get('p2_hub_flat_depth', 0.0)))
    keyway_w_mm   = max(0.0, float(args.get('p2_hub_keyway_w',   0.0)))
    keyway_h_mm   = max(0.0, float(args.get('p2_hub_keyway_h',   0.0)))
    return generate_svg(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
        print_extra_mm=pr_ex, clearance_preset=cl_preset, backlash_preset=bl_preset,
        spoke_count=sp_cnt if sp_en else 0,
        spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub_od, rim_depth_mm=sp_rim,
        fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
        include_data=include_data,
        include_callouts=include_callouts,
        flat_depth_mm=flat_depth_mm, keyway_w_mm=keyway_w_mm, keyway_h_mm=keyway_h_mm,
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

    flat1 = max(0.0, float(args.get('hub_flat_depth',    0.0)))
    kw1   = max(0.0, float(args.get('hub_keyway_w',      0.0)))
    kh1   = max(0.0, float(args.get('hub_keyway_h',      0.0)))
    flat2 = max(0.0, float(args.get('p2_hub_flat_depth', 0.0)))
    kw2   = max(0.0, float(args.get('p2_hub_keyway_w',   0.0)))
    kh2   = max(0.0, float(args.get('p2_hub_keyway_h',   0.0)))

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
        flat_depth_mm1=flat1, keyway_w_mm1=kw1, keyway_h_mm1=kh1,
        flat_depth_mm2=flat2, keyway_w_mm2=kw2, keyway_h_mm2=kh2,
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


def _cct_meta(args) -> dict:
    """Build the CCT metadata dict from request args."""
    return {'cct': dict(args), 'v': APP_VERSION, 'sv': CCT_SCHEMA_VERSION}


def _rename_step_product(step_bytes: bytes, product_name: str) -> bytes:
    """Replace the PRODUCT name(s) in a STEP file so Fusion 360 uses the right component name."""
    try:
        import re as _re_sp
        text = step_bytes.decode('utf-8', errors='replace')
        # STEP PRODUCT entity: PRODUCT('old_name','old_name','',(...))
        # Replace both the name and description fields (first two quoted args).
        safe = product_name.replace("'", " ")
        text = _re_sp.sub(
            r"PRODUCT\('[^']*','[^']*',",
            f"PRODUCT('{safe}','{safe}',",
            text,
        )
        return text.encode('utf-8')
    except Exception:
        return step_bytes


def _embed_step(step_bytes: bytes, args) -> bytes:
    """Inject CCT design params as a comment in the STEP header."""
    try:
        import re as _re2
        blob = json.dumps(_cct_meta(args), separators=(',', ':'))
        comment = f'/* CCT:{blob} */\n'
        text = step_bytes.decode('utf-8', errors='replace')
        # Insert after the HEADER ENDSEC line, before DATA
        text = _re2.sub(r'(ENDSEC;\s*\n)(DATA;)', rf'\1{comment}\2', text, count=1)
        return text.encode('utf-8')
    except Exception:
        return step_bytes


def _embed_dxf(dxf_bytes: bytes, args) -> bytes:
    """Store CCT design params as a group-code 999 comment before the DXF EOF marker."""
    try:
        blob    = json.dumps(_cct_meta(args), separators=(',', ':'))
        comment = f'999\nCCT:{blob}\n'.encode('utf-8')
        for eof_marker in (b'  0\r\nEOF\r\n', b'  0\nEOF\n', b'0\r\nEOF\r\n', b'0\nEOF\n'):
            if eof_marker in dxf_bytes:
                return dxf_bytes.replace(eof_marker, comment + eof_marker, 1)
        return dxf_bytes + comment
    except Exception:
        return dxf_bytes


def _embed_stl(stl_bytes: bytes, args) -> bytes:
    """Append CCT design params as a text trailer after the last STL triangle.

    Binary STL parsers stop after reading the declared triangle count, so the
    trailing bytes are silently ignored by all standard CAD tools and slicers.
    Read back with the same /* CCT:{...} */ regex used for STEP.
    """
    try:
        blob    = json.dumps(_cct_meta(args), separators=(',', ':'))
        trailer = f'\n/* CCT:{blob} */\n'.encode('utf-8')
        return stl_bytes + trailer
    except Exception:
        return stl_bytes


def _embed_svg(svg_str: str, args) -> str:
    """Inject CCT design params as an SVG <metadata> element."""
    try:
        import re as _re3
        blob = json.dumps(_cct_meta(args), separators=(',', ':'))
        meta_tag = f'<metadata><cct>{blob}</cct></metadata>'
        m = _re3.search(r'<svg\b[^>]*>', svg_str)
        if m:
            pos = m.end()
            return svg_str[:pos] + '\n' + meta_tag + svg_str[pos:]
    except Exception:
        pass
    return svg_str


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

    belt_height   = max(1.0, float(args.get('belt_height', 10.0)))
    clearance_h   = max(0.0, float(args.get('clearance_height', 0.0)))
    belt_height   = belt_height + clearance_h
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
                    flat_depth_mm=fd, keyway_w_mm=kw_w, keyway_h_mm=kw_h,
                )
                if fp.get('nubs_enabled') and fp.get('flange_3dprint') and fp.get('top_separate'):
                    _socket_meshes = build_socket_meshes(
                        fp, family, pitch, num_teeth, bore_mm, belt_height,
                        clearance_mm=cl_mm, print_extra_mm=pr_ex,
                        hub_od_mm=hub_od, spokes_enabled=sp_en, spoke_hub_od_mm=sp_hub,
                        rim_depth_mm=sp_rim,
                    ) or None
            _fl_enabled = request.args.get('flange_enabled') == '1'
            _fl_h = fp.get('flange_height_mm', 1.5) if _fl_enabled and fp else 1.5
            stl = generate_pulley_stl_preview(
                family, pitch, num_teeth, bore_mm, belt_height,
                cl_mm, bl_mm, pr_ex, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
                spoke_count=sp_count, spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
                fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb, rim_depth_mm=sp_rim,
                spoke_height_mm=sp_h if sp_en else 0.0,
                flange_enabled=_fl_enabled, flange_height_mm=_fl_h,
                socket_meshes=_socket_meshes,
            )
            if _fl_meshes:
                import io as _io
                pulley_mesh = trimesh.load(_io.BytesIO(stl), file_type='stl')
                # generate_pulley_stl_preview centres the pulley at origin, so its
                # Z_min now represents what was Z=0 (bottom face) before centering.
                # Shift all flange meshes (built in the uncentred Z=0=bottom frame)
                # by that same offset so they align with the centred pulley.
                z_bottom = float(pulley_mesh.bounds[0][2])
                for m in _fl_meshes:
                    m.apply_translation([0.0, 0.0, z_bottom])
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

        # 3D-print flanges: parse flange params first so we can pass flange info to STL generator
        _fl_enabled = request.args.get(f'{pfx}flange_enabled') == '1'
        fp = _parse_flange_params(request.args, pfx) if _fl_enabled else {}

        _fl_3dp   = _fl_enabled and fp.get('flange_3dprint', False)
        _fl_metal = _fl_enabled and not fp.get('flange_3dprint', False)
        # Hub raise amount: 3D-print uses flange rim height; metal uses plate thickness
        _raise_h  = (fp.get('flange_height_mm', 1.5) if _fl_3dp
                     else fp.get('plate_height_mm', 1.0) if _fl_metal
                     else 0.0)
        stl = generate_pulley_stl(
            family, pitch, num_teeth, bore_mm, belt_height,
            cl_mm, bl_mm, pr_ex, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
            spoke_count=sp_count, spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
            fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb, rim_depth_mm=sp_rim,
            spoke_height_mm=sp_h if sp_en else 0.0,
            flange_enabled=_fl_enabled,
            flange_height_mm=_raise_h,
        )
        if _fl_metal:
            import trimesh, io as _io
            from exporters.flange_exporter import generate_metal_flange_stl
            pulley_mesh = trimesh.load(_io.BytesIO(stl), file_type='stl')
            flange_bytes = generate_metal_flange_stl(
                family=family, pitch=pitch, num_teeth=num_teeth,
                bore_mm=bore_mm, belt_height_mm=belt_height,
                clearance_mm=cl_mm, print_extra_mm=pr_ex,
                flange_angle_deg=fp['flange_angle_deg'],
                rim_radius_mm=fp['rim_radius_mm'],
                plate_height_mm=fp['plate_height_mm'],
                bend_radius_mm=fp.get('bend_radius_mm', 0.0),
                which='both',
                hub_od_mm=hub_od, spokes_enabled=sp_en,
                spoke_hub_od_mm=sp_hub, rim_depth_mm=sp_rim,
                flat_depth_mm=fd, keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            )
            flange_mesh = trimesh.load(_io.BytesIO(flange_bytes), file_type='stl')
            stl = trimesh.util.concatenate([pulley_mesh, flange_mesh]).export(file_type='stl')
        elif _fl_enabled and fp.get('flange_3dprint'):
            import trimesh, io as _io
            from exporters.flange_exporter import (
                generate_3dprint_flange_stl, build_socket_meshes,
            )
            eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od

            _flange_kw = dict(
                family=family, pitch=pitch, num_teeth=num_teeth,
                bore_mm=bore_mm, belt_height_mm=belt_height,
                clearance_mm=cl_mm, print_extra_mm=pr_ex,
                flange_angle_deg=fp['flange_angle_deg'],
                rim_radius_mm=fp['rim_radius_mm'],
                flange_height_mm=fp['flange_height_mm'],
                hub_od_mm=eff_hub_od, spokes_enabled=sp_en,
                spoke_hub_od_mm=sp_hub, rim_depth_mm=sp_rim,
                flat_depth_mm=fd, keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            )

            if not fp.get('top_separate'):
                # Integrated mode: reuse the already-generated uncentered STL so
                # flanges can be placed at natural z=0 / z=belt_height positions —
                # same approach as the Assembly STL route, which is known to work.
                pulley_mesh = trimesh.load(_io.BytesIO(stl), file_type='stl')
                bot_mesh = trimesh.load(_io.BytesIO(
                    generate_3dprint_flange_stl(which='bottom', **_flange_kw)
                ), file_type='stl')
                top_mesh = trimesh.load(_io.BytesIO(
                    generate_3dprint_flange_stl(which='top', nubs_enabled=False, **_flange_kw)
                ), file_type='stl')
                stl = trimesh.util.concatenate([pulley_mesh, bot_mesh, top_mesh]).export(file_type='stl')
            else:
                # Separate top flange: use preview (centered) so nub sockets can
                # be cut via boolean on the live trimesh mesh before export.
                sockets = build_socket_meshes(
                    fp, family, pitch, num_teeth, bore_mm, belt_height,
                    clearance_mm=cl_mm, print_extra_mm=pr_ex,
                    hub_od_mm=eff_hub_od, spokes_enabled=sp_en,
                    spoke_hub_od_mm=sp_hub, rim_depth_mm=sp_rim,
                ) if fp.get('nubs_enabled') else []

                stl_preview = generate_pulley_stl_preview(
                    family, pitch, num_teeth, bore_mm, belt_height,
                    cl_mm, bl_mm, pr_ex, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
                    spoke_count=sp_count, spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
                    fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb, rim_depth_mm=sp_rim,
                    spoke_height_mm=sp_h if sp_en else 0.0,
                    flange_enabled=_fl_3dp, flange_height_mm=fp.get('flange_height_mm', 1.5),
                    socket_meshes=sockets or None,
                )
                pulley_mesh = trimesh.load(_io.BytesIO(stl_preview), file_type='stl')
                z_bottom = float(pulley_mesh.bounds[0][2])

                bot_mesh = trimesh.load(_io.BytesIO(
                    generate_3dprint_flange_stl(which='bottom', **_flange_kw)
                ), file_type='stl')
                bot_mesh.apply_translation([0.0, 0.0, z_bottom])
                stl = trimesh.util.concatenate([pulley_mesh, bot_mesh]).export(file_type='stl')

        fl_sfx = '+flange' if _fl_enabled else ''
        fname = f'{family}-{pitch}-{num_teeth}T{suffix}{fl_sfx}.stl'
        stl = _embed_stl(stl if isinstance(stl, bytes) else bytes(stl), request.args)
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

        # Flange params: only parsed when the user has flanges enabled
        _fl_enabled = request.args.get(f'{pfx}flange_enabled') == '1'
        fp = _parse_flange_params(request.args, pfx) if _fl_enabled else {}

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
            flange_enabled       = _fl_enabled,
            flange_3dprint       = fp.get('flange_3dprint', True),
            flange_angle_deg     = fp.get('flange_angle_deg', 15.0),
            flange_rim_radius_mm = fp.get('rim_radius_mm', 3.0),
            flange_height_mm     = fp.get('flange_height_mm', 1.5),
            flange_top_separate  = fp.get('top_separate', True),
            nubs_enabled         = fp.get('nubs_enabled', False),
            nub_count            = fp.get('nub_count', 4),
            nub_dia_mm           = fp.get('nub_dia_mm', 3.0),
            nub_height_mm        = fp.get('nub_height_mm', 2.0),
            nub_allowance_mm     = fp.get('nub_allowance_mm', 0.2),
            # Extra flange params needed for metal flanges in assembly export
            plate_height_mm      = fp.get('plate_height_mm', 1.0),
            bend_radius_mm       = fp.get('bend_radius_mm', 0.0),
        )

        # When flanges are enabled use the assembly exporter (multipart STEP with
        # pulley body + separate flange parts in the same file).
        _use_assembly = _fl_enabled
        p2_sfx = '-P2' if pulley == '2' else ''
        fl_sfx = '+flanges' if _fl_enabled else ''
        fname  = f'{family}-{pitch}-{num_teeth}T{p2_sfx}{fl_sfx}.step'
        try:
            if _use_assembly:
                from exporters.step_exporter import generate_pulley_assembly_step
                step_bytes = generate_pulley_assembly_step(kw)
            else:
                from exporters.step_exporter import generate_pulley_step
                step_bytes = generate_pulley_step(**{k: v for k, v in kw.items()
                                                     if k not in ('plate_height_mm', 'bend_radius_mm')})
        except ImportError as _ie:
            import subprocess, sys, traceback as _tb
            import logging as _log
            _log.getLogger(__name__).error('STEP import failed: %s\n%s', _ie, _tb.format_exc())
            if getattr(sys, 'frozen', False):
                return f'STEP import error in bundle: {_ie}', 400
            root    = os.path.dirname(os.path.abspath(__file__))
            venv_py = os.path.join(root, '.venv312', 'Scripts', 'python.exe')
            worker  = os.path.join(root, 'exporters', 'step_worker.py')
            worker_kw = dict(kw, export_type='assembly' if _use_assembly else 'pulley')
            result  = subprocess.run(
                [venv_py, worker, json.dumps(worker_kw)],
                capture_output=True, cwd=root,
            )
            if result.returncode != 0:
                return f'STEP error: {result.stderr.decode()}', 400
            step_bytes = result.stdout

        step_bytes = _rename_step_product(step_bytes, fname[:-5])
        step_bytes = _embed_step(step_bytes, request.args)
        _mirror_to_fusion(step_bytes, fname)
        _mirror_to_solidworks(step_bytes, fname)
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
        _mirror_to_fusion(step_bytes, filename)
        _mirror_to_solidworks(step_bytes, filename)
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


@app.route('/download/all-step')
def download_all_step():
    """Multipart STEP with all pulleys and their flanges in one file.
    In dual mode (dual=true) includes P1 + P2; otherwise just P1.
    """
    try:
        import json as _json
        dual = request.args.get('dual') == 'true'

        def _build_kw(pfx):
            family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
                _parse_stl_params(request.args, '2' if pfx == 'p2_' else '1')
            hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(request.args, pfx)
            sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_c, sp_h, sp_split = \
                _parse_spoke_params(request.args, pfx)
            eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od
            _fl_en = request.args.get(f'{pfx}flange_enabled') == '1'
            fp = _parse_flange_params(request.args, pfx) if _fl_en else {}
            return dict(
                family=family, pitch=pitch, num_teeth=num_teeth,
                bore_mm=bore_mm, belt_height_mm=belt_height,
                clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex,
                hub_od_mm=eff_hub_od, hub_height_mm=hub_h,
                screw_dia_mm=sd, screw_count=sc,
                captured_nut=cn, flat_depth_mm=fd,
                keyway_w_mm=kw_w, keyway_h_mm=kw_h,
                spoke_count=sp_c if sp_en else 0,
                spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
                rim_depth_mm=sp_rim, fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
                spoke_height_mm=sp_h,
                flange_enabled       = _fl_en,
                flange_3dprint       = fp.get('flange_3dprint', True),
                flange_angle_deg     = fp.get('flange_angle_deg', 15.0),
                flange_rim_radius_mm = fp.get('rim_radius_mm', 3.0),
                flange_height_mm     = fp.get('flange_height_mm', 1.5),
                flange_top_separate  = fp.get('top_separate', True),
                nubs_enabled         = fp.get('nubs_enabled', False),
                nub_count            = fp.get('nub_count', 4),
                nub_dia_mm           = fp.get('nub_dia_mm', 3.0),
                nub_height_mm        = fp.get('nub_height_mm', 2.0),
                nub_allowance_mm     = fp.get('nub_allowance_mm', 0.2),
                plate_height_mm      = fp.get('plate_height_mm', 1.0),
                bend_radius_mm       = fp.get('bend_radius_mm', 0.0),
            )

        kw1 = _build_kw('')
        kw2 = _build_kw('p2_') if dual else None

        # Belt uses raw belt_height (no clearance added — _parse_stl_params adds
        # clearance for pulleys, but the belt geometry is independent of clearance).
        belt_kw = None
        if dual:
            key   = _resolve_key(kw1['family'], kw1['pitch'])
            spec  = PULLEY_SPECS.get(key, {}) if key else {}
            pitch_mm   = spec.get('pitch', 5.0)
            _default_c = (kw1['num_teeth'] + kw2['num_teeth']) * pitch_mm / (2.0 * math.pi)
            center_dist = float(request.args.get('center_distance', _default_c))
            raw_belt_h  = max(1.0, float(request.args.get('belt_height', 10.0)))
            belt_kw = dict(
                family         = kw1['family'],
                pitch          = kw1['pitch'],
                num_teeth_left = kw1['num_teeth'],
                num_teeth_right= kw2['num_teeth'],
                center_dist_mm = center_dist,
                belt_height_mm = raw_belt_h,
            )

        _t1 = kw1['num_teeth']
        _fname_stem = (f'{kw1["family"]}-{kw1["pitch"]}-{_t1}T+{kw2["num_teeth"]}T-all'
                       if kw2 else f'{kw1["family"]}-{kw1["pitch"]}-{_t1}T-all')

        try:
            from exporters.step_exporter import generate_all_parts_step
            step_bytes = generate_all_parts_step(kw1, kw2, belt_kw)
        except ImportError as _ie:
            import subprocess, sys
            if getattr(sys, 'frozen', False):
                return f'STEP import error in bundle: {_ie}', 400
            root    = os.path.dirname(os.path.abspath(__file__))
            venv_py = os.path.join(root, '.venv312', 'Scripts', 'python.exe')
            worker  = os.path.join(root, 'exporters', 'step_worker.py')
            worker_kw = dict(kw1, export_type='all')
            if kw2:
                worker_kw['kw2'] = kw2
            if belt_kw:
                worker_kw['belt_kw'] = belt_kw
            result = subprocess.run(
                [venv_py, worker, _json.dumps(worker_kw)],
                capture_output=True, cwd=root,
            )
            if result.returncode != 0:
                return f'STEP error: {result.stderr.decode()}', 400
            step_bytes = result.stdout

        fname = _fname_stem + '.step'
        # All-parts STEP is always an assembly — don't overwrite individual part names.
        _mirror_to_fusion(step_bytes, fname)
        _mirror_to_solidworks(step_bytes, fname)
        return Response(step_bytes, mimetype='application/step',
                        headers={'Content-Disposition': f'attachment; filename="{fname}"'})
    except Exception as exc:
        import traceback
        return Response(f'All-parts STEP failed:\n{traceback.format_exc()}',
                        status=500, mimetype='text/plain')


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
        # Print support rib params (3D-print integrated-top only)
        supports_enabled     = args.get(f'{prefix}flange_supports_enabled', '0') == '1',
        support_nozzle_dia   = max(0.1, _safe_float(args.get(f'{prefix}flange_support_nozzle_dia'),  0.4)),
        support_max_spacing  = max(1.0, _safe_float(args.get(f'{prefix}flange_support_max_spacing'), 10.0)),
        support_air_gap      = max(0.0, _safe_float(args.get(f'{prefix}flange_support_air_gap'),     0.2)),
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
        belt_h    = belt_h + max(0.0, float(args.get('clearance_height', 0.0)))
        cl_mm     = _get_preset_value(spec, 'clearances',
                                      args.get('clearance_preset', 'STANDARD'),
                                      args.get('clearance_custom', 0.0))
        pe_mm     = float(args.get('print_extra', 0.0))

        hub_od         = max(0.0, float(args.get('hub_od', 0.0)))
        spokes_enabled = args.get('spokes_enabled', '0') == '1'
        spoke_hub_od   = max(0.0, float(args.get('spokes_hub_od', 0.0)))
        spoke_rim_depth = max(0.0, float(args.get('spokes_rim_depth', 0.0)))

        fp    = _parse_flange_params(args)
        which = args.get('flange_which', 'top')   # 'top' or 'bottom' (or 'both' for metal)

        flat_d = max(0.0, float(args.get('flat_depth', 0.0)))
        kw_w   = max(0.0, float(args.get('keyway_w', 0.0)))
        kw_h   = max(0.0, float(args.get('keyway_h', 0.0)))

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
                nubs_enabled=fp.get('nubs_enabled', False) and fp.get('top_separate', False),
                nub_count=fp.get('nub_count', 4),
                nub_dia_mm=fp.get('nub_dia_mm', 3.0),
                nub_height_mm=fp.get('nub_height_mm', 2.0),
                nub_allowance_mm=fp.get('nub_allowance_mm', 0.2),
                flat_depth_mm=flat_d, keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            )
            suffix    = '-upper-flange' if which == 'top' else '-lower-flange'
        else:
            stl_bytes = generate_metal_flange_stl(
                family, pitch, num_teeth, bore_mm, belt_h,
                clearance_mm=cl_mm, print_extra_mm=pe_mm,
                flange_angle_deg=fp['flange_angle_deg'],
                rim_radius_mm=fp['rim_radius_mm'],
                plate_height_mm=fp['plate_height_mm'],
                bend_radius_mm=fp['bend_radius_mm'],
                which='both',
                hub_od_mm=hub_od,
                spokes_enabled=spokes_enabled,
                spoke_hub_od_mm=spoke_hub_od,
                rim_depth_mm=spoke_rim_depth,
                flat_depth_mm=flat_d, keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            )
            suffix = '-flanges'

        type_tag  = '3DP' if fp['flange_3dprint'] else 'Metal'
        filename  = f'{family}{pitch}-{num_teeth}T-{type_tag}{suffix}.stl'

        stl_bytes = _embed_stl(stl_bytes, request.args)
        return Response(
            stl_bytes,
            mimetype='model/stl',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        import traceback
        return Response(f'Flange STL export failed:\n{traceback.format_exc()}',
                        status=500, mimetype='text/plain')


@app.route('/download/flange-step')
def download_flange_step():
    """Return STEP of a single flange plate (3D-print top with nubs, or metal top/bottom).

    The 3D-print bottom flange is integrated into the pulley STEP and is not
    available as a standalone download from this route.
    """
    try:
        import json as _json, os as _os
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
        belt_h    = belt_h + max(0.0, float(args.get('clearance_height', 0.0)))
        cl_mm     = _get_preset_value(spec, 'clearances',
                                      args.get('clearance_preset', 'STANDARD'),
                                      args.get('clearance_custom', 0.0))
        pe_mm     = float(args.get('print_extra', 0.0))

        hub_od          = max(0.0, float(args.get('hub_od', 0.0)))
        flat_depth_mm   = max(0.0, float(args.get('flat_depth', 0.0)))
        keyway_w_mm     = max(0.0, float(args.get('keyway_w', 0.0)))
        keyway_h_mm     = max(0.0, float(args.get('keyway_h', 0.0)))
        spokes_enabled  = args.get('spokes_enabled', '0') == '1'
        spoke_hub_od    = max(0.0, float(args.get('spokes_hub_od', 0.0)))
        spoke_rim_depth = max(0.0, float(args.get('spokes_rim_depth', 0.0)))

        fp    = _parse_flange_params(args)
        which = args.get('flange_which', 'top')

        kw = dict(
            family           = family,
            pitch            = pitch,
            num_teeth        = num_teeth,
            bore_mm          = bore_mm,
            belt_height_mm   = belt_h,
            clearance_mm     = cl_mm,
            print_extra_mm   = pe_mm,
            flange_3dprint   = fp['flange_3dprint'],
            flange_angle_deg = fp['flange_angle_deg'],
            rim_radius_mm    = fp['rim_radius_mm'],
            flange_height_mm = fp['flange_height_mm'],
            plate_height_mm  = fp['plate_height_mm'],
            bend_radius_mm   = fp['bend_radius_mm'],
            which            = which,
            hub_od_mm        = hub_od,
            spokes_enabled   = spokes_enabled,
            spoke_hub_od_mm  = spoke_hub_od,
            rim_depth_mm     = spoke_rim_depth,
            nubs_enabled     = fp['nubs_enabled'],
            nub_count        = fp['nub_count'],
            nub_dia_mm       = fp['nub_dia_mm'],
            nub_height_mm    = fp['nub_height_mm'],
            nub_allowance_mm = fp['nub_allowance_mm'],
            flat_depth_mm    = flat_depth_mm,
            keyway_w_mm      = keyway_w_mm,
            keyway_h_mm      = keyway_h_mm,
        )

        try:
            from exporters.step_exporter import generate_flange_step
            step_bytes = generate_flange_step(**kw)
        except ImportError:
            import subprocess, sys
            root    = _os.path.dirname(_os.path.abspath(__file__))
            venv_py = _os.path.join(root, '.venv312', 'Scripts', 'python.exe')
            worker  = _os.path.join(root, 'exporters', 'step_worker.py')
            worker_kw = dict(kw, export_type='flange')
            result  = subprocess.run(
                [venv_py, worker, _json.dumps(worker_kw)],
                capture_output=True, cwd=root,
            )
            if result.returncode != 0:
                return f'Flange STEP error: {result.stderr.decode()}', 400
            step_bytes = result.stdout

        suffix   = '-upper-flange' if which == 'top' else '-lower-flange'
        type_tag = '3DP' if fp['flange_3dprint'] else 'Metal'
        filename = f'{family}{pitch}-{num_teeth}T-{type_tag}{suffix}.step'
        step_bytes = _rename_step_product(step_bytes, filename[:-5])
        _mirror_to_fusion(step_bytes, filename)
        _mirror_to_solidworks(step_bytes, filename)
        return Response(step_bytes, mimetype='application/step',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except Exception as e:
        import traceback
        return Response(f'Flange STEP export failed:\n{traceback.format_exc()}',
                        status=500, mimetype='text/plain')


@app.route('/download/flange-assembly')
def download_flange_assembly():
    """Return an assembly STL: pulley body + bottom flange + integrated top flange + support ribs.

    Only available when the top flange is integrated (flange_top_separate=0) and
    the 3D-print mode is selected. The pulley body is generated fresh via
    generate_pulley_stl(); both flanges and any support ribs are concatenated
    (no boolean union — slicer handles overlapping).
    """
    try:
        import trimesh as _trimesh
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
        belt_h    = belt_h + max(0.0, float(args.get('clearance_height', 0.0)))
        cl_mm     = _get_preset_value(spec, 'clearances',
                                      args.get('clearance_preset', 'STANDARD'),
                                      args.get('clearance_custom', 0.0))
        bl_mm     = _get_preset_value(spec, 'backlash',
                                      args.get('backlash_preset', 'STANDARD'),
                                      args.get('backlash_custom', 0.0))
        pe_mm     = float(args.get('print_extra', 0.0))

        hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(args)
        sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, _ = _parse_spoke_params(args)

        fp = _parse_flange_params(args)

        # Pulley body mesh
        pulley_bytes = generate_pulley_stl(
            family, pitch, num_teeth, bore_mm, belt_h,
            cl_mm, bl_mm, pe_mm, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
            spoke_count=sp_cnt if sp_en else 0,
            spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
            fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
            rim_depth_mm=sp_rim,
            spoke_height_mm=sp_h if sp_en else 0.0,
        )

        import io as _io
        pulley_mesh = _trimesh.load(_io.BytesIO(pulley_bytes), file_type='stl')

        # Flange meshes (bottom + top)
        from exporters.flange_exporter import (
            generate_3dprint_flange_stl, build_support_ribs,
        )
        eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od

        flange_kw = dict(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, belt_height_mm=belt_h,
            clearance_mm=cl_mm, print_extra_mm=pe_mm,
            flange_angle_deg=fp['flange_angle_deg'],
            rim_radius_mm=fp['rim_radius_mm'],
            flange_height_mm=fp['flange_height_mm'],
            hub_od_mm=eff_hub_od,
            spokes_enabled=sp_en,
            spoke_hub_od_mm=sp_hub,
            rim_depth_mm=sp_rim,
        )

        bot_bytes = generate_3dprint_flange_stl(which='bottom', **flange_kw)
        top_bytes = generate_3dprint_flange_stl(which='top',    **flange_kw)

        bot_mesh = _trimesh.load(_io.BytesIO(bot_bytes), file_type='stl')
        top_mesh = _trimesh.load(_io.BytesIO(top_bytes), file_type='stl')

        # Support ribs
        rib_meshes = build_support_ribs(
            fp, family, pitch, num_teeth, bore_mm, belt_h,
            clearance_mm=cl_mm, print_extra_mm=pe_mm,
        )

        all_meshes = [pulley_mesh, bot_mesh, top_mesh] + rib_meshes
        combined   = _trimesh.util.concatenate(all_meshes)
        stl_bytes  = combined.export(file_type='stl')

        filename = f'{family}{pitch}-{num_teeth}T-Assembly.stl'
        stl_bytes = _embed_stl(stl_bytes, request.args)
        return Response(stl_bytes, mimetype='model/stl',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except Exception as e:
        import traceback
        return Response(f'Assembly STL export failed:\n{traceback.format_exc()}',
                        status=500, mimetype='text/plain')


def _create_github_issue(report_label, timestamp, label_seeing, label_should,
                         seeing, should_see, email, state, report_type):
    """Create a GitHub issue in the feedback repo. Silently skips if PAT not set."""
    pat  = os.environ.get('FEEDBACK_GITHUB_PAT', '').strip()
    repo = os.environ.get('FEEDBACK_GITHUB_REPO', '').strip()  # e.g. xootme/cct-feedback
    if not pat or not repo:
        return
    try:
        import urllib.request, urllib.error
        state_json = json.dumps(state, indent=2)
        params_summary = (
            f"**Family:** {state.get('family','?')}  "
            f"**Pitch:** {state.get('pitch','?')}  "
            f"**Teeth:** {state.get('teeth','?')}  "
            f"**Bore:** {state.get('bore','?')}"
        )
        body = (
            f"**Type:** {report_label}\n"
            f"**Submitted:** {timestamp}\n"
            f"**App Version:** {state.get('app_version', APP_VERSION)}  "
            f"**Build:** {state.get('build_time', BUILD_TIME)}\n\n"
            f"---\n\n"
            f"**{label_seeing}:**\n{seeing or '_(not provided)_'}\n\n"
            f"**{label_should}:**\n{should_see or '_(not provided)_'}\n\n"
            f"**Contact email:** {email or '_(not provided)_'}\n\n"
            f"---\n\n"
            f"**Parameters:** {params_summary}\n\n"
            f"<details><summary>Full app state</summary>\n\n"
            f"```json\n{state_json}\n```\n\n</details>\n"
        )
        title = f"[{report_label}] {(seeing or should_see or 'No description')[:80]}"
        label = 'feature-request' if report_type == 'feature' else 'bug'
        payload = json.dumps({'title': title, 'body': body, 'labels': [label]}).encode()
        req = urllib.request.Request(
            f'https://api.github.com/repos/{repo}/issues',
            data=payload,
            headers={
                'Authorization': f'Bearer {pat}',
                'Accept':        'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'Content-Type':  'application/json',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        return resp.get('html_url')
    except Exception:
        pass  # GitHub failure must never break the log write


def _send_report_email(report_label, timestamp, label_seeing, label_should,
                       seeing, should_see, email, state):
    """Fire-and-forget SendGrid notification. Silently skips if key not set."""
    api_key = os.environ.get('SENDGRID_API_KEY', '').strip()
    if not api_key:
        return
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        state_json = json.dumps(state, indent=2)
        body = (
            f'{report_label} — {timestamp}\n'
            f'App Version: {APP_VERSION}   Build: {BUILD_TIME}\n\n'
            f'{label_seeing}:\n  {seeing or "(not provided)"}\n\n'
            f'{label_should}:\n  {should_see or "(not provided)"}\n\n'
            f'Contact email:\n  {email or "(not provided)"}\n\n'
            f'App state:\n{state_json}\n'
        )
        message = Mail(
            from_email='noreply@cheapcadtools.com',
            to_emails='info@cheapcadtools.com',
            subject=f'[Pulley Generator] {report_label}',
            plain_text_content=body,
        )
        SendGridAPIClient(api_key).send(message)
    except Exception:
        pass  # email failure must never break the log write


@app.route('/api/report-bug', methods=['POST'])
def api_report_bug():
    """Save a bug report to logs/bug_reports.log and email a notification."""
    try:
        data = request.get_json(force=True) or {}
        seeing       = str(data.get('seeing',        '')).strip()
        should_see   = str(data.get('should_see',    '')).strip()
        error_msg    = str(data.get('error_message', '')).strip()
        user_comment = str(data.get('user_comment',  '')).strip()
        email        = str(data.get('email',         '')).strip()
        state        = data.get('state', {})          # dict of current app params
        report_type  = str(data.get('report_type', 'bug')).strip()

        # Desktop app: no GitHub PAT configured locally — forward to production server
        _forward_failed = False
        if not os.environ.get('FEEDBACK_GITHUB_PAT') and os.environ.get('PULLEY_BASE_DIR'):
            try:
                payload = json.dumps(data).encode()
                req = urllib.request.Request(
                    'https://cheapcadtools.com/api/report-bug',
                    data=payload,
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return Response(resp.read(), status=resp.status, mimetype='application/json')
            except Exception:
                _forward_failed = True  # fall through to local save, then warn the user

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
        )
        if error_msg:
            entry += f'Error message:\n  {error_msg}\n\n'
        if user_comment:
            entry += f'User comment:\n  {user_comment}\n\n'
        entry += (
            f'Contact email:\n  {email or "(not provided)"}\n\n'
            f'App state:\n{json.dumps(state, indent=2)}\n'
        )
        with open(_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(entry)

        issue_url = _create_github_issue(report_label, timestamp, label_seeing, label_should,
                                         seeing, should_see, email, state, report_type)
        if issue_url:
            _save_bug_issue_url(timestamp, issue_url)

        _send_report_email(report_label, timestamp, label_seeing, label_should,
                           seeing, should_see, email, state)

        if _forward_failed:
            ts_safe = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_filename = f'bug_report_{ts_safe}.txt'
            report_path = os.path.join(_LOG_DIR, report_filename)
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(entry.strip())
            return jsonify({
                'ok': True,
                'warning': (
                    'Your report was saved locally but could not be sent to CheapCADTools '
                    '(no internet connection or server unreachable).\n\n'
                    'To submit it manually, email the report file to info@cheapcadtools.com, '
                    'or reconnect and submit again.'
                ),
                'report_filename': report_filename,
            })
        return jsonify({'ok': True, 'issue_url': issue_url})
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/bug-report-file/<filename>')
def api_bug_report_file(filename):
    """Serve a locally-saved bug report text file for download."""
    safe = os.path.basename(filename)
    if not safe.startswith('bug_report_') or not safe.endswith('.txt'):
        return jsonify({'error': 'Not found'}), 404
    path = os.path.join(_LOG_DIR, safe)
    if not os.path.isfile(path):
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, as_attachment=True, download_name=safe, mimetype='text/plain')


# ── Provision API ─────────────────────────────────────────────────────────────
# ── Desktop app licence system ────────────────────────────────────────────────
# Licence keys are sold via WooCommerce (cheapcadtools.com/shop).
# On order completion WooCommerce calls /api/desktop/licence-import (Bearer PROVISION_SECRET).
# The desktop app calls /api/desktop/activate on first run, then /api/desktop/verify
# every DESKTOP_VERIFY_DAYS days. Offline grace: DESKTOP_GRACE_DAYS days.

_DESKTOP_LICENCES_FILE  = os.path.join(_LOG_DIR, 'desktop_licences.json')
_desktop_licences_lock  = threading.Lock()
_DESKTOP_VERIFY_DAYS    = 7
_DESKTOP_GRACE_DAYS     = 14   # allow offline this long before hard-blocking
_WC_WEBHOOK_SECRET      = os.environ.get('WC_WEBHOOK_SECRET', '')
_LMFWC_SITE_URL         = os.environ.get('LMFWC_SITE_URL', 'https://cheapcadtools.com')
_LMFWC_CONSUMER_KEY     = os.environ.get('LMFWC_CONSUMER_KEY', '')
_LMFWC_CONSUMER_SECRET  = os.environ.get('LMFWC_CONSUMER_SECRET', '')


def _load_desktop_licences():
    try:
        if os.path.exists(_DESKTOP_LICENCES_FILE):
            with open(_DESKTOP_LICENCES_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_desktop_licences(data):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_DESKTOP_LICENCES_FILE, 'w') as f:
        json.dump(data, f, indent=2)


@app.route('/api/desktop/licence-import', methods=['POST'])
def api_desktop_licence_import():
    """Admin/webhook: register a new licence key. Called by WooCommerce on order completion."""
    auth = request.headers.get('Authorization', '')
    if not _PROVISION_SECRET or auth != f'Bearer {_PROVISION_SECRET}':
        return jsonify({'error': 'unauthorized'}), 401
    data       = request.get_json(silent=True) or {}
    key        = data.get('licence_key', '').strip().upper()
    email      = data.get('email', '').lower().strip()
    order_id   = str(data.get('order_id', ''))
    valid_years = max(1, int(data.get('valid_years', 1)))
    if not key:
        return jsonify({'error': 'licence_key required'}), 400
    valid_until = (datetime.now() + timedelta(days=365 * valid_years)).isoformat()
    with _desktop_licences_lock:
        licences = _load_desktop_licences()
        licences[key] = {
            'email':           email,
            'order_id':        order_id,
            'created_at':      datetime.now().isoformat(),
            'valid_until':     valid_until,
            'activations':     [],
            'max_activations': 2,
        }
        _save_desktop_licences(licences)
    return jsonify({'ok': True, 'valid_until': valid_until})


@app.route('/api/desktop/licence-import-wc', methods=['POST'])
def api_desktop_licence_import_wc():
    """WooCommerce 'Order updated' webhook — imports LMFWC licence keys for completed orders."""
    import hmac as _hmac_mod
    import hashlib as _hash_mod
    import base64 as _b64_mod
    import urllib.request as _ur

    # Validate WooCommerce HMAC-SHA256 signature
    sig = request.headers.get('X-WC-Webhook-Signature', '')
    raw = request.get_data()
    if _WC_WEBHOOK_SECRET:
        expected = _b64_mod.b64encode(
            _hmac_mod.new(_WC_WEBHOOK_SECRET.encode(), raw, _hash_mod.sha256).digest()
        ).decode()
        if not _hmac_mod.compare_digest(expected, sig):
            return jsonify({'error': 'invalid signature'}), 401

    order = request.get_json(silent=True) or {}

    # Only process completed orders
    if order.get('status') != 'completed':
        return jsonify({'ok': True, 'skipped': 'not completed'}), 200

    order_id = order.get('id')
    email    = (order.get('billing', {}).get('email', '') or '').lower().strip()
    if not order_id:
        return jsonify({'error': 'missing order id'}), 400

    # Fetch licence keys from LMFWC REST API
    creds = _b64_mod.b64encode(
        f'{_LMFWC_CONSUMER_KEY}:{_LMFWC_CONSUMER_SECRET}'.encode()
    ).decode()
    lmfwc_url = f'{_LMFWC_SITE_URL}/wp-json/lmfwc/v2/licenses?order_id={order_id}'
    req = _ur.Request(lmfwc_url, headers={'Authorization': f'Basic {creds}'})
    try:
        with _ur.urlopen(req, timeout=10) as resp:
            lmfwc_data = json.loads(resp.read())
    except Exception as exc:
        return jsonify({'error': f'LMFWC API error: {exc}'}), 502

    licenses = lmfwc_data.get('data', [])
    if not licenses:
        return jsonify({'error': 'no licences found for this order'}), 404

    imported = []
    with _desktop_licences_lock:
        db = _load_desktop_licences()
        for lic in licenses:
            key = (lic.get('licenseKey') or '').strip().upper()
            if not key:
                continue
            expires = lic.get('expiresAt')
            valid_until = expires if expires else (datetime.now() + timedelta(days=365)).isoformat()
            db[key] = {
                'email':           email,
                'order_id':        str(order_id),
                'created_at':      datetime.now().isoformat(),
                'valid_until':     valid_until,
                'activations':     [],
                'max_activations': 2,
            }
            imported.append(key)
        _save_desktop_licences(db)

    return jsonify({'ok': True, 'imported': len(imported)})


@app.route('/api/desktop/activate', methods=['POST'])
def api_desktop_activate():
    """Called by desktop app on first run: bind a licence key to this machine."""
    data       = request.get_json(silent=True) or {}
    key        = data.get('licence_key', '').strip().upper()
    machine_id = data.get('machine_id', '').strip()
    hostname   = (data.get('hostname', '') or '')[:64]
    if not key or not machine_id:
        return jsonify({'error': 'licence_key and machine_id required'}), 400
    with _desktop_licences_lock:
        licences = _load_desktop_licences()
        rec = licences.get(key)
        if not rec:
            return jsonify({'error': 'Invalid licence key — check your order email.'}), 404
        if datetime.fromisoformat(rec['valid_until']) < datetime.now():
            return jsonify({'error': 'Licence expired — renew at cheapcadtools.com'}), 403
        for act in rec['activations']:
            if act['machine_id'] == machine_id:
                return jsonify({'ok': True, 'valid_until': rec['valid_until']})
        if len(rec['activations']) >= rec.get('max_activations', 2):
            return jsonify({'error': 'Activation limit reached (2 computers max). Email support@cheapcadtools.com to reset.'}), 403
        rec['activations'].append({
            'machine_id':   machine_id,
            'hostname':     hostname,
            'activated_at': datetime.now().isoformat(),
        })
        _save_desktop_licences(licences)
    return jsonify({'ok': True, 'valid_until': rec['valid_until']})


@app.route('/api/desktop/verify', methods=['POST'])
def api_desktop_verify():
    """Called by desktop app on startup (every ~7 days) to confirm licence still valid."""
    data       = request.get_json(silent=True) or {}
    key        = data.get('licence_key', '').strip().upper()
    machine_id = data.get('machine_id', '').strip()
    if not key or not machine_id:
        return jsonify({'error': 'licence_key and machine_id required'}), 400
    with _desktop_licences_lock:
        licences = _load_desktop_licences()
        rec = licences.get(key)
    if not rec:
        return jsonify({'error': 'Invalid licence key'}), 404
    if datetime.fromisoformat(rec['valid_until']) < datetime.now():
        return jsonify({'error': 'Licence expired — renew at cheapcadtools.com'}), 403
    if not any(act['machine_id'] == machine_id for act in rec['activations']):
        return jsonify({'error': 'Machine not activated for this licence'}), 403
    return jsonify({'ok': True, 'valid_until': rec['valid_until']})


# Environment variables (set in Render dashboard):
#   PROVISION_SECRET   — admin bearer token for /api/subscribers/add
#   PULLEY_LICENCE_B64 — base64-encoded licence.lic (generated locally via prepare_release.py)
#   PULLEY_LICENCE_EXPIRY — YYYY-MM-DD expiry date matching the licence
#   PULLEY_APP_URL     — public URL to PulleyApp.zip (GitHub Release asset)

import base64 as _base64

_PROVISION_SECRET   = os.environ.get('PROVISION_SECRET', '')
_LICENCE_B64        = os.environ.get('PULLEY_LICENCE_B64', '')
_LICENCE_EXPIRY     = os.environ.get('PULLEY_LICENCE_EXPIRY', '')
_APP_DOWNLOAD_URL   = os.environ.get('PULLEY_APP_URL', '')
_APP_VERSION        = os.environ.get('PULLEY_APP_VERSION', '')
_APP_CHANGELOG      = os.environ.get('PULLEY_APP_CHANGELOG', '')
_RUNTIME_URL        = os.environ.get('PULLEY_RUNTIME_URL', '')
_RUNTIME_VERSION    = os.environ.get('PULLEY_RUNTIME_VERSION', '')
_AUTODESK_APP_ID    = os.environ.get('AUTODESK_APP_ID', '')   # set after App Store registration
_ENTITLEMENT_URL    = 'https://apps.autodesk.com/webservices/checkentitlement'
_SUBSCRIBERS_FILE   = os.path.join(_LOG_DIR, 'subscribers.json')
_PURCHASES_FILE     = os.path.join(_LOG_DIR, 'autodesk_purchases.json')
_subscribers_lock   = threading.Lock()
_purchases_lock     = threading.Lock()


def _load_subscribers():
    try:
        if os.path.exists(_SUBSCRIBERS_FILE):
            with open(_SUBSCRIBERS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_subscribers(data):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_SUBSCRIBERS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _verify_autodesk_entitlement(user_id):
    """Return True if Autodesk confirms this user has an active App Store subscription."""
    if not _AUTODESK_APP_ID or not user_id:
        return False
    try:
        import urllib.request as _ur
        url = f'{_ENTITLEMENT_URL}?userid={user_id}&appid={_AUTODESK_APP_ID}'
        with _ur.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return bool(data.get('IsValid'))
    except Exception:
        return False


@app.route('/api/provision', methods=['POST'])
def api_provision():
    """Called by the Fusion addin to verify subscription and receive install assets.

    Verification order:
      1. Autodesk Entitlement API (primary — used once AUTODESK_APP_ID is set).
      2. Manual subscribers.json (fallback for beta users and pre-registration testing).
    """
    data    = request.get_json(silent=True) or {}
    user_id = data.get('user_id', '').strip()
    email   = data.get('email', '').lower().strip()

    if not user_id and not email:
        return jsonify({'error': 'user_id or email required'}), 400

    # ── Dev backdoor (remove before public launch) ────────────────────────────
    if data.get('backdoor_key') == 'xoot':
        entitled = True
    else:
        # ── Primary: Autodesk Entitlement API ─────────────────────────────────
        entitled = _verify_autodesk_entitlement(user_id)

        # ── Fallback: manual subscriber list (beta / pre-App-Store) ───────────
        if not entitled:
            with _subscribers_lock:
                subs = _load_subscribers()
            record   = subs.get(user_id) or subs.get(email)
            entitled = bool(record and record.get('active'))

    if not entitled:
        return jsonify({'error': 'No active subscription found for this account.'}), 403

    if not _LICENCE_B64 or not _APP_DOWNLOAD_URL:
        return jsonify({'error': 'Release not yet published — contact support.'}), 503

    return jsonify({
        'app_url':          _APP_DOWNLOAD_URL,
        'app_version':      _APP_VERSION,
        'app_changelog':    _APP_CHANGELOG,
        'runtime_url':      _RUNTIME_URL,
        'runtime_version':  _RUNTIME_VERSION,
        'licence_b64':      _LICENCE_B64,
        'licence_expiry':   _LICENCE_EXPIRY,
    })


@app.route('/api/autodesk-ipn', methods=['POST'])
def api_autodesk_ipn():
    """Instant Payment Notification from the Autodesk App Store.

    Autodesk POSTs application/x-www-form-urlencoded with fields including:
      buyer_adsk_account — buyer's Autodesk email
      appId              — app identifier (matches AUTODESK_APP_ID once registered)
      txn_id             — transaction ID
      payment_status     — 'Completed', 'Refunded', 'Reversed', etc.
      mc_gross           — amount (0.00 for free tier)
      txn_type           — transaction type

    We log every notification and send a welcome email on first Completed purchase.
    """
    payload = request.form.to_dict()
    app.logger.info(f'Autodesk IPN received: {payload}')

    status    = payload.get('payment_status', '').strip()
    txn_id    = payload.get('txn_id', '').strip()
    email     = payload.get('buyer_adsk_account', '').lower().strip()
    app_id    = payload.get('appId', '').strip()
    txn_type  = payload.get('txn_type', '').strip()
    amount    = payload.get('mc_gross', '0.00').strip()

    # Reject notifications for other apps if AUTODESK_APP_ID is configured
    if _AUTODESK_APP_ID and app_id and app_id != _AUTODESK_APP_ID:
        app.logger.warning(f'IPN appId mismatch: got {app_id}, expected {_AUTODESK_APP_ID}')
        return '', 200  # always return 200 to Autodesk

    record = {
        'ts':           int(time.time()),
        'txn_id':       txn_id,
        'txn_type':     txn_type,
        'status':       status,
        'email':        email,
        'app_id':       app_id,
        'amount':       amount,
        'raw':          payload,
    }

    # Persist to log
    with _purchases_lock:
        try:
            purchases = []
            if os.path.exists(_PURCHASES_FILE):
                with open(_PURCHASES_FILE) as f:
                    purchases = json.load(f)
        except Exception:
            purchases = []
        purchases.append(record)
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_PURCHASES_FILE, 'w') as f:
            json.dump(purchases, f, indent=2)

    # Send welcome email on first Completed purchase for this buyer
    if status == 'Completed' and email:
        already_welcomed = any(
            p.get('email') == email and p.get('status') == 'Completed' and p.get('ts') != record['ts']
            for p in purchases
        )
        if not already_welcomed:
            _send_ipn_welcome_email(email, txn_id)

    return '', 200


def _send_ipn_welcome_email(email, txn_id):
    """Send a purchase confirmation / getting-started email via SendGrid."""
    sg_key = os.environ.get('SENDGRID_API_KEY', '')
    if not sg_key:
        return
    try:
        import urllib.request as _ur
        body = json.dumps({
            'personalizations': [{'to': [{'email': email}]}],
            'from': {'email': 'support@cheapcadtools.com', 'name': 'CheapCAD Tools'},
            'subject': 'Welcome to CheapCAD Tools — your subscription is active',
            'content': [{
                'type': 'text/plain',
                'value': (
                    f'Hi,\n\n'
                    f'Thank you for subscribing to CheapCAD Tools on the Autodesk App Store!\n\n'
                    f'Your subscription is now active. Restart Fusion 360 and the CheapCAD Tools '
                    f'panel will install automatically.\n\n'
                    f'Transaction ID: {txn_id}\n\n'
                    f'If you have any questions, reply to this email or visit '
                    f'https://cheapcadtools.com/contact/\n\n'
                    f'— CheapCAD Tools'
                ),
            }],
        }).encode()
        req = _ur.Request(
            'https://api.sendgrid.com/v3/mail/send',
            data=body,
            headers={
                'Authorization': f'Bearer {sg_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        with _ur.urlopen(req, timeout=10):
            pass
        app.logger.info(f'IPN welcome email sent to {email}')
    except Exception as exc:
        app.logger.error(f'IPN welcome email failed: {exc}')


@app.route('/api/subscribers/add', methods=['POST'])
def api_subscribers_add():
    """Admin endpoint — add or reactivate a subscriber after App Store purchase."""
    auth = request.headers.get('Authorization', '')
    if not _PROVISION_SECRET or auth != f'Bearer {_PROVISION_SECRET}':
        return jsonify({'error': 'unauthorized'}), 401

    data    = request.get_json(silent=True) or {}
    user_id = data.get('user_id', '').strip()
    email   = data.get('email', '').lower().strip()
    if not user_id and not email:
        return jsonify({'error': 'user_id or email required'}), 400

    with _subscribers_lock:
        subs = _load_subscribers()
        key  = user_id or email
        subs[key] = {
            'user_id':  user_id,
            'email':    email,
            'active':   True,
            'added':    datetime.now().isoformat(),
        }
        _save_subscribers(subs)

    return jsonify({'ok': True, 'key': key})


@app.route('/api/subscribers/remove', methods=['POST'])
def api_subscribers_remove():
    """Admin endpoint — deactivate a subscriber on cancellation."""
    auth = request.headers.get('Authorization', '')
    if not _PROVISION_SECRET or auth != f'Bearer {_PROVISION_SECRET}':
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    key  = (data.get('user_id') or data.get('email') or '').strip()
    if not key:
        return jsonify({'error': 'user_id or email required'}), 400

    with _subscribers_lock:
        subs = _load_subscribers()
        if key in subs:
            subs[key]['active'] = False
            _save_subscribers(subs)

    return jsonify({'ok': True, 'key': key})


# ── Admin dashboard UI ───────────────────────────────────────────────────────
@app.route('/admin')
@app.route('/admin/')
def admin_dashboard():
    return send_from_directory(_base_dir, 'admin_dashboard.html')


# ── Admin dashboard API ───────────────────────────────────────────────────────
_RENDER_API_KEY    = os.environ.get('RENDER_API_KEY', '')
_RENDER_SERVICE_ID = os.environ.get('RENDER_SERVICE_ID', 'srv-d7bve2a8qa3s738n68ig')


def _admin_auth():
    """Return a 401 response if the request lacks a valid admin Bearer token."""
    auth = request.headers.get('Authorization', '')
    if not _PROVISION_SECRET or auth != f'Bearer {_PROVISION_SECRET}':
        return jsonify({'error': 'unauthorized'}), 401
    return None


def _read_jsonl_since(path, since_ts):
    rows = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get('ts', 0) >= since_ts:
                        rows.append(obj)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


@app.route('/api/admin/health')
def api_admin_health():
    err = _admin_auth()
    if err:
        return err

    if _HAVE_PSUTIL:
        cpu     = psutil.cpu_percent(interval=None)
        mem     = psutil.virtual_memory()
        mem_mb  = mem.used // (1024 * 1024)
        mem_pct = mem.percent
        try:
            disk     = psutil.disk_usage(_LOG_DIR)
            disk_free_mb = disk.free // (1024 * 1024)
            disk_pct     = disk.percent
        except Exception:
            disk_free_mb = disk_pct = 0
    else:
        cpu = mem_mb = mem_pct = disk_free_mb = disk_pct = 0

    try:
        with open(_DOWNLOAD_COUNT_FILE, 'r', encoding='utf-8') as f:
            dl_count = json.load(f).get('count', 0)
    except Exception:
        dl_count = 0

    try:
        bug_count = len(_parse_bug_reports())
    except Exception:
        bug_count = 0

    with _subscribers_lock:
        subs = _load_subscribers()
    active_subs = sum(1 for s in subs.values() if s.get('active'))

    return jsonify({
        'version':          APP_VERSION,
        'build_time':       BUILD_TIME,
        'ts':               int(time.time()),
        'cpu_pct':          cpu,
        'mem_mb':           mem_mb,
        'mem_pct':          mem_pct,
        'disk_free_mb':     disk_free_mb,
        'disk_pct':         disk_pct,
        'downloads':        dl_count,
        'bug_reports':      bug_count,
        'active_subs':      active_subs,
    })


@app.route('/api/admin/metrics')
def api_admin_metrics():
    err = _admin_auth()
    if err:
        return err
    hours    = min(int(request.args.get('hours', 24)), 720)
    since_ts = time.time() - hours * 3600
    rows     = _read_jsonl_since(_METRICS_FILE, since_ts)
    return jsonify({'hours': hours, 'count': len(rows), 'data': rows})


@app.route('/api/admin/constraints')
def api_admin_constraints():
    err = _admin_auth()
    if err:
        return err
    hours    = min(int(request.args.get('hours', 168)), 720)
    since_ts = time.time() - hours * 3600
    rows     = _read_jsonl_since(_CONSTRAINTS_FILE, since_ts)
    return jsonify({'hours': hours, 'count': len(rows), 'events': rows})


def _load_bug_comments():
    try:
        with open(_BUG_COMMENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_bug_comments(data):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_BUG_COMMENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def _load_bug_issue_urls():
    try:
        with open(_BUG_ISSUE_URLS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _normalize_issue_record(val):
    """Upgrade a bare URL string to the structured {url, number, state} format."""
    if isinstance(val, str):
        m = re.search(r'/issues/(\d+)$', val)
        return {'url': val, 'number': int(m.group(1)) if m else None, 'state': 'unknown'}
    return val


def _save_bug_issue_url(ts_id, url):
    os.makedirs(_LOG_DIR, exist_ok=True)
    urls = _load_bug_issue_urls()
    m = re.search(r'/issues/(\d+)$', url)
    urls[ts_id] = {'url': url, 'number': int(m.group(1)) if m else None, 'state': 'open'}
    with open(_BUG_ISSUE_URLS_FILE, 'w', encoding='utf-8') as f:
        json.dump(urls, f, indent=2)


def _parse_bug_reports():
    """Parse bug_reports.log into a list of structured dicts."""
    import re
    try:
        with open(_LOG_FILE, 'r', encoding='utf-8') as f:
            raw = f.read()
    except FileNotFoundError:
        return []

    sep = '=' * 60
    parts = raw.split(sep)
    reports = []
    i = 0
    while i < len(parts) - 1:
        hdr = parts[i].strip()
        if not (hdr.startswith('Bug Report') or hdr.startswith('Feature Request')):
            i += 1
            continue
        body  = parts[i + 1] if i + 1 < len(parts) else ''
        lines = hdr.split('\n')
        title = lines[0]
        ver_line = lines[1] if len(lines) > 1 else ''

        m = re.match(r'(Bug Report|Feature Request) — (.+)', title)
        rtype     = m.group(1) if m else 'Bug Report'
        timestamp = m.group(2).strip() if m else ''
        vm = re.match(r'App Version:\s*(\S+)\s+Build:\s*(.+)', ver_line)
        version = vm.group(1).strip() if vm else ''
        build   = vm.group(2).strip() if vm else ''

        is_feature   = rtype == 'Feature Request'
        lbl_seeing   = 'Would like to do:' if is_feature else 'Currently seeing:'
        lbl_should   = "Why it's useful:"  if is_feature else 'Should be seeing:'

        def extract(text, lbl, *stop_lbls):
            pos = text.find(lbl)
            if pos == -1:
                return ''
            start = pos + len(lbl)
            end   = len(text)
            for sl in stop_lbls:
                p = text.find(sl, start)
                if p != -1:
                    end = min(end, p)
            chunk = text[start:end].strip()
            lines_ = [l[2:] if l.startswith('  ') else l for l in chunk.split('\n')]
            return '\n'.join(lines_).strip()

        seeing    = extract(body, lbl_seeing,   lbl_should, 'Contact email:', 'App state:')
        should_see= extract(body, lbl_should,   'Contact email:', 'App state:')
        email     = extract(body, 'Contact email:', 'App state:')

        state = {}
        sm = re.search(r'App state:\n(\{.*)\Z', body, re.DOTALL)
        if sm:
            try:
                state = json.loads(sm.group(1))
            except Exception:
                pass

        reports.append({
            'id':         timestamp,
            'type':       rtype,
            'timestamp':  timestamp,
            'version':    version,
            'build':      build,
            'seeing':     seeing,
            'should_see': should_see,
            'email':      email,
            'state':      state,
            'comment':    '',
        })
        i += 2

    comments   = _load_bug_comments()
    issue_urls = _load_bug_issue_urls()
    for r in reports:
        r['comment'] = comments.get(r['timestamp'], '')
        raw = issue_urls.get(r['timestamp'])
        if raw:
            rec = _normalize_issue_record(raw)
            r['issue_url']    = rec.get('url', '')
            r['issue_number'] = rec.get('number')
            r['issue_state']  = rec.get('state', 'unknown')
        else:
            r['issue_url']    = ''
            r['issue_number'] = None
            r['issue_state']  = None
    return reports


def _write_bug_log(reports):
    """Rewrite bug_reports.log from a list of parsed report dicts."""
    sep = '=' * 60
    content = ''
    for r in reports:
        is_feature   = r['type'] == 'Feature Request'
        lbl_seeing   = 'Would like to do' if is_feature else 'Currently seeing'
        lbl_should   = "Why it's useful"  if is_feature else 'Should be seeing'
        content += (
            f'\n{sep}\n'
            f'{r["type"]} — {r["timestamp"]}\n'
            f'App Version: {r["version"]}   Build: {r["build"]}\n'
            f'{sep}\n'
            f'{lbl_seeing}:\n  {r["seeing"] or "(not provided)"}\n\n'
            f'{lbl_should}:\n  {r["should_see"] or "(not provided)"}\n\n'
            f'Contact email:\n  {r["email"] or "(not provided)"}\n\n'
            f'App state:\n{json.dumps(r["state"], indent=2)}\n'
        )
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_LOG_FILE, 'w', encoding='utf-8') as f:
        f.write(content)


@app.route('/api/admin/bug-reports')
def api_admin_bug_reports():
    err = _admin_auth()
    if err:
        return err
    reports = _parse_bug_reports()
    return jsonify({'count': len(reports), 'reports': reports})


def _bug_hash(ts_id):
    """FNV-1a 32-bit hash matching dashboard JS bugHash() — uses ctypes for exact 32-bit multiply."""
    import ctypes
    h = 0x811c9dc5
    for c in ts_id:
        h ^= ord(c)
        h = ctypes.c_uint32(h * 0x01000193).value
    return format(h, '08X')


@app.route('/api/admin/bug-reports/hash/<hash_id>')
def api_admin_bug_by_hash(hash_id):
    err = _admin_auth()
    if err:
        return err
    hash_id = hash_id.upper()
    for r in _parse_bug_reports():
        if _bug_hash(r['id']) == hash_id:
            return jsonify(r)
    return jsonify({'error': f'No report found with hash {hash_id}'}), 404


@app.route('/api/admin/bug-reports/<ts_id>', methods=['DELETE'])
def api_admin_bug_delete(ts_id):
    err = _admin_auth()
    if err:
        return err
    reports = _parse_bug_reports()
    kept = [r for r in reports if r['id'] != ts_id]
    if len(kept) == len(reports):
        return jsonify({'error': 'not found'}), 404
    _write_bug_log(kept)
    comments = _load_bug_comments()
    comments.pop(ts_id, None)
    _save_bug_comments(comments)
    issue_urls = _load_bug_issue_urls()
    if ts_id in issue_urls:
        issue_urls.pop(ts_id)
        with open(_BUG_ISSUE_URLS_FILE, 'w', encoding='utf-8') as f:
            json.dump(issue_urls, f, indent=2)
    return jsonify({'ok': True, 'remaining': len(kept)})


@app.route('/api/admin/bug-reports/<ts_id>/comment', methods=['POST'])
def api_admin_bug_comment(ts_id):
    err = _admin_auth()
    if err:
        return err
    data    = request.get_json(silent=True) or {}
    comment = str(data.get('comment', '')).strip()
    comments = _load_bug_comments()
    if comment:
        comments[ts_id] = comment
    else:
        comments.pop(ts_id, None)
    _save_bug_comments(comments)
    return jsonify({'ok': True})


def _github_api(method, path, pat, body=None):
    """Make a GitHub API call; returns (response_dict, status_code)."""
    import urllib.request, urllib.error
    url = f'https://api.github.com{path}'
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization':       f'Bearer {pat}',
        'Accept':              'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type':        'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{}'), e.code


@app.route('/api/admin/bug-reports/<ts_id>/github-close', methods=['POST'])
def api_admin_bug_github_close(ts_id):
    err = _admin_auth()
    if err:
        return err
    pat  = os.environ.get('FEEDBACK_GITHUB_PAT', '').strip()
    repo = os.environ.get('FEEDBACK_GITHUB_REPO', '').strip()
    if not pat or not repo:
        return jsonify({'error': 'GitHub not configured'}), 503
    issue_urls = _load_bug_issue_urls()
    raw = issue_urls.get(ts_id)
    if not raw:
        return jsonify({'error': 'No GitHub issue linked to this report'}), 404
    rec    = _normalize_issue_record(raw)
    number = rec.get('number')
    if not number:
        return jsonify({'error': 'Cannot determine issue number from URL'}), 400
    data   = request.get_json(silent=True) or {}
    target = 'closed' if data.get('state', 'closed') == 'closed' else 'open'
    resp, status = _github_api('PATCH', f'/repos/{repo}/issues/{number}', pat, {'state': target})
    if status not in (200, 201):
        return jsonify({'error': resp.get('message', 'GitHub error'), 'status': status}), 502
    rec['state'] = resp.get('state', target)
    issue_urls[ts_id] = rec
    with open(_BUG_ISSUE_URLS_FILE, 'w', encoding='utf-8') as f:
        json.dump(issue_urls, f, indent=2)
    return jsonify({'ok': True, 'state': rec['state']})


@app.route('/api/admin/github-sync', methods=['POST'])
def api_admin_github_sync():
    err = _admin_auth()
    if err:
        return err
    pat  = os.environ.get('FEEDBACK_GITHUB_PAT', '').strip()
    repo = os.environ.get('FEEDBACK_GITHUB_REPO', '').strip()
    if not pat or not repo:
        return jsonify({'ok': False, 'error': 'GitHub not configured', 'updated': {}})
    issue_urls = _load_bug_issue_urls()
    updated = {}
    changed = False
    for ts_id, raw in issue_urls.items():
        rec    = _normalize_issue_record(raw)
        number = rec.get('number')
        if not number:
            continue
        resp, status = _github_api('GET', f'/repos/{repo}/issues/{number}', pat)
        if status == 200:
            new_state = resp.get('state', 'unknown')
            if rec.get('state') != new_state:
                rec['state'] = new_state
                issue_urls[ts_id] = rec
                changed = True
            updated[ts_id] = new_state
    if changed:
        with open(_BUG_ISSUE_URLS_FILE, 'w', encoding='utf-8') as f:
            json.dump(issue_urls, f, indent=2)
    return jsonify({'ok': True, 'updated': updated})


@app.route('/api/admin/downloads')
def api_admin_downloads():
    err = _admin_auth()
    if err:
        return err
    try:
        with open(_DOWNLOAD_COUNT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {'count': 0}
    return jsonify(data)


@app.route('/api/admin/subscribers')
def api_admin_subscribers():
    err = _admin_auth()
    if err:
        return err
    with _subscribers_lock:
        subs = _load_subscribers()
    return jsonify({'count': len(subs), 'subscribers': subs})


@app.route('/api/admin/sales')
def api_admin_sales():
    err = _admin_auth()
    if err:
        return err
    with _purchases_lock:
        try:
            purchases = json.load(open(_PURCHASES_FILE)) if os.path.exists(_PURCHASES_FILE) else []
        except Exception:
            purchases = []
    completed = [p for p in purchases if p.get('status') == 'Completed']
    total_revenue = sum(float(p.get('amount', 0) or 0) for p in completed)
    return jsonify({
        'total':       len(completed),
        'revenue':     round(total_revenue, 2),
        'purchases':   sorted(purchases, key=lambda p: p.get('ts', 0), reverse=True),
    })


@app.route('/api/admin/render/service')
def api_admin_render_service():
    err = _admin_auth()
    if err:
        return err
    if not _RENDER_API_KEY:
        return jsonify({'error': 'RENDER_API_KEY not configured'}), 503
    try:
        import requests as _requests
        r = _requests.get(
            f'https://api.render.com/v1/services/{_RENDER_SERVICE_ID}',
            headers={'Authorization': f'Bearer {_RENDER_API_KEY}'},
            timeout=8,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/admin/render/deploys')
def api_admin_render_deploys():
    err = _admin_auth()
    if err:
        return err
    if not _RENDER_API_KEY:
        return jsonify({'error': 'RENDER_API_KEY not configured'}), 503
    try:
        import requests as _requests
        r = _requests.get(
            f'https://api.render.com/v1/services/{_RENDER_SERVICE_ID}/deploys?limit=10',
            headers={'Authorization': f'Bearer {_RENDER_API_KEY}'},
            timeout=8,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ── Async Download with Job Queue ───────────────────────────────────────────

@app.route('/download/<job_id>.step')
def download_async_step(job_id):
    """Serve generated STEP file from async download job."""
    file_path = os.path.join(_LOG_DIR, f'{job_id}.step')
    if not os.path.exists(file_path):
        return 'File not found', 404
    return send_file(file_path, mimetype='application/step',
                     as_attachment=True, download_name=f'{job_id}.step')


@app.route('/api/download-status/<job_id>')
def api_download_status(job_id):
    """Get status of a download job (queued, processing, done, or failed).

    Returns:
      {
        "id": "a1b2c3d4",
        "status": "queued" | "processing" | "done" | "failed",
        "queue_position": 2 (if queued),
        "progress": 0-100 (if processing),
        "active_jobs": 1,
        "output_file": "/download/a1b2c3d4.step" (if done),
        "error": "..." (if failed)
      }
    """
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job.to_dict())


@app.route('/api/download/all-step-async', methods=['POST'])
def api_download_all_step_async():
    """Start async STEP generation for all parts (P1, P2, belt).
    Returns immediately with job_id; client polls /api/download-status/{job_id}.

    Request body: URL query params as JSON
    Response: {"job_id": "a1b2c3d4", "status_url": "/api/download-status/a1b2c3d4"}
    """
    try:
        # Extract params from request JSON (convert from form params)
        query_params = request.get_json() or {}
        job = create_job('all-step', query_params)

        def generate_async():
            """Background worker: generate all STEP assembly."""
            start_job(job.id)  # Move from queued to processing
            try:
                import json as _json
                import subprocess
                import sys

                # Build keyword dicts same as sync route (download_all_step)
                def _build_kw(pfx):
                    family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
                        _parse_stl_params(query_params, '2' if pfx == 'p2_' else '1')
                    hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(query_params, pfx)
                    sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_c, sp_h, sp_split = \
                        _parse_spoke_params(query_params, pfx)
                    eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od
                    _fl_en = query_params.get(f'{pfx}flange_enabled') == '1'
                    fp = _parse_flange_params(query_params, pfx) if _fl_en else {}
                    return dict(
                        family=family, pitch=pitch, num_teeth=num_teeth,
                        bore_mm=bore_mm, belt_height_mm=belt_height,
                        clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex,
                        hub_od_mm=eff_hub_od, hub_height_mm=hub_h,
                        screw_dia_mm=sd, screw_count=sc,
                        captured_nut=cn, flat_depth_mm=fd,
                        keyway_w_mm=kw_w, keyway_h_mm=kw_h,
                        spoke_count=sp_c if sp_en else 0,
                        spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
                        rim_depth_mm=sp_rim, fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
                        spoke_height_mm=sp_h,
                        flange_enabled       = _fl_en,
                        flange_3dprint       = fp.get('flange_3dprint', True),
                        flange_angle_deg     = fp.get('flange_angle_deg', 15.0),
                        flange_rim_radius_mm = fp.get('rim_radius_mm', 3.0),
                        flange_height_mm     = fp.get('flange_height_mm', 1.5),
                        flange_top_separate  = fp.get('top_separate', True),
                        nubs_enabled         = fp.get('nubs_enabled', False),
                        nub_count            = fp.get('nub_count', 4),
                        nub_dia_mm           = fp.get('nub_dia_mm', 3.0),
                        nub_height_mm        = fp.get('nub_height_mm', 2.0),
                        nub_allowance_mm     = fp.get('nub_allowance_mm', 0.2),
                        plate_height_mm      = fp.get('plate_height_mm', 1.0),
                        bend_radius_mm       = fp.get('bend_radius_mm', 0.0),
                    )

                update_progress(job.id, 10)  # Parsing
                dual = query_params.get('dual') == 'true'
                kw1 = _build_kw('')
                kw2 = _build_kw('p2_') if dual else None

                update_progress(job.id, 20)  # Building params
                belt_kw = None
                if dual:
                    key   = _resolve_key(kw1['family'], kw1['pitch'])
                    spec  = PULLEY_SPECS.get(key, {}) if key else {}
                    pitch_mm   = spec.get('pitch', 5.0)
                    _default_c = (kw1['num_teeth'] + kw2['num_teeth']) * pitch_mm / (2.0 * math.pi)
                    center_dist = float(query_params.get('center_distance', _default_c))
                    raw_belt_h  = max(1.0, float(query_params.get('belt_height', 10.0)))
                    belt_kw = dict(
                        family         = kw1['family'],
                        pitch          = kw1['pitch'],
                        num_teeth_left = kw1['num_teeth'],
                        num_teeth_right= kw2['num_teeth'],
                        center_dist_mm = center_dist,
                        belt_height_mm = raw_belt_h,
                    )

                update_progress(job.id, 30)  # Generating STEP
                try:
                    from exporters.step_exporter import generate_all_parts_step
                    step_bytes = generate_all_parts_step(kw1, kw2, belt_kw)
                except ImportError:
                    # Fall back to subprocess (Python 3.12 venv)
                    root    = os.path.dirname(os.path.abspath(__file__))
                    venv_py = os.path.join(root, '.venv312', 'Scripts', 'python.exe')
                    worker  = os.path.join(root, 'exporters', 'step_worker.py')
                    worker_kw = dict(kw1, export_type='all')
                    if kw2:
                        worker_kw['kw2'] = kw2
                    if belt_kw:
                        worker_kw['belt_kw'] = belt_kw
                    result = subprocess.run(
                        [venv_py, worker, _json.dumps(worker_kw)],
                        capture_output=True, cwd=root,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(f'STEP error: {result.stderr.decode()}')
                    step_bytes = result.stdout

                update_progress(job.id, 80)  # Writing file
                _t1 = kw1['num_teeth']
                _fname = (f'{kw1["family"]}-{kw1["pitch"]}-{_t1}T+{kw2["num_teeth"]}T-all.step'
                         if kw2 else f'{kw1["family"]}-{kw1["pitch"]}-{_t1}T-all.step')
                output_path = os.path.join(_LOG_DIR, f'{job.id}.step')
                with open(output_path, 'wb') as f:
                    f.write(step_bytes)

                update_progress(job.id, 100)
                finish_job(job.id, output_file=f'/download/{job.id}.step')

            except Exception as e:
                finish_job(job.id, error=str(e))

        # Start worker thread
        thread = threading.Thread(target=generate_async, daemon=True)
        thread.start()

        return jsonify({
            'job_id': job.id,
            'status_url': f'/api/download-status/{job.id}',
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
