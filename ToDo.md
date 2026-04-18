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
- [x] Spoke count and style (radial spokes with tip/base fillets, spoke height, rim ring depth)
- [ ] Shaft keying (key slot dimensions)
- [x] Flange options — 3D-print and metal, top/bottom, gluing nubs with socket pockets, inner rim rule

---

## Backlog

### Packaging
- [ ] PyArmor — machine-specific licence install
- [ ] PyArmor — time-expiring licence install

### Infrastructure
- [x] Add code to private GitHub repo
- [x] Evaluate cloud offload for geometry calculations (AWS Lambda / similar)

### Agent / Headless API Access
Enable programmatic access without a UI so AI agents or scripts can discover and call capabilities.
This same API is shared by CAD plugins (embedded UI, direct download, or agent-mediated) — build once, serve all clients.

**Core API:**
- [ ] `GET /api/capabilities` — machine-readable list of families, pitches, output formats, hub/spoke feature flags
- [ ] `GET /api/describe?family=X&pitch=Y` — parameter constraints (min/max teeth, bore range, enums) and download URL template
- [ ] Consistent JSON error responses (`{"error": "..."}`) on all 400 routes (currently plain text)
- [ ] OpenAPI 3.0 spec at `/api/openapi.json` (hand-written or via flask-smorest) for agent auto-discovery
- [ ] (Optional) MCP server wrapper — exposes each capability as a typed Claude tool with structured geometry responses

**CAD Plugin prerequisites (shared with agent API):**
- [ ] CORS headers on all `/api/` and `/download/` routes — required for browser-context plugins (Onshape, Fusion web panel)
- [ ] `X-Frame-Options: ALLOWALL` (or `Content-Security-Policy: frame-ancestors *`) — required for embedded UI mode (webview/iframe in CAD task pane)
- [ ] API versioning (`/api/v1/`) — plugins ship and rarely update; versioned routes prevent breakage when parameters evolve
- [ ] Freeze public parameter names (`bore`, `teeth`, etc.) — plugin code bakes these in

**CAD platform integration notes:**
- **Fusion 360** — addin already exists (`Fusion Addins/`); add web panel for embedded UI + `adsk.core.WebRequestEvent` for direct download
- **Onshape** — custom tab embeds URL directly; FeatureScript can make HTTP calls for direct-download mode
- **SolidWorks** — task pane hosts a browser control for UI; .NET `HttpClient` for direct calls
- **AutoCAD** — `ShowBrowserWindow` for UI mode; AutoLISP / .NET for direct API calls

**Three plugin integration modes (all share the same server API):**
1. **Embedded UI** — iframe/webview of `cheapcadtools.com/tools/pulleys`; works today with zero server changes once CORS/iframe headers are added
2. **Direct download** — plugin reads CAD context (bore, pitch, etc.), calls `/download/step|dxf|stl`, imports file without user touching a browser
3. **Agent-mediated** — CAD-native AI (Onshape AI, Fusion Copilot, etc.) reads OpenAPI spec, calls endpoints on user's behalf
