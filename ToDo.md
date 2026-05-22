# ToDo — Timing Pulley Generator

## Backlog

### Before Public Launch
- [ ] **Remove dev backdoor password** — delete `DEV_BACKDOOR_KEY` from `PulleyWebApp.py` (Fusion addin), the `backdoor_key == 'xoot'` block from `/api/provision` in `app.py`, and `_DEV_BACKDOOR` from `packaging/launcher.py`



### STEP Geometry — `C:\Users\cmyer\Documents\small_step\`
Rust project (no deps). Build: `cargo +stable-x86_64-pc-windows-gnu build`

**Done:**
- [x] Investigated STEP AP214 structure — learned entity syntax, unit declarations, TRIMMED_CURVE, MANIFOLD_SOLID_BREP
- [x] Confirmed pulleys can use CIRCLE + TRIMMED_CURVE for land/root arcs; LINE for flanks
- [x] Built Rust StepBuilder that emits all required AP214 entities
- [x] HTD-3M 20T geometry: profile math, wrap_point/wrap_arc, 9 segments per tooth
- [x] Generates `htd_3m_20t.step` (~507 KB); loads in eDrawings (SolidWorks viewer)

**Known issues — what to fix next:**
- [ ] **Non-manifold topology**: each face creates fresh VERTEX_POINT/EDGE_CURVE entities even at shared corners — adjacent faces must reuse the same edge entities. Needs a coordinate→vertex_id cache in StepBuilder.
- [ ] **Top/bottom faces not closed**: outer profile edge loop has direction/winding issues; top and bottom annular faces (with bore hole) don't seal the solid
- [ ] **Bore appears solid**: bore cylindrical face winding is reversed — bore should cut inward, not fill
- [ ] **Teeth look triangular**: the arc wrapping for tip/root fillets produces incorrect angles after rotation; the 9-primitive profile geometry needs debugging against a known-good reference profile
- [ ] **Unit crash**: `SI_UNIT(.MILLI.,.METRE.)` (correct format) causes OCC crash on TransferRoots due to non-manifold topology failing validation at mm scale; old 3-arg format gives 1000× wrong scale. Fix is the manifold topology issue above.
- [ ] Consider outputting SHELL_BASED_SURFACE_MODEL instead of MANIFOLD_SOLID_BREP until topology is clean
- [ ] Consider adding SURFACE_CURVE + PCURVE on each edge for full AP214 compliance (required for SolidWorks/Fusion direct import without repair)

### Design Metadata in Exported Files
- [x] Embed CCT params as JSON in STEP (`/* CCT:{...} */` comment after HEADER)
- [x] Embed CCT params as JSON in DXF (group-code 999 comment before EOF)
- [x] Embed CCT params as JSON in SVG (`<metadata>` element)
- [x] Web app: restore design from URL query params (`loadParamsFromUrl`)
- [x] Web app: Import button (2D toolbar) — file picker reads embedded metadata client-side, restores all fields
- [x] Schema versioning (`sv` field) in all three formats; `migrateParams()` in web app for future param renames
- [x] Fusion addin: sidebar palette replaces separate toolbar button; "Restore from File" in sidebar
- [x] Test harness: `tests/test_repro.py` — unit, download-route, and round-trip tests for all three formats
- [x] **STL: embed CCT params by appending trailer after last triangle** (binary STL ignores
  trailing bytes; read back with same `/* CCT:{...} */` regex as STEP)

### Packaging
- [ ] PyArmor — machine-specific licence install
- [ ] PyArmor — time-expiring licence install

### Load Testing Dashboard
- [ ] High-load simulation program with real-time dashboard
  - Adjustable number of simulated concurrent users
  - Per-user behaviour controls: download frequency, 2D parameter changes, 3D parameter changes, belt/hub/spoke modifications
  - Real-time display showing each simulated user's current action (e.g. "User 3 — downloading STL with spokes")
  - Summary metrics: requests/sec, error rate, p95 latency, per-endpoint breakdown
  - Designed to surface the same serialisation and OOM issues as the concurrency test harness but under sustained, realistic mixed-workload conditions


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
- **FreeCAD** — Python-based plugin API supports Macros (simple script calls `/download/step` or `/download/dxf` and imports result) and full Workbenches (custom UI panel, toolbar buttons); fits alongside Fusion addin in repo
- **Onshape** — custom tab embeds URL directly; FeatureScript can make HTTP calls for direct-download mode
- **SolidWorks** — task pane hosts a browser control for UI; .NET `HttpClient` for direct calls
- **AutoCAD** — `ShowBrowserWindow` for UI mode; AutoLISP / .NET for direct API calls

**Three plugin integration modes (all share the same server API):**
1. **Embedded UI** — iframe/webview of `cheapcadtools.com/tools/pulleys`; works today with zero server changes once CORS/iframe headers are added
2. **Direct download** — plugin reads CAD context (bore, pitch, etc.), calls `/download/step|dxf|stl`, imports file without user touching a browser
3. **Agent-mediated** — CAD-native AI (Onshape AI, Fusion Copilot, etc.) reads OpenAPI spec, calls endpoints on user's behalf

---

## CAD Plugin Roadmap (excluding Fusion 360 and OnShape)

### SolidWorks — Listener App
**Approach:** Standalone Windows tray app (no COM add-in registration needed). Same file-watch pattern as the Fusion addin.

**Status:** Initial implementation at `solidworks_listener/listener.py`

**Architecture:**
- On startup: writes `solidworks_connected=true` + `solidworks_watch_dir=<tmp>` to `%APPDATA%\CheapCADTools\config.json`
- `watchdog` `Observer` monitors the temp folder for `.step`/`.stp`/`.dxf` files
- On new file: `win32com.client.GetActiveObject("SldWorks.Application")` → `OpenDoc6()` imports into the running SolidWorks session; no registration required
- On clean exit: clears `solidworks_connected` so Flask stops mirroring files
- Flask side: `_mirror_to_solidworks()` in `app.py` mirrors all STEP/DXF downloads when connected (same pattern as `_mirror_to_fusion`)
- Tray menu: Open Web App / Exit; icon drawn in code (no asset file needed)

**Pending:**
- [ ] Test with an actual SolidWorks installation — verify `OpenDoc6` param types (byref VARIANTs in pywin32)
- [ ] Handle the case where SolidWorks launches *after* the listener is started (currently requires SW to be open first)
- [ ] Optionally auto-launch SolidWorks if not running (use `win32com.client.Dispatch("SldWorks.Application")`)
- [ ] Add to packaging pipeline — separate PyInstaller build, `windows=True` (no console); PyArmor optional for this helper
- [ ] Distribution: bundle as `CheapCADTools-SolidWorks-Listener-setup.exe` using a lightweight installer (NSIS or Inno Setup), or just a zip
- [ ] Startup shortcut / auto-start entry in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- [ ] App Store / distribution channel: direct download from `cheapcadtools.com`; no Autodesk/SolidWorks marketplace needed for a standalone exe

**Dependencies (`solidworks_listener/requirements.txt`):** `pystray`, `Pillow`, `watchdog`, `pywin32`

---

### FreeCAD — Macro / Workbench
**Approach:** Python-native. A simple Macro is the fastest path; a full Workbench gives a persistent toolbar/panel.

**Architecture options:**
- **Macro (simplest):** Python script the user runs from FreeCAD's Macro menu. Calls `/download/step` with current params → `FreeCAD.ActiveDocument.importFile(tmp_path)`. No installation.
- **Workbench (full):** Python package registered in `~/.FreeCAD/Mod/`. Adds a sidebar panel (Qt `QDockWidget`) or toolbar buttons. Opens the CCT web app in a `QWebEngineView` embedded in the panel (same thin-launcher pattern as Fusion/OnShape).

**Pending:**
- [ ] Decide: Macro only, or full Workbench?
- [ ] Macro path: write `cct_pulleys.FCMacro` — prompts for params (or reads from open document units), calls Flask download route, imports result
- [ ] Workbench path: create `~/.FreeCAD/Mod/CCTPulleys/` package; `InitGui.py` registers workbench; panel hosts `QWebEngineView` pointed at CCT web app
- [ ] File-watch import pattern: FreeCAD Python has `FreeCAD.ActiveDocument.importFile(path)` — no COM needed, works cross-platform
- [ ] Distribution: FreeCAD Addon Manager (GitHub repo with `package.xml` metadata); no marketplace fees

---

### AutoCAD — Listener App or .NET Plugin
**Approach:** Two viable options, same trade-off as SolidWorks.

**Option A — Listener App (simplest, same pattern as SolidWorks):**
- Standalone tray app (Python or C#) watches a folder
- Imports via AutoCAD COM automation: `AutoCAD.Application` → `ActiveDocument.SendCommand("_IMPORT\n" + path + "\n")` or `Import()` via the AutoCAD API object model
- No AutoCAD plugin registration; works with any AutoCAD version that exposes COM

**Option B — ObjectARX / .NET Plugin:**
- Full AutoCAD add-in: C# .NET class library loaded via `NETLOAD` command
- `PaletteSet` hosts a `WebBrowser` / `WebView2` control showing the CCT web app (same thin-launcher pattern)
- `Application.DocumentManager.MdiActiveDocument.SendStringToExecute()` for command execution
- Registration: user runs `NETLOAD` once, or add to `acad.lsp` startup

**Pending:**
- [ ] Decide: listener app or .NET plugin?
- [ ] Listener path: extend `solidworks_listener/` pattern — add AutoCAD COM variant; `win32com.client.GetActiveObject("AutoCAD.Application")` → `SendCommand` or `Import`
- [ ] .NET path: scaffold a C# AutoCAD plugin project; host WebView2 in a `PaletteSet`; mirror Fusion addin's file-watch approach using `System.IO.FileSystemWatcher`
- [ ] Distribution: Autodesk App Store (same store as Fusion — unified account); or direct download

---

### Other Platforms (research only)
- **CATIA / Dassault** — CAA C++ SDK required; very heavyweight; not worth pursuing for v1
- **Inventor** — Autodesk; similar COM automation pattern to AutoCAD; could reuse `solidworks_listener` approach
- **Rhino / Grasshopper** — Python scripting via RhinoCommon; Grasshopper component could call `/download/step` and import; low effort relative to install base
- **NX (Siemens)** — NXOpen API (C++ or Python); niche; skip for now
