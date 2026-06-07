# PulleyWebApp — Test Suite

## Quick start

```powershell
# Full suite with live dashboard
.venv312\Scripts\python.exe tests\run_tests.py

# Skip slow timeout tests (~3 min each)
.venv312\Scripts\python.exe tests\run_tests.py --skip-slow

# Single test by name fragment
.venv312\Scripts\python.exe tests\run_tests.py --test test_burst_join

# One group
.venv312\Scripts\python.exe tests\run_tests.py --group "Queue System"

# Multiple groups
.venv312\Scripts\python.exe tests\run_tests.py --group Stress --group "Flange Geometry"
```

The dashboard opens automatically at `http://localhost:5098/` and streams results in real time as each test completes.

---

## Dashboard

| Element | Description |
|---|---|
| **⏳ Running** | Current test (animated pulse) |
| **✓ Finished** | Passed / failed / skipped with durations |
| **○ Upcoming** | All pending tests grouped by test group |
| **Group timer** | `~Xs left` while running; `Xs` actual when done |
| **Overall countdown** | Estimated time remaining (based on last run) |
| **● live** | SSE connection indicator — updates without page refresh |

Group timings are saved to `localStorage` after each run and used as countdown estimates on the next run.

---

## CLI options

| Flag | Default | Description |
|---|---|---|
| `--flask-port N` | 5099 | Port for the test Flask instance |
| `--dash-port N` | 5098 | Port for the dashboard SSE server |
| `--skip-slow` | off | Skip tests that take >30s (timeout/persistence) |
| `--no-browser` | off | Don't open browser automatically |
| `--exit-when-done` | off | Exit immediately after tests complete (for CI) |
| `--test NAME` | all | Run tests whose name contains NAME (repeatable) |
| `--group GROUP` | all | Run tests in groups matching GROUP (repeatable) |

---

## Test groups

| Group | File | Tests | Notes |
|---|---|---|---|
| API Endpoints | `test_api.py` | ~30 | Requires Flask test client |
| Exporters | `test_exporters.py` | ~5 | STL/DXF/SVG generation |
| Belt Geometry | `test_belt.py` | ~2 | Belt length/OD math |
| Input Validation | `test_invalid_inputs.py` | ~55 | Bad parameter handling |
| Priority Logic | `test_priority.py` | ~14 | Clearance/backlash presets |
| Spoke Geometry | `test_spokes.py` | ~59 | Spoke profile math |
| 3D Generation | `test_3d.py` | ~33 | STL mesh generation |
| Flange Geometry | `test_flange_geometry.py` | ~34 | Flange inner-radius rules |
| Flange Export | `test_flange.py` | ~39 | Flange STL/mesh export |
| Benchmarks | `test_benchmarks.py` | ~20 | Performance regression |
| Regression | `test_repro.py` | ~53 | Bug reproduction cases |
| Queue System — Functional | `test_queue_pytest.py` | ~10 | Session create/promote/release |
| Queue System — Timeouts | `test_queue_pytest.py` | ~3 | Idle/stale expiry (slow) |
| Queue System — Stress | `test_queue_pytest.py` | ~7 | Burst join, race, heartbeat storm |
| Trial Downloads | `test_queue_pytest.py` | ~3 | Weekly download limits |

---

## How it works

```
run_tests.py
  │
  ├── Starts ThreadingHTTPServer on --dash-port
  │     GET /          → dashboard HTML (SSE client)
  │     GET /state     → full state JSON
  │     GET /events    → SSE stream
  │     POST /result   → receives per-test results from pytest plugin
  │
  ├── Starts Flask on --flask-port (PULLEY_TESTING=1, no reloader)
  │
  ├── Discovers tests via pytest --collect-only
  │
  ├── Runs non-queue tests in ONE pytest subprocess
  │     └── _dash_plugin.py hooks pytest_runtest_logreport
  │           → POSTs each result to /result instantly
  │
  └── Runs queue tests inline
        └── Needs live Flask; uses /api/test/reset before/after each test
```

Results stream to the dashboard in real time via SSE — no polling, no page refresh.

---

## Session state (queue system)

Session state is stored in `logs/sessions.json` so all gunicorn workers share it. Active session expires after:

- **5 minutes** hard cap
- **1 minute** idle (no heartbeat) — only when there are waiting users

Waiting sessions are dropped after **30 seconds** without a heartbeat (browser closed). The queue page uses `sendBeacon` + `visibilitychange` to keep heartbeats alive even in background tabs.

Queue positions use a monotonic sequence counter (`seq`) rather than timestamps to avoid tie-breaking issues under burst load.

---

## Nightly scheduled run

A Windows Task Scheduler task runs at 2:00 AM daily:

```
Task name : PulleyWebApp Nightly Tests
Script    : tests\run_nightly.bat
Log       : %LOCALAPPDATA%\Temp\pulley_test_run.log
```

To run manually:
```powershell
schtasks /run /tn "PulleyWebApp Nightly Tests"
```

To remove:
```powershell
schtasks /delete /tn "PulleyWebApp Nightly Tests" /f
```

---

## Trial download data

Stored in `logs/trial_downloads.json` (Render persistent disk at `/var/data`).

- Limit: **2 downloads per week** per `machine_id`
- Resets: Monday each week
- Cleanup: entries older than 7 days removed automatically

---

## Troubleshooting

**Dashboard not loading** — check port 5098 is free: `netstat -an | findstr 5098`

**Flask failed to start** — check port 5099 is free; look for an existing process: `Get-Process python*`

**Tests connecting to wrong port** — the plugin hardcodes the dash port at write time; restarting `run_tests.py` regenerates it

**All tests skipped** — your `--test` / `--group` filter matched only queue tests that were already skipped; check the filter spelling

**`test_burst_join_10_sessions` fails** — race condition in `create_session`; the monotonic `seq` counter should prevent this; re-run with `--test test_burst_join` to confirm
