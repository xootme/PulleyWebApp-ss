# ToDo — Sketch Timing Pulley Generator

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

---

## Backlog

### Packaging
- [ ] PyArmor — machine-specific licence install
- [ ] PyArmor — time-expiring licence install

### Infrastructure
- [ ] Add code to private GitHub repo
- [ ] Evaluate cloud offload for geometry calculations (AWS Lambda / similar)
