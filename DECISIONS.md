# Architectural Decision Records

## ADR-012 — Admin dashboard: signed-in admins, subscribers, sales with refunds
**Date:** 2026-09-26
**Status:** Active

**Context:**
The old `admin_dashboard.html` was a static page that called bearer-token `/api/admin/*`
routes (Render-era: metrics, queue, subscribers/licences, downloads). Most of it described the
subscription model that ADR-008 replaced, and none of it showed the token ledger. Refunds had no
tooling: one issued in the PayPal/Stripe dashboard takes back tokens in proportion to the
money, so a refund net of fees left tokens behind.

**Decision:**
- One shared dashboard, `cct_common.admin` (0.17.0), at `/admin`. Every CCT tool shares the
  accounts database, so any tool's `/admin` shows every tool's subscribers. Tabs: Subscribers,
  Sales, Bug reports, Status. Every column sorts by clicking its heading (again to reverse,
  blanks always last), and each tab has a search box.
- **Who gets in:** the normal account sign-in, with the email in `ADMIN_EMAILS`
  (comma-separated; default `xootme@gmail.com`). Browser sessions only; add-in/agent device
  tokens get a 404. Anyone else gets a 404, not a 403, so the page isn't advertised. Changes
  must come from the page's own origin and be JSON (the cookie is SameSite=Lax; this closes the
  rest). The JSON lives under `/admin/api/`, not `/api/admin/`, which carries the old routes'
  wildcard CORS header.
- **Subscribers:** first subscribed, last sign-in, tokens now (free / bought), **lifetime
  tokens** (one number: every token received — signup, purchase, referral, promo and positive
  adjustments), and the **last 5 uses** (the CCT tool, tokens, date, format; refunded failed
  exports don't count). The tool comes from a new `app` column on the ledger, written by each
  tool's `TokenStore(..., app=...)` (`"pulleys"` here); older rows show the mounting app's
  `default_app`. **Grant / adjust**: a positive amount is a `promo` credit (free tokens, not
  refundable); a negative one is an `adjust`. A reason is required and recorded.
- **Sales and refunds (terms §4):** Refund… on an order quotes the account's unused
  *purchased* tokens from that order, valued at the price paid, less the processor's fee (read
  from PayPal/Stripe; editable, and entered by hand when the provider doesn't report it). A
  confirmed refund goes through the provider's API and takes back exactly those tokens, under
  the same `provider-refund:<id>` ref the provider's own refund notification uses, so the
  notification that follows takes nothing more.
- **Ledger fix that came with it:** a refund take-back used to count as *spending*, so it ate
  free tokens first. Refund adjustments are now left out of "free used"; they come off the
  purchased tokens they refunded.
- **Dropped:** Metrics, Queue, Render status, Subscribers/Licences (the old model), Downloads.
  The bearer-token `/api/admin/*` routes remain in `app.py` with nothing calling them; retiring
  them is a ToDo.

**Consequences:** `ADMIN_EMAILS` should be set on test and production at deploy. The browser
harness `tests/browser/admin_ui.js` (seeded by `admin_seed.py`) drives sorting, search, both
dialogs, bug delete and status; `cct_common/tests/test_admin.py` covers the routes on SQLite and
Postgres.

## ADR-011 — Help pictures built from the app's own drawings, shown on hover
**Date:** 2026-09-26
**Status:** Active

**Context:**
The help pages were text only. Settings like Fillet Tip or Backlash change the part by a
millimetre or less, which words describe poorly.

**Decision:**
- Each picture is built from the app's **own SVG downloads** (`/download/svg`, `svg-rim`,
  `belt-svg`) by a script in `tools/help_illustrations/`, cropped and zoomed with nested
  `viewBox`es — vector all the way, so zoomed insets stay sharp. Callouts (dimensions, numbers,
  highlighted arcs) are placed from the drawing's geometry, not by hand, so a script can be
  rerun when the geometry changes. Output lives in `static/help/*.svg`; each script's
  docstring gives the exact download URLs of its source drawings.
- Only two are not from app drawings: 3D Mode (crops of the two sample screenshots) and Belt
  / Clearance Height (a labelled diagram — the app has no side view).
- **3D-mode pictures** (Hub so far) pair two things from the app: a snapshot of its own 3D
  preview (`tools/help_illustrations/shoot3d.js` drives headless Chrome — settings as a JSON
  list of steps, view snapped, canvas captured at 2×; the builder trims it and embeds it as
  JPEG) for what the part looks like, and a **vector section of its STL download** (sliced
  with trimesh's plane intersection + shapely `polygonize`; planes nudged off vertex rows)
  for exact dimensions. The STLs carry the design metadata after the triangles, so the
  builders read exactly the declared triangle count.
- Hover keys drop only the pulley number (`spokes1_x` → `spokes_x`, `hub2_x` → `hub_x`,
  `p2_x` → `x`), so panels with same-named settings can't collide. A `PICTURES` entry can be a
  function of the label when the right picture depends on another setting (Number of Screws
  follows the Retention Method).
- The pop-up shows each picture at its own size (capped at 1000 px / the window), so pictures
  are designed to be read at that size: ~650–1000 px wide, body text ≥ 14 px.
- What the 3D help pages state about the geometry (Spoke Height is the centred web thickness;
  Flange Height is the thickness at the teeth, the lip thinner by rim × tan(angle); metal
  plates come in the pulley STL; nub pins are their socket less the allowance) is pinned by
  `tests/test_help_geometry_claims.py`, measured on STLs built through the app — so the
  pictures and text can't silently drift from the geometry again.
- Slicing gotcha (tests and builders): shapely's `polygonize` returns each face with its holes
  already cut *and* each hole as a face of its own — even-odd only the faces' exteriors, or
  the holes get filled back in.
- Horizontal slices of captured-nut hubs are taken at the nut's screw height (hub top − hex
  circumradius, using the exporter's `_nut_dims`), not mid-hub — a small nut's screw sits
  near the top.
- Where the real preset is too small to see, the picture exaggerates it and says so (Backlash).
- The pictures appear on the help pages (click for full size) and as **hover pop-ups** on the
  setting's label (`PICTURES` table at the end of `index.html`, keyed by input id without the
  pulley prefix; labels that wrap their control use `data-picture`).
- Tests: `tests/test_help_pictures.py` (files exist and are SVGs, every hover key matches a
  label); `tests/browser/help_ui.js` (pop-ups in the browser).

---

## ADR-010 — Spoke settings are fitted to the pulley, with a warning and Auto-fit
**Date:** 2026-09-26
**Status:** Active

**Context:**
Spoke settings are absolute mm. After a smaller pitch or fewer teeth they could be impossible:
fillets colliding, spokes crowding the rim, the hub filling the web. Four separate checks
disagreed (a page-side tip-circle formula that was ~2× tooth height optimistic, a server fillet
clamp, a geometry error at export time, and the builder silently dropping fillets it couldn't
fit). Edits were silently reverted or clamped as the user typed, and some exports failed.

**Decision:**
- One resolver, `geometry/spoke_fit.py`, judges a layout with the **real spoke builder**
  (`_spoke_void_polygons`, which now reports fillets it had to leave out) — "fits" means "the
  exporters can build it".
- When the requested settings don't fit, it finds the smallest change: first room between
  hub and rim (Hub OD and Rim Depth shrink together, keeping ≥ 1 mm rim and ≥ 1 mm hub wall),
  then one setting alone if that is enough, otherwise Fillet Base → Fillet Tip → Spoke Width →
  Spoke Count; fillets are then grown back. If nothing fits, spokes are left out.
- `_parse_spoke_params` builds with the fitted values, so the preview and every export agree
  and none fails. The typed values are **never** changed without the user: the page shows a
  warning listing the changes with an **Auto-fit** button (`/api/spoke-fit`).
- Tests: `tests/test_spoke_fit.py` (incl. a property sweep that every fitted result builds);
  `tests/browser/help_ui.js` (warning and Auto-fit in the browser).

---

## ADR-009 — 3D Print Compensation is a perpendicular offset of the whole tooth outline
**Date:** 2026-09-26
**Status:** Active — changes existing designs that use compensation

**Context:**
Each groove generator applied `print_extra / 2` in its own way: tooth tips moved p/2, the
groove floor moved p/2 on HTD but p on the trapezoidal profiles, and walls moved by various
amounts. Meanwhile flanges, hub/rim features and the dual layout computed the OD as
`getOuterDiameter(n, pitch, pld + print_extra − clearance)` — a full p per surface.

**Decision:**
- The value p is the distance **every** surface of the tooth outline (lands, tips, walls,
  floor) moves into the material, measured perpendicular to the surface. The outer diameter
  shrinks by 2p, matching the other OD calculations.
- `generate_profile_groove` builds the nominal groove (the generators are called with 0) and
  `_offset_groove_outline` erodes the material under the whole outline by p with a round-join
  buffer; `wrap_groove_to_pulley` then measures from the nominal OD. One place, all formats.
- The offset is exact in the flat groove frame; wrapping adds a tiny distortion.
- With p = 0 the output is byte-identical to before. With p > 0, designs change: tips move
  twice as far as they used to.
- Tests: `tests/test_print_compensation.py`.

---

## ADR-008 — Token model: free app, pay per export
**Date:** 2026-09-24
**Status:** Active (being implemented; supersedes ADR-005 once live)

**Context:**
ADR-005 sells a yearly subscription (licence.lic, Autodesk App Store, WooCommerce
licence keys). The goal is to make the app free in every CAD program and charge
only for the files people export, with one account that works across Fusion,
FreeCAD, SolidWorks and the web.

**Decision:**
- **Tokens cost $0.10 each** and are sold in packs (card fees of ~30¢ + 2.9% exceed
  a single 10¢ token). Pack sizes are decided with the purchase webhook.
- **Priced by tier, and a tier includes every tier below it:**

  | Tier | Formats | Tokens | Also unlocks |
  |---|---|---|---|
  | 2d | SVG, DXF | 1 | — |
  | stl | STL | 2 | 2d |
  | step | STEP | 3 | stl, 2d |

  At download time a STEP purchase offers the included STL/SVG/DXF as checkboxes.

  *Revised 2026-09-26:* a token is now **5¢**, and the tiers cost **2D 2 · STL 3 ·
  STEP 4** (10¢ / 15¢ / 20¢), so each tier adds one token. A CAD download was 30¢
  under 2/4/6, which was too much. The download dialog explains the difference with
  a red note above the price list. `cct_common.tokens.TIER_PRICE` (0.16.1).
- **One purchase unlocks the whole design on screen**, not a single part: a two-pulley
  drive with its belt and flanges at STEP tier costs 3 tokens. The design is
  identified by a hash of its parameters (`cct_common.tokens.design_key`).
- **Unlocks last 24 hours.** Re-downloading any unlocked format of that design is
  free in that time; moving up a tier costs only the difference (STL → STEP = 1).
- **New accounts get signup tokens** (a one-time grant). No weekly free allowance;
  the weekly trial limit (`register_trial_download`, `/api/fp-token`) is retired.
- **Sign-in offers both an email link (via Resend) and OAuth** (Microsoft, Google and
  GitHub; Apple deliberately left out — it needs a $99/year developer membership).
  GitHub is plain OAuth 2 with no ID token: after sign-in the server calls GitHub's
  API for the user id and reads the verified primary email from `/user/emails`
  (scope `user:email`), since many users keep their email private. Add-ins get a long-lived device token for the same
  account. Sign-in identities (provider + subject id) are linked to an account
  rather than the account being keyed only by email, so one person can reach the
  same tokens through several sign-in methods.
- **Charge only for delivered files:** the charge is taken before generating and
  refunded automatically if generation fails, so a failed export costs nothing.
- **The balance is never stored.** It is the sum of an append-only ledger (signup,
  purchase, spend, refund, adjust rows), so every change is auditable. The
  check-and-spend runs inside one write transaction so two workers can't both
  spend the last token.
- **Add-ins and AI agents sign in by device code (decided 2026-09-25):** the add-in or
  agent shows a short code, the person approves it on `/account/device` in a signed-in
  browser, and the add-in receives its own labelled, revocable device token once.
  **Each device token has a daily token limit, on by default (100 tokens per rolling
  24 hours, `TOKENS_DEVICE_DAILY_BUDGET`)**, chosen at approval and changed on
  `/account/devices`. Reaching it stops only that token (429 `DAILY_LIMIT_REACHED`) and
  emails the owner once a day with how to raise or remove it. Browser sessions have no
  limit, so heavy users clicking in the page are never slowed. Rationale: a runaway agent
  is contained to a known amount, without rate-limiting anyone who wants many pulleys.
- **Inactivity (decided 2026-09-25) — cleaning up dead accounts without taking paid value.**
  Purchased tokens never expire: paid prepaid value can fall under gift-card rules
  (federal minimum 5 years; some states, e.g. California, bar expiry) and state
  unclaimed-property law. Instead: *free* tokens (signup, referral, promo) expire after
  **2 years without a sign-in**; free tokens are counted as spent first. An account with
  **no purchased tokens** left is closed after **5 years without a sign-in**
  (`delete_account`: personal data removed, ledger kept). Each happens only after a
  reminder email sent at least 30 days ahead, and any sign-in resets both clocks. The
  page states the rule wherever tokens are bought or signed in to. Not legal advice —
  confirm the wording with whoever writes the terms.
- **Inactivity revised (2026-09-26) — purchased tokens end with the account.** Every
  account, including one holding purchased tokens, is closed after **5 years without a
  sign-in**, and whatever is left in it expires (ledger `expire` rows, so the record
  stays; `TokenStore.expire_all`). Owner's reason: an end date limits the liability of
  tracking and honouring paid balances forever. Mitigations for the gift-card and
  unclaimed-property risk recorded above: 5 years matches the federal gift-card minimum
  (tokens can only be bought while signed in, so none expires sooner than 5 years after
  purchase); the closing reminder (30+ days ahead) states how many tokens are at stake
  and offers a refund of unused purchased tokens, less processor fees (terms §4); the
  ledger is kept. States that bar expiry (e.g. California) and unclaimed-property
  reporting remain open questions for a lawyer. Free tokens: unchanged (2 years).
- **Tokens revalued (2026-09-26) — 1 token = 5¢, one expiry rule.** Every count doubles
  so money prices stay the same: 2D 2 tokens (10¢), STL 4 (20¢), STEP 6 (30¢); packs
  PayPal $2 = 40, $5 = 100, Stripe $5 = 100, $10 = 200, $25 = 500; the signup grant is
  20 tokens (still $1). Reasons: a finer step leaves room to price smaller things (a
  1-token item), and "20 free tokens" reads better. The separate 2-year expiry of free
  tokens is dropped — one rule for every token: they end with the account after 5 years
  without a sign-in, and each sign-in resets the clock. The ledger still tells free from
  bought tokens: free ones are spent first and are **never refunded** (terms §4 says so).
  Done before any customer existed, so no balance needed converting.
- **Hosting moves to Google Cloud Run (decided 2026-09-25),** billed only while handling
  requests and scaling to zero, so cost follows token sales. (Azure Container Apps was
  picked on 2026-09-24, then dropped: the existing Microsoft account is Microsoft 365, not
  Azure, and Cloud Run is the simpler move for one person — one-command deploys from the
  Dockerfile, one request per instance, a max-instances cost cap, no Log Analytics bill.)
  Database: Neon Postgres. Off-server backups go to a private Cloud Storage bucket,
  encrypted with a key from Secret Manager, accessed through the service's own identity.
  Render-side backups are not pursued; Render is left behind with the move.
- **Built in `cct_common.tokens`** so EBoxDesigner can use the same ledger. SQLite
  first (local, tests, single-instance deploy); a Postgres backend behind the same
  interface when hosting moves off the Render disk (see ToDo.md "Hosting").
- **Data kept, and how it's protected:** email, account id, linked sign-in identities,
  sessions and the ledger — no passwords, no card data (the payment provider holds
  it), no design files (only a parameter hash). Sign-in links, session cookies and
  add-in device tokens are stored only as SHA-256 hashes; links last 15 min, work
  once, are rate-limited, and are used by pressing a button on the page they open
  (mail scanners pre-fetch links). Cookies are HttpOnly, Secure, SameSite=Lax.
  Deleting an account strips email, identities and sessions; ledger rows remain as
  financial records without personal data (a hash of the closed email blocks a
  second signup grant).
- **Corruption and loss:** WAL + `synchronous=FULL`; every spend in one transaction;
  `integrity_check()` at startup, and charging is refused (503) if it fails rather
  than acting on damaged data. Scheduled online `backup()` copies go **off the
  server**. Recovery = restore the latest backup, then replay purchases from the
  payment provider's orders (credits are idempotent on the order number, so a full
  replay can't double-credit). Spends made after that backup are lost, which errs in
  the customer's favour; sessions made after it need a fresh sign-in.
- **No local installs (decided 2026-09-24).** Every export is generated on the server;
  the add-ins use the hosted app. A discounted local-export option was considered and
  rejected: server compute per export is ~$0.00005 against a 10–30¢ price, so a local
  discount gives away revenue while saving almost nothing, and local generation is the
  one path where the token check can be patched out. Once tokens are live the desktop
  build (PyInstaller/PyArmor, `licence.lic`, the launcher) is retired.

**Consequences:**
- Every download route must be enforced server-side; today's web limit is only a
  client-side check that fails open.
- Charging is gated behind a setting until accounts, purchase and UI are done, so
  the live app keeps working unchanged during the build.
- `licence.lic`, `/api/provision`, `subscribers.json`, licence-key activation and the
  dev backdoor are retired once tokens are live.

---

## ADR-007 — STEP export via small_step (Rust) replacing cadquery
**Date:** 2026-06-16
**Status:** Active

**Context:**
cadquery (via Python 3.12 subprocess) produced correct STEP geometry but had two
practical problems: a slow cold-start on each request (OCC kernel initialisation)
and an added dependency on a separate `.venv312` subprocess for every STEP download.
A custom Rust STEP emitter (`C:\Users\cmyer\Documents\small_step\`) was developed
as a direct CLI binary that emits AP214 B-rep without any CAD kernel.

**Decision:**
Replace all cadquery STEP calls with the `small_step` binary via `step_worker_ss.py`.
The binary is invoked as a subprocess: `small_step combined <dxf> <height> [options]`.
When `SMALL_STEP_BIN` env var is set, `_run_ss_worker()` in `app.py` uses it; if unset,
the old `step_worker.py` cadquery path remains available as a fallback.

**Consequences:**
- `step_worker.py` (cadquery) retained as a fallback but no longer called at runtime.
- `step_exporter.export_step` / `generate_pulley_step` imports removed from all routes.
- `from exporters.step_exporter import (...)` retained only for STL functions.
- `SMALL_STEP_BIN` must be set in the environment pointing to the compiled binary.
- `cadquery` removed from `requirements.txt`; Flask venv upgraded from Python 3.12 to 3.14.
- ADR-001 and ADR-002 below are superseded for STEP; cadquery is now only a fallback.

---

## ADR-001 — Python 3.12 for STEP export
**Date:** 2026-04-11  
**Status:** Superseded by ADR-007 (cadquery STEP path replaced by small_step Rust binary)

**Context:**  
The project originally ran on Python 3.14. STEP export was stubbed out with a 501 response
because cadquery-ocp wheels do not exist for Python 3.13+.

**Options evaluated:**
| Option | Notes |
|--------|-------|
| cadquery on Python 3.12 | Works, proven, already partially wired in |
| build123d | PyPI wheels exist but OCP dependency fails on Python 3.14 |
| pythonocc-core | No PyPI wheels for any version; conda only |
| gmsh (proper B-rep) | Installs on 3.14; requires full geometry reimplementation |
| gmsh (mesh→STEP) | Quick but produces mesh shell, not solid; rejected by some CAD tools |
| Python 3.12 subprocess | Complex architecture; two Python versions to maintain |

**Decision:**  
Switch the project venv to **Python 3.12** and install **cadquery**.  
Python 3.14 provides no practical benefit for this application.

**Consequences:**  
- `.venv312` (Python 3.12) created alongside the main env for cadquery only.
- Flask may run on any Python version; the `/download/step` route always shells out to `.venv312\Scripts\python.exe` via `exporters/step_worker.py` subprocess.
- cadquery added to `requirements.txt`.
- This approach is robust to VS Code interpreter selection issues — the correct Python is hardcoded in the route, not inherited from the Flask process.

---

## ADR-002 — STL via trimesh, STEP via cadquery → small_step
**Date:** 2026-04-11  
**Status:** Partially superseded by ADR-007 (STEP path changed; STL path unchanged)

**Context:**  
Two different 3D export formats are needed: STL (for 3D printing) and STEP (for CAD import).

**Decision:**  
- **STL / 3D preview:** trimesh + shapely + manifold3d. Fast, no CAD kernel overhead, works in the browser via Three.js.
- **STEP:** cadquery (OpenCASCADE kernel). Produces proper B-rep solids that import cleanly into Fusion 360, SolidWorks, FreeCAD, etc.
- The 2D pulley profile geometry (`geometry/pulley_geometry.py`) is shared by both pipelines.

---

## ADR-004 — Desktop packaging: PyArmor + PyInstaller
**Date:** 2026-05-04
**Status:** Active

**Context:**
PulleyApp needs a distributable Windows desktop build that protects the source code and works offline.

**Options evaluated:**
| Option | Notes |
|---|---|
| PyArmor + PyInstaller | PyArmor obfuscates .py source; PyInstaller bundles into a folder + .exe. Proven combination. |
| Nuitka | Compiles Python to C; stronger protection but complex build, slower compile |
| Cx_Freeze | Bundles without obfuscation; source readable |
| Ship source directly | No protection |

**Decision:**
PyArmor Pro (obfuscation) → PyInstaller `--onedir` (bundle). Produces a folder with a single launchable `PulleyApp.exe` suitable for taskbar pinning.

**Key constraints:**
- PyArmor Pro licence has 200 build device slots. **Build must run on the registered Windows dev machine only — never in CI/CD.** Each `docker run` consumes a slot permanently.
- `packaging/build_release.py` is the single local build script. Run it manually after testing.
- `sys._MEIPASS` used in `launcher.py` to resolve template/static paths inside the bundle.
- Logs redirected to `%APPDATA%\CheapCADTools\PulleyApp\logs` via `PULLEY_LOG_DIR` env var so they survive app updates.

---

## ADR-005 — Subscription licensing: licence.lic + Render provision server
**Date:** 2026-05-04
**Status:** Active

**Context:**
PulleyApp sold as a subscription via Autodesk App Store. Need to control access to the desktop build and handle expiry/renewal without per-customer machine binding complexity.

**Decision:**
- One `licence.lic` per year, no machine binding, generated locally with `packaging/prepare_release.py`.
- `--period 7` requires PyArmor's servers to confirm the licence is still valid every 7 days (customer needs internet access at least weekly).
- `--expired <date>` hard-stops the app on the expiry date regardless of internet connectivity.
- Provision server runs as additional routes on the existing Render Flask service — no separate service needed.
- `licence.lic` base64-encoded and stored as Render environment variable `PULLEY_LICENCE_B64`. Regenerate by running `prepare_release.py` and updating the env var whenever a new local release is built.
- Subscriber list in `logs/subscribers.json` on Render (persists via $1/month Disk add-on). Managed via `/api/subscribers/add` and `/api/subscribers/remove` with Bearer token auth.

**Expiry flow:**
1. Addin warns customer 30 days before `licence_expiry` date stored in `config.json`.
2. Renewal calls `/api/provision` → returns fresh `licence.lic` + new expiry date.
3. On cancellation: call `/api/subscribers/remove` → customer's next renewal attempt returns 403 → app hard-stops on existing licence expiry date.

**Entitlement verification (primary path, once App Store registration is complete):**
- Addin calls `GET https://apps.autodesk.com/webservices/checkentitlement?userid=<id>&appid=<appid>`
- Result cached for the Fusion session (one API call per launch)
- Server independently calls the same endpoint before issuing `licence.lic` (don't trust addin)
- `AUTODESK_APP_ID` env var on Render; `AUTODESK_APP_ID` constant in `PulleyWebApp.py`
- When `AUTODESK_APP_ID` is empty (pre-registration), falls through to `subscribers.json`

**`subscribers.json` fallback (beta / pre-registration):**
Managed via `/api/subscribers/add` and `/api/subscribers/remove` with Bearer token auth.
Remains useful for comped accounts (support, reviewers) after App Store registration.

---

## ADR-006 — Fusion 360 addin distribution
**Date:** 2026-05-04
**Status:** Active

**Context:**
Customers need a seamless path from Autodesk App Store purchase to running PulleyApp locally with downloads auto-importing into Fusion 360.

**Decision:**
Fusion 360 addin (`Fusion Addins/PulleyWebApp/`) handles three responsibilities:
1. **Open button** — detects local install; if missing, runs provision+install flow; if installed, launches app or opens browser.
2. **File watcher** — background thread polls `%APPDATA%\CheapCADTools\PulleyApp\downloads\` every 2 seconds; marshals new STEP/DXF files to the Fusion UI thread via custom event for auto-import.
3. **Shared config** — writes `fusion_watch_dir` to `%APPDATA%\CheapCADTools\config.json`; Flask server reads this to mirror downloads to the watch folder.

**TEST_MODE flag** (`TEST_MODE = True` at top of `PulleyWebApp.py`):
- Bypasses provision server entirely.
- Creates placeholder `PulleyApp.exe` and `licence.lic` files without downloading anything.
- Adds an Uninstall button that removes `%APPDATA%\CheapCADTools\PulleyApp\` and resets config.
- Opens dev server at `http://127.0.0.1:5154/` instead of launching the real exe.
- Set `TEST_MODE = False` before publishing to App Store.

---

## ADR-003 — Captured nut pocket shape
**Date:** 2026-04-11  
**Status:** Active

**Context:**  
Hub retention via captured hex nut requires a pocket that allows the nut to drop in from the top
and seats it so a radial set screw can thread into it.

**Decision:**  
Pocket cross-section (in the tangential–axial plane) is a **pentagon**:
- Rectangular upper section (full flat-to-flat width + 0.5 mm clearance) from hub top down to the lower hex corners — nut slides freely through this section.
- V-shaped lower section from the lower corners to a pointed tip — matches the hex nut's lower vertex and seats the nut axially.
- Pocket opens 1 mm above hub top face in the boolean subtraction to guarantee a clean open top.
- Hub height is auto-raised if shorter than the pocket depth (2 × circumradius of clearance hex).
- Hub grows an oblong lobe if OD is too narrow for 2× nut-thickness wall material.
