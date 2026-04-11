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
