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
- `app.py` — Flask routes
- `exporters/step_exporter.py` — STL generation (trimesh pipeline) and STEP (cadquery)
- `templates/index.html` — all UI, JS, and Three.js viewer
- `static/style.css` — all styles; bump `?v=N` on the CSS link in index.html when changing
- `geometry/pulley_geometry.py` — 2D profile math shared by SVG, DXF, STL, and STEP
- `static/*_help.html` — split help files: one 2D and one 3D per panel
- `web_provisioning.md` — deploy checklist
- `DECISIONS.md` — architectural decision log
