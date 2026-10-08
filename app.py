"""
app.py — Timing Pulley Generator web app (Flask)
Serves the pulley generator UI and returns SVG downloads.
"""
import hashlib
import functools
import math
import io
import os
import sys
import json
import re
from cct_common import (
    embed_step as _lib_embed_step,
    embed_stl  as _lib_embed_stl,
    embed_dxf  as _lib_embed_dxf,
    embed_svg  as _lib_embed_svg,
)
# Phase A of the cct_common wiring plan: small generic utilities, aliased to
# their old local names so call sites don't need to change.
from cct_common.text_utils import safe_float as _safe_float, safe_dl_name as _safe_dl_name
from cct_common.jsonl_log import append_jsonl as _append_jsonl, trim_jsonl as _trim_jsonl
from cct_common.github_api import request as _github_api
from cct_common.resend_email import send as _cc_send_email
from cct_common.addin_mirror import mirror_to_addins as _cc_mirror_to_addins
import subprocess
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
from flask import Flask, render_template, request, Response, jsonify, send_from_directory, send_file, redirect
from cct_common.job_queue import (
    create_job, get_job, start_job, update_progress, finish_job, get_queue_status,
    create_session, get_session_status, heartbeat, release_session, get_queue_info,
    register_trial_download, clear_all_state, clear_stale_on_startup,
    check_web_download, record_web_download,
    trial_downloads_per_week as _trial_downloads_per_week,
    register_machine_id as _cc_register_machine_id,
    configure as _cc_job_queue_configure,
    start_background_threads as _cc_job_queue_start_threads,
)
# Reused for /api/trial/status (admin diagnostic); not part of the public API.
from cct_common.job_queue import _load_trial_downloads as _cc_load_trial_downloads
from functools import wraps
from charging import charges  # token charging per export (ADR-008); off unless TOKENS_ENABLED=1
import threading as _threading
import uuid as _uuid_mod
import time as _time_mod

# ── Web download token store ───────────────────────────────────────────────────
# Short-lived single-use tokens issued by /api/fp-token; consumed by download
# routes to record a fingerprinted download without putting the FP in the URL.
_FP_TOKENS: dict = {}
_FP_TOKEN_LOCK = _threading.Lock()
_FP_TOKEN_TTL  = 300   # 5 minutes — covers slow connections and async jobs


def _issue_web_token(fp: str, ip: str) -> str:
    """Issue and store a single-use download token for this fingerprint."""
    token = _uuid_mod.uuid4().hex
    expires = _time_mod.monotonic() + _FP_TOKEN_TTL
    with _FP_TOKEN_LOCK:
        # Prune expired tokens opportunistically
        now = _time_mod.monotonic()
        expired = [t for t, v in _FP_TOKENS.items() if v['exp'] < now]
        for t in expired:
            _FP_TOKENS.pop(t, None)
        _FP_TOKENS[token] = {'fp': fp, 'ip': ip, 'exp': expires}
    return token


def _consume_web_token(req) -> None:
    """Consume a download token from the request and record the web download.
    Fails silently if the token is absent, expired, or invalid — fail open.
    """
    token = req.args.get('_fpt') or (req.get_json(silent=True) or {}).get('_fpt')
    if not token:
        return
    with _FP_TOKEN_LOCK:
        entry = _FP_TOKENS.pop(token, None)
    if entry and _time_mod.monotonic() < entry['exp']:
        record_web_download(entry['fp'], entry['ip'])


def _consume_web_token_from_body(body: dict) -> None:
    """Consume a token embedded in a POST JSON body (async job routes)."""
    token = body.pop('_fpt', None)
    if not token:
        return
    with _FP_TOKEN_LOCK:
        entry = _FP_TOKENS.pop(token, None)
    if entry and _time_mod.monotonic() < entry['exp']:
        record_web_download(entry['fp'], entry['ip'])


def _get_machine_id():
    """Get machine_id from request context, header, or generate from IP+UA."""
    # Try to get from request context (set by session decorator)
    if hasattr(request, 'machine_id') and request.machine_id:
        return request.machine_id

    # Try to get from custom header (for addin/CLI)
    mid = request.headers.get('X-Machine-ID')
    if mid:
        return mid

    # Generate from IP + User-Agent hash
    import hashlib
    ip = request.remote_addr or '0.0.0.0'
    ua = request.headers.get('User-Agent', 'unknown')
    machine_id = hashlib.sha256(f'{ip}:{ua}'.encode()).hexdigest()[:16]
    return machine_id


# ── The session queue: compiled out ─────────────────────────────────────────
# One active user at a time, the rest in a waiting room at /queue, built for
# the single-server Render host. The live site runs on Google Cloud Run, which
# scales by adding servers and never queues (the Dockerfile's QUEUE_DISABLED=1
# kept it idle). With SESSION_QUEUE off the queue is not in the app at all: no
# /queue page, no /api/session/* or /api/queue/* routes, no session check on
# the export routes, and exports built inside their request. True puts it all
# back as it was (QUEUE_DISABLED and PULLEY_TESTING then work as before).
SESSION_QUEUE = False


def queue_off() -> bool:
    """True when nothing queues: the queue compiled out, QUEUE_DISABLED, or testing."""
    return (not SESSION_QUEUE or bool(os.environ.get('QUEUE_DISABLED'))
            or bool(os.environ.get('PULLEY_TESTING')))


def _queue_route(rule, **options):
    """app.route for the session queue's own routes: registered only with
    SESSION_QUEUE on."""
    def deco(view):
        return app.route(rule, **options)(view) if SESSION_QUEUE else view
    return deco


def require_active_session(f):
    """Decorator: Check if user has active session before allowing expensive operations."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip checks in no-queue mode (local/desktop) or during testing
        from flask import current_app
        if queue_off() or current_app.config.get('TESTING') or charges.is_internal():
            return f(*args, **kwargs)

        # Get session_id from URL params, form data, or JSON body
        session_id = request.args.get('session_id')
        if not session_id and request.form:
            session_id = request.form.get('session_id')
        if not session_id and request.is_json:
            try:
                session_id = request.json.get('session_id')
            except:
                pass

        if not session_id:
            return jsonify({'error': 'No session_id. Visit /queue to join queue.', 'code': 'NO_SESSION'}), 403

        # Check if session is active
        status = get_session_status(session_id)
        if status.get('error'):
            return jsonify({'error': 'Session not found', 'code': 'SESSION_NOT_FOUND'}), 403

        if not status.get('is_active'):
            return jsonify({
                'error': f"Session not active. You are #{status.get('position')} in queue. Wait ~{status.get('estimated_wait_sec', 0)//60} min.",
                'code': 'NOT_ACTIVE',
                'position': status.get('position'),
                'estimated_wait': status.get('estimated_wait_sec'),
            }), 403

        # Weekly trial limit — replaced by per-export tokens when they're on.
        allowed = charges.enabled or not status.get('limit_exceeded', False)

        if not allowed:
            count = status.get('download_count', 0)
            limit = status.get('download_limit', 0)
            return jsonify({
                'error': f"Maximum {limit} weekly downloads reached. You have used {count} this week. Limit resets Sunday.",
                'code': 'DOWNLOAD_LIMIT_EXCEEDED',
                'count': count,
                'limit': limit
            }), 429

        # Update heartbeat
        heartbeat(session_id)

        return f(*args, **kwargs)
    return decorated_function

# ── small_step binary ─────────────────────────────────────────────────────────
# On Render set SMALL_STEP_BIN env var to the compiled Linux binary path.
# On Windows dev the release build is detected automatically as a fallback.
_SS_BIN = os.environ.get(
    'SMALL_STEP_BIN',
    os.path.join(os.path.dirname(__file__),
                 '..', 'small_step', 'target',
                 'x86_64-pc-windows-gnu', 'release', 'small_step.exe'),
)
_SS_BIN = os.path.normpath(_SS_BIN)
_SS_AVAILABLE = os.path.isfile(_SS_BIN)


def _has_spline(obj) -> bool:
    """A STEP job with a splined bore anywhere in it (dual and all-parts
    jobs nest their pulleys' settings)."""
    if isinstance(obj, dict):
        return bool(obj.get('spline')) or any(_has_spline(v) for v in obj.values())
    return False


def _run_ss_worker(worker_kw: dict, *, timeout: int = 110) -> bytes:
    """Run the small_step worker; return STEP bytes.

    In a PyInstaller frozen build sys.executable is the launcher EXE, not a
    Python interpreter, so spawning a subprocess would just re-launch the app.
    Instead, when frozen we call step_worker_ss.run() directly in-process.

    Raises RuntimeError on failure so callers can return 400.
    """
    if os.environ.get('PULLEY_STEP_BACKEND', '').strip().lower() == 'cadquery':
        return _run_cadquery_worker(worker_kw, timeout=timeout)
    # A splined or hex bore used to be refused here: small_step built a bore
    # only from a circle. It now accepts a closed LINE/ARC loop on the BORE
    # layer and carries it through the body, the hub and the flanges
    # (small_step 0.5.0, report sections 1 and 2), so the job goes through.
    # What it still cannot do it refuses BY NAME, and that message reaches
    # the user as the 400 instead of this blanket one.
    if getattr(sys, 'frozen', False):
        from exporters.step_worker_ss import run as _ss_run
        try:
            return _ss_run(worker_kw, _SS_BIN)
        except Exception as _e:
            raise RuntimeError(str(_e)) from _e

    root      = os.path.dirname(os.path.abspath(__file__))
    venv_win  = os.path.join(root, '.venv314', 'Scripts', 'python.exe')
    python    = venv_win if os.path.isfile(venv_win) else sys.executable
    worker_ss = os.path.join(root, 'exporters', 'step_worker_ss.py')
    env       = dict(os.environ, SMALL_STEP_BIN=_SS_BIN)
    result    = subprocess.run(
        [python, worker_ss, '-'], input=json.dumps(worker_kw).encode('utf-8'),
        capture_output=True, cwd=root, timeout=timeout, env=env,
    )
    if result.returncode != 0:
        import logging as _log
        _log.getLogger(__name__).error(
            'small_step worker failed (rc=%d): %s',
            result.returncode, result.stderr.decode(errors='replace'),
        )
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout


def _run_cadquery_worker(worker_kw: dict, *, timeout: int = 110) -> bytes:
    """STEP from cadquery (exporters/step_worker.py) instead of small_step —
    the reference backend of this cadquery track (PULLEY_STEP_BACKEND=cadquery).
    cadquery/OCP only has Python 3.12 wheels, so it runs in .venv312."""
    root   = os.path.dirname(os.path.abspath(__file__))
    python = os.path.join(root, '.venv312', 'Scripts', 'python.exe')
    if not os.path.isfile(python):
        python = sys.executable
    result = subprocess.run(
        [python, os.path.join(root, 'exporters', 'step_worker.py'), json.dumps(worker_kw)],
        capture_output=True, cwd=root, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])
    return result.stdout


# ── App version ───────────────────────────────────────────────────────────────
APP_VERSION        = '2.0.15'   # 2.x: the token model (ADR-008) — accounts, pay per download, hosted only
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

# ── Error log (circular buffer, max 512 KB on disk) ───────────────────────────
import logging as _logging
import logging.handlers as _log_handlers
_ERROR_LOG_FILE = os.path.join(_LOG_DIR, 'server_errors.log')
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    _err_handler = _log_handlers.RotatingFileHandler(
        _ERROR_LOG_FILE, maxBytes=256 * 1024, backupCount=1, encoding='utf-8',
    )
    _err_handler.setLevel(_logging.WARNING)
    _err_handler.setFormatter(_logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
    ))
    _logging.getLogger().addHandler(_err_handler)        # root → catches everything
    _logging.getLogger('werkzeug').addHandler(_err_handler)
    # Cloud Run (it sets K_SERVICE): a server's disk vanishes with it, and
    # there are several — so warnings and errors also go to stderr, which
    # Cloud Logging collects from every server.
    if os.environ.get('K_SERVICE'):
        _stderr_handler = _logging.StreamHandler()
        _stderr_handler.setLevel(_logging.WARNING)
        _stderr_handler.setFormatter(_logging.Formatter('%(levelname)s %(name)s: %(message)s'))
        _logging.getLogger().addHandler(_stderr_handler)
except Exception:
    pass  # never crash on log setup failure

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


def _increment_download_count(fmt=None, ip=None):
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
            recent = data.get('recent', [])
            recent.append({'ts': int(time.time()), 'ip': ip or '', 'fmt': fmt or 'other'})
            data['recent'] = recent[-10:]
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
            recent = data.get('recent', [])
            recent.append({'ts': int(time.time()), 'ip': ip or '', 'fmt': fmt or 'other'})
            data['recent'] = recent[-10:]
            with open(_DOWNLOAD_COUNT_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
    if count % 100 == 0:
        _send_milestone_email(count)


def _smtp_send(to: str, subject: str, body: str, from_addr: str = 'CheapCAD Tools <info@cheapcadtools.com>'):
    """Send email via Resend API — delegates to cct_common.resend_email.send.

    Returns (True, '') on success or (False, error_message) on failure.
    Reads the API key from the RESEND_API_KEY environment variable (set in
    Render) instead of the hardcoded key this used to carry — rotate that
    key since it was committed to git history.
    """
    return _cc_send_email(to, subject, body, from_addr=from_addr,
                          user_agent='CCT-PulleyApp/1.0', logger=app.logger)


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
from exporters.dxf_exporter import (
    generate_dxf, generate_belt_dxf, generate_belt_dxf_dual,
    generate_rim_layer_dxf, generate_combined_layout_dxf,
)
from exporters.step_exporter import (
    generate_pulley_stl, generate_pulley_stl_preview,
    generate_drive_stl_preview, _build_belt_mesh,
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
# ── CAD addin mirroring ────────────────────────────────────────────────────────
# Each desktop CAD addin (Fusion, SolidWorks, FreeCAD) writes its connection flag
# and watch directory to the shared config file when it starts. Downloads are
# mirrored into every connected addin's watch folder so the addin auto-imports
# them — and so the client can skip the redundant browser download.
# cct_common.addin_mirror's defaults (config path, target keys) already match
# what this app used locally, so no overrides are needed here.

def _mirror_to_addins(content: bytes, filename: str) -> bool:
    """Copy a download into every connected CAD addin's watch folder — see
    cct_common.addin_mirror.mirror_to_addins for the implementation.

    Automated tests must receive the real browser download — a CAD addin left
    connected on the dev machine would otherwise turn /download/* into a 204
    and fail the download/embedding tests. Skip mirroring under the test flag;
    normal local dev (QUEUE_DISABLED, no TESTING) still mirrors so a connected
    addin auto-imports the download.
    """
    from flask import current_app
    skip = bool(os.environ.get('PULLEY_TESTING') or current_app.config.get('TESTING')
                or charges.is_internal())   # a file headed into a download-window zip
    return _cc_mirror_to_addins(content, filename, skip=skip)


app = Flask(__name__,
            template_folder=os.path.join(_base_dir, 'templates'),
            static_folder=os.path.join(_base_dir, 'static'))
# ─── Cloudflare Worker proxy support ──────
# Forwarding headers (visitor IP, host, protocol) are believed only from our
# Cloudflare Worker, which proves itself with EDGE_SECRET; a request straight
# to the *.run.app address gets only what Cloud Run's front end adds. A
# forged X-Forwarded-Host could otherwise point sign-in emails at another
# site — see edge_proxy.py. PROXY_HOPS / EDGE_HOPS set the X-Forwarded-For
# entries trusted on each path (defaults 1 and 2).
from edge_proxy import EdgeAwareProxyFix
app.wsgi_app = EdgeAwareProxyFix(app.wsgi_app)
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

# ─── cct_common.job_queue: configure + start background threads ───────────────
# log_dir matches exactly what exporters/job_queue.py used to resolve itself
# (same PULLEY_LOG_DIR env var, same default). Background threads (queue
# processor + session/job cleanup) are no longer started as an import side
# effect — call explicitly, unconditionally, matching the original's
# always-on behavior (it started them the moment anything imported the
# module, tests included).
_cc_job_queue_configure(log_dir=_LOG_DIR)
_cc_job_queue_start_threads()

# ─── Startup: clear any stale queue session left by a previous process ────────
if not os.environ.get('PULLEY_TESTING'):
    clear_stale_on_startup()
# ──────────────────────────────────────────

# ─── HTTP caching ─────────────────────────
# Routes whose output is fully determined by query parameters.
_CACHEABLE_PREFIXES = (
    '/download/',
    '/api/belt', '/api/od', '/api/spec',
    '/api/preview-stl', '/api/preview', '/api/belt-preview',
)
_CACHE_MAX_AGE = 3600   # 1 hour; Cloudflare + browser cache

# Conditional-GET caching, admin CORS, and the download-signal cookie are
# registered via cct_common.flask_caching below (see the register_*() calls
# near the bottom of this file, after `app` is created).


@app.before_request
def _count_request():
    _increment_request_count()


@app.route('/api/subscribers/<path:_>', methods=['OPTIONS'])
def _subscribers_cors_preflight(_):
    r = Response('', 204)
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return r


@app.after_request
def _track_download(response):
    """Count every successful /download/* response.

    Cache headers (ETag / Cache-Control / no-store) for this and the other
    cacheable prefixes are now stamped by cct_common.flask_caching's
    register_etag_caching — see its registration near the bottom of this
    file — not here.
    """
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
            _client_ip = (
                request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                or request.remote_addr
                or ''
            )
            _increment_download_count(_fmt, ip=_client_ip)
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
    """Parse bore diameter from request args, clamped to minimum 1 mm. A
    splined bore is its spline's minor diameter, whatever the bore field says."""
    sp = _spline_of(args, key[:-len('bore')])
    if sp:
        return sp['bore']
    try:
        return max(1.0, float(args.get(key, default)))
    except (ValueError, TypeError):
        return default


def _spline_of(args, prefix=''):
    """The pulley's splined bore (cct_common.splines) as its fields — JSON for
    the STEP worker, rebuilt by the exporters — or None unless Bore Shape is
    Spline: {prefix}bore_shape=spline, then {prefix}spline_type straight
    (spline_n, spline_minor, spline_major, spline_width: ISO 14's N x d x D x B)
    or involute (spline_m, spline_z, spline_pa, spline_root: ISO 4156) or hex
    (spline_af across flats, spline_series metric / inch, and spline_rounded=1
    with spline_ac across corners for rounded stock). A size the standard
    can't make raises ValueError, which the routes answer as 400.

    Besides the spline's own fields the dict carries (ADR-017) `bore` — the
    hole's minor diameter at the default fit, what Bore Diameter shows —
    `print`, the pulley's print compensation, which the printed parts (STL)
    add to the bore and take off the shaft, and `retainer`: the DIN 471 rings
    (_ring_faces: top, bottom or both; sized to the shaft's outside diameter)
    and their counterbores, deeper by a splined washer ({prefix}spline_washer=1)."""
    if args.get(f'{prefix}bore_shape') != 'spline':
        return None
    import dataclasses
    from cct_common import splines
    g = lambda k, d: args.get(f'{prefix}spline_{k}', d)     # noqa: E731
    try:
        if g('type', 'straight') == 'involute':
            sp = splines.involute(float(g('m', 1.0)), int(float(g('z', 20))),
                                  float(g('pa', 30)), g('root', 'flat'))
        elif g('type', 'straight') == 'hex':
            ac = float(g('ac', 0)) if g('rounded', '0') == '1' else None
            sp = splines.hex_bar(float(g('af', 12.7)), ac, g('series', 'metric'))
        else:
            sp = splines.straight(int(float(g('n', 6))), float(g('minor', 23)),
                                  float(g('major', 26)), float(g('width', 6)))
    except (TypeError, ValueError) as e:
        raise ValueError(f'Spline: {e}') from None
    out = dataclasses.asdict(sp)
    out['bore'] = round(splines.hole(sp).inner, 4)
    out['print'] = max(0.0, _safe_float(args.get(f'{prefix}print_extra'), 0.0))
    out['retainer'] = _retainer_of(args, prefix, sp)
    return out


def _ring_cover(args, prefix, face):
    """(thickness, joined) of a flange or plate over the ring's face, or
    (0, False): the counterbore is measured from the part's outer face, so a
    flange there carries the first `thickness` of it (ADR-017). None with a
    hub on the top face (the hub stands through the top flange) or spokes
    (the flange stops at the spoke rim). `joined`: a printed flange unioned
    with the pulley (the bottom one; the top one unless printed separate)."""
    if args.get(f'{prefix}flange_enabled') != '1':
        return 0.0, False
    hub_on = (_safe_float(args.get(f'{prefix}hub_od'), 0.0) > 0
              and _safe_float(args.get(f'{prefix}hub_height'), 0.0) > 0)
    # the spokes as sent, not _parse_spoke_params: its spoke fit needs the
    # bore, which needs this (_spline_of -> _retainer_of) — a recursion
    sp_en = args.get(f'{prefix}spokes_enabled', '0') == '1'
    rim_depth = max(0.0, _safe_float(args.get(f'{prefix}spokes_rim_depth'), 2.0))
    if (face == 'top' and hub_on) or (sp_en and rim_depth > 0):
        return 0.0, False
    fp = _parse_flange_params(args, prefix)
    if not fp['flange_3dprint']:
        return max(0.3, fp['plate_height_mm']), False
    joined = face == 'bottom' or not fp['top_separate']
    return max(0.1, fp['flange_height_mm']), joined


def _ring_faces(args, prefix):
    """The faces with a retaining ring: {prefix}spline_ring_top / _bottom = 1
    (the Spline card, either or both), or the older single choice
    {prefix}spline_ring = top / bottom / none (saved designs and links)."""
    if f'{prefix}spline_ring_top' in args or f'{prefix}spline_ring_bottom' in args:
        return [f for f in ('top', 'bottom') if args.get(f'{prefix}spline_ring_{f}') == '1']
    old = args.get(f'{prefix}spline_ring', 'none')
    return [old] if old in ('top', 'bottom') else []


def _ring_face_z(args, prefix, bare_lo, bare_hi):
    """(z bottom, z top) of the part's outer faces at the bore, from the bare
    pulley's (its raised hub and all) and any flange over a face, flat part
    only (a metal plate's bent lip stands higher but isn't where a ring sits)."""
    return (bare_lo - _ring_cover(args, prefix, 'bottom')[0],
            bare_hi + _ring_cover(args, prefix, 'top')[0])


def _counterbore_faces(args, prefix, faces):
    """The ringed faces that get a counterbore: all of them unless the Spline
    card's "Make counterbore" is off there ({prefix}spline_cb_top / _bottom =
    0). Designs from before the choice had one on every ringed face."""
    return [f for f in faces if args.get(f'{prefix}spline_cb_{f}', '1') != '0']


def _retainer_of(args, prefix, sp):
    """The retaining rings (cct_common.retaining_rings) on either or both
    faces — one size, the shaft's — or None: `faces`, `cb_faces` (the ones
    with a counterbore; without one the washer and ring sit on the face), and
    per face the `cover` of a flange over it and whether that flange is
    `cover_joined`."""
    faces = _ring_faces(args, prefix)
    if not faces:
        return None
    from cct_common import retaining_rings as rr
    ring = rr.for_spline(sp)          # a spline's outside diameter, a hex bar's flats
    washer = rr.WASHER_THICKNESS if args.get(f'{prefix}spline_washer') == '1' else 0.0
    cb = rr.counterbore(ring, washer=washer)
    covers = {f: _ring_cover(args, prefix, f) for f in ('top', 'bottom')}
    cb_faces = _counterbore_faces(args, prefix, faces)
    stacks = {f: rr.stack(ring, washer=washer, counterbore=f in cb_faces) for f in faces}
    return {'faces': faces, 'cb_faces': cb_faces,
            # where the washer, ring and groove sit, outward from each face (cct_common)
            'stack': {f: {'washer': st.washer, 'ring': st.ring, 'groove': st.groove,
                          'shaft_end': st.shaft_end} for f, st in stacks.items()},
            'cover': {f: c[0] for f, c in covers.items()},
            'cover_joined': {f: c[1] for f, c in covers.items()},
            'ring': ring.label(), 'mcmaster': ring.mcmaster, 'd1': ring.d1,
            'd2': ring.d2, 'm': ring.m, 's': ring.s, 'n': ring.n, 'a': ring.a, 'd4': ring.d4,
            'cb_d': cb.diameter, 'cb_depth': cb.depth,
            'washer_od': cb.washer_od, 'washer_t': cb.washer_t}


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
    # With no queue (compiled out, local/desktop, testing) the page opens straight
    # away. Empty session_id tells the JS not to replace the URL.
    if queue_off():
        return render_template(
            'index.html',
            session_id='',
            families=FAMILIES,
            clearance_presets=CLEARANCE_PRESETS,
            backlash_presets=BACKLASH_PRESETS,
            belt_families=sorted(BELT_FAMILIES),
            app_version=APP_VERSION,
            build_time=BUILD_TIME,
            cct_schema_version=CCT_SCHEMA_VERSION,
            screw_sizes=_screw_size_list(),
        )

    session_id = request.args.get('session_id')

    # If no session_id, create one
    if not session_id:
        session = create_session()
        session_id = session['session_id']
        # If not immediately active, queue user
        if not session.get('is_active'):
            return redirect(f'/tools/pulleys/queue?session_id={session_id}')
        # Else fall through to render page with active session

    # Verify session is still active
    status = get_session_status(session_id)
    if not status.get('is_active'):
        # Session expired or queued - redirect to queue page
        return redirect(f'/tools/pulleys/queue?session_id={session_id}')

    # Keep session alive
    heartbeat(session_id)

    return render_template(
        'index.html',
        session_id=session_id,
        families=FAMILIES,
        clearance_presets=CLEARANCE_PRESETS,
        backlash_presets=BACKLASH_PRESETS,
        belt_families=sorted(BELT_FAMILIES),
        app_version=APP_VERSION,
        build_time=BUILD_TIME,
        cct_schema_version=CCT_SCHEMA_VERSION,
        screw_sizes=_screw_size_list(),
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
            **_step_screw_kw(args, pfx),
            captured_nut=cn, flat_depth_mm=fd,
            keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            spline=_spline_of(args, pfx),
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
        step_bytes = _run_ss_worker(dict(kw, export_type='pulley', color=_step_color(pfx)))

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


@app.route('/api/splines')
def api_splines():
    """The Bore Shape menu's spline choices (cct_common.splines): ISO 14's
    light and medium series, and ISO 4156's modules per pressure angle."""
    from cct_common import splines
    return jsonify(splines.presets())


@app.route('/api/spline')
def api_spline():
    """One pulley's splined bore at the default fit (pulley=1|2, the Bore
    Shape keys): the hole and the sample shaft's diameters, the fit's source,
    and the retaining ring with its counterbore and washer — the Size card's
    info line. 400 for a size the standard can't make."""
    from cct_common import splines
    pfx = 'p2_' if request.args.get('pulley') == '2' else ''
    try:
        sp = _spline_of(request.args, pfx)
    except ValueError as e:
        return _api_error(str(e))
    if not sp:
        return jsonify(None)
    s = _as_spline_obj(sp)
    hole, shaft = splines.hole(s), splines.shaft(s)
    return jsonify({'bore': sp['bore'], 'hole_major': round(hole.outer, 4),
                    'shaft_minor': round(shaft.inner, 4), 'shaft_major': round(shaft.outer, 4),
                    'fit': splines.fit_source(s), 'label': s.label(), 'retainer': sp['retainer']})


def _as_spline_obj(sp):
    from exporters.step_exporter import _as_spline
    return _as_spline(sp)


def _nut_counterbore_problems(args, pfx, who, sp):
    """A captured nut and the ring's top counterbore on the hub's top face
    (geometry/nut_counterbore.py): the clauses this design breaks, the same
    check the STEP worker refuses on."""
    from geometry.nut_counterbore import problems
    hub_od, hub_h, sd, sc, cn, fd, _kw_w, kw_h = _parse_hub_params(args, pfx)
    try:
        ss = _set_screw(args, pfx)
    except ValueError:
        return []
    bore_mm = _parse_stl_params(args, '2' if pfx else '1')[3]
    return problems(bore_mm=bore_mm, hub_od_mm=hub_od, hub_height_mm=hub_h, screw_count=sc,
                    captured_nut=cn, screw_dia_mm=sd, spline=sp, flat_depth_mm=fd, keyway_h_mm=kw_h,
                    nut=ss.nut if ss else None, hole=None, who=who)   # the nominal hole, as the STEP cuts it


def _counterbore_warnings(args, pfx, who, rt, root_d, three_d):
    """Where the ring's counterbore (ADR-017) can't do its job: too wide for
    the material around it. (A flange or plate over its face has the
    counterbore through it — _ring_cover.)"""
    out, cb = [], rt['cb_d']
    hub_od = _safe_float(args.get(f'{pfx}hub_od'), 0.0) if three_d else 0.0
    hub_h = _safe_float(args.get(f'{pfx}hub_height'), 0.0) if three_d else 0.0
    spokes = three_d and _parse_spoke_params(args, pfx)[0]
    for face in rt['cb_faces']:
        if face == 'top' and hub_od > 0 and hub_h > 0:
            wall, what = hub_od, f'the Ø{hub_od:g} mm hub'
        elif spokes:
            sp_hub = _parse_spoke_params(args, pfx)[1]
            wall, what = sp_hub, f'the Ø{sp_hub:g} mm spoke hub'
        else:
            wall, what = root_d, f'the tooth root (Ø{root_d:.2f} mm)'
        if cb > wall - 2.0:
            out.append(f'{who}the {face} ring\'s counterbore is Ø{cb:g} mm, leaving under 1 mm to '
                       f'{what}: pick a smaller spline, or no ring on that face.')
    return out


SPLINE_WALL = 1.0     # mm of material round a spline's reach or a ring counterbore


def _spline_fix(args, pfx, n, sp_obj, root_d, three_d):
    """Auto-fix for a splined bore the part can't hold (1 mm of wall round the
    spline's reach and the ring's counterbore): a bigger Hub OD, up to the
    tooth root less 1 mm a side; failing that, the largest smaller spline of
    the same kind (an ISO 14 size, or fewer involute teeth) that fits, with
    the Hub OD it needs. Returns ({element id: value}, [change text]) — empty
    when nothing needs fixing or nothing smaller fits."""
    from cct_common import splines as _spl
    hub_od = _safe_float(args.get(f'{pfx}hub_od'), 0.0) if three_d else 0.0
    hub_h = _safe_float(args.get(f'{pfx}hub_height'), 0.0) if three_d else 0.0
    hub_on = hub_od > 0 and hub_h > 0
    spokes = _parse_spoke_params(args, pfx)[:2] if three_d else (False, 0.0)
    body = spokes[1] if spokes[0] and spokes[1] > 0 else root_d   # the material round the bore
    hub_max = math.floor((root_d - 2 * SPLINE_WALL) * 2) / 2
    wall = 2 * SPLINE_WALL

    def needs(cand):
        """(Hub OD it needs, body Ø it needs) for a candidate spline."""
        reach = _spl.hole(cand).outer
        rt = _retainer_of(args, pfx, cand)
        hub_need = body_need = reach + wall
        for face in (rt['cb_faces'] if rt else []):
            if face == 'top' and hub_on:
                hub_need = max(hub_need, rt['cb_d'] + wall)
            else:
                body_need = max(body_need, rt['cb_d'] + wall)
        return math.ceil(hub_need * 2 - 1e-9) / 2, body_need

    def fits(cand):
        hub_need, body_need = needs(cand)
        return body_need <= body + 1e-9 and (not hub_on or hub_need <= hub_max)

    hub_need, body_need = needs(sp_obj)
    if body_need <= body + 1e-9 and (not hub_on or hub_od >= hub_need - 1e-9):
        return {}, []                                        # nothing to fix
    who = f'Pulley {n}: ' if pfx else ''
    if fits(sp_obj):                                         # only the hub is short
        return ({f'hub{n}_od': hub_need},
                [f'{who}hub OD {hub_od:g} → {hub_need:g} mm (round the spline and its ring)'])

    # a smaller spline of the same kind
    if sp_obj.kind == 'hex':
        pre = _spl.presets()['hex']
        inch = sp_obj.series == 'inch'
        sizes = [af for _name, af in pre['inch']] if inch else [float(af) for af in pre['metric']]
        for af in sorted(sizes, reverse=True):
            if af >= sp_obj.minor - 1e-6:
                continue
            cand = _spl.hex_bar(af, None, sp_obj.series)
            if fits(cand):
                fix = {f'spline{n}_hex_preset': f"{'inch' if inch else 'metric'}:{af:g}"}
                change = [f'{who}hex {sp_obj.minor:g} → {af:g} mm across flats '
                          f'(the part can\'t hold the larger one)']
                break
        else:
            return {}, []
    elif sp_obj.kind == 'straight':
        pre = _spl.presets()['straight']
        rows = sorted({tuple(r) for r in pre['light'] + pre['medium']}, key=lambda r: (r[2], r[1]),
                      reverse=True)
        for N, d, D, B in rows:
            if D >= sp_obj.major and d >= sp_obj.minor:
                continue
            cand = _spl.straight(N, d, D, B)
            if fits(cand):
                fix = {f'spline{n}_preset': f'{N},{d},{D},{B}'}
                change = [f'{who}spline {sp_obj.label()} → {cand.label()} (the part can\'t hold the larger one)']
                break
        else:
            return {}, []
    else:
        root = sp_obj.root
        for z in range(sp_obj.n - 1, 5, -1):
            try:
                cand = _spl.involute(sp_obj.module, z, sp_obj.pressure, root)
            except ValueError:
                continue
            if fits(cand):
                fix = {f'spline{n}_z': z}
                change = [f'{who}spline teeth {sp_obj.n} → {z} (the part can\'t hold the larger one)']
                break
        else:
            return {}, []
    new_hub = needs(cand)[0]
    if hub_on and hub_od < new_hub - 1e-9:
        fix[f'hub{n}_od'] = new_hub
        change.append(f'{who}hub OD {hub_od:g} → {new_hub:g} mm')
    return fix, change


def _set_screw_slot(args, pfx, n, who, root_d):
    """A set-screw hole wider than both the hub wall it crosses and the bore
    radius it opens into breaks out of the hub inside and out at once: a slot,
    not a hole (Sprocket's ADR-036, the owner 2026-10-02; bug hunt). Both
    conditions: wider than the wall alone (an M3 in a spoke-clamped Ø16 hub) is
    a sound hole. Width is the hole's diameter, or across the flats of a hex
    hole. Returns ([warning], {element id: value}, [change text]); the fix is a
    Hub OD that closes the rim (and the spokes' hub, which the hub follows),
    else the largest smaller screw of the same kind that fits, else none. Never
    the bore, which must fit the shaft."""
    from geometry import set_screw as _ss
    hub_od = _safe_float(args.get(f'{pfx}hub_od'), 0.0)
    if hub_od <= 0 or _safe_float(args.get(f'{pfx}hub_height'), 0.0) <= 0:
        return [], {}, []
    try:
        screw = _set_screw(args, pfx)
    except ValueError:
        return [], {}, []
    if screw is None:
        return [], {}, []
    r = _spoke_room(args, pfx)[1] / 2                     # the bore's reach, as a radius
    width = lambda s: s.hole.get('flats') or s.hole.get('diameter') or 0.0
    slot = lambda w, hub: w > hub / 2 - r + 1e-9 and w > r + 1e-9
    w = width(screw)
    if not slot(w, hub_od):
        return [], {}, []
    need = math.ceil(4 * (r + w) - 1e-9) / 2
    warning = (f'{who}a Ø{w:.2f} mm {screw.size} set-screw hole is wider than both the '
               f'{hub_od / 2 - r:.2f} mm of hub wall it crosses and the {r:.2f} mm bore radius it '
               f'opens into, so it cuts a slot, not a hole — use a smaller screw or a larger bore, '
               f'or a Hub OD of at least Ø{need:g} mm.')
    spokes_on = args.get(f'{pfx}spokes_enabled') == '1'
    hub_max = math.floor((root_d - 2 * SPLINE_WALL) * 2) / 2
    if need <= hub_max:
        trial = dict(args.items(), **{f'{pfx}hub_od': need, f'{pfx}spokes_hub_od': need})
        fit = _spoke_fit(trial, pfx) if spokes_on else None
        if fit is None or (fit.possible and fit.fitted['hub_od'] >= need - 1e-6):
            fix = {f'hub{n}_od': need}
            if spokes_on:
                fix[f'spokes{n}_hub_od'] = need
            return [warning], fix, [f'{who}hub OD {hub_od:g} → {need:g} mm '
                                    f'(closes the {screw.size} set-screw hole)']
    if screw.size in _ss.METRIC + _ss.INCH:
        group = _ss.METRIC if screw.size in _ss.METRIC else _ss.INCH
        for size in reversed(group[:group.index(screw.size)]):
            smaller = _ss.parse(dict(args.items(), **{f'{pfx}hub_screw_size': size}), pfx)
            if smaller is not None and not slot(width(smaller), hub_od):
                return [warning], {f'hub{n}_screw_size': size}, [
                    f'{who}set screw {screw.size} → {size} (the hub can\'t close a larger one)']
    return [warning], {}, []


def _captured_nut_hub(args, pfx, n, who, root_d):
    """A captured nut in a hub too narrow for it: no wall left round its screw
    behind a key slot — STEP can't make it (geometry/captured_nut_hub.py, the same check the STEP worker
    refuses on). Returns ([warning], {element id: value},
    [change text]); the fix is the Hub OD the nut needs (and the spokes' hub,
    which the hub follows), else none: holding the screw another way is a
    choice of screw type, not a number."""
    from geometry import captured_nut_hub as _cnh
    hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(args, pfx)
    try:
        ss = _set_screw(args, pfx)
    except ValueError:
        return [], {}, []
    bore_mm = _parse_stl_params(args, '2' if pfx else '1')[3]
    nut, hole = (ss.nut, dict(ss.hole)) if ss else (None, None)   # what the STEP worker sends
    sp = _spline_of(args, pfx) if _hex_bore(args, pfx) else None
    warn = _cnh.problems(bore_mm=bore_mm, hub_od_mm=hub_od, hub_height_mm=hub_h, screw_count=sc,
                         captured_nut=cn, screw_dia_mm=sd, flat_depth_mm=fd, keyway_w_mm=kw_w,
                         keyway_h_mm=kw_h, spline=sp, nut=nut, hole=hole, who=who)
    if not warn:
        return [], {}, []
    kh = kw_h if kw_w > 0 and kw_h > 0 else 0.0
    need = math.ceil(_cnh.min_hub_od(bore_mm=bore_mm, screw_dia_mm=sd, nut=nut, keyway_h_mm=kh,
                                     hole=hole) * 2 - 1e-9) / 2
    if need > math.floor((root_d - 2 * SPLINE_WALL) * 2) / 2:
        return warn, {}, []
    spokes_on = args.get(f'{pfx}spokes_enabled') == '1'
    if spokes_on:
        fit = _spoke_fit(dict(args.items(), **{f'{pfx}hub_od': need, f'{pfx}spokes_hub_od': need}), pfx)
        if not fit.possible or fit.fitted['hub_od'] < need - 1e-6:
            return warn, {}, []
    fix = {f'hub{n}_od': need}
    if spokes_on:
        fix[f'spokes{n}_hub_od'] = need
    return warn, fix, [f'{who}hub OD {hub_od:g} → {need:g} mm (room for the captured nut)']


def _flange_rim_fix(args, pfx, n, who, need):
    """Auto-fix for a flange sitting too little on its spoke rim (ADR-021): the
    settings whose rim AS BUILT is at least `need` mm. The spoke fit trims Rim
    Depth when hub and rim leave the openings too little room, so a deeper Rim
    Depth alone comes back trimmed (2026-10-02: the fix offered the depth
    already entered, for ever); the room has to come from the spokes' hub,
    which the 3D hub follows, down to SPLINE_WALL round the bore's reach (as
    _spline_fix asks). Checked with the real fit. Returns ({element id: value},
    [change text]) — empty when no spoke layout leaves the flange room here."""
    from geometry import spoke_fit as sf
    rim0 = max(0.0, _safe_float(args.get(f'{pfx}spokes_rim_depth'), 2.0))
    hub0 = max(0.0, _safe_float(args.get(f'{pfx}spokes_hub_od'), 0.0))
    rim = max(rim0, math.ceil(need * 10 - 1e-9) / 10)
    r_root, reach = _spoke_room(args, pfx)
    # the fit leaves Rim Depth as asked while r_root - rim - hub/2 >= MIN_WEB + MIN_OPENING
    hub_room = math.floor(4 * (r_root - rim - sf.MIN_WEB - sf.MIN_OPENING) + 1e-9) / 2
    hub = min(hub0, hub_room)
    if hub < hub0 and hub < reach + 2 * SPLINE_WALL - 1e-9:
        return {}, []
    fit = _spoke_fit(dict(args.items(), **{f'{pfx}spokes_rim_depth': rim,
                                           f'{pfx}spokes_hub_od': hub}), pfx)
    if not fit.possible or fit.fitted['rim_depth'] < need - 1e-6:
        return {}, []
    fix, change = {}, []
    if rim != rim0:
        fix[f'spokes{n}_rim_depth'] = rim
        change.append(f'{who}spoke rim depth {rim0:g} → {rim:g} mm')
    if hub != hub0:
        fix[f'spokes{n}_hub_od'] = hub
        if _safe_float(args.get(f'{pfx}hub_height'), 0.0) > 0:
            fix[f'hub{n}_od'] = hub                       # the 3D hub follows the spokes' hub
        change.append(f'{who}spokes hub OD {hub0:g} → {hub:g} mm (room for the rim)')
    return fix, change


@app.route('/api/dimensions')
def api_dimensions():
    """The Dimensions panel under the 2D view (the Sprocket app's): each
    pulley's diameters and widths, the drive's figures for two pulleys, and
    warnings where a published rule is broken (geometry/belt_specs.py), with
    an Auto-fix for the ones a setting can clear.

    Takes the preview's query plus belt_height, clearance_height and
    feature_build (flanges only count with 3D features on)."""
    try:
        return jsonify(_dimensions(request.args))
    except (ValueError, TypeError, KeyError) as e:
        return jsonify({'error': str(e)}), 400


def _dimensions(args):
    """The Dimensions panel's figures, warnings and Auto-fix as a dict (the
    page's /api/dimensions and the agents' /api/v1/check). Raises ValueError."""
    from geometry import belt_specs as bs
    from exporters.flange_exporter import _pulley_radii
    family, pitch = args.get('family', 'HTD'), args.get('pitch', '5M')
    key = _resolve_key(family, pitch)
    if key is None or key not in PULLEY_SPECS:
        raise ValueError(f'Unknown profile {family}/{pitch}')
    spec = PULLEY_SPECS[key]
    dual = args.get('dual') == 'true'
    belt_w = max(1.0, _safe_float(args.get('belt_height'), 10.0))
    extra_w = max(0.0, _safe_float(args.get('clearance_height'), 0.0))
    three_d = args.get('feature_build', '1') == '1'

    pulleys, warnings, changes, fix_set = [], [], [], {}
    for n, pfx in ((1, ''), (2, 'p2_')) if dual else ((1, ''),):
        who = f'Pulley {n}: ' if dual else ''
        _, _, teeth, _, _, cl_mm, _, pr_ex = _parse_stl_params(args, str(n))
        pd = spec['pitch'] * teeth / math.pi
        od = getOuterDiameter(teeth, spec['pitch'], spec['pitch_line_diff'])
        R_OD, _, tooth_ht = _pulley_radii(family, pitch, teeth, cl_mm, pr_ex)
        face_w = belt_w + extra_w
        p = {'teeth': teeth, 'pitch_diameter': pd, 'outside_diameter': od,
             'outside_diameter_built': 2 * R_OD, 'root_diameter': od - 2 * tooth_ht,
             'tooth_height': tooth_ht, 'pitch_line_diff': spec['pitch_line_diff'],
             'belt_width': belt_w, 'face_width': face_w, 'approx': {}}

        # A splined bore: its minor diameter is the bore, its major the
        # reach every wall is measured to (Sprocket's ADR-021) — both the
        # hole's at the default fit (ADR-017), the sample shaft's beside.
        sp = _spline_of(args, pfx)
        if sp:
            from cct_common import splines as _spl
            sp_obj = _as_spline_obj(sp)
            hole, shaft = _spl.hole(sp_obj), _spl.shaft(sp_obj)
            reach = round(hole.outer, 4)
            p.update(spline=sp_obj.label(), spline_minor=sp['bore'], spline_major=reach,
                     spline_fit=_spl.fit_source(sp_obj),
                     shaft_minor=round(shaft.inner, 4), shaft_major=round(shaft.outer, 4))
            for which, (tag, src) in _spl.basis(sp_obj).items():
                if tag == _spl.EST:
                    p['approx'][f'spline_{which}'] = src
            root_d = od - 2 * tooth_ht
            if reach > root_d - 2.0:
                warnings.append(f'{who}the spline reaches Ø{reach:g} mm, within 1 mm of the '
                                f'tooth root (Ø{root_d:.2f} mm): pick a smaller spline or more teeth.')
            hub_od = _safe_float(args.get(f'{pfx}hub_od'), 0.0)
            if three_d and hub_od > 0 and reach > hub_od - 2.0:
                warnings.append(f'{who}the spline reaches Ø{reach:g} mm, leaving under 1 mm '
                                f'of wall in the Ø{hub_od:g} mm hub.')
            rt = sp['retainer']
            if rt:
                p.update(ring=rt['ring'], ring_mcmaster=rt['mcmaster'] or None,
                         ring_face=' and '.join(rt['faces']))
                if rt['cb_faces']:
                    p.update(counterbore_d=rt['cb_d'], counterbore_depth=rt['cb_depth'])
                if rt['washer_t'] > 0:
                    p.update(washer_od=rt['washer_od'], washer_t=rt['washer_t'])
                warnings += _counterbore_warnings(args, pfx, who, rt, root_d, three_d)
                if three_d:
                    nc = _nut_counterbore_problems(args, pfx, who, sp)
                    if nc:
                        warnings += nc
                        fix_set[f'spline{n}_cb_top'] = False
                        changes.append(f'{who}no top counterbore (the pocket of the captured nut opens there)')
            sp_fix, sp_changes = _spline_fix(args, pfx, n, sp_obj, root_d, three_d)
            fix_set.update(sp_fix)
            changes += sp_changes

        if three_d:
            ss_warn, ss_fix, ss_changes = _set_screw_slot(args, pfx, n, who, od - 2 * tooth_ht)
            cn_warn, cn_fix, cn_changes = _captured_nut_hub(args, pfx, n, who, od - 2 * tooth_ht)
            hub_key = f'hub{n}_od'
            if hub_key in ss_fix and hub_key in cn_fix:   # both widen the hub: the wider one does
                if cn_fix[hub_key] > ss_fix[hub_key]:
                    ss_fix, ss_changes = {}, []
                else:
                    cn_fix, cn_changes = {}, []
            warnings += ss_warn + cn_warn
            fix_set.update(ss_fix)
            fix_set.update(cn_fix)
            changes += ss_changes + cn_changes

        flanged = three_d and args.get(f'{pfx}flange_enabled') == '1'
        p['flanged'] = flanged
        if flanged:
            fp = _parse_flange_params(args, pfx)
            spokes = _parse_spoke_params(args, pfx)[0]
            # Every flange flares from the tooth tips (R_OD); spokes only move its
            # inner edge. (A printed flange on a spoked pulley used to flare from
            # the groove bottom — fixed 2026-10-02.)
            r_ref = R_OD
            flange_od = 2 * (r_ref + fp['rim_radius_mm'])
            reach = flange_od / 2 - R_OD
            need = bs.min_flange_height(key, spec)
            p.update(flange_od=flange_od, flange_reach=reach, flange_min_height=need.value)
            if need.approximate:
                p['approx']['flange_min_height'] = need.source
            if reach < need.value - 1e-6:
                rim_id = f'flange{n}_rim_radius'
                new_rim = math.ceil((fp['rim_radius_mm'] + need.value - reach) * 10) / 10
                warnings.append(f'{who}the flange reaches {reach:.2f} mm past the OD; '
                                f'{need.source} asks for at least {need.value:.2f} mm'
                                + (' (≈)' if need.approximate else '') + '.')
                fix_set[rim_id] = new_rim
                changes.append(f'{who}flange rim radius {fp["rim_radius_mm"]:g} → {new_rim:g} mm')
            raw_angle = _safe_float(args.get(f'{pfx}flange_angle'), 15.0)
            lo, hi = bs.FLANGE_ANGLE_RANGE
            if not lo <= raw_angle <= hi:
                warnings.append(f'{who}flange angle {raw_angle:g}° is outside ISO\'s '
                                f'{lo:g}–{hi:g}°; it is built at {fp["flange_angle_deg"]:g}°.')
                fix_set[f'flange{n}_angle'] = fp['flange_angle_deg']
                changes.append(f'{who}flange angle {raw_angle:g}° → {fp["flange_angle_deg"]:g}°')
            # The flange must sit on the pulley: reach inward past the groove
            # bottom by max(3 mm, h) (belt_specs.min_flange_overlap). On a spoked
            # pulley it stops at the spoke rim's inner face, so its overlap is the
            # rim depth as built (after the spoke fit); without spokes it runs in
            # to the hub or bore and always sits on the pulley.
            sp_on, _sp_hub, sp_rim = _parse_spoke_params(args, pfx)[:3]
            if sp_on and sp_rim > 0:
                ov_need = bs.min_flange_overlap(key, spec)
                p.update(flange_overlap=sp_rim, flange_min_overlap=ov_need.value)
                p['approx']['flange_min_overlap'] = ov_need.source
                if sp_rim < ov_need.value - 1e-6:
                    rim_fix, rim_changes = _flange_rim_fix(args, pfx, n, who, ov_need.value)
                    deeper, smaller = (f'spokes{n}_rim_depth' in rim_fix, f'spokes{n}_hub_od' in rim_fix)
                    how = ('no spoke layout leaves it that much rim on this pulley; turn Spokes off, '
                           'or use a smaller bore or a bigger pulley' if not rim_fix else
                           'widen the spokes\' Rim Depth' + (' and make it room with a smaller Spokes '
                                                            'Hub OD' if smaller else '') if deeper else
                           'the spokes\' openings take the room the rim needs; give it room with a '
                           'smaller Spokes Hub OD')
                    warnings.append(f'{who}the flange sits only {sp_rim:.2f} mm inward of the groove '
                                    f'bottom (on the spoke rim); it needs at least {ov_need.value:.2f} mm '
                                    f'(≈ the larger of 3 mm and the flange height) — {how}.')
                    fix_set.update(rim_fix)
                    changes += rim_changes

        need_w = bs.min_face_width(key, belt_w, flanged)
        if need_w is not None:
            p['face_width_min'] = need_w.value
            if face_w < need_w.value - 1e-6:
                kind = 'a flanged' if flanged else 'an unflanged'
                warnings.append(f'{who}the pulley face is {face_w:g} mm wide; {need_w.source} '
                                f'asks for at least {need_w.value:g} mm on {kind} pulley '
                                f'for a {belt_w:g} mm belt.')
                # Rounded before the ceil: 14.3 - 10 is 4.300000000000001 in
                # floats, which would ceil to 4.4 where the page says 4.3.
                new_extra = math.ceil(round((need_w.value - belt_w) * 10, 6)) / 10
                if new_extra > fix_set.get('clearance_height', -1):
                    fix_set['clearance_height'] = new_extra
        pulleys.append(p)

    if 'clearance_height' in fix_set:
        changes.append(f'clearance height {extra_w:g} → {fix_set["clearance_height"]:g} mm')

    drive = None
    if dual:
        t1, t2 = pulleys[0]['teeth'], pulleys[1]['teeth']
        C = max(0.0, _safe_float(args.get('center_distance'), 100.0))
        _L, n_belt, C_corr = correct_center_distance(spec['pitch'], t1, t2, C)
        d = bs.drive(spec['pitch'], t1, t2, C_corr)
        drive = {'belt_teeth': n_belt, 'belt_length': n_belt * spec['pitch'],
                 'centre': C_corr, 'ratio': d.ratio, 'wrap_small_deg': d.wrap_small_deg,
                 'teeth_in_mesh': d.teeth_in_mesh, 'span': d.span,
                 'span_ratio': d.span / d.small_pd}
        tim = d.teeth_in_mesh
        if tim < bs.MIN_TEETH_IN_MESH:
            if tim in bs.TIM_FACTOR:
                warnings.append(f'{tim} teeth in mesh on the smaller pulley (under '
                                f'{bs.MIN_TEETH_IN_MESH}): the belt\'s torque rating is '
                                f'×{bs.TIM_FACTOR[tim]} (Gates p. 104). More teeth or a longer '
                                'centre distance adds teeth in mesh.')
            else:
                warnings.append(f'Only {tim} teeth in mesh on the smaller pulley: Gates '
                                '(p. 104) suggests a redesign — more teeth, a smaller ratio or '
                                'an idler.')
        if d.wrap_small_deg < bs.MIN_WRAP_DEG:
            warnings.append(f'The belt wraps {d.wrap_small_deg:.0f}° of the smaller pulley; '
                            f'Gates (p. 64) asks for at least {bs.MIN_WRAP_DEG:.0f}° on a '
                            'loaded pulley.')
        f1, f2 = pulleys[0]['flanged'], pulleys[1]['flanged']
        if not (f1 or f2):
            warnings.append('Neither pulley has flanges: on a two-pulley drive Gates (p. 62) '
                            'flanges one pulley on both sides (Flanges, in 3D features).')
        elif d.span / d.small_pd >= bs.LONG_SPAN_RATIO and not (f1 and f2):
            warnings.append(f'The span is {d.span / d.small_pd:.1f} × the smaller pulley '
                            f'(over {bs.LONG_SPAN_RATIO:g}): Gates (p. 63) suggests flanging '
                            'both pulleys.')

    fix = {'set': fix_set, 'changes': changes} if fix_set else None
    return {'pulleys': pulleys, 'drive': drive, 'warnings': warnings, 'fix': fix}


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
            # the profile's minimum, as teeth are (an OD of 5.37 gave an MXL 9 teeth, below
            # its 10: the page kept 9, a link of the same design clamped to 10 — 2026-10-02)
            n  = max(spec['min_teeth'], getTeethFromOD(od, spec['pitch'], spec['pitch_line_diff']))
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
@charges.charged('svg')
def download_svg():
    """Return SVG file download."""
    _consume_web_token(request)
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
        return _api_error(f'Error generating SVG: {e}')


@app.route('/download/dxf')
@charges.charged('dxf')
def download_dxf():
    """Return DXF file download for pulley 1 or pulley 2."""
    _consume_web_token(request)
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        pulley = request.args.get('pulley', '1')
        key    = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return _api_error(f'Unknown profile {family}/{pitch}')
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
            spline = _spline_of(request.args, 'p2_')
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
            spline = _spline_of(request.args, '')
            filename = f'{family}-{pitch}-{num_teeth}T.dxf'

        dxf = generate_dxf(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, clearance_mm=cl_mm, backlash_mm=bl_mm,
            print_extra_mm=pr_ex,
            spoke_count=sp_cnt if sp_en else 0,
            spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub_od,
            rim_depth_mm=sp_rim, fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
            flat_depth_mm=flat_depth_mm, keyway_w_mm=keyway_w_mm, keyway_h_mm=keyway_h_mm,
            spline=spline,
        )
        dxf = _embed_dxf(dxf if isinstance(dxf, bytes) else dxf.encode(), request.args)
        _mirror_to_addins(dxf, filename)
        return Response(
            dxf,
            mimetype='application/dxf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return _api_error(f'Error generating DXF: {e}')


@app.route('/download/svg-rim')
@charges.charged('svg')
def download_svg_rim():
    """Return rim-layer SVG: toothed profile + inner-rim, hub, bore circles."""
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        pulley = request.args.get('pulley', '1')
        pfx    = 'p2_' if pulley == '2' else ''
        key    = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return _api_error(f'Unknown profile {family}/{pitch}')
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
        svg = _embed_svg(svg, request.args)
        return Response(svg, mimetype='image/svg+xml',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except Exception as e:
        return _api_error(f'Error generating rim SVG: {e}')


@app.route('/download/dxf-rim')
@charges.charged('dxf')
def download_dxf_rim():
    """Return rim-layer DXF: toothed profile + inner-rim, hub, bore circles."""
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        pulley = request.args.get('pulley', '1')
        key    = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return _api_error(f'Unknown profile {family}/{pitch}')
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
        dxf = _embed_dxf(dxf if isinstance(dxf, bytes) else dxf.encode(), request.args)
        return Response(dxf, mimetype='application/dxf',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except Exception as e:
        return _api_error(f'Error generating rim DXF: {e}')


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
        spline=_spline_of(args, ''),
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
        spline=_spline_of(args, ''),
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
        spline=_spline_of(args, 'p2_'),
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
        spline1=_spline_of(args, ''), spline2=_spline_of(args, 'p2_'),
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
@charges.charged('svg')
def download_belt_svg():
    """Return belt SVG download.
    In dual mode: two-pulley belt layout SVG.
    In single mode: belt tooth cross-section SVG.
    """
    _consume_web_token(request)
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        dual   = request.args.get('dual') == 'true'

        if dual:
            key  = _resolve_key(family, pitch)
            if key is None or key not in PULLEY_SPECS:
                return _api_error(f'Unknown profile {family}/{pitch}')
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
                return _api_error(f'Belt SVG not available for family {family}')
            svg      = generate_belt_svg(family, pitch, n_teeth=3)
            filename = f'{family}-{pitch}-belt-profile.svg'

        svg = _embed_svg(svg, request.args)
        return Response(
            svg,
            mimetype='image/svg+xml',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return _api_error(f'Error generating belt SVG: {e}')


def _api_error(message, status=400):
    """An error as JSON — {"error": message} — which is what the page and API
    clients read (they show d.error). Never put a traceback in it: log it."""
    return jsonify({'error': message}), status


def _design_of(args) -> dict:
    """The design a file embeds for Import: the page's whole design when the request
    sends it (`design`, JSON: buildParams and the Belt Height), else the request's own
    parameters (a link, an older page). A file's own request can name fewer settings
    (a flat drawing has no height, a single belt only its profile) or other names (a
    flange's), and the file used to save those: an SVG imported with no Belt Height
    (the owner, 2026-10-07: "ensure that full metadata is saved in all files")."""
    raw = args.get('design')
    if raw:
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            d = None
        if isinstance(d, dict):
            return dict(d)
    return {k: v for k, v in dict(args).items() if k != 'design'}


def _cct_meta(args) -> dict:
    """CCT metadata dict for this tool — delegates to cct_common.build_meta."""
    from cct_common import build_meta
    return build_meta(_design_of(args), tool='pulleys', version=APP_VERSION,
                      schema_version=CCT_SCHEMA_VERSION)


def _embed_step(step_bytes: bytes, args) -> bytes:
    return _lib_embed_step(step_bytes, _design_of(args),
                           tool='pulleys', version=APP_VERSION,
                           schema_version=CCT_SCHEMA_VERSION)


def _embed_stl(stl_bytes: bytes, args) -> bytes:
    return _lib_embed_stl(stl_bytes, _design_of(args),
                          tool='pulleys', version=APP_VERSION,
                          schema_version=CCT_SCHEMA_VERSION)


def _embed_dxf(dxf_bytes: bytes, args) -> bytes:
    return _lib_embed_dxf(dxf_bytes, _design_of(args),
                          tool='pulleys', version=APP_VERSION,
                          schema_version=CCT_SCHEMA_VERSION)


def _embed_svg(svg_str: str, args) -> str:
    return _lib_embed_svg(svg_str, _design_of(args),
                          tool='pulleys', version=APP_VERSION,
                          schema_version=CCT_SCHEMA_VERSION)


def _rename_step_product(step_bytes: bytes, product_name: str) -> bytes:
    """Replace PRODUCT names in a STEP file so the CAD app shows correct component names.

    small_step emits fixed internal names (pulley, hub, top_flange, bottom_flange).
    Each gets a prefix derived from product_name (e.g. HTD-3M-40T) plus its
    display name (Pulley, Hub, TopFlange, BottomFlange).
    """
    _PART_MAP = {
        'pulley':        'Pulley',
        'hub':           'Hub',
        'top_flange':    'TopFlange',
        'bottom_flange': 'BottomFlange',
    }
    try:
        import re as _re_sp
        text = step_bytes.decode('utf-8', errors='replace')
        # Strip any +flanges suffix to get the base pulley identifier
        base = product_name.replace("'", " ").split('+')[0].rstrip('-')
        replaced = False
        for raw, display in _PART_MAP.items():
            new_name = f'{base}-{display}'
            new_text = _re_sp.sub(
                rf"PRODUCT\('{_re_sp.escape(raw)}','{_re_sp.escape(raw)}',",
                f"PRODUCT('{new_name}','{new_name}',",
                text,
            )
            if new_text != text:
                text = new_text
                replaced = True
        if not replaced:
            # Fallback: single-body STEP not from small_step — rename whatever is there
            safe = product_name.replace("'", " ")
            text = _re_sp.sub(
                r"PRODUCT\('[^']*','[^']*',",
                f"PRODUCT('{safe}','{safe}',",
                text,
            )
        return text.encode('utf-8')
    except Exception:
        return step_bytes




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
    # a field cleared while typing arrives blank: read it as 0 (no hub), not a crash
    num = lambda key: _safe_float(args.get(f'{prefix}{key}'), 0.0)   # noqa: E731
    hub_od       = max(0.0, num('hub_od'))
    hub_height   = max(0.0, num('hub_height'))
    screw_dia    = max(0.0, num('hub_screw_dia'))
    screw_count  = max(0,   int(num('hub_screw_count')))
    captured_nut = args.get(f'{prefix}hub_captured_nut', '0') == '1'
    flat_depth   = max(0.0, num('hub_flat_depth'))
    keyway_w     = max(0.0, num('hub_keyway_w'))
    keyway_h     = max(0.0, num('hub_keyway_h'))
    if args.get(f'{prefix}bore_shape') == 'spline':
        flat_depth = keyway_w = keyway_h = 0.0     # one bore shape at a time
        if not _hex_bore(args, prefix):    # a hex bar keeps its screws, on flats (ADR-018)
            screw_dia, screw_count, captured_nut = 0.0, 0, False   # a spline: none (ADR-017)
    ss = _set_screw(args, prefix)
    if ss is not None:              # a named size (ADR-013) decides these, not the page's copy
        screw_dia, captured_nut = ss.major, ss.hold == 'nut'
    return hub_od, hub_height, screw_dia, screw_count, captured_nut, flat_depth, keyway_w, keyway_h


def _printed_as_one(meshes):
    """A 3D-print pulley and the flanges printed with it, as ONE solid. They
    touch face to face (and share the bore's edge ring), so stacked as
    separate bodies the STL wasn't watertight; a union is. Falls back to
    stacking them if any part isn't a closed volume or the union fails."""
    import trimesh
    parts = [m for m in meshes if m is not None and len(m.faces)]
    if all(getattr(m, 'is_volume', False) for m in parts):
        try:
            one = trimesh.boolean.union(parts, engine='manifold')
            if one.is_watertight:
                return one
        except Exception:
            app.logger.exception('flange union failed; exporting the parts stacked')
    return trimesh.util.concatenate(parts)


def _step_color(prefix=''):
    """A pulley's colour in its STEP: the 3D view's (exporters/colors.py)."""
    from exporters import colors
    return colors.PULLEY[2 if prefix == 'p2_' else 1]


def _step_screw_kw(args, prefix=''):
    """The set screw's hole and nut for a STEP worker (ADR-013), as JSON:
    {} for no screws or an old design (the worker then cuts the nominal
    hole, as it always did)."""
    ss = _set_screw(args, prefix)
    if ss is None:
        return {}
    return {'screw_hole': dict(ss.hole), 'screw_nut': list(ss.nut) if ss.nut else None}


def _hex_bore(args, prefix=''):
    """A hex bar bore (ADR-018): unlike a spline it takes every Retention
    method, one or two screws, each on a flat (step_exporter._std_screw_step)."""
    return (args.get(f'{prefix}bore_shape') == 'spline'
            and args.get(f'{prefix}spline_type') == 'hex')


def _set_screw(args, prefix=''):
    """The hub's set screw — hole and nut from its named size and the design's
    threaded-hole settings (geometry/set_screw.py, ADR-013) — for the STL
    builders; None for no screws or a design from before sizes had names."""
    if args.get(f'{prefix}bore_shape') == 'spline' and not _hex_bore(args, prefix):
        return None                    # a splined bore takes no set screw (ADR-017)
    from geometry import set_screw
    return set_screw.parse(args, prefix)


@functools.lru_cache(maxsize=256)
def _groove_bottom_radius(family, pitch, teeth, cl, bl, pe):
    """Smallest radius of the tooth outline — what the exporters measure Rim Depth from."""
    from geometry.pulley_geometry import pulley_outline_segments
    _, _, _, wrapped = pulley_outline_segments(family, pitch, teeth, cl, bl, pe)
    return min(math.hypot(x, y) for x, y in wrapped)


def _as_spline_hole_outer(sp):
    """How far a spline's hole reaches: its fitted major (a hex bar's corners)."""
    from cct_common import splines
    return splines.hole(_as_spline_obj(sp)).outer


def _spoke_room(args, prefix=''):
    """(groove-bottom radius, bore reach) — the room a pulley's spokes are
    fitted into (mm)."""
    family = args.get('family', 'HTD')
    pitch  = args.get('pitch', '5M')
    key    = _resolve_key(family, pitch)
    spec   = PULLEY_SPECS[key]
    teeth  = max(spec['min_teeth'], int(float(args.get(f'{prefix}teeth', spec['min_teeth']))))
    cl = _get_preset_value(spec, 'clearances', args.get(f'{prefix}clearance_preset', 'STANDARD'),
                           args.get(f'{prefix}clearance_custom', 0.0))
    bl = _get_preset_value(spec, 'backlash', args.get(f'{prefix}backlash_preset', 'STANDARD'),
                           args.get(f'{prefix}backlash_custom', 0.0))
    pe = float(args.get(f'{prefix}print_extra', 0.0) or 0.0)
    r_root = _groove_bottom_radius(family, pitch, teeth, float(cl), float(bl), pe)
    # round the bore's reach: a spline's slots (a hex bar's corners) go past
    # its minor, and a spoke hub fitted to the minor was cut through by them
    sp = _spline_of(args, prefix)
    reach = _as_spline_hole_outer(sp) if sp else _get_bore(args, f'{prefix}bore')
    return r_root, reach


def _spoke_fit(args, prefix=''):
    """Check a pulley's requested spoke settings against its size (geometry/spoke_fit.py).
    prefix '' is pulley 1 (or the only pulley), 'p2_' pulley 2."""
    from geometry.spoke_fit import fit_spokes
    r_root, reach = _spoke_room(args, prefix)
    return fit_spokes(
        r_root, reach,
        max(0.0, float(args.get(f'{prefix}spokes_hub_od', 0.0))),
        max(0.0, float(args.get(f'{prefix}spokes_rim_depth', 2.0))),
        max(0.0, float(args.get(f'{prefix}spokes_width', 4.0))),
        max(0.0, float(args.get(f'{prefix}spokes_fillet_tip', 1.0))),
        max(0.0, float(args.get(f'{prefix}spokes_fillet_base', 1.5))),
        max(0, int(float(args.get(f'{prefix}spokes_count', 4)))))


def _parse_spoke_params(args, prefix=''):
    """Return spoke params tuple from request args.
    Returns (enabled, hub_od, rim_depth, width, fillet_tip, fillet_base, count, height, split).

    Settings that don't fit the pulley are built with their fitted values
    (geometry/spoke_fit.py), and spokes are left off when none fit — so the
    preview and every export agree, and none fails on an impossible layout.
    The page shows what was changed, with an Auto-fit button (/api/spoke-fit).
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
    if enabled:
        try:
            fit = _spoke_fit(args, prefix)
        except Exception:
            app.logger.exception('spoke fit failed; building the settings as given')
            fit = None
        if fit is not None and not fit.possible:
            enabled = False
        elif fit is not None and not fit.ok:
            f = fit.fitted
            hub_od, rim_depth, width = f['hub_od'], f['rim_depth'], f['width']
            fillet_tip, fillet_base, count = f['fillet_tip'], f['fillet_base'], f['count']
    return enabled, hub_od, rim_depth, width, fillet_tip, fillet_base, count, height, split


def _screw_size_list():
    from geometry import set_screw
    return set_screw.size_list()


@app.route('/api/screws')
def api_screws():
    """Every set-screw size's holes under the threaded-hole settings given
    (screw_hole_shape, thread_engagement, hex_flat) — the Threaded screw
    holes dialog's examples and the hub panel's note (ADR-013)."""
    from geometry import set_screw
    return jsonify(set_screw.holes_for(request.args))


@app.route('/api/spoke-fit')
def api_spoke_fit():
    """Do this pulley's spoke settings fit it? Unprefixed pulley params, as the
    page sends them for one pulley. Returns ok, possible, the fitted settings
    (what the preview and downloads use) and one line per change."""
    from geometry.spoke_fit import describe
    try:
        fit = _spoke_fit(request.args, '')
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': fit.ok, 'possible': fit.possible, 'fitted': fit.fitted,
                    'changes': describe(fit) if not fit.ok else []})


def _spline_preview_json(args, pfx, origin, bare):
    """The 3D view's sample shaft, rings and washers for one pulley (ADR-017),
    placed as the preview placed the pulley: `origin` is where the pulley's
    own frame (bore on the axis, z = 0 at its bottom) landed, `bare` its
    z extent. JSON {shaft, rings, washers}: base64 STL, or null."""
    import base64
    from exporters import spline_parts as parts
    sp = _spline_of(args, pfx)
    if not sp:
        return jsonify({'shaft': None, 'rings': None, 'washers': None})
    lo, hi = _ring_face_z(args, pfx, *bare)
    out = {}
    for name, mesh in parts.preview_parts(sp, hi - lo).items():
        if mesh is not None:
            mesh.apply_translation([float(origin[0]), float(origin[1]), float(origin[2]) + lo])
            mesh = base64.b64encode(mesh.export(file_type='stl')).decode('ascii')
        out[name] = mesh
    return jsonify(out)


@app.route('/api/preview-stl')
def api_preview_stl():
    """Return binary STL for the Three.js 3D viewer (centred at origin).
    part=spline (one pulley) or spline1 / spline2 (a drive): JSON of the
    splined bore's shaft, rings and washers instead (_spline_preview_json)."""
    try:
        dual = request.args.get('dual') == 'true'
        want = request.args.get('part', 'all')
        spline_part = want if want.startswith('spline') else None
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
            clearance_h = max(0.0, float(request.args.get('clearance_height', 0.0)))
            part = 'p1' if spline_part else want
            place = {}
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
                set_screw1=_set_screw(request.args, ''), set_screw2=_set_screw(request.args, 'p2_'),
                flat_depth_mm1=fd1, flat_depth_mm2=fd2,
                keyway_w_mm1=kw_w1, keyway_h_mm1=kw_h1,
                keyway_w_mm2=kw_w2, keyway_h_mm2=kw_h2,
                spline1=_spline_of(request.args, ''), spline2=_spline_of(request.args, 'p2_'),
                spoke_count1=sp_cnt if sp_en else 0,
                spoke_width_mm1=sp_w, spoke_hub_od_mm1=sp_hub,
                fillet_tip_mm1=sp_ft, fillet_base_mm1=sp_fb, rim_depth_mm1=sp_rim,
                spoke_height_mm1=sp_h if sp_en else 0.0,
                spoke_count2=sp_cnt2 if sp_en2 else 0,
                spoke_width_mm2=sp_w2, spoke_hub_od_mm2=sp_hub2,
                fillet_tip_mm2=sp_ft2, fillet_base_mm2=sp_fb2, rim_depth_mm2=sp_rim2,
                spoke_height_mm2=sp_h2 if sp_en2 else 0.0,
                part=part, place=place,
                flange1=fl1, flange2=fl2,
                clearance_height_mm=clearance_h,
            )
            if spline_part:
                n = '2' if spline_part == 'spline2' else '1'
                return _spline_preview_json(request.args, 'p2_' if n == '2' else '', place[f'origin{n}'],
                                            place[f'bounds{n}'])
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
                    spline=_spline_of(request.args, ''),
                )
                if fp.get('nubs_enabled') and fp.get('flange_3dprint') and fp.get('top_separate'):
                    _socket_meshes = build_socket_meshes(
                        fp, family, pitch, num_teeth, bore_mm, belt_height,
                        clearance_mm=cl_mm, print_extra_mm=pr_ex,
                        hub_od_mm=hub_od, spokes_enabled=sp_en, spoke_hub_od_mm=sp_hub,
                        rim_depth_mm=sp_rim, spoke_height_mm=sp_h if sp_en else 0.0,
                    ) or None
            _fl_enabled = request.args.get('flange_enabled') == '1'
            _fl_h = fp.get('flange_height_mm', 1.5) if _fl_enabled and fp else 1.5
            if _fl_enabled and fp and not fp.get('flange_3dprint'):
                _fl_h = fp.get('plate_height_mm', 1.0)   # the hub sits on a metal plate
            stl = generate_pulley_stl_preview(
                family, pitch, num_teeth, bore_mm, belt_height,
                cl_mm, bl_mm, pr_ex, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
                spline=_spline_of(request.args, ''),
                spoke_count=sp_count, spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
                fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb, rim_depth_mm=sp_rim,
                spoke_height_mm=sp_h if sp_en else 0.0,
                flange_enabled=_fl_enabled, flange_height_mm=_fl_h,
                socket_meshes=_socket_meshes,
                set_screw=_set_screw(request.args, ''),
                centre=False,       # built where the flanges and the spline parts are
            )
            import io as _io
            import trimesh
            pulley_mesh = trimesh.load(_io.BytesIO(stl), file_type='stl')
            bare = (float(pulley_mesh.bounds[0][2]), float(pulley_mesh.bounds[1][2]))
            combined = (trimesh.util.concatenate([pulley_mesh] + _fl_meshes) if _fl_meshes
                        else pulley_mesh)
            offset = -combined.centroid                 # centred for Three.js's auto-fit
            if spline_part:
                return _spline_preview_json(request.args, '', offset, bare)
            combined.apply_translation(offset)
            stl = combined.export(file_type='stl')
        return Response(stl, mimetype='model/stl',
                        headers={'Cache-Control': 'no-store'})
    except Exception as e:
        import traceback
        app.logger.error('STL preview failed:\n%s', traceback.format_exc())
        return _api_error(f'Error generating STL preview: {e}')


def _pulley_stl(args):
    """One pulley's printed STL as the download builds it — the pulley,
    its integrated or metal flanges, the ring's counterbore — with the
    file name's parts and the bare pulley's z extent (its raised hub and all,
    no flanges): (stl bytes, name stem, flanges on, (z lo, z hi)). Raises on
    bad input, as the route did."""
    pulley = args.get('pulley', '1')
    family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
        _parse_stl_params(args, pulley)
    pfx = 'p2_' if pulley == '2' else ''
    hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(args, pfx)
    sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, _ = \
        _parse_spoke_params(args, pfx)
    suffix   = '-P2' if pulley == '2' else ''
    sp_count = sp_cnt if sp_en else 0

    # 3D-print flanges: parse flange params first so we can pass flange info to STL generator
    _fl_enabled = args.get(f'{pfx}flange_enabled') == '1'
    fp = _parse_flange_params(args, pfx) if _fl_enabled else {}

    _fl_3dp   = _fl_enabled and fp.get('flange_3dprint', False)
    _fl_metal = _fl_enabled and not fp.get('flange_3dprint', False)
    # Hub raise amount: 3D-print uses flange rim height; metal uses plate thickness
    _raise_h  = (fp.get('flange_height_mm', 1.5) if _fl_3dp
                 else fp.get('plate_height_mm', 1.0) if _fl_metal
                 else 0.0)
    stl = generate_pulley_stl(
        family, pitch, num_teeth, bore_mm, belt_height,
        cl_mm, bl_mm, pr_ex, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
        spline=_spline_of(args, pfx),
        spoke_count=sp_count, spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
        fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb, rim_depth_mm=sp_rim,
        spoke_height_mm=sp_h if sp_en else 0.0,
        flange_enabled=_fl_enabled,
        flange_height_mm=_raise_h,
        set_screw=_set_screw(args, pfx),
    )
    import io as _io0
    import trimesh as _tm0
    _bare = tuple(float(v) for v in _tm0.load(_io0.BytesIO(stl), file_type='stl').bounds[:, 2])
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
            spline=_spline_of(args, pfx),
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
            spline=_spline_of(args, pfx),
        )

        if not fp.get('top_separate'):
            # Integrated mode: reuse the already-generated uncentered STL so
            # flanges can be placed at natural z=0 / z=belt_height positions —
            # same approach as the Assembly STL route, which is known to work.
            pulley_mesh = trimesh.load(_io.BytesIO(stl), file_type='stl')
            bot_mesh = trimesh.load(_io.BytesIO(
                generate_3dprint_flange_stl(which='bottom', **_flange_kw)
            ), file_type='stl')
            # The top flange's hole is the hub's own circle, but drawn with
            # different points: the union then touches itself at single
            # vertices, which merge into a non-manifold edge when an STL is
            # read. Cut the hole 0.1 mm into the hub so they overlap (the
            # union hides the difference).
            _top_kw = dict(_flange_kw)
            if hub_h > 0.0 and _top_kw['hub_od_mm'] > bore_mm + 0.4:
                _top_kw['hub_od_mm'] -= 0.2
            top_mesh = trimesh.load(_io.BytesIO(
                generate_3dprint_flange_stl(which='top', nubs_enabled=False, **_top_kw)
            ), file_type='stl')
            stl = _printed_as_one([pulley_mesh, bot_mesh, top_mesh]).export(file_type='stl')
        else:
            # Separate top flange: the preview builder, which can cut nub
            # sockets on the live trimesh mesh, left uncentred (see centre=).
            sockets = build_socket_meshes(
                fp, family, pitch, num_teeth, bore_mm, belt_height,
                clearance_mm=cl_mm, print_extra_mm=pr_ex,
                hub_od_mm=eff_hub_od, spokes_enabled=sp_en,
                spoke_hub_od_mm=sp_hub, rim_depth_mm=sp_rim,
                spoke_height_mm=sp_h if sp_en else 0.0,
            ) if fp.get('nubs_enabled') else []

            stl_preview = generate_pulley_stl_preview(
                family, pitch, num_teeth, bore_mm, belt_height,
                cl_mm, bl_mm, pr_ex, hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h,
                spline=_spline_of(args, pfx),
                spoke_count=sp_count, spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
                fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb, rim_depth_mm=sp_rim,
                spoke_height_mm=sp_h if sp_en else 0.0,
                flange_enabled=_fl_3dp, flange_height_mm=fp.get('flange_height_mm', 1.5),
                socket_meshes=sockets or None,
                set_screw=_set_screw(args, pfx),
                centre=False,   # on the axis, so the bottom flange lines up with it
            )
            pulley_mesh = trimesh.load(_io.BytesIO(stl_preview), file_type='stl')
            z_bottom = float(pulley_mesh.bounds[0][2])

            bot_mesh = trimesh.load(_io.BytesIO(
                generate_3dprint_flange_stl(which='bottom', **_flange_kw)
            ), file_type='stl')
            bot_mesh.apply_translation([0.0, 0.0, z_bottom])
            stl = _printed_as_one([pulley_mesh, bot_mesh]).export(file_type='stl')
    return (stl if isinstance(stl, bytes) else bytes(stl), f'{family}-{pitch}-{num_teeth}T{suffix}',
            _fl_enabled, _bare)


def _print_supports(args, pulley, fp):
    """A pulley's print supports, for its -w-supports STL: the ribs under the
    top flange's overhang and, on a spoked pulley, the grid under its hub and
    spoke web, which hang a flange height above the bed (flange_exporter)."""
    from exporters.flange_exporter import build_center_supports
    pfx = 'p2_' if pulley == '2' else ''
    family, pitch, teeth, bore, belt_h, cl_mm, _bl, pr_ex = _parse_stl_params(args, pulley)
    out = build_support_ribs(fp, family, pitch, teeth, bore, belt_h,
                             clearance_mm=cl_mm, print_extra_mm=pr_ex)
    sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_cnt, sp_h, _ = _parse_spoke_params(args, pfx)
    if out and sp_en and sp_cnt > 0:
        r_root, reach = _spoke_room(args, pfx)
        r_hub = sp_hub / 2.0 if sp_hub > 0 else reach / 2.0 + 1.0
        out += build_center_supports(
            fp, bore_reach_mm=reach / 2.0, r_hub_mm=r_hub, r_rim_mm=max(r_root - sp_rim, r_hub + 1.0),
            face_height_mm=belt_h, web_height_mm=sp_h, spoke_count=sp_cnt, spoke_width_mm=sp_w,
            fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb)
    return out


@app.route('/download/stl')
@charges.charged('stl')
def download_stl():
    """Return binary STL file download."""
    _consume_web_token(request)
    try:
        args = request.args
        stl, stem, _fl_enabled, _ = _pulley_stl(args)
        pulley = args.get('pulley', '1')
        fp = _parse_flange_params(args, 'p2_' if pulley == '2' else '') if _fl_enabled else {}
        printed = _fl_enabled and fp.get('flange_3dprint')
        # Names (the owner, 2026-10-03, 2026-10-05): GT-2M-30T is the pulley, with no
        # flanges or both; -1flange when its top flange is a separate part, so only one
        # is on it (the flange itself is -flange-only); -w-supports for the copy with
        # print supports (with_supports=1).
        fname = stem + ('-1flange' if printed and fp.get('top_separate') else '')
        if args.get('with_supports') == '1':
            ribs = _print_supports(args, pulley, fp) if printed else []
            if not ribs:
                return _api_error('Print supports go with a printed top flange made in place '
                                  '(not a separate part) and Add Print Supports ticked.')
            import io as _io_s
            import trimesh as _tm_s
            stl = _tm_s.util.concatenate([_tm_s.load(_io_s.BytesIO(stl), file_type='stl')] + ribs
                                         ).export(file_type='stl')
            fname += '-w-supports'
        fname += '.stl'
        stl = _embed_stl(stl, request.args)
        # Mirror to a connected CAD addin's watch folder (CCT_Import) so it auto-
        # imports, same as STEP downloads; skip the browser download if it landed.
        if _mirror_to_addins(stl, fname):
            return ('', 204)
        return Response(stl, mimetype='model/stl',
                        headers={'Content-Disposition': f'attachment; filename="{fname}"'})
    except Exception as e:
        import traceback
        app.logger.error('STL download failed:\n%s', traceback.format_exc())
        return _api_error(f'Error generating STL: {e}')


@app.route('/download/step')
@charges.charged('step')
@require_active_session
def download_step():
    _consume_web_token(request)
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
            **_step_screw_kw(request.args, pfx),
            captured_nut=cn, flat_depth_mm=fd,
            keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            spline=_spline_of(request.args, pfx),
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
            step_bytes = _run_ss_worker(dict(kw, export_type='pulley', color=_step_color(pfx)))
        except RuntimeError as _e:
            return _api_error(f'STEP error: {_e}')

        step_bytes = _rename_step_product(step_bytes, fname[:-5])
        step_bytes = _embed_step(step_bytes, request.args)
        dl_name = _safe_dl_name(fname)
        # Mirror with the RAW name (keep '+') so addins detect multi-body files.
        if _mirror_to_addins(step_bytes, fname):
            # A connected CAD addin received the file; skip the browser download
            # so it isn't duplicated in the user's Downloads folder.
            return ('', 204)
        return Response(step_bytes, mimetype='application/step',
                        headers={'Content-Disposition': f'attachment; filename="{dl_name}"'})
    except Exception as e:
        import logging as _log, traceback as _tb
        _log.getLogger(__name__).error('STEP generation failed: %s\n%s', e, _tb.format_exc())
        return _api_error(f'Error generating STEP: {e}')


@app.route('/download/belt-step')
@charges.charged('step')
def download_belt_step():
    """Two-pulley belt STEP export (cadquery, B-rep with true arcs + B-spline teeth)."""
    _consume_web_token(request)
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
            return _api_error(f'Unknown profile {family}/{pitch}')
        spec = PULLEY_SPECS[key]

        num_teeth1   = max(spec['min_teeth'], int(request.args.get('teeth',    spec['min_teeth'])))
        num_teeth2   = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
        belt_h       = float(request.args.get('belt_height', 10.0))
        _default_c   = (num_teeth1 + num_teeth2) * spec['pitch'] / (2.0 * math.pi)
        center_dist  = float(request.args.get('center_distance', _default_c))

        try:
            step_bytes = _run_ss_worker(dict(
                export_type='belt',
                family=family, pitch=pitch,
                num_teeth_left=num_teeth1, num_teeth_right=num_teeth2,
                center_dist_mm=center_dist,
                belt_height_mm=belt_h,
            ))
        except RuntimeError as _e:
            return _api_error(f'Belt STEP error: {_e}')
        filename = f'{family}-{pitch}-{num_teeth1}T-{num_teeth2}T-belt.step'
        step_bytes = _embed_step(step_bytes, request.args)
        if _mirror_to_addins(step_bytes, filename):
            return ('', 204)
        return Response(
            step_bytes,
            mimetype='application/step',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        import traceback
        app.logger.error('Belt STEP export failed:\n%s', traceback.format_exc())
        return _api_error('Belt STEP export failed. The error has been logged.', 500)


@app.route('/download/assembly-step')
@charges.charged('step')
@require_active_session
def download_assembly_step():
    """The whole design as one assembly STEP (the owner, 2026-10-06): every part
    the Download window has ticked (parts=p1,p2,belt,sh1,wa1 …; all when absent),
    each a component of its own, named as its STL is and placed as assembled
    (exporters/assembly.py; small_step assemble). STEP is nominal: no print
    supports, no bloat."""
    _consume_web_token(request)
    try:
        from exporters import assembly as _asm
        args = request.args
        only = {p.strip() for p in args.get('parts', '').split(',') if p.strip()} or None
        m = _asm.manifest(args, only=only)
        if not m['parts']:
            return _api_error('No parts chosen for the STEP file.')
        kw = {'1': _step_kw_of(args, '')}
        if args.get('dual') == 'true':
            kw['2'] = _step_kw_of(args, 'p2_')
        has_belt = any(p.get('make', {}).get('kind') == 'belt' for p in m['parts'])
        belt_kw = _belt_step_kw(args, kw['1'], kw['2']) if has_belt else None
        try:
            step_bytes = _run_ss_worker(dict(export_type='assembly', manifest=m, kw=kw, belt_kw=belt_kw),
                                        timeout=110)
        except RuntimeError as _e:
            return _api_error(f'STEP error: {_e}')
        lone = _asm.single_make(m)
        if lone:            # one pulley alone: its own STEP, named as /download/step names it
            step_bytes = _rename_step_product(step_bytes, lone['name'])
        step_bytes = _embed_step(step_bytes, args)
        fname = f"{(lone or m)['name']}.step"
        # Mirror as '-all': the addins import an assembly without importToTarget.
        if _mirror_to_addins(step_bytes, f"{m['name']}-all.step"):
            return ('', 204)
        return Response(step_bytes, mimetype='application/step',
                        headers={'Content-Disposition': f'attachment; filename="{_safe_dl_name(fname)}"'})
    except Exception as e:
        import logging as _log, traceback as _tb
        _log.getLogger(__name__).error('assembly STEP failed: %s\n%s', e, _tb.format_exc())
        return _api_error(f'Error generating the assembly STEP: {e}')


@app.route('/download/all-step')
@charges.charged('step')
def download_all_step():
    """Multipart STEP with all pulleys and their flanges in one file.
    In dual mode (dual=true) includes P1 + P2; otherwise just P1.
    """
    _consume_web_token(request)
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
                **_step_screw_kw(request.args, pfx),
                captured_nut=cn, flat_depth_mm=fd,
                keyway_w_mm=kw_w, keyway_h_mm=kw_h,
                spline=_spline_of(request.args, pfx),
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
                n_belt_teeth   = int(request.args.get('n_belt', 0)),
            )

        _t1 = kw1['num_teeth']
        _fname_stem = (f'{kw1["family"]}-{kw1["pitch"]}-{_t1}T+{kw2["num_teeth"]}T-all'
                       if kw2 else f'{kw1["family"]}-{kw1["pitch"]}-{_t1}T-all')

        worker_kw = dict(kw1, export_type='all', color=_step_color(''))
        if kw2:
            worker_kw['kw2'] = dict(kw2, color=_step_color('p2_'))
        if belt_kw:
            worker_kw['belt_kw'] = belt_kw
        try:
            step_bytes = _run_ss_worker(worker_kw)
        except RuntimeError as _e:
            return _api_error(f'STEP error: {_e}')

        fname = _fname_stem + '.step'
        # All-parts STEP is always an assembly — don't overwrite individual part names.
        # Embed the CCT signature so the CAD addins' watchers recognise the file.
        step_bytes = _embed_step(step_bytes, request.args)
        dl_name = _safe_dl_name(fname)
        # Mirror with the RAW name (keep '+') so addins detect the assembly.
        if _mirror_to_addins(step_bytes, fname):
            return ('', 204)
        return Response(step_bytes, mimetype='application/step',
                        headers={'Content-Disposition': f'attachment; filename="{dl_name}"'})
    except Exception as exc:
        import traceback
        app.logger.error('All-parts STEP failed:\n%s', traceback.format_exc())
        return _api_error('All-parts STEP failed. The error has been logged.', 500)


@app.route('/download/belt-stl')
@charges.charged('stl')
def download_belt_stl():
    """Return binary STL of the two-pulley belt body."""
    _consume_web_token(request)
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        dual   = request.args.get('dual') == 'true'

        if not dual:
            return Response(
                'Belt STL export requires Two Pulley Drive mode.',
                status=400, mimetype='text/plain')

        key = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return _api_error(f'Unknown profile {family}/{pitch}')
        spec = PULLEY_SPECS[key]

        num_teeth1  = max(spec['min_teeth'], int(request.args.get('teeth',    spec['min_teeth'])))
        num_teeth2  = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
        belt_h      = float(request.args.get('belt_height', 10.0))
        _default_c  = (num_teeth1 + num_teeth2) * spec['pitch'] / (2.0 * math.pi)
        center_dist = float(request.args.get('center_distance', _default_c))

        mesh = _build_belt_mesh(family, pitch, num_teeth1, num_teeth2,
                                center_dist, belt_h, cx1=0.0)
        if mesh is None:
            return _api_error(f'Belt STL not available for {family}/{pitch}')

        stl_bytes = mesh.export(file_type='stl')
        if isinstance(stl_bytes, memoryview):
            stl_bytes = bytes(stl_bytes)
        stl_bytes = _embed_stl(stl_bytes, request.args)
        filename  = f'{family}-{pitch}-{num_teeth1}T-{num_teeth2}T-belt.stl'
        if _mirror_to_addins(stl_bytes, filename):
            return ('', 204)
        return Response(stl_bytes, mimetype='model/stl',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except Exception as e:
        import traceback
        app.logger.error('belt STL failed:\n%s', traceback.format_exc())
        return _api_error(f'Error generating belt STL: {e}')

@app.route('/download/belt-dxf')
@charges.charged('dxf')
def download_belt_dxf():
    """Return belt DXF download.
    In dual mode: two-pulley belt layout DXF.
    In single mode: belt tooth cross-section DXF.
    """
    _consume_web_token(request)
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        dual   = request.args.get('dual') == 'true'

        if dual:
            key = _resolve_key(family, pitch)
            if key is None or key not in PULLEY_SPECS:
                return _api_error(f'Unknown profile {family}/{pitch}')
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
                return _api_error(f'Belt DXF not available for family {family}')
            dxf_bytes = generate_belt_dxf(family, pitch, n_teeth=3)
            filename  = f'{family}-{pitch}-belt-profile.dxf'

        dxf_bytes = _embed_dxf(dxf_bytes, request.args)
        return Response(
            dxf_bytes,
            mimetype='application/dxf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return _api_error(f'Error generating belt DXF: {e}')


@app.route('/download/all-dxf')
@charges.charged('dxf')
def download_all_dxf():
    """Combined layout DXF: P1 profile, P2 profile, belt outline in one file."""
    _consume_web_token(request)
    try:
        family = request.args.get('family', 'HTD')
        pitch  = request.args.get('pitch',  '5M')
        key    = _resolve_key(family, pitch)
        if key is None or key not in PULLEY_SPECS:
            return _api_error(f'Unknown profile {family}/{pitch}')
        spec = PULLEY_SPECS[key]

        num_teeth1 = max(spec['min_teeth'], int(request.args.get('teeth',    spec['min_teeth'])))
        num_teeth2 = max(spec['min_teeth'], int(request.args.get('p2_teeth', spec['min_teeth'])))
        bore1      = _get_bore(request.args, 'bore')
        bore2      = _get_bore(request.args, 'p2_bore')
        pe1        = float(request.args.get('print_extra',    0.0))
        pe2        = float(request.args.get('p2_print_extra', 0.0))
        cl1 = _get_preset_value(spec, 'clearances',
                                request.args.get('clearance_preset',    'STANDARD'),
                                request.args.get('clearance_custom',    0.0))
        bl1 = _get_preset_value(spec, 'backlash',
                                request.args.get('backlash_preset',     'STANDARD'),
                                request.args.get('backlash_custom',     0.0))
        cl2 = _get_preset_value(spec, 'clearances',
                                request.args.get('p2_clearance_preset', 'STANDARD'),
                                request.args.get('p2_clearance_custom', 0.0))
        bl2 = _get_preset_value(spec, 'backlash',
                                request.args.get('p2_backlash_preset',  'STANDARD'),
                                request.args.get('p2_backlash_custom',  0.0))

        _default_c = (num_teeth1 + num_teeth2) * spec['pitch'] / (2.0 * math.pi)
        center_dist = float(request.args.get('center_distance', _default_c))

        sp1_en, sp1_hub_od, sp1_rim, sp1_w, sp1_ft, sp1_fb, sp1_cnt, _, _ = \
            _parse_spoke_params(request.args, '')
        sp2_en, sp2_hub_od, sp2_rim, sp2_w, sp2_ft, sp2_fb, sp2_cnt, _, _ = \
            _parse_spoke_params(request.args, 'p2_')

        dxf_bytes = generate_combined_layout_dxf(
            family=family, pitch=pitch,
            num_teeth1=num_teeth1, bore_mm1=bore1,
            clearance_mm1=cl1, backlash_mm1=bl1, print_extra_mm1=pe1,
            spoke_count1=sp1_cnt if sp1_en else 0,
            spoke_width_mm1=sp1_w, spoke_hub_od_mm1=sp1_hub_od,
            rim_depth_mm1=sp1_rim, fillet_tip_mm1=sp1_ft, fillet_base_mm1=sp1_fb,
            flat_depth_mm1=float(request.args.get('hub_flat_depth',    0.0)),
            keyway_w_mm1=float(request.args.get('hub_keyway_w',        0.0)),
            keyway_h_mm1=float(request.args.get('hub_keyway_h',        0.0)),
            num_teeth2=num_teeth2, bore_mm2=bore2,
            clearance_mm2=cl2, backlash_mm2=bl2, print_extra_mm2=pe2,
            spoke_count2=sp2_cnt if sp2_en else 0,
            spoke_width_mm2=sp2_w, spoke_hub_od_mm2=sp2_hub_od,
            rim_depth_mm2=sp2_rim, fillet_tip_mm2=sp2_ft, fillet_base_mm2=sp2_fb,
            flat_depth_mm2=float(request.args.get('p2_hub_flat_depth', 0.0)),
            keyway_w_mm2=float(request.args.get('p2_hub_keyway_w',     0.0)),
            keyway_h_mm2=float(request.args.get('p2_hub_keyway_h',     0.0)),
            spline1=_spline_of(request.args, ''), spline2=_spline_of(request.args, 'p2_'),
            center_dist_mm=center_dist,
        )
        dxf_bytes = _embed_dxf(dxf_bytes, request.args)
        filename = f'{family}-{pitch}-{num_teeth1}T-{num_teeth2}T-all.dxf'
        _mirror_to_addins(dxf_bytes, filename)
        return Response(
            dxf_bytes,
            mimetype='application/dxf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return _api_error(f'Error generating combined DXF: {e}')


@app.route('/api/fp-token', methods=['POST'])
def api_fp_token():
    """Issue a single-use download token after checking the weekly web limit.

    Body: {"fp": "<32-char hex fingerprint>"}
    Response:
      {"ok": true,  "token": "<hex>"}          — allowed, use token in download URL
      {"ok": true,  "token": null}              — no FP provided, allow without recording
      {"ok": false, "count": N, "limit": M}     — limit reached
    Fails open: any server error returns {"ok": true, "token": null}.
    """
    try:
        data = request.get_json(silent=True) or {}
        fp   = (data.get('fp') or '').strip()
        ip   = (request.headers.get('X-Forwarded-For', request.remote_addr or '')
                .split(',')[0].strip())

        # Localhost / desktop build, or tokens on (they replace this limit): no limit
        if charges.enabled or not SESSION_QUEUE or os.environ.get('QUEUE_DISABLED') or ip in ('127.0.0.1', '::1'):
            return jsonify({'ok': True, 'token': None})

        if not fp or len(fp) > 64:
            return jsonify({'ok': True, 'token': None})

        allowed, count, limit = check_web_download(fp, ip)
        if not allowed:
            return jsonify({'ok': False, 'count': count, 'limit': limit})

        token = _issue_web_token(fp, ip)
        return jsonify({'ok': True, 'token': token})
    except Exception:
        return jsonify({'ok': True, 'token': None})   # fail open


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


# ── The parts that go with a splined bore (ADR-017) ─────────────────────────
def _spline_part(args):
    """(prefix, spline dict, part) for a spline-part download, or ValueError."""
    pfx = 'p2_' if args.get('pulley') == '2' else ''
    sp = _spline_of(args, pfx)
    if not sp:
        raise ValueError('Bore Shape is not Spline, so there is no spline part')
    part = args.get('part', 'shaft')
    if part not in ('shaft', 'washer'):
        raise ValueError(f'Unknown spline part {part!r}')
    if part == 'washer' and not (sp['retainer'] and sp['retainer']['washer_t'] > 0):
        raise ValueError('No splined washer: choose a retaining ring and tick Splined washer')
    return pfx, sp, part


def _part_span(args, pfx):
    """The part's outer faces at the bore (z low, z high) — where its rings
    sit: the bare pulley as the download builds it, and any flange over a
    face (_ring_face_z)."""
    q = args.to_dict() if hasattr(args, 'to_dict') else dict(args)
    q['pulley'] = '2' if pfx else '1'
    return _ring_face_z(args, pfx, *_pulley_stl(q)[3])


def _spline_part_file(args, fmt):
    """The bytes (or text) and file name of a spline part in one format."""
    from exporters import spline_parts as parts
    pfx, sp, part = _spline_part(args)
    teeth = args.get(f'{pfx}teeth', args.get('teeth', ''))
    base = f"{args.get('family', 'HTD')}-{args.get('pitch', '5M')}-{teeth}T-spline-{part}"
    if part == 'washer':
        data = {'stl': parts.washer_stl, 'svg': parts.washer_svg, 'dxf': parts.washer_dxf}[fmt](sp)
    elif fmt == 'stl':
        lo, hi = _part_span(args, pfx)
        data = parts.shaft_stl(sp, hi - lo)
    else:
        data = {'svg': parts.shaft_svg, 'dxf': parts.shaft_dxf}[fmt](sp)
    return data, f'{base}.{fmt}'


def _send_spline_part(fmt, mimetype, embed):
    _consume_web_token(request)
    try:
        data, filename = _spline_part_file(request.args, fmt)
        data = embed(data, request.args)
        if isinstance(data, str):
            data = data.encode('utf-8')
        if _mirror_to_addins(data, filename):
            return ('', 204)
        return Response(data, mimetype=mimetype,
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except ValueError as e:
        return _api_error(str(e))
    except Exception as e:
        import traceback
        app.logger.error('spline part failed:\n%s', traceback.format_exc())
        return _api_error(f'Error generating the spline part: {e}')


@app.route('/download/spline-stl')
@charges.charged('stl')
def download_spline_stl():
    """The sample splined shaft or the splined washer (part=shaft|washer), STL."""
    return _send_spline_part('stl', 'model/stl', _embed_stl)


@app.route('/download/spline-svg')
@charges.charged('svg')
def download_spline_svg():
    """The sample shaft's or washer's outline, SVG."""
    return _send_spline_part('svg', 'image/svg+xml', _embed_svg)


@app.route('/download/spline-dxf')
@charges.charged('dxf')
def download_spline_dxf():
    """The sample shaft's or washer's outline, DXF (true lines and arcs)."""
    return _send_spline_part('dxf', 'application/dxf', _embed_dxf)


@app.route('/download/flange-stl')
@charges.charged('stl')
def download_flange_stl():
    """Return STL of a single flange plate.

    Required query params: family, pitch, teeth, bore, belt_height,
                           flange_which ('top'|'bottom'),
                           flange_3dprint ('1'|'0'),
                           flange_angle, flange_rim_radius, flange_height (3D print),
                           flange_plate_height, flange_bend_radius (metal).
    Optional: hub_od, spokes_enabled, spokes_hub_od, clearance_preset, backlash_preset.
    """
    _consume_web_token(request)
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
        # The spokes as fitted (_parse_spoke_params), like the pulley itself:
        # the typed values could leave the flange's inner edge off the real
        # spokes (fuzz: rim depth 6.3 typed, 4.1 built).
        spokes_enabled, spoke_hub_od, spoke_rim_depth = _parse_spoke_params(args)[:3]

        fp    = _parse_flange_params(args)
        which = args.get('flange_which', 'top')   # 'top' or 'bottom' (or 'both' for metal)

        # The page sends the short names; the pulley routes' hub_ names work too
        flat_d = max(0.0, float(args.get('flat_depth', args.get('hub_flat_depth', 0.0))))
        kw_w   = max(0.0, float(args.get('keyway_w', args.get('hub_keyway_w', 0.0))))
        kw_h   = max(0.0, float(args.get('keyway_h', args.get('hub_keyway_h', 0.0))))

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
                spline=_spline_of(args, ''),
            )
            suffix    = '-flange-only' if which == 'top' else '-lower-flange'
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
                spline=_spline_of(args, ''),
            )
            suffix = '-flanges'

        # the pulley's own name (the owner, 2026-10-05): GT-2M-30T-flange-only is
        # the separate printed top flange; P2's carries -P2 (pulley=2 names it only)
        p2_sfx = '-P2' if args.get('pulley') == '2' else ''
        if fp['flange_3dprint'] and which == 'top':
            filename = f'{family}-{pitch}-{num_teeth}T{p2_sfx}{suffix}.stl'
        else:
            type_tag = '3DP' if fp['flange_3dprint'] else 'Metal'
            filename = f'{family}-{pitch}-{num_teeth}T{p2_sfx}-{type_tag}{suffix}.stl'

        stl_bytes = _embed_stl(stl_bytes, request.args)
        return Response(
            stl_bytes,
            mimetype='model/stl',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        import traceback
        app.logger.error('Flange STL export failed:\n%s', traceback.format_exc())
        return _api_error('Flange STL export failed. The error has been logged.', 500)


@app.route('/download/flange-step')
@charges.charged('step')
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
            step_bytes = _run_ss_worker(dict(kw, export_type='flange'))
        except RuntimeError as _e:
            return _api_error(f'Flange STEP error: {_e}')

        suffix   = '-upper-flange' if which == 'top' else '-lower-flange'
        type_tag = '3DP' if fp['flange_3dprint'] else 'Metal'
        filename = f'{family}{pitch}-{num_teeth}T-{type_tag}{suffix}.step'
        step_bytes = _rename_step_product(step_bytes, filename[:-5])
        step_bytes = _embed_step(step_bytes, request.args)
        if _mirror_to_addins(step_bytes, filename):
            return ('', 204)
        return Response(step_bytes, mimetype='application/step',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except Exception as e:
        import traceback
        app.logger.error('Flange STEP export failed:\n%s', traceback.format_exc())
        return _api_error('Flange STEP export failed. The error has been logged.', 500)


@app.route('/download/flange-assembly')
@charges.charged('stl')
def download_flange_assembly():
    """Return an assembly STL: pulley body + bottom flange + integrated top flange + support ribs.

    Only available when the top flange is integrated (flange_top_separate=0) and
    the 3D-print mode is selected. The pulley body is generated fresh via
    generate_pulley_stl(); both flanges and any support ribs are concatenated
    (no boolean union — slicer handles overlapping).
    """
    _consume_web_token(request)
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
            spline=_spline_of(args, ''),
            spoke_count=sp_cnt if sp_en else 0,
            spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
            fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
            rim_depth_mm=sp_rim,
            spoke_height_mm=sp_h if sp_en else 0.0,
            set_screw=_set_screw(args),
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
        app.logger.error('Assembly STL export failed:\n%s', traceback.format_exc())
        return _api_error('Assembly STL export failed. The error has been logged.', 500)


# The GitHub issue comes from cct_common.bug_report: description only — no
# design, no address (see that module and the privacy policy).
from cct_common.bug_report import _create_github_issue as _cc_create_issue
from cct_common.bug_report import _report_id as _cc_report_id


def _notify_bug_report(report_id, report_label, timestamp, label_seeing, label_should,
                       seeing, should_see, email):
    """Email info@ that a report came in, through Resend: the description and
    the reporter's address (to reply), NOT the design — an emailed design
    can't be deleted on request; the log and the database copy can.
    Skips without RESEND_API_KEY; a send failure never fails the report."""
    if not os.environ.get('RESEND_API_KEY', '').strip():
        return
    try:
        body = (f'{report_label} {report_id} — {timestamp}\n'
                f'App Version: {APP_VERSION}   Build: {BUILD_TIME}\n\n'
                f'{label_seeing}:\n  {seeing or "(not provided)"}\n\n'
                f'{label_should}:\n  {should_see or "(not provided)"}\n\n'
                f'Contact email:\n  {email or "(not provided)"}\n\n'
                f'The design is in the report log / database under {report_id}.\n')
        _smtp_send('info@cheapcadtools.com', f'[Pulley Generator] {report_label} {report_id}', body)
    except Exception:
        pass


def _load_bug_issue_urls():
    try:
        with open(_BUG_ISSUE_URLS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_bug_issue_url(ts_id, url):
    """Link a newly-filed GitHub issue to a report — used by /api/report-bug
    below (the write side, kept local). The read/admin side
    (cct_common.bug_report_admin) reads the same bug_issue_urls.json file."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    urls = _load_bug_issue_urls()
    m = re.search(r'/issues/(\d+)$', url)
    urls[ts_id] = {'url': url, 'number': int(m.group(1)) if m else None, 'state': 'open'}
    with open(_BUG_ISSUE_URLS_FILE, 'w', encoding='utf-8') as f:
        json.dump(urls, f, indent=2)


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

        if not seeing and not should_see:
            return jsonify({'error': 'At least one description field is required.'}), 400

        is_feature   = report_type == 'feature'
        report_label = 'Feature Request' if is_feature else 'Bug Report'
        label_seeing = 'Would like to do' if is_feature else 'Currently seeing'
        label_should = "Why it's useful"  if is_feature else 'Should be seeing'

        os.makedirs(_LOG_DIR, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        report_id = _cc_report_id(timestamp, seeing, should_see)
        entry = (
            f'\nReport id:\n  {report_id}\n'
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
        if _bug_store is not None:          # lasts beyond this server (Cloud Run)
            _bug_store.add(report_id, report_type=report_type, seeing=seeing,
                           should_see=should_see, error_msg=error_msg,
                           user_comment=user_comment, email=email, state=state,
                           app_version=APP_VERSION)

        issue_url = _cc_create_issue(report_id, report_label, timestamp, label_seeing,
                                     label_should, seeing, should_see, report_type,
                                     'Timing Pulley Generator', APP_VERSION)
        if issue_url:
            _save_bug_issue_url(timestamp, issue_url)
            if _bug_store is not None:
                _bug_store.set_issue_url(report_id, issue_url)

        _notify_bug_report(report_id, report_label, timestamp, label_seeing, label_should,
                           seeing, should_see, email)
        return jsonify({'ok': True, 'issue_url': issue_url, 'report_id': report_id})
    except Exception as e:
        import traceback
        app.logger.error('bug report failed:\n%s', traceback.format_exc())
        return _api_error(str(e), 500)


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
# periodically. All of this — desktop licence activation, WooCommerce/LMFWC
# import, Autodesk App Store entitlement/IPN/webhook — is registered via
# cct_common.licensing.register_licensing_routes() near the bottom of this
# file, once all the env-var constants below are defined.
_WC_WEBHOOK_SECRET      = os.environ.get('WC_WEBHOOK_SECRET', '')
_LMFWC_SITE_URL         = os.environ.get('LMFWC_SITE_URL', 'https://cheapcadtools.com')
_LMFWC_CONSUMER_KEY     = os.environ.get('LMFWC_CONSUMER_KEY', '')
_LMFWC_CONSUMER_SECRET  = os.environ.get('LMFWC_CONSUMER_SECRET', '')


# /api/admin/licences (list/resend-email/reset) are registered via
# cct_common.licensing below. The licence purchase confirmation email
# template (formerly _send_licence_purchase_email) is now the
# licence_email_body callable passed to register_licensing_routes().


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
# Placeholders for future marketplace listings — not yet implemented. Once
# PulleyApp is listed on the OnShape App Store / SolidWorks App Store, each
# needs its own IPN/webhook route (mirroring /api/autodesk-ipn) and
# entitlement check, following the same pattern as _AUTODESK_APP_ID above.
_ONSHAPE_APP_ID      = os.environ.get('ONSHAPE_APP_ID', '')
_SOLIDWORKS_APP_ID   = os.environ.get('SOLIDWORKS_APP_ID', '')
# subscribers.json / purchases.json are written by cct_common.licensing
# (register_licensing_routes below), but still read directly here for the
# admin dashboard's health/subscribers/sales endpoints, which have no
# equivalent in cct_common.licensing's route set.
_SUBSCRIBERS_FILE   = os.path.join(_LOG_DIR, 'subscribers.json')
_PURCHASES_FILE     = os.path.join(_LOG_DIR, 'purchases.json')
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


# /api/provision, /api/autodesk-ipn, /api/woo-webhook, /api/subscribers/add,
# /api/subscribers/remove are now registered via cct_common.licensing below
# (register_licensing_routes). This also drops the dev backdoor
# (backdoor_key == 'xoot') that used to bypass entitlement checks here.


# ── Admin dashboard ──────────────────────────────────────────────────────────
# /admin is cct_common.admin (sign in as an ADMIN_EMAILS account), mounted
# below once the accounts and bug-report stores exist. The bearer-token
# /api/admin/* routes of the old admin_dashboard.html were retired
# 2026-09-27 (health, metrics, queue, sales, bug-report admin, …); only
# /api/admin/licences remains, registered with the desktop licensing
# routes (cct_common.licensing) until the desktop build is retired.


# ── Async Download with Job Queue ───────────────────────────────────────────

@app.route('/api/download/step-async', methods=['POST'])
@require_active_session
def api_download_step_async():
    """Start async STEP generation for a single pulley (P1 or P2).

    Mirrors the reliable all-step path: generate to a file on disk, then the
    client navigates to its /download/result/... link (a static file, instant
    response). A direct window.location to the slow /download/step route was
    being dropped ("Removed") by Chromium on some setups; serving a
    pre-generated static file avoids the multi-second navigation entirely.
    """
    try:
        query_params = request.get_json() or {}
        _consume_web_token_from_body(query_params)
        charge_ctx, refusal = charges.begin_async('step', query_params)
        if refusal:
            return refusal
        job = create_job('step', query_params)

        _app = app

        def generate_async():
            _ctx = _app.app_context()
            _ctx.push()
            start_job(job.id)
            try:
                with charges.charge_in_job(charge_ctx, '/api/download/step-async') as _charge_q:
                    pulley = query_params.get('pulley', '1')
                    family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
                        _parse_stl_params(query_params, pulley)
                    pfx = 'p2_' if pulley == '2' else ''
                    hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(query_params, pfx)
                    sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_c, sp_h, sp_split = \
                        _parse_spoke_params(query_params, pfx)
                    eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od
                    _fl_enabled = query_params.get(f'{pfx}flange_enabled') == '1'
                    fp = _parse_flange_params(query_params, pfx) if _fl_enabled else {}

                    update_progress(job.id, 20)

                    kw = dict(
                        family=family, pitch=pitch, num_teeth=num_teeth,
                        bore_mm=bore_mm, belt_height_mm=belt_height,
                        clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex,
                        hub_od_mm=eff_hub_od, hub_height_mm=hub_h,
                        screw_dia_mm=sd, screw_count=sc,
                        **_step_screw_kw(query_params, pfx),
                        captured_nut=cn, flat_depth_mm=fd,
                        keyway_w_mm=kw_w, keyway_h_mm=kw_h,
                        spline=_spline_of(query_params, pfx),
                        spoke_count=sp_c if sp_en else 0,
                        spoke_width_mm=sp_w, spoke_hub_od_mm=sp_hub,
                        rim_depth_mm=sp_rim, fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
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
                        plate_height_mm      = fp.get('plate_height_mm', 1.0),
                        bend_radius_mm       = fp.get('bend_radius_mm', 0.0),
                    )

                    update_progress(job.id, 30)
                    step_bytes = _run_ss_worker(dict(kw, export_type='pulley',
                                                     color=_step_color('p2_' if pulley == '2' else '')),
                                                timeout=110)
                    update_progress(job.id, 80)

                    p2_sfx = '-P2' if pulley == '2' else ''
                    fl_sfx = '+flanges' if _fl_enabled else ''
                    fname  = f'{family}-{pitch}-{num_teeth}T{p2_sfx}{fl_sfx}.step'
                    step_bytes = _rename_step_product(step_bytes, fname[:-5])
                    step_bytes = _embed_step(step_bytes, query_params)

                    dl_name = _safe_dl_name(fname)
                    result_url = save_result(_LOG_DIR, step_bytes, dl_name)
                    # Paid only once the file is fetched in full (results.py).
                    charges.await_delivery(_charge_q, result_url)
                    # Mirror with the RAW filename (keep any '+'): the CAD addins
                    # detect multi-body assemblies by a '+' in the name and must skip
                    # importToTarget for them. _safe_dl_name strips '+' for Chromium's
                    # benefit and is only needed for the browser download.
                    _mirrored = _mirror_to_addins(step_bytes, fname)

                    # The mirrored flag tells the client to skip the browser
                    # download when a CAD addin already received the file.
                    _j = get_job(job.id)
                    if _j is not None:
                        _j.mirrored = _mirrored

                    update_progress(job.id, 100)
                    _finish_job(job.id, output_file=result_url)
            except Exception as e:
                _finish_job(job.id, error=str(e))
            finally:
                _ctx.pop()

        if queue_off():
            generate_async()  # run in request thread — no daemon thread, no zombie
        else:
            threading.Thread(target=generate_async, daemon=True).start()
        return jsonify({
            'job_id': job.id,
            'status_url': f'/api/download-status/{job.id}',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
        # Finished on another server (Cloud Run) — see shared_jobs.py.
        shared = _shared_jobs.get(job_id) if _shared_jobs is not None else None
        if shared is not None:
            return jsonify(shared)
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job.to_dict())


def _step_kw_of(args, pfx):
    """The worker's keywords for one pulley (pfx '' or 'p2_') of a design's query —
    the all-parts and assembly STEPs' (the single-pulley route builds its own)."""
    family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
        _parse_stl_params(args, '2' if pfx == 'p2_' else '1')
    hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(args, pfx)
    sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_c, sp_h, sp_split = \
        _parse_spoke_params(args, pfx)
    eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od
    _fl_en = args.get(f'{pfx}flange_enabled') == '1'
    fp = _parse_flange_params(args, pfx) if _fl_en else {}
    return dict(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, belt_height_mm=belt_height,
        clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex,
        hub_od_mm=eff_hub_od, hub_height_mm=hub_h,
        screw_dia_mm=sd, screw_count=sc,
        **_step_screw_kw(args, pfx),
        captured_nut=cn, flat_depth_mm=fd,
        keyway_w_mm=kw_w, keyway_h_mm=kw_h,
        spline=_spline_of(args, pfx),
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


def _belt_step_kw(args, kw1, kw2):
    """The worker's belt keywords for a drive (both pulleys' keywords from _step_kw_of)."""
    key = _resolve_key(kw1['family'], kw1['pitch'])
    pitch_mm = (PULLEY_SPECS.get(key, {}) if key else {}).get('pitch', 5.0)
    default_c = (kw1['num_teeth'] + kw2['num_teeth']) * pitch_mm / (2.0 * math.pi)
    return dict(
        family         = kw1['family'],
        pitch          = kw1['pitch'],
        num_teeth_left = kw1['num_teeth'],
        num_teeth_right= kw2['num_teeth'],
        center_dist_mm = float(args.get('center_distance', default_c)),
        belt_height_mm = max(1.0, float(args.get('belt_height', 10.0))),
        n_belt_teeth   = int(args.get('n_belt', 0)),
    )


@app.route('/api/download/all-step-async', methods=['POST'])
@require_active_session
def api_download_all_step_async():
    """Start async STEP generation for all parts (P1, P2, belt).
    Returns immediately with job_id; client polls /api/download-status/{job_id}.

    Request body: URL query params as JSON
    Response: {"job_id": "a1b2c3d4", "status_url": "/api/download-status/a1b2c3d4"}
    """
    try:
        # Extract params from request JSON (convert from form params)
        query_params = request.get_json() or {}
        _consume_web_token_from_body(query_params)
        charge_ctx, refusal = charges.begin_async('step', query_params)
        if refusal:
            return refusal
        job = create_job('all-step', query_params)

        _app = app

        def generate_async():
            """Background worker: generate all STEP assembly.

            Runs in a daemon thread — push an app context so _mirror_to_addins
            and any other helper that calls current_app can find it.
            """
            _ctx = _app.app_context()
            _ctx.push()
            start_job(job.id)  # Move from queued to processing
            try:
                with charges.charge_in_job(charge_ctx, '/api/download/all-step-async') as _charge_q:
                    import json as _json
                    import subprocess
                    import sys

                    _build_kw = lambda pfx: _step_kw_of(query_params, pfx)   # noqa: E731

                    update_progress(job.id, 10)  # Parsing
                    dual = query_params.get('dual') == 'true'
                    kw1 = _build_kw('')
                    kw2 = _build_kw('p2_') if dual else None

                    update_progress(job.id, 20)  # Building params
                    belt_kw = _belt_step_kw(query_params, kw1, kw2) if dual else None

                    update_progress(job.id, 30)  # Generating STEP

                    async_worker_kw = dict(kw1, export_type='all', color=_step_color(''))
                    if kw2:
                        async_worker_kw['kw2'] = dict(kw2, color=_step_color('p2_'))
                    if belt_kw:
                        async_worker_kw['belt_kw'] = belt_kw
                    step_bytes = _run_ss_worker(async_worker_kw, timeout=110)
                    update_progress(job.id, 80)  # Writing file
                    # Embed the CCT signature so the CAD addins' watchers recognise
                    # the file and import it (they skip files without the marker).
                    step_bytes = _embed_step(step_bytes, query_params)
                    _t1 = kw1['num_teeth']
                    _fname = (f'{kw1["family"]}-{kw1["pitch"]}-{_t1}T+{kw2["num_teeth"]}T-all.step'
                             if kw2 else f'{kw1["family"]}-{kw1["pitch"]}-{_t1}T-all.step')
                    dl_name = _safe_dl_name(_fname)
                    result_url = save_result(_LOG_DIR, step_bytes, dl_name)
                    # Paid only once the file is fetched in full (results.py).
                    charges.await_delivery(_charge_q, result_url)
                    # Mirror with the RAW name (keep '+'): addins detect the '-all'
                    # assembly and '+' multi-body files to skip importToTarget.
                    _mirrored = _mirror_to_addins(step_bytes, _fname)
                    _j = get_job(job.id)
                    if _j is not None:
                        _j.mirrored = _mirrored

                    update_progress(job.id, 100)
                    _finish_job(job.id, output_file=result_url)

            except Exception as e:
                _finish_job(job.id, error=str(e))
            finally:
                _ctx.pop()

        if queue_off():
            generate_async()  # run in request thread — no daemon thread, no zombie
        else:
            threading.Thread(target=generate_async, daemon=True).start()

        return jsonify({
            'job_id': job.id,
            'status_url': f'/api/download-status/{job.id}',
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Session Management (Single-User Queue) ──────────────────────────────────

@_queue_route('/api/session/create', methods=['POST'])
def api_session_create():
    """Create a new session (immediate access or enqueue)."""
    result = create_session()
    return jsonify(result)


@_queue_route('/api/session/status', methods=['GET'])
def api_session_status():
    """Get status of a session."""
    session_id = request.args.get('session_id')
    if not session_id:
        return jsonify({'error': 'Missing session_id'}), 400
    status = get_session_status(session_id)
    return jsonify(status)


@_queue_route('/api/session/heartbeat', methods=['POST'])
def api_session_heartbeat():
    """Keep session alive (prevent idle timeout)."""
    session_id = request.json.get('session_id') if request.json else None
    if not session_id:
        return jsonify({'error': 'Missing session_id'}), 400
    success = heartbeat(session_id)
    return jsonify({'success': success})


@_queue_route('/api/session/release', methods=['POST'])
def api_session_release():
    """Release a session (manual end)."""
    data = request.json if request.is_json else {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'error': 'Missing session_id'}), 400
    release_session(session_id)
    return jsonify({'success': True})


@_queue_route('/api/queue/status', methods=['GET'])
def api_queue_status():
    """Get queue info for UI display."""
    return jsonify(get_queue_info())


@_queue_route('/api/test/reset', methods=['POST'])
def api_test_reset():
    """Reset all queue and session state. Enabled when PULLEY_TESTING=1 env var is set."""
    if not os.environ.get('PULLEY_TESTING'):
        return jsonify({'error': 'Not in testing mode'}), 403
    clear_all_state()
    return jsonify({'success': True})


@app.route('/api/trial/register', methods=['POST'])
def api_trial_register():
    """Register a download from a trial user (FreeCAD addin).

    Request: POST /api/trial/register
    Body: { "mid": "machine_id", "fmt": "step|dxf|svg" }

    Response: { "allowed": true/false, "count": N, "limit": 2 }
    """
    data = request.json if request.is_json else {}
    machine_id = data.get('mid', '').strip()
    fmt = data.get('fmt', 'step').strip()[:10]

    if not machine_id:
        return jsonify({'error': 'mid (machine_id) required'}), 400

    allowed, count, limit = register_trial_download(machine_id, fmt)

    return jsonify({
        'allowed': allowed,
        'count': count,
        'limit': limit,
        'message': 'Download registered' if allowed else f'Trial limit reached ({count}/{limit} this week)'
    }), 200 if allowed else 429


@app.route('/api/trial/status', methods=['GET'])
def api_trial_status():
    """Check trial download status for a machine.

    Request: GET /api/trial/status?mid=machine_id

    Response: { "count": N, "limit": 2, "week_start": "YYYY-MM-DD" }
    """
    machine_id = request.args.get('mid', '').strip()

    if not machine_id:
        return jsonify({'error': 'mid (machine_id) required'}), 400

    from datetime import datetime, timedelta

    data = _cc_load_trial_downloads()
    now = datetime.now()
    week_start = now - timedelta(days=now.weekday())

    if machine_id not in data:
        count = 0
    else:
        count = len([
            d for d in data[machine_id]
            if datetime.fromisoformat(d['timestamp']) >= week_start
        ])

    return jsonify({
        'count': count,
        'limit': _trial_downloads_per_week(),
        'week_start': week_start.strftime('%Y-%m-%d'),
    })


@_queue_route('/api/session/register-machine', methods=['POST'])
def api_session_register_machine():
    """Register machine_id with session for addin/CLI access.

    Was broken before this route was wired onto cct_common.job_queue: it
    referenced a module-level _SESSIONS dict that exporters/job_queue.py
    never actually had (sessions are file-backed, not an in-memory dict),
    so every call 500'd. register_machine_id() is the fix.
    """
    data = request.json if request.is_json else {}
    session_id = data.get('session_id')
    machine_id = data.get('machine_id')

    if not session_id or not machine_id:
        return jsonify({'error': 'Missing session_id or machine_id'}), 400

    if not _cc_register_machine_id(session_id, machine_id):
        return jsonify({'error': 'Session not found', 'code': 'SESSION_NOT_FOUND'}), 403

    return jsonify({
        'success': True,
        'session_id': session_id,
        'machine_id': machine_id
    }), 200


def _addin_design_params(body: dict) -> dict:
    """The design parameters of an add-in API request (they sit under
    "params", next to machine_id) — what the token charge prices."""
    params = body.get('params')
    return params if isinstance(params, dict) else {}


@app.route('/api/download/step', methods=['POST'])
@charges.charged('step', params_from=_addin_design_params)
def api_download_step():
    """API endpoint for addins to download STEP files.

    Request JSON: {machine_id, params_dict}
    Returns: STEP file or error
    """
    data = request.json if request.is_json else {}
    machine_id = data.get('machine_id')
    params_dict = data.get('params', {})

    if not machine_id:
        return jsonify({'error': 'Missing machine_id'}), 400

    # Weekly trial limit — replaced by per-export tokens when they're on.
    if not charges.enabled:
        allowed, count, limit = register_trial_download(machine_id, 'step')
        if not allowed:
            return jsonify({
                'error': f'Download limit reached: {count}/{limit} per week',
                'code': 'DOWNLOAD_LIMIT_EXCEEDED'
            }), 429

    try:
        pulley = params_dict.get('pulley', '1')
        family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
            _parse_stl_params(params_dict, pulley)
        pfx = 'p2_' if pulley == '2' else ''
        hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(params_dict, pfx)
        sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_c, sp_h, sp_split = _parse_spoke_params(params_dict, pfx)

        eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od

        _fl_enabled = params_dict.get(f'{pfx}flange_enabled') == '1'
        fp = _parse_flange_params(params_dict, pfx) if _fl_enabled else {}

        kw = dict(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, belt_height_mm=belt_height,
            clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex,
            hub_od_mm=eff_hub_od, hub_height_mm=hub_h,
            screw_dia_mm=sd, screw_count=sc,
            **_step_screw_kw(params_dict, pfx),
            captured_nut=cn, flat_depth_mm=fd,
            keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            spline=_spline_of(params_dict, pfx),
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

        step_data = _run_ss_worker(dict(kw, export_type='pulley',
                                        color=_step_color('p2_' if pulley == '2' else '')))
        step_data = _embed_step(step_data, params_dict)

        fname = f'{family}-{pitch}-{num_teeth}T.step'
        return Response(step_data, mimetype='application/octet-stream',
                       headers={'Content-Disposition': f'attachment; filename="{fname}"'})

    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


@app.route('/api/download/dxf', methods=['POST'])
@charges.charged('dxf', params_from=_addin_design_params)
def api_download_dxf():
    """API endpoint for addins to download DXF files."""
    data = request.json if request.is_json else {}
    machine_id = data.get('machine_id')
    params_dict = data.get('params', {})

    if not machine_id:
        return jsonify({'error': 'Missing machine_id'}), 400

    # Weekly trial limit — replaced by per-export tokens when they're on.
    if not charges.enabled:
        allowed, count, limit = register_trial_download(machine_id, 'dxf')
        if not allowed:
            return jsonify({
                'error': f'Download limit reached: {count}/{limit} per week',
                'code': 'DOWNLOAD_LIMIT_EXCEEDED'
            }), 429

    try:
        pulley = params_dict.get('pulley', '1')
        family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
            _parse_stl_params(params_dict, pulley)
        pfx = 'p2_' if pulley == '2' else ''
        hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(params_dict, pfx)

        dxf_data = dxf_exporter.export_dxf(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, hub_od_mm=hub_od, hub_height_mm=hub_h,
            screw_dia_mm=sd, screw_count=sc, captured_nut=cn,
            flat_depth_mm=fd, keyway_width_mm=kw_w, keyway_height_mm=kw_h,
            clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex
        )
        dxf_data = _embed_dxf(dxf_data, params_dict)

        fname = f'{family}-{pitch}-{num_teeth}T.dxf'
        return Response(dxf_data, mimetype='application/octet-stream',
                       headers={'Content-Disposition': f'attachment; filename="{fname}"'})

    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


@app.route('/api/download/stl', methods=['POST'])
@charges.charged('stl', params_from=_addin_design_params)
def api_download_stl():
    """API endpoint for addins to download STL files."""
    data = request.json if request.is_json else {}
    machine_id = data.get('machine_id')
    params_dict = data.get('params', {})

    if not machine_id:
        return jsonify({'error': 'Missing machine_id'}), 400

    # Weekly trial limit — replaced by per-export tokens when they're on.
    if not charges.enabled:
        allowed, count, limit = register_trial_download(machine_id, 'stl')
        if not allowed:
            return jsonify({
                'error': f'Download limit reached: {count}/{limit} per week',
                'code': 'DOWNLOAD_LIMIT_EXCEEDED'
            }), 429

    try:
        pulley = params_dict.get('pulley', '1')
        family, pitch, num_teeth, bore_mm, belt_height, cl_mm, bl_mm, pr_ex = \
            _parse_stl_params(params_dict, pulley)
        pfx = 'p2_' if pulley == '2' else ''
        hub_od, hub_h, sd, sc, cn, fd, kw_w, kw_h = _parse_hub_params(params_dict, pfx)
        sp_en, sp_hub, sp_rim, sp_w, sp_ft, sp_fb, sp_c, sp_h, sp_split = _parse_spoke_params(params_dict, pfx)

        eff_hub_od = sp_hub if (sp_en and sp_hub > bore_mm and hub_od <= bore_mm) else hub_od

        stl_data = generate_pulley_stl(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, belt_height_mm=belt_height,
            clearance_mm=cl_mm, backlash_mm=bl_mm, print_extra_mm=pr_ex,
            hub_od_mm=eff_hub_od, hub_height_mm=hub_h,
            screw_dia_mm=sd, screw_count=sc, captured_nut=cn, flat_depth_mm=fd,
            keyway_w_mm=kw_w, keyway_h_mm=kw_h,
            spline=_spline_of(params_dict, pfx),
            spoke_count=sp_c if sp_en else 0, spoke_hub_od_mm=sp_hub,
            rim_depth_mm=sp_rim, spoke_width_mm=sp_w,
            fillet_tip_mm=sp_ft, fillet_base_mm=sp_fb,
            spoke_height_mm=sp_h,
            set_screw=_set_screw(params_dict, pfx),
        )
        stl_data = _embed_stl(stl_data, params_dict)

        fname = f'{family}-{pitch}-{num_teeth}T.stl'
        return Response(stl_data, mimetype='model/stl',
                       headers={'Content-Disposition': f'attachment; filename="{fname}"'})

    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


@_queue_route('/queue')
def queue_page():
    """Queue management UI page."""
    return render_template('queue.html')


from cct_common.flask_shutdown import register_shutdown_route
from cct_common.live_reload import register_live_reload
from cct_common.flask_caching import (
    register_etag_caching, register_admin_cors, register_download_signal,
)
from cct_common.licensing import register_licensing_routes
register_shutdown_route(app)  # POST /api/shutdown — see cct_common/flask_shutdown.py
register_live_reload(app)  # /api/_boot_id + /_cct_live_reload.js — see cct_common/live_reload.py
# list/hash-lookup/delete/comment/github-close/github-sync — see
# cct_common/bug_report_admin.py. Parses this repo's own bug_reports.log
# format (written by the local /api/report-bug route) directly.
register_etag_caching(app, cacheable_prefixes=_CACHEABLE_PREFIXES,
                      build_time=BUILD_TIME, max_age=_CACHE_MAX_AGE)
register_admin_cors(app, prefixes=('/api/subscribers/',))   # the desktop licence flow
register_download_signal(app)  # default download_prefix='/download/' already matches


def _licence_email_subject(key, order_id, valid_until):
    return f'Your CheapCAD Tools licence key — Order #{order_id}'


def _licence_email_body(key, order_id, valid_until):
    return (
        f'Hi,\n\n'
        f'Thank you for purchasing CheapCAD Tools Timing Pulleys for FreeCAD!\n\n'
        f'Your licence key is:\n\n'
        f'    {key}\n\n'
        f'Valid until: {valid_until}\n\n'
        f'--- How to activate ---\n\n'
        f'1. Open FreeCAD\n\n'
        f'2. Go to Part Design menu → Timing Pulley (or use the toolbar)\n\n'
        f'3. In the CCT panel, click "Paid Local App"\n\n'
        f'4. Paste your licence key and click Activate\n\n'
        f'5. The app will download and install automatically\n\n'
        f'The key can be used on up to 2 computers. If you need to move\n'
        f'to a new machine, contact support@cheapcadtools.com to reset.\n\n'
        f'Order: #{order_id}\n\n'
        f'If you have any questions, reply to this email or visit\n'
        f'https://cheapcadtools.com/contact/\n\n'
        f'— CheapCAD Tools'
    )


def _purchase_welcome_email_body(txn_id):
    return (
        f'Hi,\n\n'
        f'Thank you for subscribing to CheapCAD Tools on the Autodesk App Store!\n\n'
        f'Your subscription is now active. Restart Fusion 360 and the CheapCAD Tools '
        f'panel will install automatically.\n\n'
        f'Transaction ID: {txn_id}\n\n'
        f'If you have any questions, reply to this email or visit '
        f'https://cheapcadtools.com/contact/\n\n'
        f'— CheapCAD Tools'
    )


register_licensing_routes(
    app, admin_secret=_PROVISION_SECRET, log_dir=_LOG_DIR,
    app_download_url=_APP_DOWNLOAD_URL, app_version=_APP_VERSION,
    app_changelog=_APP_CHANGELOG, runtime_url=_RUNTIME_URL,
    runtime_version=_RUNTIME_VERSION, licence_b64=_LICENCE_B64,
    licence_expiry=_LICENCE_EXPIRY, email_sender=_smtp_send,
    licence_email_subject=_licence_email_subject,
    licence_email_body=_licence_email_body,
    purchase_welcome_email_subject='Welcome to CheapCAD Tools — your subscription is active',
    purchase_welcome_email_body=_purchase_welcome_email_body,
    wc_webhook_secret=_WC_WEBHOOK_SECRET, lmfwc_site_url=_LMFWC_SITE_URL,
    lmfwc_consumer_key=_LMFWC_CONSUMER_KEY, lmfwc_consumer_secret=_LMFWC_CONSUMER_SECRET,
    autodesk_app_id=_AUTODESK_APP_ID,
)
# /api/desktop/{licence-import,licence-import-wc,activate,verify},
# /api/admin/licences[/<key>/resend-email|reset], /api/provision,
# /api/autodesk-ipn, /api/woo-webhook, /api/subscribers/{add,remove} — see
# cct_common/licensing.py. Deliberately drops the old dev backdoor
# (backdoor_key == 'xoot') that used to bypass entitlement checks.


# ── Accounts and tokens (ADR-008) ─────────────────────────────────────────────
# Off unless TOKENS_ENABLED=1 — the live site is unchanged until accounts,
# charging and the UI are all done. See accounts_setup.py.
from accounts_setup import (
    init_accounts, make_backup_alert, make_email_sender, make_inactivity_notify,
    make_limit_notify,
)
from cct_common.deploy_mode import is_live as _cc_is_live

_ACCOUNTS_LIVE = _cc_is_live('CCT_ACCOUNTS_MODE')
_accounts_email = make_email_sender(
    _smtp_send, live=_ACCOUNTS_LIVE,
    has_key=bool(os.environ.get('RESEND_API_KEY', '').strip()),
    logger=app.logger)
def _payment_settings():
    from cct_common.payments import DEFAULT_PACKS, PayPalConfig, StripeConfig, parse_packs
    env = lambda k: os.environ.get(k, '').strip()
    return {
        'packs': parse_packs(env('TOKEN_PACKS')) if env('TOKEN_PACKS') else DEFAULT_PACKS,
        'paypal': PayPalConfig(env('PAYPAL_CLIENT_ID'), env('PAYPAL_CLIENT_SECRET'),
                               env('PAYPAL_WEBHOOK_ID'), live=env('PAYPAL_LIVE') == '1'),
        'stripe': StripeConfig(env('STRIPE_SECRET_KEY'), env('STRIPE_WEBHOOK_SECRET')),
    }


_accounts_state = init_accounts(
    app, log_dir=_LOG_DIR,
    enabled=os.environ.get('TOKENS_ENABLED') == '1',
    live=_ACCOUNTS_LIVE,
    email_sender=_accounts_email,
    signup_grant=int(os.environ.get('TOKENS_SIGNUP_GRANT', '20')),   # 20 x 5 cents = $1
    # Hourly verified backups (cct_common.db_backup); none under the test
    # harness. The off-server copy (Cloud Storage) plugs in as backup_upload.
    backup_dir=None if os.environ.get('PULLEY_TESTING') else os.path.join(_LOG_DIR, 'backups'),
    backup_alert=make_backup_alert(
        _accounts_email, alert_to=os.environ.get('BACKUP_ALERT_EMAIL', '').strip(),
        logger=app.logger),
    # Daily: free tokens expire after 2 years without a sign-in; after 5 every
    # account closes and its remaining tokens expire — each after a reminder
    # email. Not in tests.
    inactivity_notify=None if os.environ.get('PULLEY_TESTING') else make_inactivity_notify(
        _accounts_email, app_name='CheapCAD Tools',
        site_url=os.environ.get('SITE_URL', 'https://cheapcadtools.com/tools/pulleys')),
    # Daily token limit on each add-in/agent token (blank: no limit by default).
    device_daily_budget=(int(os.environ['TOKENS_DEVICE_DAILY_BUDGET'])
                         if os.environ.get('TOKENS_DEVICE_DAILY_BUDGET', '').strip()
                         else 100),
    # Postgres on Cloud Run (a Secret Manager secret); unset = the SQLite file.
    database_url=os.environ.get('DATABASE_URL', '').strip() or None,
    # New accounts per network per day that get the free signup tokens —
    # later ones are still made, without them. Blank or 0: no limit.
    signups_per_ip_per_day=int(os.environ.get('TOKENS_SIGNUPS_PER_IP_PER_DAY', '2') or 0) or None,
    # "Continue with Google / Microsoft / GitHub" — each appears once its
    # client id and secret are set (secrets from Secret Manager on Cloud Run).
    # Redirect URI to register: <site>/account/oauth/<provider>/callback
    oauth_clients={p: (os.environ.get(f'{p.upper()}_CLIENT_ID', '').strip(),
                       os.environ.get(f'{p.upper()}_CLIENT_SECRET', '').strip())
                   for p in ('google', 'microsoft', 'github')},
    # Token packs at /account/buy (cct_common.payments): PayPal for $2/$5,
    # Stripe from $5 — each provider once its keys are set (secrets from
    # Secret Manager). PAYPAL_LIVE=1 leaves the PayPal sandbox. TOKEN_PACKS
    # overrides the packs, e.g. "paypal:2=20,5=50;stripe:5=50,10=100,25=250".
    payments=_payment_settings(),
)
charges.attach(app, _accounts_state,  # per-export token charging — see charging.py
               # The buy page, once a payment provider is set up.
               buy_url=(os.environ.get('TOKENS_BUY_URL', '').strip()
                        or ('/account/buy' if _accounts_state.payment_providers else '')),
               limit_notify=make_limit_notify(_accounts_email, app_name='CheapCAD Tools'))

# Finished background downloads live at unguessable, expiring links —
# see results.py (replaces the old /download/<job_id>.step). RESULTS_BUCKET
# (Cloud Run) keeps them in Cloud Storage so any server can serve them.
import results as _results
from results import register_result_routes, save_result
_results.configure(bucket=os.environ.get('RESULTS_BUCKET', '').strip() or None)
register_result_routes(app, log_dir=_LOG_DIR)

# Finished jobs' status in the database too, for status polls that land on
# another server (Cloud Run) — see shared_jobs.py. Only with DATABASE_URL:
# a single server (local, Render) answers from memory.
from shared_jobs import SharedJobs
_shared_jobs = (SharedJobs(os.environ['DATABASE_URL'].strip())
                if os.environ.get('DATABASE_URL', '').strip() else None)

# Bug reports in the database too, when there is one — see bug_store.py.
from bug_store import BugReports
_bug_store = (BugReports(os.environ['DATABASE_URL'].strip())
              if os.environ.get('DATABASE_URL', '').strip() else None)

# Every download that fails — a route's error answer, a failed job, or what the
# page saw (a request cut off at the time limit, a lost instance) — for the admin
# page's Download failures tab (cct_common.download_failures). The database on
# Cloud Run; locally a file beside the logs.
from cct_common.download_failures import (DownloadFailures, record_job_failure,
                                          register_download_failure_log)
_failure_store = None
try:
    _failure_store = DownloadFailures(os.environ.get('DATABASE_URL', '').strip()
                                      or os.path.join(_LOG_DIR, 'download_failures.db'))
except Exception:
    app.logger.exception('download failures will not be kept: the store would not open')
register_download_failure_log(app, _failure_store, app_name='pulleys', app_version=APP_VERSION)

if _accounts_state.accounts is not None:
    from cct_common.admin import register_admin
    register_admin(app, _accounts_state.accounts, app_name='Timing Pulleys',
                   app_version=APP_VERSION, bug_reports=_bug_store, default_app='pulleys',
                   download_failures=_failure_store)


def _finish_job(job_id, **kw):
    finish_job(job_id, **kw)
    if kw.get('error'):
        record_job_failure(_failure_store, get_job(job_id), str(kw['error']),
                           app_name='pulleys', app_version=APP_VERSION)
    if _shared_jobs is not None:
        try:
            _shared_jobs.publish(get_job(job_id))
        except Exception:
            app.logger.exception('Could not publish job %s to the shared store', job_id)

# The download window's zip: every ticked file for every part shown, one
# charge for the whole design — see bundles.py.
# The agent interface (ADR-019): /api/v1/describe, check, files and quote, for
# cct_common's MCP gateway and CAD plugins.
import agent as _agent
_agent.register(app, charges, version=APP_VERSION)

from bundles import register_bundle_routes
register_bundle_routes(
    app, log_dir=_LOG_DIR, record_trial=_consume_web_token_from_body,
    create_job=create_job, start_job=start_job, update_progress=update_progress,
    finish_job=_finish_job, guard=require_active_session,
    run_inline=queue_off,
)


if __name__ == '__main__':
    import argparse as _ap
    _p = _ap.ArgumentParser()
    _p.add_argument('--port', type=int, default=5000)
    _p.add_argument('--no-debug', action='store_true')
    _args, _ = _p.parse_known_args()
    _debug = not _args.no_debug and not os.environ.get('PULLEY_TESTING')
    app.run(debug=_debug, host='0.0.0.0', port=_args.port)
