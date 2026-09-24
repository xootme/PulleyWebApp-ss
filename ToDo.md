# ToDo — Timing Pulley Generator

Pruned 2026-09-23: items verified done or superseded against the code were
removed (see git history of this file for the old health check, metadata
and test-gap sections).

---

## Token Model (replaces subscription licensing)

**Plan:** the app is free in every CAD program; only file outputs cost tokens.
One account works across all CAD programs (Fusion, FreeCAD, SolidWorks, web).

| Output | Tokens | Price |
|---|---|---|
| 2D (SVG, DXF) | 1 | $0.10 |
| STL | 2 | $0.20 |
| STEP | 3 | $0.30 |

### Decisions (ADR-008)
- [x] ADR-008 written: tiers include lower tiers (STEP 3 ⊃ STL 2 ⊃ 2D 1); one purchase unlocks the **whole design**; unlocks last **24 h**; upgrade pays the **difference**; **signup tokens**, no weekly allowance; sign-in by **email link and OAuth** (Microsoft, Google, GitHub — not Apple)
- [ ] Pack sizes and prices (card fees ~30¢ + 2.9% exceed a 10¢ token — e.g. 50 for $5, 250 for $20)
- [ ] Signup grant size (placeholder: 10)
- [ ] Autodesk App Store channel: can it sell token packs, or does it stay a subscription / go away?

### Accounts and ledger
- [x] `cct_common.tokens` (canonical repo): SQLite ledger — tier pricing, 24 h unlocks, upgrades, refund-on-failure, idempotent credits, atomic spend; 29 tests in `tests/test_tokens.py`
- [ ] Run `sync_cct_common.py` here to bring in cct_common 0.7.0 — wait until cct_common's unrelated uncommitted edits (bug_report, js_extract, editor2d) are committed or dropped, or the sync copies them in
- [ ] Postgres backend behind the same `TokenStore` interface (when hosting moves)
- [x] `cct_common.accounts` + `cct_common.account_routes` (cct_common 0.7.0, `b8835e9`): linked identities, email sign-in links (15 min, single use, rate-limited, confirm-button page), hashed revocable sessions (web cookie 30 d, add-in device token 1 y), account page data/history, account deletion; 38 tests
- [x] `cct_common.sqlite_db`: WAL + `synchronous=FULL`, `integrity_check()`, online `backup()`
- [ ] Wire into `app.py` (after the sync): store at `logs/accounts.sqlite3`, `register_account_routes` with Resend, startup integrity check → 503 on token routes if it fails
- [ ] Sign-in UI in the page (email box, account menu with balance, sign out)
- [ ] OAuth sign-in (Microsoft, Google, GitHub) on `AccountStore.sign_in`
- [ ] Add-in device sign-in (device-code flow: add-in shows a code, user approves in the browser, add-in receives a device token)
- [ ] GitHub: no ID token — fetch the user from the API; request `user:email` and take the verified primary address from `/user/emails`
- [ ] Register OAuth apps (owner action, all free): Microsoft Entra admin center; Google Cloud Console; GitHub → Settings → Developer settings → OAuth Apps
- [ ] Charge-at-download checkboxes: a STEP purchase offers the included STL/SVG/DXF
- [ ] WooCommerce pack purchase → webhook credits the account (reuse the HMAC-verified webhook pattern in `cct_common.licensing`)
- [ ] Account page: balance, purchase history, per-export history
- [ ] Admin dashboard: balances, grants/refunds, sales

### Database backups (must be live before charging real money)
Nothing is backed up today — the existing `logs/*.json` (subscribers, purchases, licences)
sit only on the Render disk too. `SqliteDB.backup()` and `integrity_check()` exist; the rest doesn't.
- [ ] Scheduled backup job: `backup()` of `accounts.sqlite3` hourly (the ledger is small), plus a daily copy
- [ ] Copy every backup **off the server** (e.g. Azure Blob Storage or S3-compatible storage) — a backup on the same disk dies with it
- [ ] Encrypt backups before upload (they contain emails) and keep the key outside the bucket
- [ ] Retention: hourly for 7 days, daily for 90 days; delete older copies automatically
- [ ] Verify each backup after writing it (`integrity_check()` on the copy) and alert (email) on any failure or a missed run
- [ ] Also back up the remaining `logs/*.json` state until it moves into the database
- [ ] Startup check in `app.py`: `integrity_check()`; if it fails, token routes return 503 and an alert is sent
- [ ] Written restore procedure: restore latest backup → replay payment-provider orders (idempotent on order number) → check balances
- [ ] Practise a restore from the off-server copy before launch, then quarterly
- [ ] After the move to Postgres (Neon): turn on point-in-time restore and keep the off-server copies as a second line

### Charging exports
- [ ] Charge only after a file is generated successfully; async STEP charges at job completion, not at request
- [ ] Gate every download route — today the web `/download/svg`, `/dxf`, `/stl`, `/flange-stl`, belt/rim/all routes have no check at all
- [ ] Add-in API routes (`/api/download/step|dxf|stl`): swap `register_trial_download(machine_id, fmt)` for a token spend
- [ ] Clear "not enough tokens" response (HTTP 402 + JSON) and a buy-tokens link in the web UI and add-ins

### Running locally (desktop app)
- [ ] Local app requests a one-time **export ticket** from the server (server deducts tokens) before writing a file
- [ ] Keep the small_step binary server-only so STEP (3 tokens) can only come from the server; 2D/STL may run locally
- [ ] Accept that local enforcement can be bypassed; PyArmor only raises the bar

### Retire once tokens are live
- [ ] `licence.lic` / PyArmor licence flow, `/api/provision`, `subscribers.json`, the desktop licence-key activation routes
- [ ] Weekly trial download limit (`register_trial_download`)
- [ ] Dev backdoor (see Before Public Launch) — goes with the launcher licence check

### Hosting (move off the Render disk)
Render can't scale while a disk is attached (only one instance allowed), and every state
file (`logs/*.json`, queue sessions, trial counts) lives on that disk.
- [ ] Move all state to Postgres (ledger, accounts, queue sessions) — no disk
- [ ] Pick host. Prices checked 2026-09-23:
  - **Azure Container Apps (Consumption)** — ~$0.000024/vCPU-s, monthly free grant 180k vCPU-s / 360k GiB-s / 2M requests; scales to zero. Existing Microsoft account.
  - **Google Cloud Run** — same free grant and similar rates.
  - **Railway** — $5/$20 plans with that much usage included; closest to Render.
  - **Render Pro** — $25/mo workspace + $7–25 per instance; autoscaling needs Pro and no disk.
  - Not a fit: AWS App Runner (closed to new customers 2026-04-30), Lambda (would need the queue/async-job design rewritten).
- [ ] Database: **Neon** Postgres (free tier, scales to zero, ~$0.106/CU-hour) works with any host
- [ ] Dockerfile: Python + deps + Linux small_step binary
- [ ] One request per instance (exports are CPU-bound); max instances as a cost cap; decide min instances (cold start vs idle cost)
- [ ] Re-evaluate whether the session queue is still needed once autoscaling is in place
- [ ] Update `gunicorn.conf.py` comment — Render Standard is 2 GB now, not 1 GB

---

## Marketing — sample files

Post sample parts that say they were generated with CheapCAD Tools. Every download already
embeds the design (`/* CCT:{...} */`), and the web app's Import button restores it — so each
listing can say "open this file in CCT to change the tooth count/bore".

### Prepare
- [ ] Choose a sample set covering the main families (HTD, GT, T, imperial) plus spokes/flange/nubs showpieces
- [ ] For each: STEP + STL + DXF, a rendered image, and a short description with one "made with CCT" link
- [ ] Per-site referral links (`?ref=grabcad`, `?ref=printables`, …) so analytics shows which site sends customers
- [ ] Pick a licence — CC BY keeps the attribution attached (Printables/Thingiverse ask per upload)
- [ ] Read each site's self-promotion rules before posting; lead with a useful part, not an ad

### Where to post
- [ ] **GrabCAD** (STEP)
- [ ] **Printables**, **MakerWorld**, **Thingiverse**, **Thangs**, **Cults3D** (STL)
- [ ] **Onshape public documents**, **TraceParts / 3D ContentCentral** (STEP)
- [ ] **Autodesk Fusion community gallery / forum** — link back to the App Store listing
- [ ] **Sketchfab** — interactive 3D embed for the website
- [ ] **Reddit:** r/functionalprint, r/3Dprinting, r/Fusion360, r/FreeCAD, r/robotics; DXF samples for r/lasercutting, r/hobbycnc, r/CNC
- [ ] **Hackaday.io** / **Instructables** — a build write-up with the files attached
- [ ] **FreeCAD forum** — alongside the FreeCAD workbench

---

## Marketing — free, automatable

Priority order: 1 → 2 → (4 + 5 together with the token ledger). Automate content generation,
not community posting — bulk auto-posting breaks most sites' rules.

### 1. Programmatic SEO pages (highest value)
- [ ] Generate a static landing page per common config (e.g. "HTD-5M 20 tooth pulley 8 mm bore"): preview image, dimension table (OD, pitch diameter, teeth), free 2D preview, "Download STEP — 3 tokens" and "Open in CCT to customize" buttons
- [ ] Choose the config set (family × pitch × common tooth counts × common bores) — hundreds of long-tail pages
- [ ] Auto-generated `sitemap.xml` + schema.org structured data (Product / SoftwareApplication)
- [ ] Submit the sitemap to Google Search Console

### 2. Every file shared is an advert
- [ ] "Made with CheapCAD Tools" in STEP product name (`rename_step_product`), DXF comment, STL header
- [ ] "Share this design" button → link that restores the design (`loadParamsFromUrl` already exists)
- [ ] Branded filenames (e.g. `HTD-5M-20T_cct.step`)
- [ ] (Optional) small engraved logo, removable as a paid option

### 3. Embeddable free calculators
- [ ] Belt length, center distance, drive ratio, tooth-count picker — reuse the existing geometry code
- [ ] Embeddable via `<iframe>`/JS snippet, each with a link back to the tool

### 4. Automated email (Resend already wired)
- [ ] Onboarding sequence after signup: day 0 (how tokens work), day 3 (spokes/flanges), day 7 (open your file in Fusion/FreeCAD)
- [ ] Low-balance and unused-token reminders
- [ ] Monthly "what's new" built from release notes

### 5. Referral tokens
- [ ] "Give 10, get 10" tokens per referred signup — runs on the token ledger, costs tokens not cash

### 6. Auto-rendered video and images
- [ ] Headless Three.js or Blender renders turntable videos/images of showcase pulleys from STL
- [ ] Scheduled posting to YouTube Shorts and Pinterest via their free APIs

### 7. Model-site uploads
- [ ] Script that builds each upload package (files, images, description, `?ref=` link)
- [ ] API upload where supported (Thingiverse, Sketchfab, Onshape public docs); post manually or in small batches elsewhere (GrabCAD, Printables)

### 8. Mention alerts (reply by hand)
- [ ] F5Bot (Reddit / Hacker News) + Google Alerts for "timing pulley generator", "GT2 pulley STL", "pulley STEP file", etc.
- [ ] Reply manually with genuinely helpful answers — never auto-reply

### 9. AI-assistant discovery
- [ ] Once the headless API + OpenAPI spec exist (see Agent / Headless API Access), publish an MCP server and list it in MCP registries — every agent call is a paid export

### 10. One-time listings
- [ ] AlternativeTo, Product Hunt launch, GitHub awesome-lists (3D printing / CAD), FreeCAD Addon Manager, Autodesk App Store listing keywords

---

## Before Public Launch
- [ ] **Remove dev backdoor password `'xoot'`** — the server side is already gone (dropped by `cct_common.licensing`). Still present in:
  - `packaging/launcher.py:60` and `packaging/launcher_ss.py:55` (still grant access locally)
  - Fusion add-in `DEV_BACKDOOR_KEY`: `Fusion Addins/PulleyWebApp/PulleyWebApp.py`, `CCT_Addins/fusion360/TimingPulley/PulleyWebApp.py`, and its `dist/PulleyWebApp` + `dist/PulleyWebAppTrial` copies
- [ ] Add a test that `/api/provision` returns 403 for an unknown user (no test covers it) — or drop it with the token model

## Release and manual checks
The last desktop release (1.2.1, 2026-07-01) predates the cct_common migration (2026-07-16 → 07-23).
- [ ] New desktop release: `build_release_ss.py` → GitHub Release → `prepare_release.py` → Render env vars → add-in update banner appears in Fusion
- [ ] Manual (can't be checked from code): Render shows the latest commit live; 3D preview renders in a browser
- [ ] Manual: Fusion add-in provisions, launches the desktop app, and auto-imports STEP/STL from both the desktop and online apps
- [ ] Manual: FreeCAD add-in download + import
- [ ] `exporters/addin_helpers.py` is tested (`tests/test_addin_helpers.py`) but neither add-in uses it — wire it in or delete it

## CAD add-in update flow — use cct_common's shutdown/live-reload APIs
Both add-ins still force-kill the desktop server instead of calling `POST /api/shutdown`
(see EBoxDesigner D036 for the zombie-reloader failure this avoids).
- [ ] **Fusion 360:** `PulleyWebApp.py:372` runs `taskkill /F /IM PulleyApp.exe` → call `/api/shutdown` first
- [ ] **FreeCAD:** `cct_pulley/commands.py:373` (`taskkill`) and `cct_pulley/server.py:123` (`terminate()`) → same change
- [ ] Confirm an open browser tab reloads via `/_cct_live_reload.js` across an add-in-triggered update

## small_step known issues
Full repros in `C:\Users\cmyer\Documents\small_step\STEP_SOLUTIONS.md`.
- [ ] FreeCAD rejects complex pulley profiles — small_step emits no SURFACE_CURVE/PCURVE (§5, "open obligation"). Fusion/eDrawings are fine.
- [ ] Wire-order `EDGE_LOOP` bug on a spoke + metal-flange STD-2M 74T pulley — "NOT YET INVESTIGATED" (2026-07-22 entry)
- [ ] Self-crossing unfilleted spoke void in the "hub overlap" regime (wide spoke vs hub radius) — "NOT fixed" (2026-07-22 bowtie entry)

## Load Testing Dashboard
`record_benchmarks.py` and the WSL gunicorn concurrency check exist; the simulator does not.
- [ ] High-load simulation with a real-time dashboard
  - Adjustable number of simulated concurrent users
  - Per-user behaviour: download frequency, 2D/3D parameter changes, belt/hub/spoke modifications
  - Live view of each simulated user's current action
  - Requests/sec, error rate, p95 latency, per-endpoint breakdown

## Agent / Headless API Access
Nothing below exists yet. One API serves agents and CAD plugins alike.

**Core API:**
- [ ] `GET /api/capabilities` — families, pitches, output formats, hub/spoke feature flags
- [ ] `GET /api/describe?family=X&pitch=Y` — parameter constraints and download URL template
- [ ] JSON errors on all 400 responses — the SVG/DXF routes still return plain text (`app.py` 968, 981, 1028, 1041, 1074, 1086, 1119)
- [ ] OpenAPI 3.0 spec at `/api/openapi.json`
- [ ] (Optional) MCP server wrapper

**CAD plugin prerequisites:**
- [ ] CORS on `/api/` and `/download/` routes — today only `/api/admin/` and `/api/subscribers/` have it
- [ ] `Content-Security-Policy: frame-ancestors` for embedded-UI mode (webview/iframe in a CAD task pane)
- [ ] API versioning (`/api/v1/`) — plugins ship and rarely update
- [ ] Freeze public parameter names (`bore`, `teeth`, etc.)

**Three plugin integration modes (all share the same server API):**
1. **Embedded UI** — iframe/webview of `cheapcadtools.com/tools/pulleys`
2. **Direct download** — plugin reads CAD context, calls `/download/step|dxf|stl`, imports the file
3. **Agent-mediated** — CAD-native AI reads the OpenAPI spec and calls endpoints

---

## CAD Plugin Roadmap (excluding Fusion 360 and Onshape)

### SolidWorks — Listener App
`solidworks_listener/listener.py` + `SolidWorksListener.spec` exist. Tray app, file-watch
import via `GetActiveObject("SldWorks.Application")` → `OpenDoc6()`; Flask mirrors downloads
via `_mirror_to_solidworks()`.
- [ ] Test with an actual SolidWorks installation — verify `OpenDoc6` param types (byref VARIANTs in pywin32)
- [ ] Handle SolidWorks launching *after* the listener (`listener.py:80` only attaches once, no retry)
- [ ] Optionally auto-launch SolidWorks (`win32com.client.Dispatch`)
- [ ] Installer (NSIS / Inno Setup) or zip; startup entry in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- [ ] Distribution: direct download from `cheapcadtools.com`

### FreeCAD — Workbench
Built: `CCT_Addins/FreeCAD/TimingPulley` (`InitGui.py`, `package.xml`, `cct_pulley/`, tests).
- [ ] Publish through the FreeCAD Addon Manager

### AutoCAD — Listener App or .NET Plugin
No code yet.
- [ ] Decide: listener app (COM `AutoCAD.Application` → `Import`, same pattern as SolidWorks) or .NET plugin (`PaletteSet` + WebView2, `FileSystemWatcher`)
- [ ] Build the chosen option
- [ ] Distribution: Autodesk App Store (same account as Fusion) or direct download

### Other Platforms (research only)
- **Inventor** — COM automation like AutoCAD; could reuse the listener approach
- **Rhino / Grasshopper** — RhinoCommon Python; a component could call `/download/step`
- **CATIA, NX** — heavyweight SDKs; skip for v1
