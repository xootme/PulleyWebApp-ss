"""
tests/run_tests.py — PulleyWebApp test runner with live SSE dashboard.

Usage:
    python tests/run_tests.py [--flask-port 5099] [--dash-port 5098]
                               [--skip-slow] [--no-browser] [--exit-when-done]

Architecture:
  1. A ThreadingHTTPServer on --dash-port serves the dashboard HTML and SSE stream.
     It also exposes POST /result so the pytest plugin can push results in real time.
  2. A Flask process starts on --flask-port (PULLEY_TESTING=1, no debugger reloader).
  3. A single pytest subprocess runs all non-queue tests; the plugin posts each result
     to /result as it completes — instant dashboard update, no polling.
  4. Queue tests run inline (need the live Flask) after pytest finishes.
  5. The browser opens after discovery so the pending list is already populated.
"""

import argparse
import importlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT    = Path(__file__).parent.parent
VENV_PY = ROOT / '.venv312' / 'Scripts' / 'python.exe'
if not VENV_PY.exists():
    VENV_PY = Path(sys.executable)

# ── Test group labels ──────────────────────────────────────────────────────────

PYTEST_GROUPS = {
    'test_api':             'API Endpoints',
    'test_exporters':       'Exporters',
    'test_belt':            'Belt Geometry',
    'test_invalid_inputs':  'Input Validation',
    'test_priority':        'Priority Logic',
    'test_spokes':          'Spoke Geometry',
    'test_3d':              '3D Generation',
    'test_flange_geometry': 'Flange Geometry',
    'test_flange':          'Flange Export',
    'test_benchmarks':      'Benchmarks',
    'test_repro':           'Regression',
    'test_nightly_random':  'Nightly Random',
}
QUEUE_FILE = 'test_queue_pytest'

SLOW_TESTS = {
    'test_idle_timeout_drops_active_session',
    'test_stale_queued_session_removed',
    'test_heartbeat_prevents_idle_timeout',
    'test_state_persistence_across_restart',
}

# ── Shared state ───────────────────────────────────────────────────────────────

_lock = threading.Lock()
_state = {
    'server':         'starting',
    'started':        datetime.now().isoformat(),
    'finished':       None,
    'tests':          [],
    'group_timings':  {},
    'random_configs': [],   # [{run_idx, family, pitch, ...}] set when nightly runs
    'flask_url':      'http://localhost:5000',
}
_subscribers  = []   # SSE client queues
_group_starts = {}   # {group: float timestamp}


def _snapshot():
    with _lock:
        return json.loads(json.dumps(_state))


def _push(data):
    """Push event to all SSE clients. Must NOT be called while holding _lock."""
    payload = json.dumps(data)
    # Snapshot subscribers without holding lock during the slow put loop
    with _lock:
        subs = list(_subscribers)
    dead = []
    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            dead.append(q)
    if dead:
        with _lock:
            for q in dead:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass


def _add_test(name, group, slow=False):
    with _lock:
        _state['tests'].append({
            'name': name, 'group': group, 'slow': slow,
            'status': 'pending', 'started': None, 'ended': None,
            'duration': None, 'error': None,
        })


def _update_test(name, **kwargs):
    event = None
    with _lock:
        for t in _state['tests']:
            if t['name'] == name:
                t.update(kwargs)
                if kwargs.get('status') in ('passed', 'failed', 'skipped'):
                    t['ended'] = datetime.now().isoformat()
                    if t['started']:
                        s = datetime.fromisoformat(t['started'])
                        e = datetime.fromisoformat(t['ended'])
                        t['duration'] = round((e - s).total_seconds(), 1)
                event = {'type': 'test', **t}
                break
    if event:
        _push(event)


def _update_test_fuzzy(name, **kwargs):
    """Update test by name with fuzzy matching — handles Class::method vs method mismatches."""
    parts      = name.split('::')
    candidates = set('::'.join(parts[i:]) for i in range(len(parts)))
    event = None
    with _lock:
        for t in _state['tests']:
            if t['name'] in candidates or name.endswith(t['name']):
                t.update(kwargs)
                if kwargs.get('status') in ('passed', 'failed', 'skipped'):
                    t['ended'] = datetime.now().isoformat()
                    if t.get('started'):
                        s = datetime.fromisoformat(t['started'])
                        e = datetime.fromisoformat(t['ended'])
                        t['duration'] = round((e - s).total_seconds(), 1)
                event = {'type': 'test', **t}
                break
    if event:
        _push(event)


def _set_server(status):
    with _lock:
        _state['server'] = status
    _push({'type': 'server', 'server': status})


def _finish():
    with _lock:
        _state['finished'] = datetime.now().isoformat()
    _push({'type': 'finished', 'finished': _state['finished']})


def _group_start(group):
    now = time.time()
    with _lock:
        _group_starts[group] = now
    _push({'type': 'group_start', 'group': group,
           'started': datetime.fromtimestamp(now).isoformat()})


def _group_end(group):
    now = time.time()
    with _lock:
        start = _group_starts.get(group)
        if start:
            dur = round(now - start, 1)
            _state['group_timings'][group] = dur
    dur = _state['group_timings'].get(group)
    if dur is not None:
        _push({'type': 'group_end', 'group': group, 'duration': dur})


def _reset_state(flask_url=None):
    with _lock:
        _state['server']         = 'starting'
        _state['started']        = datetime.now().isoformat()
        _state['finished']       = None
        _state['tests']          = []
        _state['group_timings']  = {}
        _state['random_configs'] = []
        if flask_url:
            _state['flask_url']  = flask_url
        _group_starts.clear()
    _push({'type': 'clear', 'started': _state['started']})


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PulleyWebApp — Test Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Segoe UI", system-ui, sans-serif; background: #0f0f13; color: #e0e0e8; padding: 24px; }
h1   { font-size: 22px; color: #f1f5f9; margin-bottom: 4px; }
.sub { font-size: 12px; color: #666; margin-bottom: 20px; }
.meta { display: flex; gap: 16px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
.badge { font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 4px; }
.badge-ok   { background: #1a4a2a; color: #2ecc71; }
.badge-err  { background: #4a1a1a; color: #e74c3c; }
.badge-warn { background: #3a3a1a; color: #f1c40f; }
.conn-dot   { font-size: 11px; color: #888; }
.conn-dot.live { color: #2ecc71; }
.countdown  { font-size: 13px; color: #888; margin-left: auto; font-family: monospace; }
.countdown.warn   { color: #f1c40f; }
.countdown.urgent { color: #e74c3c; font-weight: 700; }
.progress-wrap { background: #1a1a20; border-radius: 6px; height: 8px; margin-bottom: 24px; overflow: hidden; }
.progress-bar  { height: 100%; border-radius: 6px; transition: width 0.3s; background: #3498db; width: 0%; }
.stats { display: flex; gap: 24px; margin-bottom: 24px; flex-wrap: wrap; }
.stat { text-align: center; }
.stat-num { font-size: 32px; font-weight: 700; }
.stat-lbl { font-size: 11px; color: #888; }
.ok  { color: #2ecc71; } .err { color: #e74c3c; } .skip { color: #888; } .run { color: #3498db; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 12px; color: #666; font-weight: 600;
     border-bottom: 1px solid #222; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
td { padding: 7px 12px; border-bottom: 1px solid #16161c; vertical-align: top; }
tr.sec-hdr td { font-size: 13px; font-weight: 700; padding: 14px 12px 7px;
                border-top: 2px solid #2a2a30; }
tr.run-hdr  td { color: #3498db; border-top-color: #1a3a5a; background: #090d14; }
tr.done-hdr td { color: #2ecc71; border-top-color: #1a3a2a; background: #090e0b; }
tr.todo-hdr td { color: #888;    border-top-color: #222;    background: #0f0f13; }
tr.grp-row td  { background: #111116; color: #aaa; font-size: 12px; font-weight: 700;
                  letter-spacing: .04em; text-transform: uppercase; padding: 7px 12px 4px; }
tr.row-passed { background: #091409; color: #d0ecd0; }
tr.row-failed { background: #1c0a0a; color: #f0c0c0; }
tr.row-running{ background: #090d14; color: #b0d0f0; animation: pulse 1.4s ease-in-out infinite; }
tr.row-skipped { opacity: 0.5; }
tr.row-pending { opacity: 0.6; color: #aaa; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.6} }
td.icon { width: 26px; font-size: 14px; text-align: center; color: #888; }
td.icon.ok  { color: #2ecc71; } td.icon.err { color: #e74c3c; }
td.icon.run { color: #3498db; }
td.grp  { width: 200px; color: #bbb; font-size: 12px; font-weight: 500; }
td.dur  { width: 72px; color: #eee; text-align: right; font-size: 13px; font-weight: 700; }
.grp-timer  { float: right; font-size: 12px; font-family: monospace; color: #888; }
.grp-timer.live { color: #5dade2; font-weight: 600; }
.grp-timer.warn { color: #f1c40f; font-weight: 600; }
.grp-timer.done { color: #666; }
.slow-badge { font-size: 10px; background: #2a2a10; color: #aaa;
              border-radius: 3px; padding: 1px 5px; margin-left: 6px; }
.raw-name   { font-size: 11px; color: #666; margin-left: 8px; font-family: monospace; }
.error-box  { font-size: 11px; color: #e74c3c; margin-top: 4px; font-family: monospace;
              white-space: pre-wrap; max-height: 100px; overflow-y: auto;
              background: #1a0808; padding: 5px 8px; border-radius: 3px; }
.repro-btn  { font-size: 10px; font-weight: 600; padding: 2px 8px; margin-left: 8px;
              background: #1a3a5a; color: #3498db; border: 1px solid #1e4a70;
              border-radius: 3px; cursor: pointer; text-decoration: none;
              vertical-align: middle; display: inline-block; }
.repro-btn:hover { background: #1e4a70; }
</style>
</head>
<body>
<h1>PulleyWebApp — Test Dashboard</h1>
<div class="sub" id="sub">Loading…</div>

<div class="meta">
  <span id="srv" class="badge badge-warn">⏳ Starting…</span>
  <span id="summary"></span>
  <span id="dot" class="conn-dot">● connecting</span>
  <span id="cd" class="countdown" style="display:none"></span>
</div>

<div class="progress-wrap"><div class="progress-bar" id="bar"></div></div>

<div class="stats">
  <div class="stat"><div class="stat-num ok"   id="n-pass">0</div><div class="stat-lbl">Passed</div></div>
  <div class="stat"><div class="stat-num err"  id="n-fail">0</div><div class="stat-lbl">Failed</div></div>
  <div class="stat"><div class="stat-num skip" id="n-skip">0</div><div class="stat-lbl">Skipped</div></div>
  <div class="stat"><div class="stat-num run"  id="n-run">0</div><div class="stat-lbl">Running</div></div>
  <div class="stat"><div class="stat-num"      id="n-pend">0</div><div class="stat-lbl">Pending</div></div>
  <div class="stat"><div class="stat-num"      id="n-tot">0</div><div class="stat-lbl">Total</div></div>
</div>

<table>
  <thead><tr>
    <th style="width:26px"></th>
    <th>Test</th>
    <th style="width:200px">Group</th>
    <th style="width:72px;text-align:right">Time</th>
  </tr></thead>
  <tbody id="tbody"></tbody>
</table>

<script>
// ── State ──────────────────────────────────────────────────────────────────────
let tests         = [];
let started       = null;
let finished      = null;
let timerID       = null;
let grpStarts     = {};   // {group → Date}
let grpActual     = {};   // {group → seconds}  (this run)
let grpHistory    = {};   // {group → seconds}  (last run, from localStorage)
let randomConfigs = [];   // [{run_idx, family, pitch, ...}] for nightly tests
let flaskUrl      = 'http://localhost:5000';

const HIST_KEY  = 'cctp_grp_timings';
const DUR_KEY   = 'cctp_last_duration';

function loadHistory() {
  try { grpHistory = JSON.parse(localStorage.getItem(HIST_KEY) || '{}'); }
  catch(_) { grpHistory = {}; }
}
loadHistory();

function saveHistory() {
  if (!finished) return;
  Object.assign(grpHistory, grpActual);
  localStorage.setItem(HIST_KEY, JSON.stringify(grpHistory));
  const dur = Math.round((new Date(finished) - new Date(started)) / 1000);
  if (dur > 5) localStorage.setItem(DUR_KEY, dur);
}

// ── Helpers ────────────────────────────────────────────────────────────────────
const ICON = { pending:'○', running:'⏳', passed:'✓', failed:'✗', skipped:'–' };
const ICON_CLS = { passed:'ok', failed:'err', running:'run' };

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function pretty(name) {
  return name.replace(/^.*::/, '').replace(/^test_/,'').replace(/_/g,' ')
             .replace(/\[.*\]$/,'').replace(/\b\w/g,c=>c.toUpperCase());
}

function rowId(name) { return 'r-' + name.replace(/\W/g,'-'); }

function reproUrl(name) {
  // Extract config index from e.g. "test_random_stl_preview[2]"
  const m = name.match(/\[(\d+)\]$/);
  if (!m) return null;
  const idx = parseInt(m[1]);
  const cfg = randomConfigs[idx];
  if (!cfg) return null;
  const params = Object.entries(cfg)
    .filter(([k, v]) => k !== 'run_idx' && !(k === 'dual' && !v))
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v === true ? 'true' : v)}`)
    .join('&');
  return `${flaskUrl}/?${params}&sv=1`;
}

function makeRow(t) {
  const dur  = t.duration != null ? t.duration + 's' : (t.status==='running' ? '…' : '');
  const err  = t.error ? `<div class="error-box">${esc(t.error)}</div>` : '';
  const slow = t.slow  ? '<span class="slow-badge">slow</span>' : '';
  const raw  = `<span class="raw-name">${esc(t.name)}</span>`;
  const ic   = ICON_CLS[t.status] || '';
  // Repro button for nightly random tests
  let repro = '';
  if (t.group === 'Nightly Random') {
    const url = reproUrl(t.name);
    if (url) repro = `<a class="repro-btn" href="${url}" target="pulley-repro">▶ View in App</a>`;
  }
  return `<tr id="${rowId(t.name)}" class="row-${t.status}">
    <td class="icon ${ic}">${ICON[t.status]||'?'}</td>
    <td>${pretty(t.name)}${slow}${raw}${repro}${err}</td>
    <td class="grp">${esc(t.group)}</td>
    <td class="dur">${dur}</td>
  </tr>`;
}

// ── Group timer label ──────────────────────────────────────────────────────────
function grpTimerHtml(group) {
  const actual = grpActual[group];
  if (actual != null) return `<span class="grp-timer done">${actual}s</span>`;
  const hist = grpHistory[group];
  const start = grpStarts[group];
  if (start && hist) {
    const rem = Math.max(0, hist - Math.round((Date.now() - start) / 1000));
    const cls = rem < 10 ? 'warn' : 'live';
    return `<span class="grp-timer ${cls}">~${rem}s left</span>`;
  }
  if (hist) return `<span class="grp-timer">~${hist}s</span>`;
  return '';
}

// ── Stats + progress bar ───────────────────────────────────────────────────────
function updateStats() {
  const c = {passed:0,failed:0,skipped:0,running:0,pending:0};
  for (const t of tests) c[t.status] = (c[t.status]||0) + 1;
  const total = tests.length, done = c.passed+c.failed+c.skipped;
  const pct   = total ? Math.round(100*done/total) : 0;
  const col   = c.failed ? '#e74c3c' : (done===total&&total ? '#2ecc71' : '#3498db');
  document.getElementById('n-pass').textContent = c.passed;
  document.getElementById('n-fail').textContent = c.failed;
  document.getElementById('n-skip').textContent = c.skipped;
  document.getElementById('n-run').textContent  = c.running;
  document.getElementById('n-pend').textContent = c.pending;
  document.getElementById('n-tot').textContent  = total;
  const bar = document.getElementById('bar');
  bar.style.width = pct + '%';
  bar.style.background = col;
  const sb = document.getElementById('summary');
  if (finished) {
    sb.className   = 'badge ' + (c.failed ? 'badge-err' : 'badge-ok');
    sb.textContent = c.failed ? `✗ ${c.failed} failed` : '✓ All passed';
  }
}

// ── Elapsed + overall countdown ────────────────────────────────────────────────
function updateTimers() {
  if (!started) return;
  const now  = finished ? new Date(finished) : new Date();
  const secs = Math.round((now - new Date(started)) / 1000);
  document.getElementById('sub').textContent =
    'Started ' + started.slice(0,19).replace('T',' ') + ' · Elapsed ' + secs + 's';

  const el = document.getElementById('cd');
  if (finished) { el.style.display = 'none'; return; }
  const groups  = [...new Set(tests.map(t=>t.group))];
  const estTotal= groups.reduce((s,g)=>s+(grpHistory[g]||0), 0)
                  || parseInt(localStorage.getItem(DUR_KEY)||'0');
  if (!estTotal) { el.style.display='none'; return; }
  const rem = estTotal - secs;
  el.style.display = '';
  if (rem <= 0) {
    el.textContent = 'Finishing…'; el.className = 'countdown urgent';
  } else {
    const m=Math.floor(rem/60), s=rem%60;
    el.textContent = m > 0 ? `~${m}m ${s}s remaining` : `~${s}s remaining`;
    el.className   = 'countdown' + (rem<30?' urgent':rem<60?' warn':'');
  }
}

// ── Table render ───────────────────────────────────────────────────────────────
function rebuildTable() {
  const running  = tests.filter(t => t.status==='running');
  const done     = tests.filter(t => ['passed','failed','skipped'].includes(t.status));
  const pending  = tests.filter(t => t.status==='pending');

  function section(list, label, cls, alwaysShow=false) {
    if (!list.length && !alwaysShow) return '';
    let html = `<tr class="sec-hdr ${cls}-hdr"><td colspan="4">${label}</td></tr>`;
    if (!list.length) {
      html += `<tr><td></td><td colspan="3" style="color:#333;font-style:italic;padding:7px 12px">All done</td></tr>`;
      return html;
    }
    let prevGrp = null;
    for (const t of list) {
      if (t.group !== prevGrp) {
        html += `<tr class="grp-row">
          <td></td>
          <td>${esc(t.group)}</td>
          <td></td>
          <td style="text-align:right">${grpTimerHtml(t.group)}</td>
        </tr>`;
        prevGrp = t.group;
      }
      html += makeRow(t);
    }
    return html;
  }

  document.getElementById('tbody').innerHTML =
    section(running, '⏳ Running',                           'run') +
    section(done,    '✓ Finished',                           'done') +
    section(pending, `○ Upcoming — ${pending.length} tests`, 'todo', true);
}

// ── Server badge ───────────────────────────────────────────────────────────────
function setServer(s) {
  const el = document.getElementById('srv');
  const M  = { starting:['badge-warn','⏳ Starting…'],
               running: ['badge-ok',  '● Server Running'],
               failed:  ['badge-err', '✗ Server Failed'] };
  const [cls,txt] = M[s] || ['badge-warn', s];
  el.className = 'badge '+cls; el.textContent = txt;
}

// ── Apply full state snapshot ──────────────────────────────────────────────────
function applyState(state) {
  tests         = state.tests    || [];
  started       = state.started;
  finished      = state.finished;
  randomConfigs = state.random_configs || [];
  if (state.flask_url) flaskUrl = state.flask_url;
  if (state.group_timings) Object.assign(grpActual, state.group_timings);
  loadHistory();
  setServer(state.server || 'starting');
  rebuildTable(); updateStats(); updateTimers();
}

// ── SSE connection ─────────────────────────────────────────────────────────────
function connect() {
  const es  = new EventSource('/events');
  const dot = document.getElementById('dot');
  es.onopen = () => { dot.textContent='● live'; dot.className='conn-dot live'; };
  es.onerror = () => {
    dot.textContent='○ reconnecting…'; dot.className='conn-dot';
    es.close(); setTimeout(connect, 3000);
  };
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.type === 'random_configs') {
      randomConfigs = d.configs || [];
      rebuildTable();
    } else if (d.type === 'clear') {
      tests=[];started=d.started;finished=null;grpStarts={};grpActual={};randomConfigs=[];
      loadHistory();
      document.getElementById('summary').textContent='';
      document.getElementById('summary').className='';
      document.getElementById('cd').style.display='none';
      document.getElementById('bar').style.cssText='width:0%;background:#3498db';
      setServer('starting'); rebuildTable(); updateStats();
    } else if (d.type === 'test') {
      const i = tests.findIndex(t=>t.name===d.name);
      if (i>=0) tests[i]=d; else tests.push(d);
      rebuildTable(); updateStats();
    } else if (d.type === 'group_start') {
      grpStarts[d.group] = new Date(d.started);
      rebuildTable();
    } else if (d.type === 'group_end') {
      grpActual[d.group] = d.duration;
      rebuildTable();
    } else if (d.type === 'server') {
      setServer(d.server);
    } else if (d.type === 'finished') {
      finished=d.finished; updateStats(); updateTimers(); saveHistory();
      if (timerID) { clearInterval(timerID); timerID=null; }
    } else if (d.type === 'state_ready') {
      fetch('/state').then(r=>r.json()).then(applyState).catch(()=>{});
    }
  };
}

// ── Boot ───────────────────────────────────────────────────────────────────────
fetch('/state').then(r=>r.json()).then(applyState).catch(()=>{});
connect();
timerID = setInterval(()=>{ updateTimers(); rebuildTable(); }, 1000);
</script>
</body>
</html>"""


# ── SSE / result HTTP server ───────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        if self.path == '/':
            self._send(200, 'text/html; charset=utf-8', _HTML.encode())
        elif self.path == '/state':
            body = json.dumps(_snapshot()).encode()
            self._send(200, 'application/json', body,
                       extra=[('Access-Control-Allow-Origin', '*')])
        elif self.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            q = queue.Queue(maxsize=500)
            with _lock:
                _subscribers.append(q)
            try:
                while True:
                    try:
                        payload = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b'data: {"type":"ping"}\n\n')
                        self.wfile.flush()
                        continue
                    self.wfile.write(f'data: {payload}\n\n'.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass
        else:
            self._send(404, 'text/plain', b'Not found')

    def do_POST(self):
        if self.path == '/result':
            # Pytest plugin posts each test result here in real time
            length = int(self.headers.get('Content-Length', 0))
            data   = json.loads(self.rfile.read(length))
            name   = data.get('name', '')
            status = data.get('status', 'failed')
            error  = data.get('error')
            # Try exact match first, then suffix match (handles Class::method vs method)
            _update_test_fuzzy(name, status=status,
                               started=data.get('started', datetime.now().isoformat()),
                               error=error)
            self._send(200, 'application/json', b'{"ok":true}')
        else:
            self._send(404, 'text/plain', b'Not found')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _send(self, code, ct, body, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def start_dash_server(port):
    srv = ThreadingHTTPServer(('127.0.0.1', port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ── Pytest plugin (injected via conftest) ──────────────────────────────────────

_PLUGIN_TEMPLATE = '''
import requests as _req, datetime as _dt

_DASH_URL = "http://127.0.0.1:{port}/result"
_started  = {{}}

def pytest_runtest_logstart(nodeid, location):
    _started[nodeid] = _dt.datetime.now().isoformat()

def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    nodeid   = report.nodeid          # tests/test_foo.py::Class::test_name
    parts    = nodeid.split("::")
    name     = "::".join(parts[1:])   # Class::test_name
    started  = _started.pop(nodeid, _dt.datetime.now().isoformat())

    if report.passed:
        status, error = "passed", None
    elif report.skipped:
        status, error = "skipped", None
    else:
        status = "failed"
        error  = str(report.longrepr)[:800] if report.longrepr else None

    try:
        _req.post(_DASH_URL,
                  json={{"name": name, "status": status,
                         "started": started, "error": error}},
                  timeout=2)
    except Exception:
        pass
'''


def _write_plugin(dash_port):
    """Write the plugin as a conftest plugin file. Returns path for cleanup."""
    path = ROOT / 'tests' / '_dash_plugin.py'
    path.write_text(_PLUGIN_TEMPLATE.format(port=dash_port), encoding='utf-8')
    return path


def _plugin_args(plugin_path):
    """Return pytest args to load the plugin. Uses -p with the module stem."""
    # Add the tests/ dir to PYTHONPATH so pytest can import _dash_plugin
    return ['-p', plugin_path.stem]


# ── Flask server ───────────────────────────────────────────────────────────────

def start_flask(port):
    import requests as _req
    env  = {**os.environ, 'PULLEY_TESTING': '1'}
    proc = subprocess.Popen(
        [str(VENV_PY), 'app.py', '--port', str(port), '--no-debug'],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f'http://localhost:{port}'
    for _ in range(30):
        try:
            if _req.get(base, timeout=1).status_code < 500:
                _set_server('running')
                return proc, base
        except Exception:
            pass
        time.sleep(1)
    _set_server('failed')
    proc.terminate()
    return None, base


# ── Test discovery ─────────────────────────────────────────────────────────────

def collect_pytest_tests():
    """Return list of (test_name, group, node_id) for all non-queue test files."""
    env = {**os.environ}
    # Pass nightly flag through so pytest --collect-only sees the skip markers correctly
    result = subprocess.run(
        [str(VENV_PY), '-m', 'pytest', 'tests/', '--collect-only',
         '--ignore=tests/run_tests.py',
         f'--ignore=tests/{QUEUE_FILE}.py',
         '--ignore=tests/_dash_plugin.py'],
        cwd=str(ROOT), capture_output=True, text=True, env=env
    )
    tests = []
    for line in (result.stdout + result.stderr).splitlines():
        line = line.strip()
        if '::' not in line or line.startswith('=') or line.startswith(' '):
            continue
        parts     = line.split('::')
        file_stem = Path(parts[0]).stem
        test_name = '::'.join(parts[1:])
        group     = PYTEST_GROUPS.get(file_stem,
                       file_stem.replace('test_', '').replace('_', ' ').title())
        tests.append((test_name, group, line))
    return tests


def load_random_configs():
    """Load the most recent nightly random config file into _state['random_configs'].
    Called after discovery so the dashboard can build repro URLs for each test.
    """
    if os.environ.get('PULLEY_NIGHTLY') != '1':
        return
    log_dir = ROOT / 'logs' / 'nightly_random'
    files   = sorted(log_dir.glob('*.json')) if log_dir.exists() else []
    if not files:
        return
    try:
        data    = json.loads(files[-1].read_text(encoding='utf-8'))
        configs = data.get('configs', [])
        with _lock:
            _state['random_configs'] = configs
        _push({'type': 'random_configs', 'configs': configs})
    except Exception:
        pass


def collect_queue_tests(tmod, skip_slow):
    """Return list of (group, cls, method, skip) for queue tests."""
    groups = [
        ('Queue System — Functional', tmod.TestSessionBasics),
        ('Queue System — Timeouts',   tmod.TestSessionTimeouts),
        ('Queue System — Stress',     tmod.TestStress),
        ('Trial Downloads',           tmod.TestTrialDownloads),
    ]
    plan = []
    for group_name, cls in groups:
        for method in sorted(m for m in dir(cls) if m.startswith('test_')):
            is_slow = method in SLOW_TESTS
            _add_test(method, group_name, slow=is_slow)
            plan.append((group_name, cls, method, is_slow and skip_slow))
    return plan


# ── Test runners ───────────────────────────────────────────────────────────────

def run_pytest(pytest_tests, dash_port):
    """Run all non-queue tests in one pytest process.

    Results stream to the dashboard two ways:
    1. Live stdout parsing — pytest -v prints PASSED/FAILED/SKIPPED immediately
       as each test finishes, giving instant per-test visibility.
    2. Plugin POSTs — provides error details for failures via /result.
    """
    if not pytest_tests:
        return True

    plugin_path = _write_plugin(dash_port)

    # Build name → (group) lookup for stdout parsing
    name_to_group = {n: g for n, g, _ in pytest_tests}

    seen_groups = []
    for _, group, _ in pytest_tests:
        if group not in seen_groups:
            seen_groups.append(group)
            _group_start(group)

    env = {**os.environ,
           'PYTHONPATH': str(ROOT / 'tests') + os.pathsep + os.environ.get('PYTHONPATH', '')}
    plugin_args = _plugin_args(plugin_path)

    # Use Popen + stdout streaming so we can parse results line-by-line
    proc = subprocess.Popen(
        [str(VENV_PY), '-m', 'pytest', 'tests/',
         '--ignore=tests/run_tests.py',
         f'--ignore=tests/{QUEUE_FILE}.py',
         '--ignore=tests/_dash_plugin.py',
         *plugin_args,
         '--tb=no',   # errors come via plugin; suppress inline tracebacks
         '-v',        # verbose: prints one line per test as it finishes
         '--no-header', '--color=no'],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    # Parse pytest verbose output: "tests/test_foo.py::Class::test_name PASSED"
    # Update dashboard immediately when each line arrives — no waiting for plugin
    import re as _re
    _STATUS_RE = _re.compile(
        r'^(tests/[^\s]+)::([^\s]+)\s+(PASSED|FAILED|SKIPPED|ERROR)\s*$'
    )

    for line in proc.stdout:
        line = line.rstrip()
        m = _STATUS_RE.match(line)
        if not m:
            continue
        node_parts = m.group(2)   # e.g. "TestClass::test_name" or "test_name"
        raw_status = m.group(3).lower()
        status = 'failed' if raw_status in ('failed', 'error') else raw_status

        # Update dashboard — fuzzy match handles Class::method vs method
        _update_test_fuzzy(node_parts, status=status,
                           started=datetime.now().isoformat())

    proc.wait()

    for group in seen_groups:
        _group_end(group)

    # Any still-pending tests were not collected (likely import errors)
    with _lock:
        for t in _state['tests']:
            if t['status'] == 'pending' and t['group'] in seen_groups:
                t['status'] = 'failed'
                t['error']  = 'Not collected by pytest (possible import error)'
                _push({'type': 'test', **t})

    plugin_path.unlink(missing_ok=True)
    return proc.returncode == 0


def run_queue_test(group_name, cls, method_name, skip, tmod):
    import requests as _req
    if skip:
        _update_test(method_name, status='skipped')
        return True

    _update_test(method_name, status='running', started=datetime.now().isoformat())
    instance = cls()
    try:
        _req.post(f'{tmod.BASE_URL}/api/test/reset', timeout=5)
    except Exception:
        pass
    try:
        getattr(instance, method_name)()
        _update_test(method_name, status='passed')
        return True
    except Exception as e:
        tb = traceback.format_exc()
        _update_test(method_name, status='failed',
                     error=f'{type(e).__name__}: {e}\n{tb[-600:]}')
        return False
    finally:
        try:
            _req.post(f'{tmod.BASE_URL}/api/test/reset', timeout=5)
        except Exception:
            pass


# ── Main ───────────────────────────────────────────────────────────────────────

def _matches(name, group, filters_test, filters_group):
    """Return True if this test should run given the CLI filters."""
    if filters_test:
        return any(f.lower() in name.lower() for f in filters_test)
    if filters_group:
        return any(f.lower() in group.lower() for f in filters_group)
    return True


def main():
    ap = argparse.ArgumentParser(
        description='PulleyWebApp test runner with live dashboard.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples:
  python tests/run_tests.py                           # all tests
  python tests/run_tests.py --group "Queue System"   # one group
  python tests/run_tests.py --group Stress --group Functional
  python tests/run_tests.py --test test_burst_join   # single test by name fragment
  python tests/run_tests.py --test test_burst --test test_fast
''')
    ap.add_argument('--flask-port',     type=int, default=5099)
    ap.add_argument('--dash-port',      type=int, default=5098)
    ap.add_argument('--skip-slow',      action='store_true')
    ap.add_argument('--no-browser',     action='store_true')
    ap.add_argument('--exit-when-done', action='store_true')
    ap.add_argument('--test',  dest='tests',  action='append', default=[],
                    metavar='NAME',  help='Run tests whose name contains NAME (repeatable)')
    ap.add_argument('--group', dest='groups', action='append', default=[],
                    metavar='GROUP', help='Run tests in groups matching GROUP (repeatable)')
    args = ap.parse_args()

    _reset_state(flask_url=f'http://localhost:{args.flask_port}')
    start_dash_server(args.dash_port)
    dash_url = f'http://localhost:{args.dash_port}/'
    print(f'Dashboard : {dash_url}')

    # Start Flask
    print(f'Flask     : starting on port {args.flask_port}…')
    proc, base_url = start_flask(args.flask_port)
    if proc is None:
        print('ERROR: Flask failed to start.')
        _finish()
        sys.exit(1)
    print(f'Flask     : ready at {base_url}')

    # Discover all tests and register only the filtered ones as pending
    sys.path.insert(0, str(ROOT))
    all_pytest = collect_pytest_tests()
    pytest_tests = [(n, g, nid) for n, g, nid in all_pytest
                    if _matches(n, g, args.tests, args.groups)]
    for name, group, _ in pytest_tests:
        _add_test(name, group)

    import tests.test_queue_pytest as tmod
    os.environ['PULLEY_TEST_URL'] = base_url
    tmod.BASE_URL = base_url
    importlib.reload(tmod)
    tmod.BASE_URL = base_url
    all_queue = collect_queue_tests(tmod, args.skip_slow)
    # collect_queue_tests already called _add_test — re-filter and skip non-matching
    queue_plan = [(g, cls, m, skip or not _matches(m, g, args.tests, args.groups))
                  for g, cls, m, skip in all_queue]

    if args.tests or args.groups:
        active = sum(1 for _, _, _, skip in queue_plan if not skip) + len(pytest_tests)
        print(f'Filter    : running {active} matching test(s)')

    # Load nightly random configs into state (noop unless PULLEY_NIGHTLY=1)
    load_random_configs()

    # Push state_ready → open browsers re-fetch /state to see pending list
    _push({'type': 'state_ready', 'started': _state['started']})

    # Open browser after discovery so /state is populated
    if not args.no_browser:
        webbrowser.open(dash_url)

    # ── Run tests ──────────────────────────────────────────────────────────────
    any_failed = False

    # 1. All matching non-queue tests via single pytest process + plugin
    if not run_pytest(pytest_tests, args.dash_port):
        any_failed = True

    # 2. Queue tests inline (need live Flask)
    cur_group = None
    for group_name, cls, method, skip in queue_plan:
        if group_name != cur_group:
            if cur_group:
                _group_end(cur_group)
            cur_group = group_name
            _group_start(group_name)
        if not run_queue_test(group_name, cls, method, skip, tmod):
            any_failed = True
    if cur_group:
        _group_end(cur_group)

    _finish()

    with _lock:
        p = sum(1 for t in _state['tests'] if t['status'] == 'passed')
        f = sum(1 for t in _state['tests'] if t['status'] == 'failed')
        s = sum(1 for t in _state['tests'] if t['status'] == 'skipped')
    print(f'Results   : {p} passed  {f} failed  {s} skipped')

    if args.exit_when_done:
        proc.terminate()
        proc.wait()
        sys.exit(1 if any_failed else 0)

    # Keep Flask alive so repro buttons in dashboard remain clickable
    print(f'Dashboard : {dash_url}')
    print(f'App       : {args.app_url}  (repro buttons use this — Ctrl+C to quit)')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    proc.terminate()
    proc.wait()
    sys.exit(1 if any_failed else 0)


if __name__ == '__main__':
    main()
