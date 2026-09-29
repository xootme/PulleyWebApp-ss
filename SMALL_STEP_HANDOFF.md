# small_step: changes needed for the 2026-09-27 pulley work

For the agent working on small_step. The app side landed in PulleyWebApp-ss
on `token-model` (ADR-013 and the bug fixes of the same day). STL and the 3D
preview already have all of it; STEP does not. Nothing in
`exporters/step_worker_ss.py` or small_step was changed — the owner asked to
hold the STEP side until you're ready. When a small_step release supports
item 1, tell the PulleyWebApp-ss session and it will wire the worker.

## 1. Set-screw holes sized by how the screw holds — REQUIRED

**Today** the worker passes `--screws <dia> <count> [--captured-nut]` and
small_step cuts every hole at `dia` (the screw's nominal size) and picks the
nut from `dia` (nearest metric). STL no longer does:

| hold (`hub_screw_hold`) | hole | nut pocket | spacing (2 screws) |
|---|---|---|---|
| `thread` | self-tapping bore: **round** Ø = major − e%·(major − minor), or **hex** across flats = f%·major (the design's `screw_hole_shape`, `thread_engagement`, `hex_flat`) | none | 90° |
| `nut` | plain clearance hole, ISO 273 medium (M5 → Ø5.5) | from the size's own nut — DIN 934 metric, machine-screw nuts for inch sizes | 180° |
| `insert` | round, the Ø entered (`hub_screw_hole_dia`) | none | 90° |
| `Custom` size | round, the Ø entered | with a nut: nearest metric nut to that Ø | as above |

All numbers come from `geometry/set_screw.py` (`parse(args, prefix)` →
`SetScrew(hole, nut, major, hold)`), which uses `cct_common.screws`. The
worker should pass them through; small_step should not re-derive them.

**Proposed CLI** (yours to shape — only the worker calls it):
```
--screws <count> --screw-hole round <diameter>
--screws <count> --screw-hole hex <across_flats>
                 [--nut <across_flats> <height>]      # captured nut
```
Keep `--screws <dia> <count> [--captured-nut]` working unchanged: designs
from before sizes had names (only `hub_screw_dia`) must re-export exactly
as they did (`set_screw.parse` returns None for them).

**Geometry to match the STL** (`exporters/step_exporter.py`,
`_add_hub_and_bore` / `_build_pulley_mesh`, helpers `_screw_hole_cutter`,
`_screw_nut`):
- Hole: radial, one-sided (hub OD in to the bore), unchanged placement.
- **Hex hole (new)**: a hexagonal prism along the radial axis with a
  **corner pointing up and down (±Z)**, flats vertical on each side, so the
  roof prints without support. Its faces are planes meeting the hub's
  cylinders — check this stays inside the STEP entity vocabulary before
  extending it (and record any new obligation, as for E-Box D035).
- Nut pocket: unchanged shape (width = AF + 0.5, radial depth = height +
  0.5, hex-tip bottom, circumradius R = (AF + 0.5)/√3, screw at
  hub_top − AF/√3, hub auto-raised / oblong lobes from `min_hub_r =
  R_bore + 3·height`), but from the nut passed in, not a table lookup.
- **Pocket inner face moved 0.05 mm into the bore** (`_POCKET_OVERLAP`):
  R_bore − 0.05, or flat face − 0.05 (D-shaft), or key-slot face − 0.05
  (keyway). A flat face exactly at the bore radius only touched the round
  bore along a line (non-manifold STL); match it so STL and STEP agree, and
  it avoids a tangent plane/cylinder contact in the B-rep too.

**Worker wiring (PulleyWebApp-ss, after your release):** add the hole and
nut to the params the app sends the worker (from `_set_screw(args,
prefix)` in app.py), emit the new flags in `_build_pulley_cmd`, then update
`static/Hub_help.html` ("STEP downloads still show every set-screw hole at
the screw's nominal size") and the ToDo entry.

## 2. Backlash "Tight" on Imperial / T / AT — CHECK ONLY

`generate_imperial_groove` clamped negative backlash to 0, so Tight was the
Standard groove on 14 profiles. It now narrows the groove (stopping before
the groove floor closes). The worker builds the STEP from the app's own DXF,
so small_step gets the new outline with no change — but these are groove
shapes it has never seen. Please run your parity / FreeCAD checks on, e.g.,
T5 30T, AT10 30T and XL 30T with `backlash_preset=TIGHT`.

## 3. Metal flange plate profile — CHECK your own

`geometry/flange_geometry.profile_metal` had the bend's arcs joined to the
wrong faces: the top flat face (z = plate thickness) ran into the larger
(outer) arc and the bottom flat face into the smaller one, so the outline
traced the seam at r_tooth_OD twice — self-touching, and the revolved STL
was non-manifold. Fixed: the arc centre is above the plate, so the
**smaller arc is the upper face** and the larger arc the lower one.
`small_step flange-metal` builds the plate from numbers itself, so this
fix does not reach STEP. Check whether small_step's profile has the same
crossing (a doubled edge at r_tooth_OD, z 0…thickness); if it does, the
enclosed region is the same, only the traversal needs fixing.

## 4. Bottom flange bore — INFORMATION

For STL the bottom flange is now revolved 0.3 mm inside the bore
(`_BORE_OVERCUT`) and the bore profile (round / D-flat / keyway) cut
through it, so the flange's bore is exactly the pulley's; 3D-printed flanges
are also unioned with the pulley into one solid. If STEP flanged pulleys
show seams or slivers at the bore, this is the equivalent fix.

## 5. Captured nut: the pocket and the bore — REQUIRED (measured 2026-09-27)

Measured on an AT10 24T pulley, bore 12, hub OD 30 × 16, M5 captured nut,
sliced 1 mm below the hub top along the pocket's axis (+X), in the STL, the
cadquery STEP and the small_step STEP:

| bore | STL | cadquery STEP | small_step STEP |
|---|---|---|---|
| round | pocket opens into the bore | pocket on the bore | **0.4 mm wall** (6.0–6.4) |
| D-flat 1 mm | pocket on the flat* | pocket on the flat | **no flat in the hub**; 0.4 wall off the round bore |
| keyway 4 × 1.96 | pocket on the slot face | pocket on the slot face | **no slot in the hub**; 0.4 wall off the round bore |

\* the downloaded STL left a 0.95 mm wall in front of the flat; fixed in the
app the same day (`_add_hub_and_bore` now handles the D-flat like the
preview).

What small_step needs, in `crates/ss-pulley/src/lib.rs`:
- **No wall.** `xi = bore_r + 0.4; // 0.4 mm wall between bore and nut
  pocket` (≈ line 2937) is a stand-in — there should be no gap. Put the
  pocket's inner face where the STL puts it: `bore_r − 0.05` for a round
  bore, `flat_x − 0.05` for a D-flat, `bore_r + keyway_h − 0.05` for a key
  slot (`_POCKET_OVERLAP`; cadquery now uses the same overlap, see 6c).
- **Keep the D-flat / key slot through the hub when there's a captured
  nut.** Without a nut small_step cuts them through the hub (z 5, 12, 20 all
  show the flat at 5.00); with one the hub's bore is round.
- **Key slot depth.** small_step puts the slot face at 7.617 for
  `--keyway 4 1.96` on a 12 mm bore — the depth measured from the chord at
  the slot edge (√(6² − 2²) + 1.96). The STL and cadquery put it at 7.96 —
  bore radius + depth, at the slot's centre (ISO 773 t₂ + r, which the
  app's Default Key fills in). Match the app.

## 6. STEP/STL parity fixes from the cadquery fuzz — CHECK each (2026-09-27)

The cadquery track (PulleyWebApp `cadquery-track`, now fast-forwarded into
-ss `token-model`) fuzzed cadquery STEP against the STL on three seeds, 150
pulleys each, to 0 failures. These are the rules the STL and cadquery now
agree on; small_step should match each one. Every case is in
`tests/test_step_stl_parity.py` — run `fuzz_pulley.py --backend small_step`
to see which small_step still misses.

a. **Metal flange, hub wider than the pulley: raise the hub by the plate
   thickness** (`--top-metal … <plate_height>`), not the 3D-print flange
   height. The hub sits on the plate. cadquery used flange_height (2.5 vs
   2.4 plate in the fuzz case).
b. **Key slot from the bore's centre out** (`R_bore + keyway_h` long,
   `keyway_w` wide). Same cut for a normal key, but a key wider than the
   bore (fuzz: 6 mm key, 4.1 mm bore) otherwise leaves two slivers beside
   the bore.
c. **Nut pocket reaches `_POCKET_OVERLAP` (0.05) into the bore** — also
   from section 5. With the pocket face exactly on `x = R_bore` (tangent to
   the bore) cadquery built an invalid solid (two captured nuts over
   spokes).
d. **45° support cone under each captured-nut lobe over the spoke
   pocket**: a truncated cone on the lobe's axis, from `R_hub − h` at the
   bottom to `R_hub` at the lobe's underside, `h = min(spoke_h + pocket,
   R_hub − 0.5)`. cadquery and the preview had it; the STL download didn't
   (fixed). Check small_step builds it.
e. **No hub height → no hub and no set screw.** cadquery used to invent a
   nut-sized hub when hub_height was 0 but a screw was set; the STL and the
   preview show none. Check small_step doesn't build one.
f. **Nub sockets stop at the belt face** (`belt − depth` to `belt`). The
   STL's cutters reached 20 mm up and carved the hub on small pulleys
   (fixed); cadquery was already right. Check small_step doesn't cut into
   the hub.

App-side only, nothing for small_step: `/download/flange-stl` now fits the
spokes like every other export; spoke-void arcs in the STL are drawn within
0.01 mm; a merged printed top flange overlaps the hub 0.1 mm so the STL is
watertight.

## 7. Splined bores — REQUIRED for STEP (2026-09-28, ADR-014)

Bore Shape can now be a spline (ISO 14 straight-sided, ISO 4156 involute),
from `cct_common.splines` — the same module Sprocket Designer's bores use
(its handoff has the same obligation). The app's STEP download refuses a
spline today (`_run_ss_worker`: "STEP can't carry a splined bore yet"),
so nothing is exported wrong; this is to lift that.

- **The hole** is `splines.path(sp)`: closed, counter-clockwise, first
  slot / space on +x, lines and circular arcs only (each involute flank is
  tangent arcs within 0.001 mm). The worker job carries it as
  `spline = {kind, n, minor, major, width, module, pressure, root}`;
  `cct_common.splines.Spline(**spline)` rebuilds it. Bore diameter = minor.
- **What small_step needs**: a bore profile that isn't a CIRCLE (+ flat /
  key) — a closed wire of LINEs and CIRCLE arcs cut through the pulley and
  any integrated flange. No new entity type: cadquery's STEP of the same
  hole is LINE and CIRCLE edges only.
- **Set screw**: one screw into the first slot (+x); a captured nut's inner
  face on the slot bottom, `R = major / 2` (as a keyway's face).
- **Reference**: the cadquery track's STEP (`PULLEY_STEP_BACKEND=cadquery`)
  cuts it exactly — valid solid, volume within 7e-4 of the STL
  (`tests/test_bore_shape.py`).

## Not STEP-related (nothing to do)

The Threaded screw holes dialog and your-default / design values, hub OD
from links, "Flange cuts hub" removal, help pictures, JSON errors (no
tracebacks in responses), retired `/api/admin/*` routes, the removed `xoot`
backdoor and the off-site database backups are all app-side.
