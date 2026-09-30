# Architectural Decision Records

## ADR-018 — Hex bar bores, metric and inch, with their retaining rings (cct_common 0.21.0)
**Date:** 2026-09-29
**Status:** Active (2D, STL, cadquery STEP); small_step STEP pending (as splines, ADR-014)

**Context:** A hex bar carries the torque on its own, prints far more easily than a spline and is
forgiving of fit; FRC robots use 1/2" hex with ordinary 1/2" round-shaft retaining rings, the groove
turned round inside the flats. The user asked for it beside the splines, metric and inch.

**Decision:**
- **A third spline type, Hex bar** (`cct_common.splines.hex_bar(af, ac, series)`), so it has
  everything a spline has: the rings and counterbores, the washer, the sample shaft (a grooved hex
  bar), the Spline card, the 3D view, SVG/DXF/STEP. A flat faces +X; lines and arcs only.
- **Sizes**: metric stock 5-32 mm and inch 1/4"-1" across flats as presets, any AF typed (to 80 mm);
  **rounded hex** optional, its across-corners circle typed or from a preset with a published figure
  (REV 1/2" rounded hex, 13.75 mm, docs.revrobotics.com). Sharp corners are the default.
- **Fit: H11 hole on an h11 bar**, across flats and across corners, each at its zone's middle —
  EN 10278 gives bright hexagon bar h11 up to 80 mm (h12 above); no standard covers the hole.
- **Rings**: the round-shaft ring for the across-flats size, else the largest under it — DIN 471 for
  metric, the **inch SH series** (Rotor Clip SH = Truarc 5100; new in `retaining_rings`, from Rotor
  Clip's catalogue pp. 20-21 and SH data sheet, 1/4"-1-1/2") for inch (`for_spline` picks by kind and
  series). SH-50 on 1/2" hex matches FRC practice (groove .468", width .039"). The counterbore is the
  ring's released clearance diameter (L2), groove-width deep, as DIN's d4.
- **Every Retention method** (unlike a spline, ADR-017), one or two screws, each on a flat: the
  first facing +X (on the bore's radius, placed as on a round bore at 0°, a captured nut against
  the flat); a second 180° on for captured nuts, as on a round bore, and 120° on for threaded or
  insert screws (a round bore's 90° is a hex's corner; 120° keeps them unopposed). First version:
  one screw only — too strict, the owner asked why two captured nuts weren't allowed.
  Rings and screws can go together.
- `Spline` gains an optional `series` field (default ""): old dicts and code keep working.

**Consequences:** cct_common `tests/test_splines.py` / `test_retaining_rings.py` (hex fit, outline,
labels; the SH table, rings for hex); here `tests/test_spline_retainer.py` hex cases and
`tests/browser/bore_ui.js`. Help picture `spline_hex.svg`. The fuzzer draws hex bars too.

## ADR-017 — A splined bore's fit, sample shaft and retaining ring (cct_common 0.20.0)
**Date:** 2026-09-29
**Status:** Active (2D, STL, cadquery STEP); small_step STEP pending (it refuses splines, ADR-014)

**Context:** ADR-014 cut a spline's nominal outline. A printed pulley on a splined shaft also needs the
fit (how much looser the hole is than the shaft), something that holds it on the shaft axially, and a
shaft to try it on. The sources are in Sprocket's `Pdf/`: ISO 14:1982 (scanned; Table 2's fits), the
ISO 4156-1:2005 design worksheet, and DIN 471:2011 (external retaining rings), checked against
McMaster-Carr's DIN 471 rings (98541A…, 3–29 mm, all match).

**Decision:**
- **The standard's default fit only**, no choice: ISO 14 sliding (hole H9 / H10 / H7 on B / D / d,
  shaft d10 / a11 / f7, zones from ISO 286-2 — `cct_common.iso286`); ISO 4156 H/h, tolerance class 6,
  on the space / tooth width (the minor from the form diameter + 0.2 m, per the worksheet). Hole and
  shaft are each drawn at the **middle of their tolerance zone** (`splines.hole` / `splines.shaft`;
  `path` is now the fitted hole, `shaft_path` the mating shaft). Bore Diameter is the fitted hole's
  minor (23.0105 for 6 × 23 × 26).
- **Print compensation on printed parts only** (STL): `print_extra` grows the bore and counterbore and
  shrinks the sample shaft and the washer's OD (`splines.printed`, a mitred offset); the shaft's groove
  widens by 2c and its floor comes in by c. SVG, DXF and STEP stay at the nominal fit.
- **Retention: DIN 471 rings** (`cct_common.retaining_rings`) on either or both faces — the smallest
  d1 ≥ the shaft's outside diameter; one ring holds the part one way, a ring on each face both ways.
  The **Spline card** (3D Mode, shown with a splined bore) sets them: `spline_ring_top` /
  `spline_ring_bottom` (= 1), default top; the older `spline_ring` = top / bottom / none (saved designs,
  links) still reads. It also has **Show sample shaft**: the 3D view draws the shaft grey, rings dark,
  washers blue, placed like the pulley (`/api/preview-stl?part=spline|spline1|spline2`, JSON of STLs).
  The sample shaft runs DIN's n past each ringed face (its groove's outer wall on the face) and 5 mm
  past any other. The faces are the bare pulley's and a flange's flat thickness over them
  (`_ring_face_z`) — a metal plate's bent lip stands higher, but the ring sits on the flat. The face gets a **counterbore**
  Ø d4 (DIN's clearance for the lugs) and m deep, so the ring sits flush with the groove's outer wall at
  the face; a cylinder on the axis, cut after flanges are joined on (`_cut_counterbore`), and in the
  cadquery STEP. **Splined washer** (`spline_washer=1`): Ø d4 − 1, 1.5 mm, the bore's spline for its
  hole; the counterbore deepens by its thickness.
- **A spline takes no set screw**: Retention is locked to None on the page while Bore Shape is Spline
  (the previous choice returns after), and the server zeroes the screw whatever is sent.
- **Two new parts** in the download window, per pulley with a spline: the **sample splined shaft**
  (the part's measured height + 5 mm past the far face + DIN's n past the groove) and the **splined
  washer**; STL / SVG / DXF (`/download/spline-stl|svg|dxf?part=shaft|washer`), no STEP yet.
- **The page takes the figures from the server** (`/api/spline`): the fitted hole and shaft, the fit's
  source, the ring (label, McMaster PN), counterbore and washer. The Dimensions panel lists them and
  warns when the counterbore leaves under 1 mm to the hub / spoke hub / tooth root.
- **The counterbore's depth is from the part's outer face.** A flange or plate over the ring's face
  (no hub there — a hub stands up through the top flange — and no spokes) has the counterbore through
  it, as deep as it is thick; the pulley takes the rest (`_ring_cover`, `_open_for_ring`). A joined
  printed flange is cut with the pulley; a separate top flange and a metal plate carry their share
  themselves, and the STEP (which leaves them out) cuts only the rest. Asked for as an Auto-fix
  ("flange ID = counterbore OD"), made the rule instead: there is no flange-ID setting to fix.
- **The top flange's hole is the bore's shape** (STL and STEP), as the bottom's always was: a round
  hole at the bore closed a spline's slots and a key's slot for the flange's thickness.
- **Auto-fix for a spline the part can't hold** (first bug report, 2026-09-29: a 25 mm hub round a
  6 × 23 × 26 spline and its 35.5 mm counterbore): 1 mm of wall round the spline's reach and the
  counterbore; Hub OD grows up to the tooth root less 1 mm a side, else the largest smaller spline of
  the same kind that fits (ISO 14 size / fewer involute teeth), with the hub it needs. The Hub card
  names the counterbore too. A blank hub field is no hub, not a 500.
- **Later** (ToDo): a set screw that lands in a spline root, and a clamp hub (slot + screw boss).

**Consequences:** `tests/test_spline_retainer.py` (22), `tests/test_bore_shape.py` on the fitted
figures, `tests/browser/bore_ui.js` (42 checks); `cct_common/tests/test_splines.py`,
`test_iso286.py`, `test_retaining_rings.py`. Help picture `spline_ring.svg`
(`build_spline_ring.py`); `spline_involute.svg` relabelled. `/download/stl`'s body is `_pulley_stl`,
which the shaft's length measures. SMALL_STEP_HANDOFF §8.

**Amended 2026-09-30 — the counterbore is optional, per face.** The Spline card's
"Make counterbore" (under each ticked ring, on by default; `spline_cb_top` / `_bottom` = 0 turns
it off; links and designs from before have one) leaves that face plain: the washer lies on the
face and the ring on it, standing proud, the shaft's groove and end moved out with them. Where
the stack sits is one rule for every splined part, so it is shared:
`cct_common.retaining_rings.stack(ring, washer, counterbore)` gives the washer, ring and groove
spans and the shaft's end, measured outward from the face; the retainer carries it per face with
`cb_faces`. Every cut (the pulley's counterbore, a flange's or plate's share of it, the STEP),
the counterbore warnings and Auto-fix's wall, and the Hub card's check follow `cb_faces` only.

## ADR-016 — Undo / redo: settings snapshots; a state the page is about to correct is never a step
**Date:** 2026-09-28
**Status:** Active

**Context:** Sprocket Designer added undo/redo (its ADR-022, EBoxDesigner's pattern). The pulley page
corrects some settings after a pause — a centre distance snapped to a whole belt 3 s after typing or
**Min**, teeth under the minimum, a ratio / OD / belt length snapped, Pulley 2 set by Lock Ratio — and a
first version recorded the uncorrected value as a step, so Undo could return to an invalid state.

**Decision:**
- **A step is a snapshot of the page's settings as saved** (`collectSettings`; `applySettings`
  restores it through the same path startup uses). One gesture is one step: a visit to a field
  however many edits, one click with its knock-ons; the page's own follow-ups (an automatic clearance
  height) fold into the step that caused them. Panel open/closed isn't part of a step.
- **Pending fix-ups hold recording**: a scheduled correction holds by name, a running one by depth
  (so nested fixes can't release each other); the settled result is recorded as one step. Undo while a
  correction is pending drops that change.
- ↶ / ↷ in the top bar, Ctrl+Z / Ctrl+Y outside text fields, tooltips naming the change.
- Found with it: re-snapping a centre shown to 0.01 mm read a hair over its belt and added a tooth
  (49.07 → 51.61) on Redo and on reload. `correct_center_distance` and the page allow 0.01 tooth of
  slack (`BELT_TOOTH_SLACK`).

**Consequences:** `tests/browser/undo_ui.js` (27 checks, real mouse and key events);
`tests/test_center_distance.py` (re-snapping 2 000 random drives keeps the belt).

## ADR-015 — Dimensions panel with spec warnings; a new design's clearance meets the spec
**Date:** 2026-09-28
**Status:** Active

**Context:** Sprocket Designer shows a Dimensions table under its drawing with warnings where a
recommended spec is broken. The belt standards and manuals are in `Pdf/` (ISO 13050, ISO 5294, ISO 17396
preview, Gates Light Power & Precision, Fenner precision belting).

**Decision:**
- **The panel** (`/api/dimensions`, `geometry/belt_specs.py`) lists each pulley's diameters, groove
  depth, pitch differential, belt / face width, spline and flange figures, and for two pulleys the drive:
  belt, centre, ratio, wrap and teeth in mesh on the smaller pulley, free span.
- **Warnings name their source**: face width (ISO 13050 Tables 8/17/26/34, ISO 5294 Table 4, Gates
  Table 23), flange height past the OD (ISO 13050 Annex D ht + a, ISO 5294 Annex A, Gates Table 22),
  flange angle 8–25°, teeth in mesh under 6 (Gates torque factors), wrap under 60°, flanging a
  two-pulley drive and long spans (Gates). **Auto-fix** sets clearance height, flange rim radius and
  angle. No published figure → the rule on the app's own data, marked ≈, or no check (T, AT and
  STD 2/3/5 face width).
- **A new design's Clearance Height makes the face the spec's minimum** (belt ÷ 20 where there is no
  figure) and follows the profile, belt width and flanges until the user types one; saved designs and
  links keep theirs (`clearance_auto` in the saved settings).
- A printed flange on a spoked pulley measures its rim from the tooth root (as built), so the panel
  reports its real reach past the OD.

**Consequences:** `tests/test_dimensions.py`; `tests/browser/dims_ui.js` (20 checks).

## ADR-014 — Bore Shape in the Size card, splines included (cct_common.splines)
**Date:** 2026-09-28
**Status:** Active (2D, STL, cadquery STEP); small_step STEP pending

**Context:**
The bore's shape lived in the 3D hub's Retention menu (D-Shaft, Keyway, each with an optional set
screw of its own), so a 2D-only user (laser / waterjet) couldn't cut a D-flat or keyway, and there
was no spline. Sprocket Designer puts **Bore Shape** — Round, D-flat, Keyway, Spline — under Bore
Diameter in its Size card (its ADR-021), and its spline geometry moved to `cct_common.splines`
(0.19.0) so both apps draw the same holes.

**Decision:**
- **Bore Shape is a Size-card setting** (per pulley), used by every export, 2D and 3D. D-flat and
  keyway keep their fields and their `hub_flat_depth` / `hub_keyway_w` / `hub_keyway_h` query keys;
  a spline sends `bore_shape=spline` and `spline_type` (straight: `spline_n`, `spline_minor`,
  `spline_major`, `spline_width`; involute: `spline_m`, `spline_z`, `spline_pa`, `spline_root`),
  prefixed `p2_` for Pulley 2. One shape at a time: a spline zeroes the flat and key server-side.
- **Retention is the set screw only** (threaded / captured nut / insert / none). With a shaped bore
  it is one screw, on the flat, into the key slot or into the first spline slot (all on +X); the
  captured nut sits on that face — for a spline, the slot bottom (major diameter), as for a key.
- **A spline replaces the round bore** and sets Bore Diameter to its minor diameter (`_get_bore`);
  the page locks the field and says why. Its major diameter is the bore's reach for every wall
  check, and the Dimensions panel warns when it comes within 1 mm of the tooth root or the hub OD.
- **Exact where the format allows**: SVG paths and DXF LINE/ARC entities straight from
  `splines.path`; cadquery STEP builds the same lines and three-point arcs (LINE and CIRCLE edges —
  no new STEP entity type); STL and the preview sample it at 0.02 mm chords.
- **small_step can't cut a spline yet**, so its STEP download refuses with that reason (400) rather
  than export a round hole (SMALL_STEP_HANDOFF §7).
- **Old designs move over**: a saved design with D-Shaft / Keyway retention becomes Bore Shape D-flat /
  Keyway, its screw (if it had one) becoming the Retention; old links with `hub_flat_depth` /
  `hub_keyway_w` open as D-flat / Keyway, with or without a hub.

**Consequences:** `tests/test_bore_shape.py` and `tests/browser/bore_ui.js` (26 checks);
`cct_common/tests/test_splines.py` for the geometry. Help pictures `bore_shape.svg`,
`spline_straight.svg`, `spline_involute.svg` (`build_bore_shape.py`), `hub_retention.svg` without
the D-Shaft / Keyway columns.

## ADR-013 — Set-screw holes sized by how the screw holds (cct_common.screws)
**Date:** 2026-09-27
**Status:** Active (STL); STEP pending

**Context:**
Every set-screw hole was cut at the screw's nominal diameter (an M5 got a 5.0 mm hole), from a
page-side table of M3–M10. A "standard" set screw therefore had almost nothing to thread into, and
a captured-nut screw had no clearance. E-Box Designer already sized screw holes properly; its
table and rules moved to `cct_common.screws` (0.18.0, E-Box D099) so both apps share them.

**Decision:**
- **How the screw holds decides the hole** (`geometry/set_screw.py`):
  *threaded* — the self-tapping bore (round by thread engagement %, or hex by hex flats %);
  *captured nut* — a plain ISO 273 clearance hole, never threaded, and that size's nut
  (DIN 934 metric, the same pockets as before; machine-screw nuts for inch); *heat-set insert* —
  a round hole of the diameter entered. *Custom* sizes take an entered diameter too (with a nut,
  the nearest metric nut).
- **Sizes**: metric M2–M10 and inch #2-56 to 1/4-20, rendered into every size dropdown from the
  server; the page keeps no size or hole table of its own (`/api/screws` gives it hole sizes for
  its notes).
- **Threaded-hole settings are app-wide**, in a *Threaded screw holes* dialog on the 3D title
  bar: they depend on the printer and filament, not on a pulley. Same fields, names and defaults
  as E-Box Designer (round 50% / hex 82%). Two things are kept apart: **your default**
  (this browser, `pulley_thread_default`; *Save as my default*) which new designs and Reset start
  from, and **the design's values**, which are **sent with every design** (`screw_hole_shape`,
  `thread_engagement`, `hex_flat`), embedded in each download, and restored with it from a file
  or link — without touching your default. The dialog shows the design's values against your
  default, with *use my default* and *factory settings* links.
- **Query parameters**: `hub_screw_size`, `hub_screw_hold` (thread | nut | insert),
  `hub_screw_hole_dia`, alongside the existing `hub_screw_count`, `hub_captured_nut` and
  `hub_screw_dia` (now the screw's nominal diameter: STEP and older readers use it).
- **Old designs are untouched**: a link or file with only `hub_screw_dia` has no named size, so
  `set_screw.parse` returns None and the exporters cut the nominal hole they always did.
- **STL only for now**: both trimesh builders take the spec (`_screw_hole_cutter`,
  `_screw_nut`). small_step still gets `hub_screw_dia` and cuts the nominal diameter; the STEP
  side is held until the small_step work is ready (the hex bore will be a new obligation there).
- A hex bore keeps a corner up once turned radial, so its roof prints without support.

**Consequences:** `tests/test_set_screw_holes.py` measures each kind of hole in the STL download
and the preview; `tests/browser/screw_ui.js` (33 checks) drives the controls, the dialog, saving
and restoring. Captured-nut STLs still aren't watertight — as before this change (ToDo).

## ADR-012 — Admin dashboard: signed-in admins, subscribers, sales with refunds
**Date:** 2026-09-26
**Status:** Active

**Context:**
The old `admin_dashboard.html` was a static page that called bearer-token `/api/admin/*`
routes (Render-era: metrics, queue, subscribers/licences, downloads). Most of it described the
subscription model that ADR-008 replaced, and none of it showed the token ledger. Refunds had no
tooling: one issued in the PayPal/Stripe dashboard takes back tokens in proportion to the
money, so a refund net of fees left tokens behind.

**Decision:**
- One shared dashboard, `cct_common.admin` (0.17.0), at `/admin`. Every CCT tool shares the
  accounts database, so any tool's `/admin` shows every tool's subscribers. Tabs: Subscribers,
  Sales, Bug reports, Status. Every column sorts by clicking its heading (again to reverse,
  blanks always last), and each tab has a search box.
- **Who gets in:** the normal account sign-in, with the email in `ADMIN_EMAILS`
  (comma-separated; default `xootme@gmail.com`). Browser sessions only; add-in/agent device
  tokens get a 404. Anyone else gets a 404, not a 403, so the page isn't advertised. Changes
  must come from the page's own origin and be JSON (the cookie is SameSite=Lax; this closes the
  rest). The JSON lives under `/admin/api/`, not `/api/admin/`, which carries the old routes'
  wildcard CORS header.
- **Subscribers:** first subscribed, last sign-in, tokens now (free / bought), **lifetime
  tokens** (one number: every token received — signup, purchase, referral, promo and positive
  adjustments), and the **last 5 uses** (the CCT tool, tokens, date, format; refunded failed
  exports don't count). The tool comes from a new `app` column on the ledger, written by each
  tool's `TokenStore(..., app=...)` (`"pulleys"` here); older rows show the mounting app's
  `default_app`. **Grant / adjust**: a positive amount is a `promo` credit (free tokens, not
  refundable); a negative one is an `adjust`. A reason is required and recorded.
- **Sales and refunds (terms §4):** Refund… on an order quotes the account's unused
  *purchased* tokens from that order, valued at the price paid, less the processor's fee (read
  from PayPal/Stripe; editable, and entered by hand when the provider doesn't report it). A
  confirmed refund goes through the provider's API and takes back exactly those tokens, under
  the same `provider-refund:<id>` ref the provider's own refund notification uses, so the
  notification that follows takes nothing more.
- **Ledger fix that came with it:** a refund take-back used to count as *spending*, so it ate
  free tokens first. Refund adjustments are now left out of "free used"; they come off the
  purchased tokens they refunded.
- **Dropped:** Metrics, Queue, Render status, Subscribers/Licences (the old model), Downloads.
  The bearer-token `/api/admin/*` routes remain in `app.py` with nothing calling them; retiring
  them is a ToDo.

**Consequences:** `ADMIN_EMAILS` should be set on test and production at deploy. The browser
harness `tests/browser/admin_ui.js` (seeded by `admin_seed.py`) drives sorting, search, both
dialogs, bug delete and status; `cct_common/tests/test_admin.py` covers the routes on SQLite and
Postgres.

## ADR-011 — Help pictures built from the app's own drawings, shown on hover
**Date:** 2026-09-26
**Status:** Active

**Context:**
The help pages were text only. Settings like Fillet Tip or Backlash change the part by a
millimetre or less, which words describe poorly.

**Decision:**
- Each picture is built from the app's **own SVG downloads** (`/download/svg`, `svg-rim`,
  `belt-svg`) by a script in `tools/help_illustrations/`, cropped and zoomed with nested
  `viewBox`es — vector all the way, so zoomed insets stay sharp. Callouts (dimensions, numbers,
  highlighted arcs) are placed from the drawing's geometry, not by hand, so a script can be
  rerun when the geometry changes. Output lives in `static/help/*.svg`; each script's
  docstring gives the exact download URLs of its source drawings.
- Only two are not from app drawings: 3D Mode (crops of the two sample screenshots) and Belt
  / Clearance Height (a labelled diagram — the app has no side view).
- **3D-mode pictures** (Hub so far) pair two things from the app: a snapshot of its own 3D
  preview (`tools/help_illustrations/shoot3d.js` drives headless Chrome — settings as a JSON
  list of steps, view snapped, canvas captured at 2×; the builder trims it and embeds it as
  JPEG) for what the part looks like, and a **vector section of its STL download** (sliced
  with trimesh's plane intersection + shapely `polygonize`; planes nudged off vertex rows)
  for exact dimensions. The STLs carry the design metadata after the triangles, so the
  builders read exactly the declared triangle count.
- Hover keys drop only the pulley number (`spokes1_x` → `spokes_x`, `hub2_x` → `hub_x`,
  `p2_x` → `x`), so panels with same-named settings can't collide. A `PICTURES` entry can be a
  function of the label when the right picture depends on another setting (Number of Screws
  follows the Retention Method).
- The pop-up shows each picture at its own size (capped at 1000 px / the window), so pictures
  are designed to be read at that size: ~650–1000 px wide, body text ≥ 14 px.
- What the 3D help pages state about the geometry (Spoke Height is the centred web thickness;
  Flange Height is the thickness at the teeth, the lip thinner by rim × tan(angle); metal
  plates come in the pulley STL; nub pins are their socket less the allowance) is pinned by
  `tests/test_help_geometry_claims.py`, measured on STLs built through the app — so the
  pictures and text can't silently drift from the geometry again.
- Slicing gotcha (tests and builders): shapely's `polygonize` returns each face with its holes
  already cut *and* each hole as a face of its own — even-odd only the faces' exteriors, or
  the holes get filled back in.
- Horizontal slices of captured-nut hubs are taken at the nut's screw height (hub top − hex
  circumradius, using the exporter's `_nut_dims`), not mid-hub — a small nut's screw sits
  near the top.
- Where the real preset is too small to see, the picture exaggerates it and says so (Backlash).
- The pictures appear on the help pages (click for full size) and as **hover pop-ups** on the
  setting's label (`PICTURES` table at the end of `index.html`, keyed by input id without the
  pulley prefix; labels that wrap their control use `data-picture`).
- Tests: `tests/test_help_pictures.py` (files exist and are SVGs, every hover key matches a
  label); `tests/browser/help_ui.js` (pop-ups in the browser).

---

## ADR-010 — Spoke settings are fitted to the pulley, with a warning and Auto-fit
**Date:** 2026-09-26
**Status:** Active

**Context:**
Spoke settings are absolute mm. After a smaller pitch or fewer teeth they could be impossible:
fillets colliding, spokes crowding the rim, the hub filling the web. Four separate checks
disagreed (a page-side tip-circle formula that was ~2× tooth height optimistic, a server fillet
clamp, a geometry error at export time, and the builder silently dropping fillets it couldn't
fit). Edits were silently reverted or clamped as the user typed, and some exports failed.

**Decision:**
- One resolver, `geometry/spoke_fit.py`, judges a layout with the **real spoke builder**
  (`_spoke_void_polygons`, which now reports fillets it had to leave out) — "fits" means "the
  exporters can build it".
- When the requested settings don't fit, it finds the smallest change: first room between
  hub and rim (Hub OD and Rim Depth shrink together, keeping ≥ 1 mm rim and ≥ 1 mm hub wall),
  then one setting alone if that is enough, otherwise Fillet Base → Fillet Tip → Spoke Width →
  Spoke Count; fillets are then grown back. If nothing fits, spokes are left out.
- `_parse_spoke_params` builds with the fitted values, so the preview and every export agree
  and none fails. The typed values are **never** changed without the user: the page shows a
  warning listing the changes with an **Auto-fit** button (`/api/spoke-fit`).
- Tests: `tests/test_spoke_fit.py` (incl. a property sweep that every fitted result builds);
  `tests/browser/help_ui.js` (warning and Auto-fit in the browser).

---

## ADR-009 — 3D Print Compensation is a perpendicular offset of the whole tooth outline
**Date:** 2026-09-26
**Status:** Active — changes existing designs that use compensation

**Context:**
Each groove generator applied `print_extra / 2` in its own way: tooth tips moved p/2, the
groove floor moved p/2 on HTD but p on the trapezoidal profiles, and walls moved by various
amounts. Meanwhile flanges, hub/rim features and the dual layout computed the OD as
`getOuterDiameter(n, pitch, pld + print_extra − clearance)` — a full p per surface.

**Decision:**
- The value p is the distance **every** surface of the tooth outline (lands, tips, walls,
  floor) moves into the material, measured perpendicular to the surface. The outer diameter
  shrinks by 2p, matching the other OD calculations.
- `generate_profile_groove` builds the nominal groove (the generators are called with 0) and
  `_offset_groove_outline` erodes the material under the whole outline by p with a round-join
  buffer; `wrap_groove_to_pulley` then measures from the nominal OD. One place, all formats.
- The offset is exact in the flat groove frame; wrapping adds a tiny distortion.
- With p = 0 the output is byte-identical to before. With p > 0, designs change: tips move
  twice as far as they used to.
- Tests: `tests/test_print_compensation.py`.

---

## ADR-008 — Token model: free app, pay per export
**Date:** 2026-09-24
**Status:** Active (being implemented; supersedes ADR-005 once live)

**Context:**
ADR-005 sells a yearly subscription (licence.lic, Autodesk App Store, WooCommerce
licence keys). The goal is to make the app free in every CAD program and charge
only for the files people export, with one account that works across Fusion,
FreeCAD, SolidWorks and the web.

**Decision:**
- **Tokens cost $0.10 each** and are sold in packs (card fees of ~30¢ + 2.9% exceed
  a single 10¢ token). Pack sizes are decided with the purchase webhook.
- **Priced by tier, and a tier includes every tier below it:**

  | Tier | Formats | Tokens | Also unlocks |
  |---|---|---|---|
  | 2d | SVG, DXF | 1 | — |
  | stl | STL | 2 | 2d |
  | step | STEP | 3 | stl, 2d |

  At download time a STEP purchase offers the included STL/SVG/DXF as checkboxes.

  *Revised 2026-09-26:* a token is now **5¢**, and the tiers cost **2D 2 · STL 3 ·
  STEP 4** (10¢ / 15¢ / 20¢), so each tier adds one token. A CAD download was 30¢
  under 2/4/6, which was too much. The download dialog explains the difference with
  a red note above the price list. `cct_common.tokens.TIER_PRICE` (0.16.1).
- **One purchase unlocks the whole design on screen**, not a single part: a two-pulley
  drive with its belt and flanges at STEP tier costs 3 tokens. The design is
  identified by a hash of its parameters (`cct_common.tokens.design_key`).
- **Unlocks last 24 hours.** Re-downloading any unlocked format of that design is
  free in that time; moving up a tier costs only the difference (STL → STEP = 1).
- **New accounts get signup tokens** (a one-time grant). No weekly free allowance;
  the weekly trial limit (`register_trial_download`, `/api/fp-token`) is retired.
- **Sign-in offers both an email link (via Resend) and OAuth** (Microsoft, Google and
  GitHub; Apple deliberately left out — it needs a $99/year developer membership).
  GitHub is plain OAuth 2 with no ID token: after sign-in the server calls GitHub's
  API for the user id and reads the verified primary email from `/user/emails`
  (scope `user:email`), since many users keep their email private. Add-ins get a long-lived device token for the same
  account. Sign-in identities (provider + subject id) are linked to an account
  rather than the account being keyed only by email, so one person can reach the
  same tokens through several sign-in methods.
- **Charge only for delivered files:** the charge is taken before generating and
  refunded automatically if generation fails, so a failed export costs nothing.
- **The balance is never stored.** It is the sum of an append-only ledger (signup,
  purchase, spend, refund, adjust rows), so every change is auditable. The
  check-and-spend runs inside one write transaction so two workers can't both
  spend the last token.
- **Add-ins and AI agents sign in by device code (decided 2026-09-25):** the add-in or
  agent shows a short code, the person approves it on `/account/device` in a signed-in
  browser, and the add-in receives its own labelled, revocable device token once.
  **Each device token has a daily token limit, on by default (100 tokens per rolling
  24 hours, `TOKENS_DEVICE_DAILY_BUDGET`)**, chosen at approval and changed on
  `/account/devices`. Reaching it stops only that token (429 `DAILY_LIMIT_REACHED`) and
  emails the owner once a day with how to raise or remove it. Browser sessions have no
  limit, so heavy users clicking in the page are never slowed. Rationale: a runaway agent
  is contained to a known amount, without rate-limiting anyone who wants many pulleys.
- **Inactivity (decided 2026-09-25) — cleaning up dead accounts without taking paid value.**
  Purchased tokens never expire: paid prepaid value can fall under gift-card rules
  (federal minimum 5 years; some states, e.g. California, bar expiry) and state
  unclaimed-property law. Instead: *free* tokens (signup, referral, promo) expire after
  **2 years without a sign-in**; free tokens are counted as spent first. An account with
  **no purchased tokens** left is closed after **5 years without a sign-in**
  (`delete_account`: personal data removed, ledger kept). Each happens only after a
  reminder email sent at least 30 days ahead, and any sign-in resets both clocks. The
  page states the rule wherever tokens are bought or signed in to. Not legal advice —
  confirm the wording with whoever writes the terms.
- **Inactivity revised (2026-09-26) — purchased tokens end with the account.** Every
  account, including one holding purchased tokens, is closed after **5 years without a
  sign-in**, and whatever is left in it expires (ledger `expire` rows, so the record
  stays; `TokenStore.expire_all`). Owner's reason: an end date limits the liability of
  tracking and honouring paid balances forever. Mitigations for the gift-card and
  unclaimed-property risk recorded above: 5 years matches the federal gift-card minimum
  (tokens can only be bought while signed in, so none expires sooner than 5 years after
  purchase); the closing reminder (30+ days ahead) states how many tokens are at stake
  and offers a refund of unused purchased tokens, less processor fees (terms §4); the
  ledger is kept. States that bar expiry (e.g. California) and unclaimed-property
  reporting remain open questions for a lawyer. Free tokens: unchanged (2 years).
- **Tokens revalued (2026-09-26) — 1 token = 5¢, one expiry rule.** Every count doubles
  so money prices stay the same: 2D 2 tokens (10¢), STL 4 (20¢), STEP 6 (30¢); packs
  PayPal $2 = 40, $5 = 100, Stripe $5 = 100, $10 = 200, $25 = 500; the signup grant is
  20 tokens (still $1). Reasons: a finer step leaves room to price smaller things (a
  1-token item), and "20 free tokens" reads better. The separate 2-year expiry of free
  tokens is dropped — one rule for every token: they end with the account after 5 years
  without a sign-in, and each sign-in resets the clock. The ledger still tells free from
  bought tokens: free ones are spent first and are **never refunded** (terms §4 says so).
  Done before any customer existed, so no balance needed converting.
- **Hosting moves to Google Cloud Run (decided 2026-09-25),** billed only while handling
  requests and scaling to zero, so cost follows token sales. (Azure Container Apps was
  picked on 2026-09-24, then dropped: the existing Microsoft account is Microsoft 365, not
  Azure, and Cloud Run is the simpler move for one person — one-command deploys from the
  Dockerfile, one request per instance, a max-instances cost cap, no Log Analytics bill.)
  Database: Neon Postgres. Off-server backups go to a private Cloud Storage bucket,
  encrypted with a key from Secret Manager, accessed through the service's own identity.
  Render-side backups are not pursued; Render is left behind with the move.
- **Built in `cct_common.tokens`** so EBoxDesigner can use the same ledger. SQLite
  first (local, tests, single-instance deploy); a Postgres backend behind the same
  interface when hosting moves off the Render disk (see ToDo.md "Hosting").
- **Data kept, and how it's protected:** email, account id, linked sign-in identities,
  sessions and the ledger — no passwords, no card data (the payment provider holds
  it), no design files (only a parameter hash). Sign-in links, session cookies and
  add-in device tokens are stored only as SHA-256 hashes; links last 15 min, work
  once, are rate-limited, and are used by pressing a button on the page they open
  (mail scanners pre-fetch links). Cookies are HttpOnly, Secure, SameSite=Lax.
  Deleting an account strips email, identities and sessions; ledger rows remain as
  financial records without personal data (a hash of the closed email blocks a
  second signup grant).
- **Corruption and loss:** WAL + `synchronous=FULL`; every spend in one transaction;
  `integrity_check()` at startup, and charging is refused (503) if it fails rather
  than acting on damaged data. Scheduled online `backup()` copies go **off the
  server**. Recovery = restore the latest backup, then replay purchases from the
  payment provider's orders (credits are idempotent on the order number, so a full
  replay can't double-credit). Spends made after that backup are lost, which errs in
  the customer's favour; sessions made after it need a fresh sign-in.
- **No local installs (decided 2026-09-24).** Every export is generated on the server;
  the add-ins use the hosted app. A discounted local-export option was considered and
  rejected: server compute per export is ~$0.00005 against a 10–30¢ price, so a local
  discount gives away revenue while saving almost nothing, and local generation is the
  one path where the token check can be patched out. Once tokens are live the desktop
  build (PyInstaller/PyArmor, `licence.lic`, the launcher) is retired.

**Consequences:**
- Every download route must be enforced server-side; today's web limit is only a
  client-side check that fails open.
- Charging is gated behind a setting until accounts, purchase and UI are done, so
  the live app keeps working unchanged during the build.
- `licence.lic`, `/api/provision`, `subscribers.json`, licence-key activation and the
  dev backdoor are retired once tokens are live.

---

## ADR-007 — STEP export via small_step (Rust) replacing cadquery
**Date:** 2026-06-16
**Status:** Active

**Context:**
cadquery (via Python 3.12 subprocess) produced correct STEP geometry but had two
practical problems: a slow cold-start on each request (OCC kernel initialisation)
and an added dependency on a separate `.venv312` subprocess for every STEP download.
A custom Rust STEP emitter (`C:\Users\cmyer\Documents\small_step\`) was developed
as a direct CLI binary that emits AP214 B-rep without any CAD kernel.

**Decision:**
Replace all cadquery STEP calls with the `small_step` binary via `step_worker_ss.py`.
The binary is invoked as a subprocess: `small_step combined <dxf> <height> [options]`.
When `SMALL_STEP_BIN` env var is set, `_run_ss_worker()` in `app.py` uses it; if unset,
the old `step_worker.py` cadquery path remains available as a fallback.

**Consequences:**
- `step_worker.py` (cadquery) retained as a fallback but no longer called at runtime.
- `step_exporter.export_step` / `generate_pulley_step` imports removed from all routes.
- `from exporters.step_exporter import (...)` retained only for STL functions.
- `SMALL_STEP_BIN` must be set in the environment pointing to the compiled binary.
- `cadquery` removed from `requirements.txt`; Flask venv upgraded from Python 3.12 to 3.14.
- ADR-001 and ADR-002 below are superseded for STEP; cadquery is now only a fallback.

---

## ADR-001 — Python 3.12 for STEP export
**Date:** 2026-04-11  
**Status:** Superseded by ADR-007 (cadquery STEP path replaced by small_step Rust binary)

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

## ADR-002 — STL via trimesh, STEP via cadquery → small_step
**Date:** 2026-04-11  
**Status:** Partially superseded by ADR-007 (STEP path changed; STL path unchanged)

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

## ADR-005 — Subscription licensing: licence.lic + Render provision server
**Date:** 2026-05-04
**Status:** Active

**Context:**
PulleyApp sold as a subscription via Autodesk App Store. Need to control access to the desktop build and handle expiry/renewal without per-customer machine binding complexity.

**Decision:**
- One `licence.lic` per year, no machine binding, generated locally with `packaging/prepare_release.py`.
- `--period 7` requires PyArmor's servers to confirm the licence is still valid every 7 days (customer needs internet access at least weekly).
- `--expired <date>` hard-stops the app on the expiry date regardless of internet connectivity.
- Provision server runs as additional routes on the existing Render Flask service — no separate service needed.
- `licence.lic` base64-encoded and stored as Render environment variable `PULLEY_LICENCE_B64`. Regenerate by running `prepare_release.py` and updating the env var whenever a new local release is built.
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
