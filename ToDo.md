# ToDo — Timing Pulley Generator

## Backlog

### Tests needed — post-deploy gaps (identified 2026-06-23) — ✅ DONE (2026-06-24)

All three regression-coverage gaps now have tests (added 2026-06-24, before the socket-void merge deploy):
- #1 → `tests/test_3d.py::TestSpokeRimStlTopologyFix` (spoke+rim STL, download + preview, min/mid/high tooth counts × 5 families)
- #2 → `tests/test_addin_helpers.py` (AddinDownloader: success, machine_id in payload, 429 limit, HTTP/network errors)
- #3 → `tests/test_flange.py::TestFlangeNubClipping` (nub pins clipped at the spoke rim boundary, do not intrude into the void)

Original specs retained below for reference.

#### 1. TopologyException regression — spokes + rim STL (`step_exporter.py`)
**Commits:** `85585bd`, `6bd2a31` — added `buffer(0)` to clean near-coincident tooth vertices before Shapely boolean ops in `generate_pulley_stl` and `generate_pulley_stl_preview`.

**What to test:**
- `generate_pulley_stl` with spokes enabled (`spoke_count≥2`) **and** `rim_depth_mm > 0` — must not raise `TopologyException` or any exception
- `generate_pulley_stl_preview` with same combo (preview path was fixed separately in `6bd2a31`)
- Cover at least: HTD-5M, GT-3M, T-T5, Imperial-XL at min_teeth and a mid-range tooth count (e.g. 36T)
- High tooth counts (60T+) are most likely to produce near-coincident vertices — include at least one
- Verify returned bytes are valid STL (header `b'...'` first 80 bytes, then triangle count > 0)

**Trigger params:** `spoke_count=4, spoke_width_mm=4.0, spoke_hub_od_mm=14.0, rim_depth_mm=2.0`

**File to add tests to:** `tests/test_3d.py` (new class `TestSpokeRimStlTopologyFix`)

---

#### 2. `addin_helpers.AddinDownloader` — zero tests (new module `exporters/addin_helpers.py`)
**Commit:** `7d5de4a`

**Public surface to test:**
- `AddinDownloader(base_url, machine_id, timeout)` constructor
- `.download_step(params)` → bytes on HTTP 200
- `.download_dxf(params)` → bytes on HTTP 200
- `.download_stl(params)` → bytes on HTTP 200
- `DownloadLimitExceeded` raised on HTTP 429; `e.count` and `e.limit` set from response JSON `{"count": N, "limit": M}`
- `DownloadError` raised on any other non-200 status (e.g. 500)
- `DownloadError` raised on `urllib.error.URLError` (network failure / timeout)
- `machine_id` appears in every outgoing request URL

**How to mock:** `unittest.mock.patch('urllib.request.urlopen')` — return a `MagicMock` with `.read()` returning bytes and `.status` / `.getcode()` returning 200; for error cases raise `urllib.error.HTTPError`.

**File:** new `tests/test_addin_helpers.py`

---

#### 3. Flange nub clipping — clipping order reversed (`exporters/flange_exporter.py`)
**Commits:** multiple around `fix nub clipping` — old code clipped at hub-inner first then hub-outer; new code always clips at `r_spoke_outer = (R_OD - tooth_ht) - rim_depth` first, then conditionally clips at `r_spoke_inner` only if `r_nub - r_pin <= r_spoke_inner`.

**What to test** (mesh inspection via trimesh):
- `generate_3dprint_flange_stl(... nub_count>0, spokes_enabled=True)` must not raise any exception
- No nub vertex extends radially past `r_spoke_outer` (rim boundary): all vertices with `z > 0` have `sqrt(x²+y²) <= r_spoke_outer + tolerance`
- When nubs are small (`r_nub - r_pin > r_spoke_inner`), no clipping at hub inner — vertices exist at `r ≈ r_spoke_outer`, not truncated further inward
- `build_flange_meshes(... nub_count>0, spokes_enabled=True)` (preview path uses same logic)

**Key values to compute in test:**
```python
from geometry.flange_geometry import _pulley_radii
R_OD, _, tooth_ht = _pulley_radii(family, pitch, teeth)
R_tr = R_OD - tooth_ht
r_spoke_outer = R_tr - rim_depth_mm   # nubs must not exceed this
```

**File:** `tests/test_flange.py` (new class `TestFlangeNubClipping`)

---

### Before Public Launch
- [ ] **Remove dev backdoor password** — delete `DEV_BACKDOOR_KEY` from `PulleyWebApp.py` (Fusion addin), the `backdoor_key == 'xoot'` block from `/api/provision` in `app.py`, and `_DEV_BACKDOOR` from `packaging/launcher.py`



### STEP Geometry — `C:\Users\cmyer\Documents\small_step\`
Rust project (no deps). Build: `cargo +stable-x86_64-pc-windows-gnu build`

**Done:**
- [x] Investigated STEP AP214 structure — learned entity syntax, unit declarations, TRIMMED_CURVE, MANIFOLD_SOLID_BREP
- [x] Confirmed pulleys can use CIRCLE + TRIMMED_CURVE for land/root arcs; LINE for flanks
- [x] Built Rust StepBuilder that emits all required AP214 entities
- [x] HTD-3M 20T geometry: profile math, wrap_point/wrap_arc, 9 segments per tooth
- [x] Non-manifold topology fixed: coordinate→vertex_id cache in StepBuilder; adjacent faces reuse same edge entities
- [x] Top/bottom cap faces closed: winding/direction correct; bore loop ≥ 3 arcs (OCCT drops self-referencing 1- or 2-arc bore)
- [x] Arc encoding: CW arcs use ascending TRIMMED_CURVE + same_sense=.F. in EDGE_CURVE (OCCT/FreeCAD/Fusion/eDrawings all agree)
- [x] Spoke void geometry: full-height spoke webs (partial-height webs later found OCCT-invalid — see Known issues below)
- [x] Flange generation: 3D-print and metal flanges as named solids (PRODUCT hierarchy per solid, June 11)
- [x] Glue-nub system: pins + sockets, clipped crescent merge, B2 socket↔void merge, June 13–16
- [x] **B2 socket-over-void fix (June 16)**: when any socket center is over a void window, fall back to standard crescent path; wrong-geometry artifacts resolved for 30-nub/50-nub configs
- [x] **Generalized socket↔void merge — ribbon eliminated (June 24)**: replaced the two-face "trench" merge with `build_merged_socket_void_solid_v2` + `trace_cap_loops` — a single top cap whose holes are the merged outlines of every void unioned with overlapping socket crescents. Handles filleted spokes generically (the old `extract_void_info` bailed on fillets, which is why the ~0.224 mm ribbon was always present on filleted-spoke pulleys), sockets over webs/voids/straddling edges, and full-circle sockets. Falls back to the crescent path on any inconsistency (`SMALL_STEP_DIAG` reports `merge: v2 applied|fallback`). Validated OCCT-valid across 8 configs + confirmed ribbon-free in Fusion/FreeCAD/eDrawings. Regression test: `tests/test_nub_socket_merge.py`.
- [x] `run_ss_dev.bat` updated to use release binary (was pointing to stale debug build)
- [x] `require_active_session` now respects `PULLEY_TESTING=1` env var for dev mode

**Known issues — small_step:**
- [ ] FreeCAD rejects complex pulley profiles (missing SURFACE_CURVE/PCURVE) — eDrawings and Fusion work fine
- [ ] Merged-pin path regresses curved-tooth 50T flange samples (pre-existing; separate-solid fallback still committed)
- [x] ~~B2 socket↔void ribbon~~ — RESOLVED June 24 by the generalized merge (see Done above)

- [ ] **BUG — partial-height spokes produce an OCCT-invalid solid** (`build_partial_height_solid`). Found June 24; **pre-existing** (reproduced on the pre-merge binary, so unrelated to the June 24 merge work). Currently guarded: partial-height spoke STEP is blocked in `app.py::_run_ss_worker` with a user-facing message — **remove the guard when this is fixed.**

  **Symptom:** any pulley with `spoke_height_mm > 0` and `< belt_height` (recessed/partial-height spoke web) generates a STEP whose pulley solid is `BRepCheck`-invalid. Opens but is non-watertight; unreliable for booleans/printing.

  **ROOT CAUSE (fully located June 24):** OCCT reports `BRepCheck_UnorientableShape` on the **10 spoke-island cap faces** (web caps at z1, z2 — 5 spokes × 2). The shell itself is `NoError` and every edge is shared exactly twice with opposite ORIENTED_EDGE sense (topology is manifold). The real defect is **per-face**: `ShapeAnalysis_Wire::CheckSelfIntersection` returns true on each island cap wire. The flagged pairs are the **hub arc edge and its two neighbouring hub-fillet arcs** (`fillet_base`): they meet **tangent (G1)** at the shared vertex, and OCCT's tolerance-based wire self-intersection check treats two tangent adjacent curved edges as intersecting. The rim side does *not* trip it — the rim arc (r≈56 mm) is nearly straight, so no sliver; only the tightly-curved hub arc (r≈hub_r) + fillet forms a sliver within tolerance.

  **Decisive evidence:**
  - Non-filleted partial-height (`fillet_tip=fillet_base=0`) is **OCCT-valid** (165 faces, 0 invalid). Fillets are the trigger.
  - Full-height filleted (`spoke_height=0`) is valid — the *same* fillet/arc tangency lives in the void **hole** loop there but is wound the opposite way (concave fillet → clean "S" junction), so it isn't flagged.
  - Dense geometric sampling of the island wire shows it closed, simple, correct winding (area +190 top / −190 bottom). The "self-intersection" is purely OCCT's tangent-tolerance false positive, but FreeCAD/eDrawings/Fusion all use OCCT so they all reject it.
  - Scratchpad probes used: `occ_status.py` (per-face `BRepCheck_Status`), `occ_diag.py` (`ShapeAnalysis_Wire` checks), `occ_selfint.py` (`CheckIntersectingEdges(i,j)` → flags adjacent hub-arc/fillet pairs).

  **Tried (did NOT fix):** swapping `is_top` flags — flips normal + winding together, face stays self-consistent, no change (expected).

  **FIXED (June 25) for NARROW spokes — `split_island_hub_arc`:** the tangent
  hub-arc↔fillet junction is broken by laying a straight **chord across the
  hub-arc run** of each web cap. The hub arc moves onto its own thin
  circular-segment sub-face (hub arc + chord = a secant, not tangent); the main
  web cap gets a flat hub edge (chord meets the fillets at an angle). No wire
  contains a tangent arc-arc pair, so OCCT accepts it. **Fillets are kept.**
  Validated OCCT-valid across 4/5/6/8 spokes + the merge test
  `partial_height_d15` (filleted, flange + nubs). _(Tried and rejected first:
  zeroing fillets — it breaks wide spokes, which need the fillet arc for
  `inject_synthetic_hub_arc`; see git history.)_

  **Nub sockets — DONE (June 25):** narrow filleted partial-height with fused nub
  sockets at ANY depth is OCCT-valid. Built via a unified per-cell rim wall
  (z0..z3) + socket-floor z-level splits threaded through the web layer (flanks,
  fillets, hub wall). Validated nub heights 0.5..9 mm on a 10 mm pulley (floor
  above, in, and below the web). Tests: `tests/test_nub_socket_merge.py`
  `partial_height_d15` / `_deep` / `_plain`.

  **STILL BROKEN — WIDE spokes (base fillet does not reach the hub):** root cause
  now clear. When the `fillet_base` doesn't reach `hub_r`, the void's real inner
  boundary is where adjacent fillets meet (above `hub_r`), and the hub region
  below that is a **solid collar** — so the web is a CONNECTED annulus, not
  separate spoke islands. `inject_synthetic_hub_arc` fakes a hub arc + radial
  `line_a`/`line_b` down to `hub_r`, which places **walls inside that solid
  collar**. The shell closes (0 dangling) but those interior walls make it
  globally **non-orientable** (OCCT `UnorientableShape` on the islands;
  `ShapeFix_Shell` reverses the whole wall region). Guarded with a clear error.
  **Fix:** drop the synthetic injection; treat the void at its true inner outline
  (no hub arc) and build the web cap as ONE connected annulus (trace the outer
  loop = spoke rim arcs + void detours, hub circle inner; no z1..z2 hub wall).
  Reuses the `trace_cap_loops` engine. Repro: P2 HTD-8M-75T, hub_od 15,
  rim_depth 10, 11 spokes.

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
