"""
test_dashboard.py — live SSE test-runner dashboard, shared engine.

Extracted from PulleyWebApp-ss's tests/run_tests.py (2026-07-23), which had
grown into a genuinely reusable pattern: a ThreadingHTTPServer serves a
live-updating dashboard (SSE) while a single pytest subprocess streams each
test's result to it in real time, plus an optional second phase for tests
that need a live server running in-process (a project's own async queue
system, etc. — anything that can't run inside the pytest subprocess).

A project wires this up with a small tests/run_tests.py that builds a
RunnerConfig and calls run(config):

    from pathlib import Path
    import sys
    from cct_common.test_dashboard import RunnerConfig, run

    ROOT = Path(__file__).parent.parent

    def _server_cmd(port: int) -> list[str]:
        return [str(ROOT / '.venv' / 'Scripts' / 'python.exe'), 'app.py',
                '--port', str(port), '--no-debug']

    cfg = RunnerConfig(
        title='EBoxDesigner — Test Dashboard',
        root=ROOT,
        pytest_groups={'test_bloat': 'Bloat', 'test_holes': 'Holes'},
        server_cmd=_server_cmd,
        server_env={'EBOX_TESTING': '1'},
    )
    sys.exit(run(cfg))

See PulleyWebApp-ss/tests/run_tests.py for the full-featured reference
wrapper (inline_batches for its async job-queue tests, a repro-link builder
for nightly random configs, a warnings_fn for connected-CAD-addin banners)
and EBoxDesigner-ss/tests/run_tests.py for a minimal one (pytest only, no
extra hooks).

Everything here is deliberately generic: no project's app.py, test module
names, or feature set is referenced. Project-specific behavior — which
test files map to which dashboard group, how to start the dev server, any
non-pytest tests that need a live server, any extra dashboard warnings —
is supplied entirely through RunnerConfig fields and hook callables.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Sequence

# ── Public config surface ───────────────────────────────────────────────

InlineTest = tuple  # (group_name: str, test_name: str, run_fn: Callable[[], None])


@dataclass
class RunnerConfig:
    title: str
    root: Path
    venv_py: Path | None = None
    """Python interpreter used for the pytest subprocess and (if server_cmd
    doesn't build its own) the dev server. Falls back to sys.executable."""

    pytest_groups: dict[str, str] = field(default_factory=dict)
    """test_foo.py stem -> dashboard group label. Any file not listed gets
    an auto-generated label (stem, 'test_' stripped, title-cased)."""

    always_ignore: list[str] = field(default_factory=list)
    """Extra --ignore=tests/... args always passed to pytest (beyond the
    runner's own files, which are always excluded)."""

    extra_pytest_args: list[str] = field(default_factory=list)
    """Extra raw pytest args, e.g. ['-m', 'not freecad'] to exclude a slow
    marker by default. Applied to both discovery and the real run, so the
    dashboard's pending list matches what actually executes."""

    optional_test_files: list[tuple[str, str]] = field(default_factory=list)
    """[(env_var_name, 'tests/test_foo.py'), ...] -- each file is ignored
    UNLESS os.environ[env_var_name] == '1'. Mirrors PulleyWebApp-ss's
    PULLEY_NIGHTLY-gated tests/test_nightly_random.py."""

    slow_tests: set[str] = field(default_factory=set)
    """Method names (not full node ids) flagged 'slow' in the dashboard --
    purely cosmetic (a badge), doesn't affect whether they run."""

    server_cmd: Callable[[int], list[str]] | None = None
    """Given a port, return the subprocess argv to launch the dev server.
    If None, no server is started (pytest-only projects)."""

    server_env: dict[str, str] = field(default_factory=dict)
    """Extra env vars merged into os.environ for the server subprocess."""

    server_ready_check: Callable[[str], bool] | None = None
    """Given the server's base URL, return True once it's ready. Default:
    GET / and accept any status < 500."""

    inline_batches: Callable[[str], list["InlineBatch"]] | None = None
    """Given the server's base URL, return extra tests that must run
    in-process against the LIVE server (not inside the pytest subprocess) --
    e.g. a project's own async job-queue test suite. Called once, after the
    server is up and pytest discovery has happened, before pytest runs."""

    pre_discovery_hook: Callable[[], None] | None = None
    """Called once, after the server is confirmed ready, before pytest
    test discovery. E.g. pre-generating nightly random configs so pytest
    fixtures just load a file instead of generating them mid-run."""

    warnings_fn: Callable[[], list[str]] | None = None
    """Called once at startup; each returned string renders as a dashboard
    warning banner (e.g. 'a connected CAD addin may bypass the path this
    run is testing'). Return [] or None for no banner."""

    repro_group: str | None = None
    """If set, tests in this dashboard group whose node id ends in '[N]'
    get a 'View in App' button built client-side from repro_configs_fn()[N]
    and the running server's URL (query-string params = the config dict)."""
    repro_configs_fn: Callable[[], list[dict]] | None = None

    flask_port: int = 5099
    dash_port: int = 5098


@dataclass
class InlineBatch:
    group: str
    tests: list[tuple[str, Callable[[], None], bool]]
    """[(test_name, run_fn, skip), ...] -- run_fn takes no args and raises
    on failure; skip marks it 'skipped' without calling run_fn."""


# ── Dashboard state (per run() call — no module-level globals, so a single
#    process could in principle drive more than one dashboard) ────────────

class _DashboardState:
    def __init__(self, title: str, flask_url: str):
        self.lock = threading.Lock()
        self.title = title
        self.state = {
            'server':         'starting',
            'started':        datetime.now().isoformat(),
            'finished':       None,
            'tests':          [],
            'group_timings':  {},
            'repro_configs':  [],
            'flask_url':      flask_url,
            'warnings':       [],
        }
        self.subscribers: list[queue.Queue] = []
        self.group_starts: dict[str, float] = {}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def push(self, data):
        payload = json.dumps(data)
        with self.lock:
            subs = list(self.subscribers)
        dead = []
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        if dead:
            with self.lock:
                for q in dead:
                    try:
                        self.subscribers.remove(q)
                    except ValueError:
                        pass

    def add_test(self, name, group, slow=False):
        with self.lock:
            self.state['tests'].append({
                'name': name, 'group': group, 'slow': slow,
                'status': 'pending', 'started': None, 'ended': None,
                'duration': None, 'error': None,
            })

    def update_test(self, name, **kwargs):
        event = None
        with self.lock:
            for t in self.state['tests']:
                if t['name'] == name:
                    t.update(kwargs)
                    self._stamp_duration(t, kwargs)
                    event = {'type': 'test', **t}
                    break
        if event:
            self.push(event)

    def update_test_fuzzy(self, name, **kwargs):
        """Update by name with fuzzy matching -- handles Class::method vs
        bare method mismatches between pytest's node id and our own
        dashboard-registered name."""
        parts = name.split('::')
        candidates = set('::'.join(parts[i:]) for i in range(len(parts)))
        event = None
        with self.lock:
            for t in self.state['tests']:
                if t['name'] in candidates or name.endswith(t['name']):
                    t.update(kwargs)
                    self._stamp_duration(t, kwargs)
                    event = {'type': 'test', **t}
                    break
        if event:
            self.push(event)

    @staticmethod
    def _stamp_duration(t, kwargs):
        if kwargs.get('status') in ('passed', 'failed', 'skipped'):
            t['ended'] = datetime.now().isoformat()
            if t.get('started'):
                s = datetime.fromisoformat(t['started'])
                e = datetime.fromisoformat(t['ended'])
                t['duration'] = round((e - s).total_seconds(), 1)

    def set_server(self, status):
        with self.lock:
            self.state['server'] = status
        self.push({'type': 'server', 'server': status})

    def finish(self):
        with self.lock:
            self.state['finished'] = datetime.now().isoformat()
        self.push({'type': 'finished', 'finished': self.state['finished']})

    def group_start(self, group):
        now = time.time()
        with self.lock:
            self.group_starts[group] = now
        self.push({'type': 'group_start', 'group': group,
                   'started': datetime.fromtimestamp(now).isoformat()})

    def group_end(self, group):
        now = time.time()
        with self.lock:
            start = self.group_starts.get(group)
            if start:
                self.state['group_timings'][group] = round(now - start, 1)
        dur = self.state['group_timings'].get(group)
        if dur is not None:
            self.push({'type': 'group_end', 'group': group, 'duration': dur})


# ── Dashboard HTML (title + repro-group are the only per-project bits) ──

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{TITLE}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: "Segoe UI", system-ui, sans-serif; background: #0f0f13; color: #e0e0e8; padding: 24px; }}
h1   {{ font-size: 22px; color: #f1f5f9; margin-bottom: 4px; }}
.sub {{ font-size: 12px; color: #666; margin-bottom: 20px; }}
.meta {{ display: flex; gap: 16px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }}
.badge {{ font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 4px; }}
.badge-ok   {{ background: #1a4a2a; color: #2ecc71; }}
.badge-err  {{ background: #4a1a1a; color: #e74c3c; }}
.badge-warn {{ background: #3a3a1a; color: #f1c40f; }}
.warn-banner {{ background: #3a2a0a; color: #f1c40f; border: 1px solid #6a5010;
              border-radius: 6px; padding: 10px 14px; margin: 12px 0;
              font-size: 14px; font-weight: 600; }}
.conn-dot   {{ font-size: 11px; color: #888; }}
.conn-dot.live {{ color: #2ecc71; }}
.countdown  {{ font-size: 13px; color: #888; margin-left: auto; font-family: monospace; }}
.countdown.warn   {{ color: #f1c40f; }}
.countdown.urgent {{ color: #e74c3c; font-weight: 700; }}
.progress-wrap {{ background: #1a1a20; border-radius: 6px; height: 8px; margin-bottom: 24px; overflow: hidden; }}
.progress-bar  {{ height: 100%; border-radius: 6px; transition: width 0.3s; background: #3498db; width: 0%; }}
.stats {{ display: flex; gap: 24px; margin-bottom: 24px; flex-wrap: wrap; }}
.stat {{ text-align: center; }}
.stat-num {{ font-size: 32px; font-weight: 700; }}
.stat-lbl {{ font-size: 11px; color: #888; }}
.ok  {{ color: #2ecc71; }} .err {{ color: #e74c3c; }} .skip {{ color: #888; }} .run {{ color: #3498db; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }}
col.c-icon {{ width: 32px; }}
col.c-name {{ width: auto; }}
col.c-grp  {{ width: 200px; }}
col.c-dur  {{ width: 80px; }}
th {{ text-align: left; padding: 8px 12px; color: #aaa; font-weight: 700;
     border-bottom: 2px solid #333; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
th:last-child {{ text-align: right; }}
td {{ padding: 7px 12px; border-bottom: 1px solid #1a1a20; vertical-align: top; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
td.name {{ white-space: normal; }}
tr.sec-hdr td {{ font-size: 13px; font-weight: 700; padding: 14px 12px 7px;
                border-top: 2px solid #2a2a30; }}
tr.run-hdr  td {{ color: #3498db; border-top-color: #1a3a5a; background: #090d14; }}
tr.done-hdr td {{ color: #2ecc71; border-top-color: #1a3a2a; background: #090e0b; }}
tr.todo-hdr td {{ color: #888;    border-top-color: #222;    background: #0f0f13; }}
tr.grp-row td  {{ background: #111116; color: #aaa; font-size: 12px; font-weight: 700;
                  letter-spacing: .04em; text-transform: uppercase; padding: 7px 12px 4px; }}
tr.row-passed {{ background: #091409; }}
tr.row-failed {{ background: #1c0a0a; }}
tr.row-running{{ background: #090d14; animation: pulse 1.4s ease-in-out infinite; }}
tr.row-skipped {{ opacity: 0.5; }}
tr.row-pending {{ opacity: 0.6; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.6}} }}
td.icon {{ width: 26px; font-size: 14px; text-align: center; color: #666; }}
td.icon.ok  {{ color: #2ecc71; }} td.icon.err {{ color: #e74c3c; }}
td.icon.run {{ color: #3498db; }}
td.name {{ color: #e8e8ee; font-size: 13px; }}
td.grp  {{ width: 200px; color: #7ec8e3; font-size: 12px; font-weight: 600; }}
td.dur  {{ width: 72px; color: #f0f0f0; text-align: right; font-size: 13px; font-weight: 700; }}
.grp-timer  {{ float: right; font-size: 12px; font-family: monospace; color: #888; }}
.grp-timer.live {{ color: #5dade2; font-weight: 600; }}
.grp-timer.warn {{ color: #f1c40f; font-weight: 600; }}
.grp-timer.done {{ color: #666; }}
.slow-badge {{ font-size: 10px; background: #2a2a10; color: #aaa;
              border-radius: 3px; padding: 1px 5px; margin-left: 6px; }}
.raw-name   {{ font-size: 11px; color: #666; margin-left: 8px; font-family: monospace; }}
.error-box  {{ font-size: 11px; color: #e74c3c; margin-top: 4px; font-family: monospace;
              white-space: pre-wrap; max-height: 100px; overflow-y: auto;
              background: #1a0808; padding: 5px 8px; border-radius: 3px; }}
.current-test {{ font-size: 13px; font-family: monospace; padding: 8px 14px;
                border-radius: 6px; margin-bottom: 12px; min-height: 36px;
                background: #0d1520; border-left: 4px solid #3498db; color: #b0d0f0; }}
.current-test.cur-pass {{ background: #0a1a0e; border-left-color: #2ecc71; color: #a0d4a0; }}
.current-test.cur-fail {{ background: #1c0a0a; border-left-color: #e74c3c; color: #e0a0a0; }}
.current-test.cur-run  {{ background: #0d1520; border-left-color: #3498db; color: #b0d0f0; }}
.repro-btn  {{ font-size: 10px; font-weight: 600; padding: 2px 8px; margin-left: 8px;
              background: #1a3a5a; color: #3498db; border: 1px solid #1e4a70;
              border-radius: 3px; cursor: pointer; text-decoration: none;
              vertical-align: middle; display: inline-block; }}
.repro-btn:hover {{ background: #1e4a70; }}
</style>
</head>
<body>
<h1>{TITLE}</h1>
<div class="sub" id="sub">Loading…</div>

<div id="warn-banner" class="warn-banner" style="display:none"></div>

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

<div class="current-test" id="current-test"></div>

<table>
  <colgroup>
    <col class="c-icon">
    <col class="c-name">
    <col class="c-grp">
    <col class="c-dur">
  </colgroup>
  <thead><tr>
    <th></th>
    <th>Test</th>
    <th>Group</th>
    <th style="text-align:right">Time</th>
  </tr></thead>
  <tbody id="tbody"></tbody>
</table>

<script>
// ── State ──────────────────────────────────────────────────────────────────────
let tests         = [];
let started       = null;
let finished      = null;
let timerID       = null;
let grpStarts     = {{}};   // {{group -> Date}}
let grpActual     = {{}};   // {{group -> seconds}}  (this run)
let grpHistory    = {{}};   // {{group -> seconds}}  (last run, from localStorage)
let reproConfigs  = [];     // [{{...}}] for the configured repro group, if any
let flaskUrl      = '{FLASK_URL}';
const REPRO_GROUP = {REPRO_GROUP_JSON};

const HIST_KEY  = 'cct_dash_grp_timings::{DASH_KEY}';
const DUR_KEY   = 'cct_dash_last_duration::{DASH_KEY}';

function loadHistory() {{
  try {{ grpHistory = JSON.parse(localStorage.getItem(HIST_KEY) || '{{}}'); }}
  catch(_) {{ grpHistory = {{}}; }}
}}
loadHistory();

function saveHistory() {{
  if (!finished) return;
  Object.assign(grpHistory, grpActual);
  localStorage.setItem(HIST_KEY, JSON.stringify(grpHistory));
  const dur = Math.round((new Date(finished) - new Date(started)) / 1000);
  if (dur > 5) localStorage.setItem(DUR_KEY, dur);
}}

// ── Helpers ────────────────────────────────────────────────────────────────────
const ICON = {{ pending:'○', running:'⏳', passed:'✓', failed:'✗', skipped:'–' }};
const ICON_CLS = {{ passed:'ok', failed:'err', running:'run' }};

function esc(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

function pretty(name) {{
  return name.replace(/^.*::/, '').replace(/^test_/,'').replace(/_/g,' ')
             .replace(/\[.*\]$/,'').replace(/\b\w/g,c=>c.toUpperCase());
}}

function rowId(name) {{ return 'r-' + name.replace(/\W/g,'-'); }}

function reproUrl(name) {{
  if (!REPRO_GROUP) return null;
  const m = name.match(/\[(\d+)\]$/);
  if (!m) return null;
  const idx = parseInt(m[1]);
  const cfg = reproConfigs[idx];
  if (!cfg) return null;
  const params = Object.entries(cfg)
    .filter(([k, v]) => k !== 'run_idx' && !(k === 'dual' && !v))
    .map(([k, v]) => `${{encodeURIComponent(k)}}=${{encodeURIComponent(v === true ? 'true' : v)}}`)
    .join('&');
  return `${{flaskUrl}}/?${{params}}&sv=1`;
}}

function makeRow(t) {{
  const dur  = t.duration != null ? t.duration + 's' : (t.status==='running' ? '…' : '');
  const err  = t.error ? `<div class="error-box">${{esc(t.error)}}</div>` : '';
  const slow = t.slow  ? '<span class="slow-badge">slow</span>' : '';
  const raw  = `<span class="raw-name">${{esc(t.name)}}</span>`;
  const ic   = ICON_CLS[t.status] || '';
  let repro = '';
  if (REPRO_GROUP && t.group === REPRO_GROUP) {{
    const url = reproUrl(t.name);
    if (url) repro = `<a class="repro-btn" href="${{url}}" target="cct-repro">▶ View in App</a>`;
  }}
  return `<tr id="${{rowId(t.name)}}" class="row-${{t.status}}">
    <td class="icon ${{ic}}">${{ICON[t.status]||'?'}}</td>
    <td class="name">${{pretty(t.name)}}${{slow}}${{raw}}${{repro}}${{err}}</td>
    <td class="grp">${{esc(t.group)}}</td>
    <td class="dur">${{dur}}</td>
  </tr>`;
}}

// ── Group timer label ──────────────────────────────────────────────────────────
function grpTimerHtml(group) {{
  const actual = grpActual[group];
  if (actual != null) return `<span class="grp-timer done">${{actual}}s</span>`;
  const hist = grpHistory[group];
  const start = grpStarts[group];
  if (start && hist) {{
    const rem = Math.max(0, hist - Math.round((Date.now() - start) / 1000));
    const cls = rem < 10 ? 'warn' : 'live';
    return `<span class="grp-timer ${{cls}}">~${{rem}}s left</span>`;
  }}
  if (hist) return `<span class="grp-timer">~${{hist}}s</span>`;
  return '';
}}

// ── Stats + progress bar ───────────────────────────────────────────────────────
function updateStats() {{
  const c = {{passed:0,failed:0,skipped:0,running:0,pending:0}};
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
  if (finished) {{
    sb.className   = 'badge ' + (c.failed ? 'badge-err' : 'badge-ok');
    sb.textContent = c.failed ? `✗ ${{c.failed}} failed` : '✓ All passed';
  }}
}}

// ── Elapsed + overall countdown ────────────────────────────────────────────────
function updateTimers() {{
  if (!started) return;
  const now  = finished ? new Date(finished) : new Date();
  const secs = Math.round((now - new Date(started)) / 1000);
  document.getElementById('sub').textContent =
    'Started ' + started.slice(0,19).replace('T',' ') + ' · Elapsed ' + secs + 's';

  const el = document.getElementById('cd');
  if (finished) {{ el.style.display = 'none'; return; }}
  const groups  = [...new Set(tests.map(t=>t.group))];
  const estTotal= groups.reduce((s,g)=>s+(grpHistory[g]||0), 0)
                  || parseInt(localStorage.getItem(DUR_KEY)||'0');
  if (!estTotal) {{ el.style.display='none'; return; }}
  const rem = estTotal - secs;
  el.style.display = '';
  if (rem <= 0) {{
    el.textContent = 'Finishing…'; el.className = 'countdown urgent';
  }} else {{
    const m=Math.floor(rem/60), s=rem%60;
    el.textContent = m > 0 ? `~${{m}}m ${{s}}s remaining` : `~${{s}}s remaining`;
    el.className   = 'countdown' + (rem<30?' urgent':rem<60?' warn':'');
  }}
}}

// ── Table render ───────────────────────────────────────────────────────────────
function rebuildTable() {{
  const running  = tests.filter(t => t.status==='running');
  const done     = tests.filter(t => ['passed','failed','skipped'].includes(t.status));
  const pending  = tests.filter(t => t.status==='pending');

  function section(list, label, cls, alwaysShow=false) {{
    if (!list.length && !alwaysShow) return '';
    let html = `<tr class="sec-hdr ${{cls}}-hdr"><td colspan="4">${{label}}</td></tr>`;
    if (!list.length) {{
      html += `<tr><td></td><td colspan="3" style="color:#333;font-style:italic;padding:7px 12px">All done</td></tr>`;
      return html;
    }}
    let prevGrp = null;
    for (const t of list) {{
      if (t.group !== prevGrp) {{
        html += `<tr class="grp-row">
          <td></td>
          <td>${{esc(t.group)}}</td>
          <td></td>
          <td style="text-align:right">${{grpTimerHtml(t.group)}}</td>
        </tr>`;
        prevGrp = t.group;
      }}
      html += makeRow(t);
    }}
    return html;
  }}

  document.getElementById('tbody').innerHTML =
    section(running, '⏳ Running',                           'run') +
    section(done,    '✓ Finished',                           'done') +
    section(pending, `○ Upcoming — ${{pending.length}} tests`, 'todo', true);

  const active = running[0] || done[done.length - 1];
  const el = document.getElementById('current-test');
  if (active) {{
    const icon = active.status === 'running' ? '⏳' : (active.status === 'passed' ? '✓' : '✗');
    el.textContent = `${{icon}}  ${{pretty(active.name)}}   [${{active.group}}]`;
    el.className   = 'current-test ' + (active.status === 'failed' ? 'cur-fail' : active.status === 'running' ? 'cur-run' : 'cur-pass');
  }} else {{
    el.textContent = '';
  }}
}}

// ── Server badge ───────────────────────────────────────────────────────────────
function setServer(s) {{
  const el = document.getElementById('srv');
  const M  = {{ starting:['badge-warn','⏳ Starting…'],
               running: ['badge-ok',  '● Server Running'],
               failed:  ['badge-err', '✗ Server Failed'],
               none:    ['badge-ok',  '● No server needed'] }};
  const [cls,txt] = M[s] || ['badge-warn', s];
  el.className = 'badge '+cls; el.textContent = txt;
}}

// ── Apply full state snapshot ──────────────────────────────────────────────────
function applyState(state) {{
  tests         = state.tests    || [];
  started       = state.started;
  finished      = state.finished;
  reproConfigs  = state.repro_configs || [];
  if (state.flask_url) flaskUrl = state.flask_url;
  if (state.group_timings) Object.assign(grpActual, state.group_timings);
  const wb = document.getElementById('warn-banner');
  const warnings = state.warnings || [];
  if (warnings.length) {{
    wb.style.display = 'block';
    wb.innerHTML = '⚠ ' + warnings.map(esc).join(' &nbsp;·&nbsp; ');
  }} else {{
    wb.style.display = 'none';
  }}
  loadHistory();
  setServer(state.server || 'starting');
  rebuildTable(); updateStats(); updateTimers();
}}

// ── SSE connection ─────────────────────────────────────────────────────────────
function connect() {{
  const es  = new EventSource('/events');
  const dot = document.getElementById('dot');
  es.onopen = () => {{ dot.textContent='● live'; dot.className='conn-dot live'; }};
  es.onerror = () => {{
    dot.textContent='○ reconnecting…'; dot.className='conn-dot';
    es.close(); setTimeout(connect, 3000);
  }};
  es.onmessage = ev => {{
    const d = JSON.parse(ev.data);
    if (d.type === 'repro_configs') {{
      reproConfigs = d.configs || [];
      rebuildTable();
    }} else if (d.type === 'clear') {{
      tests=[];started=d.started;finished=null;grpStarts={{}};grpActual={{}};reproConfigs=[];
      loadHistory();
      document.getElementById('summary').textContent='';
      document.getElementById('summary').className='';
      document.getElementById('cd').style.display='none';
      document.getElementById('bar').style.cssText='width:0%;background:#3498db';
      setServer('starting'); rebuildTable(); updateStats();
    }} else if (d.type === 'test') {{
      const i = tests.findIndex(t=>t.name===d.name);
      if (i>=0) tests[i]=d; else tests.push(d);
      rebuildTable(); updateStats();
    }} else if (d.type === 'group_start') {{
      grpStarts[d.group] = new Date(d.started);
      rebuildTable();
    }} else if (d.type === 'group_end') {{
      grpActual[d.group] = d.duration;
      rebuildTable();
    }} else if (d.type === 'server') {{
      setServer(d.server);
    }} else if (d.type === 'finished') {{
      finished=d.finished; updateStats(); updateTimers(); saveHistory();
      if (timerID) {{ clearInterval(timerID); timerID=null; }}
    }} else if (d.type === 'state_ready') {{
      fetch('/state').then(r=>r.json()).then(applyState).catch(()=>{{}});
    }}
  }};
}}

// ── Boot ───────────────────────────────────────────────────────────────────────
fetch('/state').then(r=>r.json()).then(applyState).catch(()=>{{}});
connect();
timerID = setInterval(()=>{{ updateTimers(); }}, 1000);
</script>
</body>
</html>"""


# ── SSE / result HTTP server ─────────────────────────────────────────────

def _make_handler(dash: _DashboardState, html: str):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/':
                self._send(200, 'text/html; charset=utf-8', html.encode())
            elif self.path == '/state':
                body = json.dumps(dash.snapshot()).encode()
                self._send(200, 'application/json', body,
                           extra=[('Access-Control-Allow-Origin', '*')])
            elif self.path == '/events':
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                q = queue.Queue(maxsize=500)
                with dash.lock:
                    dash.subscribers.append(q)
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
                        dash.subscribers.remove(q)
                    except ValueError:
                        pass
            else:
                self._send(404, 'text/plain', b'Not found')

        def do_POST(self):
            if self.path == '/result':
                length = int(self.headers.get('Content-Length', 0))
                data = json.loads(self.rfile.read(length))
                name = data.get('name', '')
                status = data.get('status', 'failed')
                error = data.get('error')
                dash.update_test_fuzzy(name, status=status,
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

    return _Handler


def _start_dash_server(dash: _DashboardState, html: str, port: int):
    srv = ThreadingHTTPServer(('127.0.0.1', port), _make_handler(dash, html))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ── Pytest plugin (streams results to the dashboard) ────────────────────

_PLUGIN_TEMPLATE = '''
import requests as _req, datetime as _dt

_DASH_URL = "http://127.0.0.1:{port}/result"
_started  = {{}}

def pytest_runtest_logstart(nodeid, location):
    _started[nodeid] = _dt.datetime.now().isoformat()

def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    nodeid   = report.nodeid
    parts    = nodeid.split("::")
    name     = "::".join(parts[1:])
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


def _write_plugin(root: Path, dash_port: int) -> Path:
    path = root / 'tests' / '_dash_plugin.py'
    path.write_text(_PLUGIN_TEMPLATE.format(port=dash_port), encoding='utf-8')
    return path


# ── Test discovery + pytest run ───────────────────────────────────────────

def _build_ignore_args(cfg: RunnerConfig) -> list[str]:
    args = list(cfg.always_ignore)
    for env_var, path in cfg.optional_test_files:
        if os.environ.get(env_var) != '1':
            args.append(f'--ignore={path}')
    return args


def _collect_pytest_tests(cfg: RunnerConfig, venv_py: Path):
    ignore_args = [
        '--ignore=tests/run_tests.py',
        '--ignore=tests/_dash_plugin.py',
        *_build_ignore_args(cfg),
    ]
    result = subprocess.run(
        [str(venv_py), '-m', 'pytest', 'tests/', '--collect-only',
         *ignore_args, *cfg.extra_pytest_args],
        cwd=str(cfg.root), capture_output=True, text=True,
    )
    tests = []
    for line in (result.stdout + result.stderr).splitlines():
        line = line.strip()
        if '::' not in line or line.startswith('=') or line.startswith(' '):
            continue
        parts = line.split('::')
        file_stem = Path(parts[0]).stem
        test_name = '::'.join(parts[1:])
        group = cfg.pytest_groups.get(
            file_stem, file_stem.replace('test_', '').replace('_', ' ').title())
        tests.append((test_name, group, line))
    return tests


def _run_pytest(cfg: RunnerConfig, venv_py: Path, dash: _DashboardState,
                pytest_tests, dash_port: int) -> bool:
    if not pytest_tests:
        return True

    plugin_path = _write_plugin(cfg.root, dash_port)

    seen_groups = []
    for _, group, _ in pytest_tests:
        if group not in seen_groups:
            seen_groups.append(group)
            dash.group_start(group)

    env = {**os.environ,
           'PYTHONPATH': str(cfg.root / 'tests') + os.pathsep + os.environ.get('PYTHONPATH', '')}
    run_ignore = [
        '--ignore=tests/run_tests.py',
        '--ignore=tests/_dash_plugin.py',
        *_build_ignore_args(cfg),
    ]

    proc = subprocess.Popen(
        [str(venv_py), '-m', 'pytest', 'tests/',
         *run_ignore, *cfg.extra_pytest_args,
         '-p', plugin_path.stem,
         '--tb=no', '-vv', '--no-header', '--color=no'],
        cwd=str(cfg.root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    import re as _re
    _STATUS_RE = _re.compile(
        r'^tests/[^\s]+::(.+?)\s+(PASSED|FAILED|SKIPPED|ERROR)(?:$|[\s(])'
    )

    def _read_stdout():
        for line in proc.stdout:
            m = _STATUS_RE.match(line.rstrip())
            if not m:
                continue
            node_parts = m.group(1)
            raw_status = m.group(2).lower()
            status = 'failed' if raw_status in ('failed', 'error') else raw_status
            dash.update_test_fuzzy(node_parts, status=status,
                                   started=datetime.now().isoformat())

    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stdout_thread.start()
    proc.wait()
    stdout_thread.join(timeout=10)

    for group in seen_groups:
        dash.group_end(group)

    rc = proc.returncode
    with dash.lock:
        for t in dash.state['tests']:
            if t['status'] == 'pending' and t['group'] in seen_groups:
                if rc in (0, 5):
                    t['status'] = 'skipped'
                else:
                    t['status'] = 'failed'
                    t['error'] = 'Not collected by pytest (possible import error)'
                dash.push({'type': 'test', **t})

    plugin_path.unlink(missing_ok=True)
    return rc in (0, 5)


def _run_inline_batch(dash: _DashboardState, batch: InlineBatch, skip_slow: bool,
                      matches) -> bool:
    ok = True
    dash.group_start(batch.group)
    for name, run_fn, slow in batch.tests:
        if not matches(name, batch.group):
            continue
        if slow and skip_slow:
            dash.update_test(name, status='skipped')
            continue
        dash.update_test(name, status='running', started=datetime.now().isoformat())
        try:
            run_fn()
            dash.update_test(name, status='passed')
        except Exception as e:
            tb = traceback.format_exc()
            dash.update_test(name, status='failed',
                             error=f'{type(e).__name__}: {e}\n{tb[-600:]}')
            ok = False
    dash.group_end(batch.group)
    return ok


# ── Server lifecycle ───────────────────────────────────────────────────

def _default_ready_check(base_url: str) -> bool:
    import requests as _req
    try:
        return _req.get(base_url, timeout=1).status_code < 500
    except Exception:
        return False


def _start_server(cfg: RunnerConfig, venv_py: Path, dash: _DashboardState, port: int):
    if cfg.server_cmd is None:
        dash.set_server('none')
        return None, f'http://localhost:{port}'

    env = {**os.environ, **cfg.server_env}
    proc = subprocess.Popen(
        cfg.server_cmd(port), cwd=str(cfg.root), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f'http://localhost:{port}'
    check = cfg.server_ready_check or _default_ready_check
    for _ in range(30):
        if check(base):
            dash.set_server('running')
            return proc, base
        time.sleep(1)
    dash.set_server('failed')
    proc.terminate()
    return None, base


def _free_port(port: int):
    import socket as _sock
    with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
        s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        if s.connect_ex(('127.0.0.1', port)) != 0:
            return
    import signal as _sig
    try:
        result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            if f':{port} ' in line and 'LISTENING' in line:
                pid = int(line.split()[-1])
                if pid > 0:
                    try:
                        os.kill(pid, _sig.SIGTERM)
                        print(f'Freed port {port}: killed PID {pid}')
                    except (OSError, PermissionError):
                        pass
    except Exception:
        pass
    time.sleep(0.3)


def _matches(name, group, filters_test, filters_group):
    if filters_test:
        return any(f.lower() in name.lower() for f in filters_test)
    if filters_group:
        return any(f.lower() in group.lower() for f in filters_group)
    return True


# ── Main entry ─────────────────────────────────────────────────────────

def run(cfg: RunnerConfig, argv: Sequence[str] | None = None) -> int:
    """Run the dashboard + test suite described by cfg. Returns a process
    exit code (0 = all passed, 1 = at least one failure)."""
    ap = argparse.ArgumentParser(
        description=f'{cfg.title} runner with live dashboard.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--flask-port', type=int, default=cfg.flask_port)
    ap.add_argument('--dash-port', type=int, default=cfg.dash_port)
    ap.add_argument('--skip-slow', action='store_true')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--exit-when-done', action='store_true')
    ap.add_argument('--test', dest='tests', action='append', default=[], metavar='NAME')
    ap.add_argument('--group', dest='groups', action='append', default=[], metavar='GROUP')
    args = ap.parse_args(argv)

    venv_py = cfg.venv_py if cfg.venv_py and cfg.venv_py.exists() else Path(sys.executable)

    _free_port(args.flask_port)
    _free_port(args.dash_port)

    dash = _DashboardState(cfg.title, f'http://localhost:{args.flask_port}')
    dash.state['warnings'] = (cfg.warnings_fn() if cfg.warnings_fn else []) or []

    dash_key = cfg.title.replace(' ', '_')
    html = _HTML.format(
        TITLE=cfg.title,
        FLASK_URL=f'http://localhost:{args.flask_port}',
        REPRO_GROUP_JSON=json.dumps(cfg.repro_group),
        DASH_KEY=dash_key,
    )
    _start_dash_server(dash, html, args.dash_port)
    dash_url = f'http://localhost:{args.dash_port}/'
    print(f'Dashboard : {dash_url}')

    proc, base_url = _start_server(cfg, venv_py, dash, args.flask_port)
    if cfg.server_cmd is not None:
        if proc is None:
            print('ERROR: server failed to start.')
            dash.finish()
            return 1
        print(f'Server    : ready at {base_url}')

    if cfg.pre_discovery_hook:
        cfg.pre_discovery_hook()

    sys.path.insert(0, str(cfg.root))
    all_pytest = _collect_pytest_tests(cfg, venv_py)
    pytest_tests = [(n, g, nid) for n, g, nid in all_pytest
                    if _matches(n, g, args.tests, args.groups)]
    for name, group, _ in pytest_tests:
        dash.add_test(name, group)

    batches = []
    if cfg.inline_batches:
        batches = cfg.inline_batches(base_url)
        for b in batches:
            filtered = [(n, fn, slow) for n, fn, slow in
                        [(n, fn, n in cfg.slow_tests) for n, fn, _ in b.tests]
                        if _matches(n, b.group, args.tests, args.groups)]
            for n, _fn, slow in filtered:
                dash.add_test(n, b.group, slow=slow)

    if cfg.repro_configs_fn:
        try:
            configs = cfg.repro_configs_fn() or []
            with dash.lock:
                dash.state['repro_configs'] = configs
            dash.push({'type': 'repro_configs', 'configs': configs})
        except Exception:
            pass

    dash.push({'type': 'state_ready', 'started': dash.state['started']})
    if not args.no_browser:
        import webbrowser
        webbrowser.open(dash_url)

    any_failed = False
    if not _run_pytest(cfg, venv_py, dash, pytest_tests, args.dash_port):
        any_failed = True

    for b in batches:
        if not _run_inline_batch(dash, b, args.skip_slow,
                                 lambda n, g: _matches(n, g, args.tests, args.groups)):
            any_failed = True

    dash.finish()

    with dash.lock:
        p = sum(1 for t in dash.state['tests'] if t['status'] == 'passed')
        f = sum(1 for t in dash.state['tests'] if t['status'] == 'failed')
        s = sum(1 for t in dash.state['tests'] if t['status'] == 'skipped')
        failed_tests = [t for t in dash.state['tests'] if t['status'] == 'failed']
        skipped_tests = [t for t in dash.state['tests'] if t['status'] == 'skipped']
    print(f'Results   : {p} passed  {f} failed  {s} skipped')

    if failed_tests:
        print('Failed    :')
        lines = []
        for t in failed_tests:
            line = f"  {t['group']} :: {t['name']}"
            print(line)
            lines.append(line)
            err = t.get('error')
            if err:
                first_line = err.splitlines()[0]
                print(f'      {first_line}')
                lines.append(f'    {first_line}')
        fail_log = cfg.root / 'tests' / 'last_failures.txt'
        fail_log.write_text(
            f"Run at {datetime.now().isoformat()}\n" + '\n'.join(lines) + '\n', encoding='utf-8')
        print(f'Failed test names written to {fail_log}')

    if skipped_tests:
        lines = [f"  {t['group']} :: {t['name']}" for t in skipped_tests]
        skip_log = cfg.root / 'tests' / 'last_skipped.txt'
        skip_log.write_text(
            f"Run at {datetime.now().isoformat()}\n" + '\n'.join(lines) + '\n', encoding='utf-8')
        print(f'Skipped test names written to {skip_log}')

    if args.exit_when_done:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        sys.stdout.flush()
        sys.stderr.flush()
        return 1 if any_failed else 0

    print(f'Dashboard : {dash_url}')
    if proc is not None:
        print(f'Server    : http://localhost:{args.flask_port}  (Ctrl+C to quit)')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    if proc is not None:
        proc.terminate()
        proc.wait()
    return 1 if any_failed else 0
