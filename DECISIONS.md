# Architectural Decision Records

## ADR-001 — Python 3.12 for STEP export
**Date:** 2026-04-11  
**Status:** Active

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

## ADR-002 — STL via trimesh, STEP via cadquery
**Date:** 2026-04-11  
**Status:** Active

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

## ADR-005 — Subscription licensing: annual licence.lic + Render provision server
**Date:** 2026-05-04
**Status:** Active

**Context:**
PulleyApp sold as a subscription via Autodesk App Store. Need to control access to the desktop build and handle expiry/renewal without per-customer machine binding complexity.

**Decision:**
- One `licence.lic` per year, no machine binding, generated locally with `packaging/prepare_release.py`.
- `--period 7` requires PyArmor's servers to confirm the licence is still valid every 7 days (customer needs internet access at least weekly).
- `--expired <date>` hard-stops the app on the expiry date regardless of internet connectivity.
- Provision server runs as additional routes on the existing Render Flask service — no separate service needed.
- `licence.lic` base64-encoded and stored as Render environment variable `PULLEY_LICENCE_B64`. Rotate annually by running `prepare_release.py` and updating the env var.
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
