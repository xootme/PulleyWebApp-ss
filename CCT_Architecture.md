# CheapCAD Tools — Platform Architecture

**Publisher:** Myerchin Enterprises Inc., Bellevue WA
**Brand:** CheapCAD Tools (`cheapcadtools.com`)
**Mission:** Professional-grade parametric CAD tools at a fraction of the cost of building them yourself.
**Model:** Browser-based generator tools, sold as annual subscriptions. Each tool is an independent web service that doubles as a desktop app. Subscriptions are sold through whichever channel the CAD platform supports — or directly through the CCT website for platforms without a payment marketplace.

---

## 1. Platform Overview

```
User
 │
 ▼
cheapcadtools.com  (WordPress on GreenGeeks — landing pages, about, contact, privacy)
 │
 ├── /tools/pulleys  ──► Cloudflare Worker (cct-tools-router)
 ├── /tools/gears    ──►   │
 └── /tools/...      ──►   │
                           ▼
                    Render.com — one Flask service per tool
                    (pulleywebapp.onrender.com, etc.)
```

- **WordPress (GreenGeeks)** serves the marketing site only. No tool code lives there.
- **Cloudflare Worker** (`cct-tools-router`) intercepts `cheapcadtools.com/*`. It routes `/tools/<slug>` to the matching Render service and passes everything else through to GreenGeeks unchanged. Adding a new tool requires one line in the Worker's routing table.
- **Render** hosts each tool as an independent Python/Flask + gunicorn service. A GitHub push to `main` triggers automatic redeploy (~2 minutes).

---

## 2. Anatomy of a CCT Tool

Every tool is a self-contained Flask application in its own GitHub repo. The same codebase runs in two modes:

| Mode | URL | Port | Entry point |
|------|-----|------|-------------|
| **Web (Render)** | `cheapcadtools.com/tools/<slug>` | 80/443 | `gunicorn app:app` |
| **Desktop (offline)** | `localhost:5154` | 5154 | `packaging/launcher.py` → same `app:app` |

### Key files

| File | Purpose |
|------|---------|
| `app.py` | Flask routes — UI, preview, download, provision, admin API |
| `geometry/<tool>_geometry.py` | Core math — shared by all export formats |
| `geometry/flange_geometry.py` | Flange cross-section profiles and inner-radius rules |
| `exporters/` | Format-specific exporters (SVG, DXF, STL, STEP, PNG) |
| `exporters/job_queue.py` | Session + queue state (disk-persisted); disabled by `QUEUE_DISABLED` |
| `exporters/step_worker.py` | STEP subprocess worker — cadquery path (unexercised fallback) |
| `exporters/step_worker_ss.py` | STEP subprocess worker — small_step Rust path (fast, no cadquery) |
| `templates/index.html` | Full UI — all controls, JS, Three.js 3D viewer |
| `static/style.css` | All styles; bump `?v=N` on the CSS link when changed |
| `static/*_help.html` | Context help panels |
| `packaging/build_release.py` | Local-only desktop build (PyArmor + PyInstaller) |
| `packaging/launcher.py` | PyInstaller entry point; opens browser, sets `QUEUE_DISABLED=1` |
| `web_provisioning.md` | Deployment checklist — follow before every push |
| `DECISIONS.md` | Architectural decision log |
| `TODO.md` | Backlog |

### Export pipeline

```
User parameters (query string)
        │
        ▼
geometry/<tool>_geometry.py   ← 2D profile math, shared by all formats
        │
        ├── exporters/svg_exporter.py      → SVG  (browser download)
        ├── exporters/dxf_exporter.py      → DXF  (browser download)
        ├── exporters/png_exporter.py      → PNG  (2D preview)
        ├── exporters/step_exporter.py     → STL  (trimesh)
        │
        └── /download/step  ──► SMALL_STEP_BIN set?
                                    │
                            Yes ────┴──── No
                             │             │
                   step_worker_ss.py   step_worker.py
                   (small_step Rust)   (cadquery, unexercised fallback)
```

**STEP — small_step path (active):** `/download/step`, `/download/flange-step`, and `/download/all-step` all use `exporters/step_worker_ss.py`, which generates a DXF then calls the `small_step` Rust binary. `SMALL_STEP_BIN` env var must point to the compiled binary. The cadquery fallback (`exporters/step_worker.py`) still exists but is not exercised at runtime.

### Embedded metadata

Every exported file has CCT design parameters embedded as JSON so users can restore a design from a saved file:

| Format | Location |
|--------|----------|
| STEP / STL | `/* CCT:{...} */` comment after the STEP header |
| DXF | Group-code `999` comment before EOF |
| SVG | `<metadata><cct>{...}</cct></metadata>` element |

The web app reads these back via a file-picker import button. The Fusion addin reads them on import to attach params to the Fusion component attributes.

---

## 3. Server Queue System

STEP export is memory-intensive. Concurrent requests caused memory exhaustion and server crashes on the free/starter Render tier. The queue system serialises access: **only one user may run a STEP export at a time**; everyone else waits in a fair FIFO queue.

### Two builds

The queue is only active in the **online/Render build**. The desktop build (and local dev server) sets `QUEUE_DISABLED=1`, which makes `@require_active_session` a no-op and skips session creation on the index route. This is set automatically by `packaging/launcher.py` before Flask starts.

### Components

| File | Role |
|------|------|
| `exporters/job_queue.py` | In-memory session + queue state, disk-persisted to `logs/session.json` and `logs/queue.json`. Background cleanup thread (10 s interval). |
| `app.py` | `@require_active_session` decorator guards `/download/step` and other expensive routes. Session API routes below. |
| `templates/queue.html` | Queue status UI — join, position counter, countdown, release button. Auto-refreshes every 5 s. |

### Session API routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/session/create` | POST | Join queue or start session immediately |
| `/api/session/status` | GET | Poll for active/waiting/position/ETA |
| `/api/session/heartbeat` | POST | Keep session alive |
| `/api/session/release` | POST | End session, promote next in queue |
| `/api/queue/status` | GET | Queue snapshot (active user, length) |
| `/queue` | GET | Queue management page |

### Timeout rules

| Event | Timeout |
|-------|---------|
| Base session | 5 min from session start |
| STEP grace | +1 min if STEP export started (6 min total) |
| Idle | 1 min without heartbeat → auto-release → back of queue |

### User flow

```
User → GET /queue → click "Join Queue"
    │
    ├─ No active session: session created, redirect /?session_id=XXX
    │
    └─ Session active: user enqueued, sees position + ETA countdown

Active user (home page):
    Session ID in URL → @require_session checks access before STEP export
    Browser heartbeat every 5 s
    On download: user clicks "End Session" or idle timeout fires

Session expires / released:
    _promote_next() moves first queue entry to active
    On next /api/session/status poll, promoted user sees is_active: true
```

### Configuration

All timeouts in `exporters/job_queue.py`:
```python
SESSION_TIMEOUT_SEC = 5 * 60  # 5 min base
STEP_GRACE_SEC      = 60      # +1 min for STEP
IDLE_TIMEOUT_SEC    = 60      # 1 min idle logout
```

> **Limitation:** in-memory + disk state works on a single Render instance. Multi-instance deployments would need Redis for shared session storage.

---

## 4. small_step — Rust STEP Emitter

`small_step` is a hand-written Rust B-rep STEP emitter at `C:\Users\cmyer\Documents\small_step\`. It generates AP214 STEP files directly from DXF 2D profiles without any boolean kernel — geometry is computed analytically.

### Why it exists

cadquery (the original Python STEP path) required Python ≤3.12 and a 2 GB OCP wheel; it was slow (3–8 s per export) and could not be imported into the main Flask process. small_step generates the same STEP files in under 0.5 s with no Python dependency. cadquery has been removed from requirements.txt; the Flask venv is now Python 3.14.

### CLI interface

```
small_step combined <input.dxf> <height_mm> [spoke_height_mm]
    [--top-3d  <r_inner> <r_tooth_od> <rim_r> <angle_deg> <flange_h>]
    [--nubs    <count> <dia_mm> <height_mm> <allowance_mm> <r_outer_mm>]
    [--bot-3d  <r_inner> <r_tooth_od> <rim_r> <angle_deg> <flange_h>]
    [--top-metal / --bot-metal ...]
    [--hub <od> <h>  [--flat <d>] [--keyway <w> <h>]]

small_step flange-3d   <r_inner> <r_tooth_od> <rim_r> <angle_deg> <flange_h> [top|bottom]
small_step flange-metal <r_inner> <r_od> <rim_r> <angle_deg> <thick> <bend> [top|bottom]
```

**`--nubs` parameter:** `r_outer_mm` is the radius at which the nub OD is tangent (= `R_groove_bottom − min(tooth_ht, 3mm)`). small_step subtracts `dia/2` internally to get the nub centre. Do **not** pass the centre radius — that shifts nubs inward by `dia/2` and causes them to breach the flange ID.

**Flange inner radius with spokes:** pass `r_tooth_od = R_groove_bottom = R_OD − tooth_height` (not the full tooth-tip OD). The flat section of the flange runs from `r_inner` to `r_tooth_od`; for spoke pulleys `r_inner = R_groove_bottom − rim_depth`.

### Building

```
cd C:\Users\cmyer\Documents\small_step
cargo build --release --target x86_64-pc-windows-gnu
# Binary: target/x86_64-pc-windows-gnu/release/small_step.exe
```

Set `SMALL_STEP_BIN` env var to the binary path to activate the fast STEP path.

### STEP validation

A validation suite lives in `tests/`:

| Script | Validators |
|--------|-----------|
| `run_validate_noedraw.py` | NIST SFA 5.45, OCC (via cadquery OCP), FreeCAD 1.1 headless |
| `run_quick_validate.py` | Same + eDrawings 2026 COM (requires interactive terminal) |
| `test_ss_validators.py` | Full random-config validator mirroring nightly test logic |

---

## 5. Subscription & Licensing

### Common backbone (all platforms)

The provision server on Render is platform-agnostic. Every plugin calls the same endpoint regardless of CAD platform:

```
Plugin calls POST /api/provision  { user_id, email, [platform] }
        │
Render checks subscribers.json or calls platform entitlement API
        │
Returns:
  - licence.lic  (base64, PyArmor time-limited — same file for all platforms)
  - app_url      (GitHub Releases zip download URL)
  - app_version  (YYYYMMDD build string)
  - licence_expiry (YYYY-MM-DD)
        │
Plugin downloads app zip, writes licence.lic, stores expiry in config.json
```

**Licence mechanics (all platforms):**
- `licence.lic` is a PyArmor file: `--period 7` (weekly online check) + `--expired <date>` (hard stop).
- No machine binding — one licence per subscription, any machine.
- Renewal: plugin warns 30 days before expiry, calls `/api/provision` again.
- Cancellation: call `/api/subscribers/remove` → next renewal returns 403 → app stops at expiry.
- Beta/comped accounts: `logs/subscribers.json` on Render disk, managed via `/api/subscribers/add|remove`.

---

### Mode A — Autodesk platforms (Fusion 360 + Inventor)

**Same mechanism for both.** They share the Autodesk App Store, the same PayPal IPN format, and the same `checkentitlement` API. Inventor is a copy-paste of the Fusion addin with different constants.

```
Customer buys on Autodesk App Store
  └── PayPal IPN → POST /api/autodesk-ipn
        └── Render logs purchase, sends welcome email (SendGrid)

Plugin calls GET checkentitlement
  https://apps.autodesk.com/webservices/checkentitlement?userid={id}&appid={appid}
  └── Returns { "IsValid": true/false }  (no auth needed, public API)

Plugin calls POST /api/provision { user_id, email }
  └── Render independently verifies checkentitlement, returns licence.lic + app_url
```

Identity source: `app.currentUser.userId` + `app.currentUser.email` (Fusion/Inventor API).  
Set `AUTODESK_APP_ID` env var on Render after App Store registration; leave empty to fall back to `subscribers.json` (pre-registration / beta).

---

### Mode B — Platform-with-OAuth (OnShape)

OnShape's App Store handles payment. User identity comes from OAuth authentication the user completes once when installing the extension.

```
Customer buys on OnShape App Store
  └── OnShape IPN/webhook → POST /api/onshape-ipn  (details TBC with PTC)
        └── Render logs purchase, adds email to subscribers.json

Extension authenticates user via OAuth
  └── GET https://cad.onshape.com/api/users/sessioninfo  (Bearer token)
        └── Returns { userId, email }

Plugin calls POST /api/provision { user_id: email, email, platform: "onshape" }
  └── Render checks subscribers.json by email, returns licence.lic
```

No platform entitlement API equivalent — CCT's `subscribers.json` is the source of truth.  
OAuth client ID/secret stored as Render env vars `ONSHAPE_CLIENT_ID` / `ONSHAPE_CLIENT_SECRET`.

---

### Mode C — Direct sale (SolidWorks, FreeCAD, Rhino)

These platforms have no usable independent payment infrastructure:
- **SolidWorks** — Dassault's 3DEXPERIENCE marketplace is enterprise-only; no IPN API for independent developers.
- **Rhino** — Food4Rhino is a listing site only; no payment processing.
- **FreeCAD** — Open source, no marketplace.

For these platforms, CCT sells directly via the website (WooCommerce). The plugin has a one-time activation step.

```
Customer buys at cheapcadtools.com (WooCommerce)
  └── WooCommerce webhook → POST /api/cct-ipn
        └── Render adds email to subscribers.json, sends activation email

User opens plugin → one-time "Activate" dialog
  └── User enters their email address
        └── Plugin calls POST /api/provision { user_id: email, email, platform: "direct" }
              └── Render checks subscribers.json, returns licence.lic

Email stored in %APPDATA%\CheapCADTools\config.json (never re-entered)
```

No platform identity API needed — email is the identity anchor.

---

### Platform comparison

| Platform | Marketplace | Payment channel | Identity source | Entitlement check |
|----------|-------------|----------------|-----------------|------------------|
| Fusion 360 | Autodesk App Store | Autodesk/PayPal IPN | `app.currentUser.userId` | `checkentitlement` API |
| Inventor | Autodesk App Store | Same as Fusion | Same as Fusion | Same as Fusion |
| OnShape | OnShape App Store | OnShape IPN (TBC) | OAuth → sessioninfo | `subscribers.json` by email |
| SolidWorks | Direct (CCT website) | WooCommerce | Email (user-entered) | `subscribers.json` by email |
| FreeCAD | Direct (CCT website) | WooCommerce | Email (user-entered) | `subscribers.json` by email |
| Rhino | Direct (CCT website) | WooCommerce | Email (user-entered) | `subscribers.json` by email |

---

### Key env vars on Render (per tool service)

| Var | Purpose |
|-----|---------|
| `PROVISION_SECRET` | Guards all `/api/admin/*` and `/api/subscribers/*` endpoints |
| `PULLEY_LICENCE_B64` | Current licence.lic, base64 encoded |
| `PULLEY_LICENCE_EXPIRY` | Expiry date (YYYY-MM-DD) |
| `PULLEY_APP_URL` | GitHub Release download URL for app zip |
| `PULLEY_APP_VERSION` | Build date string; addin compares to local version.txt |
| `RENDER_API_KEY` | For admin dashboard proxy endpoints |
| `SENDGRID_API_KEY` | Welcome / milestone emails |
| `AUTODESK_APP_ID` | Set after Autodesk App Store registration; empty → subscribers.json fallback |
| `ONSHAPE_CLIENT_ID` | OnShape OAuth app client ID (Mode B only) |
| `ONSHAPE_CLIENT_SECRET` | OnShape OAuth app client secret (Mode B only) |
| `SMALL_STEP_BIN` | Path to compiled small_step binary; activates the fast Rust STEP path |
| `QUEUE_DISABLED` | Set to `1` to bypass the session queue (desktop/local builds only — set by launcher.py) |

---

## 6. The Desktop App

The desktop app is the same Flask app, packaged:

```
packaging/build_release.py   (run manually on dev machine ONLY)
        │
        ├── pyarmor gen  → obfuscates .py source files
        └── pyinstaller  → bundles into PulleyApp_<date>.zip (onedir, single .exe)
```

**Critical:** PyArmor Pro licence has 200 device slots total. **Never run `build_release.py` or any `pyarmor gen` command in CI/CD.** Each Docker container consumes a slot permanently. Build only on the registered Windows dev machine (`C:\Users\cmyer\Documents\PayArmor\pyarmor-regfile-11621.zip`).

`launcher.py` is the PyInstaller entry point. It:
- Sets `PULLEY_BASE_DIR` so Flask finds templates/static inside the bundle (`sys._MEIPASS`)
- Sets `QUEUE_DISABLED=1` to bypass the session queue (single local user — no contention)
- Opens `localhost:5154` in the default browser
- Runs Flask in a background thread

The desktop app and web app are separate release artifacts. A git push updates the web app immediately. The desktop app requires a full build + GitHub Release upload + Render env var update to distribute to customers.

---

## 7. CAD Plugin Pattern

CCT CAD plugins are **thin bridges** — they do not duplicate the web app UI. The web app is the UI. The plugin's only jobs are:

1. **Subscription gate** — check entitlement, run provision, install/update desktop app
2. **Open the web app** — in a browser or embedded panel
3. **Import results** — auto-import downloaded files into the CAD tool

### Fusion 360 addin (the reference implementation)

```
Addin starts
    │
    ├── Writes fusion_watch_dir → %APPDATA%\CheapCADTools\config.json
    │
    ├── Background thread: polls WATCH_DIR every 2s
    │       New .step/.dxf file detected
    │               └── fires Fusion custom event
    │                       └── UI thread: importManager.importToTarget()
    │
    └── Sidebar palette (sidebar.html — just buttons):
            [Open Timing Pulleys]   → provision check → launch PulleyApp.exe
            [Restore from File]     → file picker → read CCT metadata → open web app with params
            [Import History]        → pick previous import → re-open web app with params
```

The Flask app reads `config.json` and mirrors every download to `fusion_watch_dir`. The addin catches those files and imports them. No REST API, no polling — just a shared local directory.

### OnShape extension (in progress)

OnShape has no local file system, so the file-watcher pattern is replaced with a REST API upload:

```
Panel (thin — just buttons):
    [Open Timing Pulleys]  → opens cheapcadtools.com/tools/pulleys
                               ?onshape=1&documentId=xxx&workspaceId=yyy
    [Open Local Server]    → opens localhost:5000

Web app detects ?onshape=1
    └── Download STEP button becomes "Import to OnShape"
            └── POST /api/onshape/import
                    ├── Generates STEP (same pipeline)
                    └── Uploads to OnShape via
                        POST https://cad.onshape.com/api/v6/translations/d/{did}/w/{wid}
                        (HMAC-SHA256 with user's OnShape API key + secret)
```

The panel is registered as an Application Extension in the OnShape Dev Portal:
- URL: `https://cheapcadtools.com/tools/pulleys/onshape`
- Context: Document (receives `documentId`, `workspaceId` via postMessage)
- Permissions: Read documents + Write documents only

---

## 8. Building a New CCT Tool

### Step 1 — New Flask repo

Copy the PulleyWebApp structure. Minimum files:
```
app.py
geometry/<tool>_geometry.py
exporters/
templates/index.html
static/style.css
requirements.txt
Procfile  (web: gunicorn app:app)
```

### Step 2 — Deploy to Render

- Connect GitHub repo to new Render web service
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Set `PROVISION_SECRET` env var

### Step 3 — Add to Cloudflare Worker

One line in `cct-tools-router`:
```javascript
'/tools/gears': 'https://gearapp.onrender.com',
```

The tool is immediately live at `cheapcadtools.com/tools/gears`.

### Step 4 — WordPress page

Add a page at `cheapcadtools.com/gears` (or add to nav) with a link to `cheapcadtools.com/tools/gears`. Keep it simple — the tool is the product.

### Step 5 — Desktop packaging (optional)

Copy `packaging/` directory. Update tool name in `launcher.py` and `build_release.py`. Run `build_release.py` locally, upload zip to GitHub Releases, update Render env vars.

### Step 6 — Fusion addin (optional)

Copy `Fusion Addins/PulleyWebApp/`, update:
- `BUTTON_ID`, `PALETTE_ID`, `WEB_URL`, `LOCAL_URL` constants
- `PulleyWebApp.manifest` — tool name and description
- `sidebar.html` — tool name in heading
- Everything else (provision, file watcher, import handler) reuses unchanged

---

## 9. Conventions

### URLs and routing
- Tool live URL: `cheapcadtools.com/tools/<slug>`
- Tool direct URL: `<toolname>.onrender.com`
- Admin dashboard: local `admin_dashboard.html` — no server needed, credentials in sessionStorage
- Provision endpoint: `POST /api/provision` — guards the download; requires subscription
- Admin endpoints: `/api/admin/*` — guarded by `PROVISION_SECRET` Bearer token
- Subscriber management: `/api/subscribers/add|remove` — Bearer token

### Python
- Python 3.14 — cadquery removed; no Python version constraint on STEP export.
- **Active STEP path:** `small_step` Rust binary via `exporters/step_worker_ss.py`. Requires `SMALL_STEP_BIN` env var pointing to the compiled binary.
- **Unexercised fallback:** cadquery via `exporters/step_worker.py` — not called at runtime; not maintained.
- STL/preview: trimesh + shapely + manifold3d (no cadquery dependency, runs on any Python).

### Desktop build
- Never run `build_release.py` in CI/CD — PyArmor device slots are permanent and limited.
- Remove `DEV_BACKDOOR_KEY` / `_DEV_BACKDOOR` before any public release.
- Set `TEST_MODE = False` in Fusion addin before publishing to App Store.

### Deployment checklist (before every push)
See `web_provisioning.md` for the full checklist. Short version:
1. Review diff
2. Update help files and TODO.md
3. Check CCT metadata schema — bump `CCT_SCHEMA_VERSION` if params renamed/removed
4. Run `pytest tests/` — all must pass
5. Run benchmarks and concurrency tests
6. `git push` → Render redeploys
7. Run `generate_checkin_report.py` → commit the report

### Brand
- **Name:** CheapCAD Tools
- **Primary red:** `#761516`
- **Background gray:** `#eaebed`
- **Dark UI (panels/desktop):** `#1a1a1e` background, `#e8e8ec` text
- **Font:** Roboto (web); Segoe UI (desktop panels)
- **Tone:** Direct, no-nonsense, engineering-focused. "Professional grade at an affordable price."

---

## 10. Live Configuration

### Cloudflare Worker — `cct-tools-router`
- **Route:** `cheapcadtools.com/*` (catches all requests — Worker decides pass-through vs. route)
- **Zone:** `cheapcadtools.com`

Worker script routes the following paths to Render; everything else passes through to GreenGeeks:

```javascript
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Tool routing table — add one line per new tool
    const tools = {
      '/tools/pulleys': 'https://pulleywebapp.onrender.com',
    };

    if (path === '/tools' || path === '/tools/') {
      return fetch('https://tools-hub.onrender.com/', request);
    }

    for (const [prefix, origin] of Object.entries(tools)) {
      if (path.startsWith(prefix)) {
        const stripped = path.slice(prefix.length) || '/';
        const target = new URL(origin);
        target.pathname = stripped;
        target.search = url.search;
        return fetch(new Request(target.toString(), request));
      }
    }

    // Render-only paths (static assets, API, downloads) — route to Render unconditionally
    const renderOnly = ['/static/', '/api/', '/download/', '/preview/', '/admin'];
    if (renderOnly.some(p => path.startsWith(p))) {
      return fetch('https://pulleywebapp.onrender.com' + path + url.search, request);
    }

    // Everything else — pass through to GreenGeeks
    return fetch(request);
  }
}
```

> **Important:** The route must be `cheapcadtools.com/*` (not `/tools*`). The Worker handles
> pass-through to GreenGeeks for all non-tool paths, so this is safe.

### Render Configuration (per tool service)
- **Repo:** `https://github.com/xootme/PulleyWebApp-ss` (branch `main`)
- **Runtime:** Python 3
- **Build Command:** `bash render_build.sh`
- **Start Command:** `gunicorn app:app`
- **Custom Domain:** none required — Worker handles routing

`render_build.sh` does three things: `chmod +x bin/small_step_linux`, runs `bin/small_step_linux --version`
to verify the binary, then `pip install -r requirements.txt`.

The `small_step` Rust binary is a **pre-compiled musl-static x86_64 Linux binary** committed directly
to `bin/small_step_linux` in the repo — `small_step` is a private repo and cannot be cloned as a
Render submodule. `step_worker_ss.py` auto-detects it at `bin/small_step_linux` (no `SMALL_STEP_BIN`
env var needed on Render). See `RELEASE.md` in the `small_step` repo for the rebuild procedure.

### DNS (Cloudflare)
No CNAME record for `tools` is required. The Worker runs on the root domain proxy.

---

## 11. Infrastructure Contacts & Credentials

| Service | Account | Notes |
|---------|---------|-------|
| Render | xootme@gmail.com | Service ID `srv-d7bve2a8qa3s738n68ig`; API key in Windows Credential Manager |
| GitHub | xootme | PAT in Windows Credential Manager (`git:https://github.com`); use `git credential fill` |
| GreenGeeks | xootpro | SSH key `~/.ssh/id_ed25519_greengeeks`; paramiko for WordPress edits |
| Cloudflare | — | Worker: `cct-tools-router`; route: `cheapcadtools.com/*` |
| Autodesk App Store | — | App ID set in `AUTODESK_APP_ID` env var after registration |
| SendGrid | — | API key in Render env var `SENDGRID_API_KEY` |

SSH to GreenGeeks: `ssh -i ~/.ssh/id_ed25519_greengeeks xootpro@chi203.greengeeks.net`
SSH to Render: `ssh -i ~/.ssh/id_ed25519_claude_cct srv-d7bve2a8qa3s738n68ig@ssh.oregon.render.com`
