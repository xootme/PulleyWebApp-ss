# ToDo — Timing Pulley Generator

## Completed
- [x] SVG download — Pulley 1 & Pulley 2
- [x] Belt SVG download (single and dual-pulley)
- [x] DXF download — Pulley 1 & Pulley 2 (ezdxf, proper arc entities)
- [x] DXF download for Belt (single tooth cross-section)
- [x] DXF download for dual-pulley belt drive layout
- [x] Download popup menus (SVG / DXF choice per button)
- [x] Help docs — Pulley 1, Pulley 2, Two Pulley Drive (with labelled SVG diagram)

---

## In Progress

### 3D Export UI
New panel / tab for generating 3D-ready profiles with the following options:
- [ ] Extrusion depth (mm)
- [ ] Spoke count and style
- [ ] Hub diameter and length
- [ ] Shaft keying (key slot dimensions)
- [ ] Flange options (diameter, thickness)

### 3D File Formats
- [ ] STEP (.step) export
- [ ] STL (.stl) export

### 3D Geometry Library Options
Evaluated options for multi-level extrusions (pulley body + flange + hub + bore cutout + spokes):

| Option | Library | Output | Notes |
|---|---|---|---|
| **A — CadQuery** | `cadquery` | STEP + STL | True B-rep, exact arc geometry, heavy (~200 MB) |
| **B — Build123d** | `build123d` | STEP + STL | Modern CadQuery successor, cleaner API, same OpenCASCADE backend |
| **C — numpy-stl** | `numpy-stl` | STL only | Lightweight, faceted OD lands, fine for FDM printing |

**Recommended:** CadQuery or Build123d — `wrap_groove_to_pulley()` polygon drops straight in as a wire; each hub/flange/spoke parameter is one `.extrude()` or `.cut()` call; single export produces both STEP and STL.

Layered features supported: hub, flange, keyway, set screw hole, spokes, timing mark — all standard B-rep operations on the base solid.

### 3D Visualization Options
Options for showing a 3D preview in the browser:

| Option | Approach | Pros | Cons |
|---|---|---|---|
| **A — Three.js** | Client renders STL via WebGL | Fast, interactive (orbit/zoom/pan), no server load | ~600 KB JS dependency |
| **B — model-viewer** | `<model-viewer>` web component renders GLB | One HTML tag, AR support, mobile friendly | Server GLB conversion via `trimesh` |
| **C — trimesh PNG** | Server renders isometric PNG | Zero new frontend code, fits existing preview pattern | Static image, no interaction |
| **D — OpenSCAD** | Generate `.scad` file for download | Zero server rendering, fully parametric | Requires OpenSCAD installed locally |

**Recommended:** Two-tier — trimesh isometric PNG for quick preview (fits existing preview panel pattern) + Three.js STL viewer as a third **3D** tab alongside existing Pulley / Belt tabs. Three.js loaded only when 3D tab is active.

---

## Backlog

### Packaging
- [ ] PyArmor — machine-specific licence install
- [ ] PyArmor — time-expiring licence install

### Infrastructure
- [x] Add code to private GitHub repo
- [x] Evaluate cloud offload for geometry calculations (AWS Lambda / similar)
