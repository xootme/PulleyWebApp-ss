# PulleyWebApp — Claude Code Project Instructions

## Python Version
**Use Python 3.12.** The project venv must be Python 3.12.
- cadquery requires Python ≤3.12 (OCP wheels do not exist for 3.13+)
- build123d and pythonocc-core have the same constraint
- Python 3.14 was tested and confirmed incompatible with all STEP export libraries

## STEP Export
STEP files are generated using **cadquery** in `.venv312` (Python 3.12).
The `/download/step` route in `app.py` always shells out to `.venv312\Scripts\python.exe`
via `exporters/step_worker.py` — Flask itself can run on any Python version.
Do not move cadquery imports into the main Flask process.

## STL / 3D Preview
STL generation uses **trimesh + shapely + manifold3d** (no cadquery dependency).
The 3D preview in the browser uses Three.js loading STL over HTTP.

## Deploy Procedure
Always follow `web_provisioning.md` Steps 1–3 before pushing.
Never `git push` unless the user explicitly says "deploy".

## Key Files
- `app.py` — Flask routes; provision API routes (`/api/provision`, `/api/subscribers/*`) are at the bottom
- `exporters/step_exporter.py` — STL generation (trimesh pipeline) and STEP (cadquery)
- `templates/index.html` — all UI, JS, and Three.js viewer
- `static/style.css` — all styles; bump `?v=N` on the CSS link in index.html when changing
- `geometry/pulley_geometry.py` — 2D profile math shared by SVG, DXF, STL, and STEP
- `static/*_help.html` — split help files: one 2D and one 3D per panel
- `web_provisioning.md` — deploy checklist and local release build procedure
- `DECISIONS.md` — architectural decision log

## Desktop Packaging & Distribution
- `packaging/build_release.py` — local-only build: PyArmor obfuscation → PyInstaller → zip
- `packaging/prepare_release.py` — generates licence.lic and prints Render env vars to set
- `packaging/launcher.py` — PyInstaller entry point (sets `PULLEY_BASE_DIR`, opens browser)
- `packaging/PulleyApp.spec` — PyInstaller spec
- `packaging/generate_customer_licence.py` — one-off machine-bound licence for a specific customer

**PyArmor rule:** Never run `build_release.py` or any `pyarmor gen` command in CI/CD.
Each `docker run` consumes a device slot. Always build on the registered Windows dev machine.
PyArmor Pro licence: `C:\Users\cmyer\Documents\PayArmor\pyarmor-regfile-11621.zip` (200 slots max, dev machine only).

## Fusion 360 Addin
- Lives at `C:\Users\cmyer\Documents\Fusion Addins\PulleyWebApp\`
- `PulleyWebApp.py` — main addin; `TEST_MODE = True` at the top bypasses provision server
- In TEST_MODE: creates placeholder install files, adds Uninstall button, opens dev server at port 5154
- Set `TEST_MODE = False` before shipping

## Subscription / Licence Model
- Subscriptions sold via Autodesk App Store (0% commission currently)
- Provision server runs on Render ($7/month starter, always-on)
- Annual `licence.lic` generated locally with `prepare_release.py` → base64 stored as Render env var `PULLEY_LICENCE_B64`
- Entitlement verified via Autodesk API: `GET https://apps.autodesk.com/webservices/checkentitlement?userid=&appid=` — no auth required, returns `{"IsValid": true/false}`
- `AUTODESK_APP_ID` env var on Render + constant in addin `PulleyWebApp.py` — set after App Store registration; leave empty to fall back to `subscribers.json`
- `PROVISION_SECRET` env var on Render guards `/api/subscribers/add` and `/api/subscribers/remove` (beta/comped accounts)
- Subscriber list in `logs/subscribers.json` on Render (persists on $1/month disk add-on)
- On expiry: PyArmor blocks app launch; addin shows renewal prompt 30 days before expiry date
