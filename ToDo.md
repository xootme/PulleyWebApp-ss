# ToDo — Timing Pulley Generator

## Completed
- [x] SVG download — Pulley 1 & Pulley 2
- [x] Belt SVG download (single and dual-pulley)
- [x] DXF download — Pulley 1 & Pulley 2 (ezdxf, proper arc entities)
- [x] DXF download for Belt (single tooth cross-section)
- [x] DXF download for dual-pulley belt drive layout
- [x] Download popup menus (SVG / DXF / STL choice per button)
- [x] Help docs — Pulley 1, Pulley 2, Two Pulley Drive (with labelled SVG diagram)
- [x] STL export — single pulley (trimesh + manifold3d, watertight, bore subtracted)
- [x] STL export — dual-pulley drive (both pulleys + belt, phase-aligned to belt teeth)
- [x] Interactive 3D preview — Three.js WebGL viewer (orbit / zoom / pan)
- [x] Dual-pulley 3D preview — pulleys rendered blue (P1) and red (P2), two-material scene
- [x] Hub boss — cylindrical boss on top of pulley body with bore subtracted through full height
- [x] Hub retention — radial set-screw holes (standard) or captured hex nut pockets (pentagon profile, open top)
- [x] Hub settings persisted in localStorage across page reloads
- [x] STEP export — cadquery B-rep solid via Python 3.12 subprocess; includes all hub features

---

## In Progress

### 3D Export — Advanced Features
Enhanced pulley solid with:
- [ ] Spoke count and style
- [ ] Shaft keying (key slot dimensions)
- [ ] Flange options (diameter, thickness)

---

## Backlog

### Packaging
- [ ] PyArmor — machine-specific licence install
- [ ] PyArmor — time-expiring licence install

### Infrastructure
- [x] Add code to private GitHub repo
- [x] Evaluate cloud offload for geometry calculations (AWS Lambda / similar)
