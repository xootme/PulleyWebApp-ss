# small_step: what to add for the new pulley features (2026-09-28 / 29)

What the small_step (ss) STEP path needs so that PulleyWebApp-ss can export
STEP for everything the app added on 2026-09-28 and 29. The app side is done
on `token-model` (= PulleyWebApp `cadquery-track`, commit `0205156`). STL, 2D,
the 3D preview and the **cadquery STEP** already have all of it. The ss STEP
has none of it yet: it refuses the new bores outright, so nothing is exported
wrong in the meantime.

Items 1–6 of `SMALL_STEP_HANDOFF.md` (set-screw holes by hold, captured nut,
parity fixes, …) still stand as written; this replaces its §7–8.

## Where things are

| | |
|---|---|
| The refusal | `app.py::_run_ss_worker` → `_has_spline(worker_kw)` → 400 *"STEP can't carry a splined bore yet"*. Covers splines **and** hex bars (both are `spline` in the job). |
| The ss worker | `exporters/step_worker_ss.py` — writes the pulley DXF (`generate_dxf`) and runs `small_step combined <dxf> <height> [--top-3d …] [--bot-3d …] [--top-metal …] [--bot-metal …] [--hub od h [--flat d] [--keyway w h] [--screws dia n [--captured-nut]]]` |
| small_step today | `crates/ss-pulley/src/lib.rs`: the `BORE` DXF layer is **circles only** (smallest = bore); D-flat and keyway bores are built procedurally from the hub flags (`build_bore_loop_hub`, `build_d_flat_bore_loop`, `build_keyway_bore_loop`); flanges are revolved with a **round** hole of `r_inner`; two screws sit 90° apart (180° with captured nuts). |
| Reference build | the cadquery track, `PULLEY_STEP_BACKEND=cadquery`: `exporters/step_exporter.py::generate_pulley_step` — `_cq_spline_solid` (the bore), the ring-counterbore block at the end, the top-flange re-cut in step 7, `_std_screw_step` (screw spacing). |
| Reference tests | `tests/test_bore_shape.py`, `tests/test_spline_retainer.py`, `tests/test_hex_bore.py` — their `cadquery` cases build the STEP and require one valid solid whose volume is within **5e-3** of the STL's (at print compensation 0), plus the nominal counterbore and spline radii. |
| STEP vocabulary | **no new entity types.** cadquery's STEP of every case below is LINE and CIRCLE edges on PLANE and CYLINDRICAL_SURFACE faces. |
| Units / size | mm. STEP is **nominal**: ignore `spline['print']` (print compensation is STL-only). |

## The job's new fields

`worker_kw['spline']` is `None`, or a dict:

```
kind      'straight' (ISO 14) | 'involute' (ISO 4156) | 'hex' (hex bar)
n, minor, major, width, module, pressure, root, series   — the Spline's own fields
bore      the hole's minor diameter at the default fit (a hex: across flats). = bore_mm
print     print compensation — STL only, ignore
retainer  None, or:
  faces        ['top'] | ['bottom'] | ['top', 'bottom']   — which faces get a ring
  cb_d         counterbore diameter (mm)
  cb_depth     counterbore depth (mm): the ring's groove width, plus a washer
  cover        {'top': t, 'bottom': t}   thickness of a flange / plate over that face, 0 if none
  cover_joined {'top': bool, 'bottom': bool}   whether the cadquery solid includes that flange
  (and the ring's own sizes: ring, d1, d2, m, s, n, a, d4, washer_od, washer_t — not needed for STEP)
```

Rebuild the outline with the vendored `cct_common` (0.21.0):
`splines.Spline(**{k: spline[k] for k in dataclasses.fields(Spline)})` —
`step_exporter._as_spline(spline)` does exactly this — then
`splines.path(sp)` is the hole.

## 1. A bore from any closed LINE / ARC loop — REQUIRED

Splines (ISO 14 straight-sided, ISO 4156 involute) and hex bars (sharp or
rounded corners) replace the round bore.

- **The loop**: `cct_common.splines.path(sp)` — closed, counter-clockwise,
  LINEs and circular ARCs only, the first slot / space / flat on +X.
  - straight-sided: 4 segments per slot (two walls, slot bottom arc, land arc);
  - involute: each flank split into tangent arcs within 0.001 mm of the
    involute — tens to a few hundred arcs, **centres off the axis**;
  - hex bar: 6 LINEs (sharp) or 6 LINEs + 6 on-axis ARCs (rounded corners).
- **Getting it to small_step**: the worker can already draw it —
  `generate_dxf(..., spline=params['spline'])` puts the loop on the `BORE`
  layer as LINE / ARC entities in place of the circle
  (`dxf_exporter._spline_bore_to_dxf`). small_step has to accept a `BORE`
  loop of LINE / ARC and use it everywhere the bore circle is used today:
  the caps' hole loops at every z level, the bore's side faces (a PLANE per
  LINE, a CYLINDRICAL_SURFACE per ARC — off-axis for involute flanks), and
  through the body, the hub, the spoke hub and the flanges (§2).
- **With a hub**: the hub's bore is the same loop. The app sends no
  `--flat` / `--keyway` with a spline.
- **Accept**: `test_bore_shape.py::test_cadquery_step_is_the_stl` (straight
  and involute), `test_hex_bore.py::test_cadquery_step_is_the_stl` (inch
  rounded hex, metric sharp) — the same assertions with small_step.

## 2. Flanges take the bore's shape — REQUIRED (also a keyway bug today)

When a flange's hole is the bore (`r_inner` = bore/2: no hub on that face,
no spokes), small_step revolves it with a **round** hole. That closes a
spline's slots or a hex's corners for the flange's thickness — and today,
with no spline at all, **a keyway's slot** (round bore + key: the key can't
pass the flange).

- Cut the bore's own profile through every flange the solid includes whose
  hole is the bore: the loop of §1, or the D-flat / keyway shape.
- The STL does it with `flange_exporter._revolve_through_bore` (both
  flanges); cadquery re-cuts `bore_solid` / `kw_box` after the top flange's
  union (`generate_pulley_step`, step 7).
- **Accept**: `test_spline_retainer.py::test_the_top_flange_hole_is_the_bore_shape`
  shows the rule on the STL (spline and keyway, joined and separate top flange).

## 3. Retaining-ring counterbores — REQUIRED

A DIN 471 / inch SH ring sits in a groove on the shaft at each ringed face,
flush with the part's outer face, in a counterbore.

- For each face in `retainer['faces']`: a cylinder Ø `cb_d` on the axis,
  from the **part's outer face at the bore** inward, `cb_depth` deep in
  total. Faces added: a plane annulus (the floor, between `cb_d` and the
  bore loop) and a CYLINDRICAL_SURFACE wall; below the floor the bore
  continues as §1.
- **The outer face**: the top of the hub if there is one on top (the hub
  stands through the top flange); otherwise the outer face of whatever
  flange or plate lies over that face, else the pulley's own face. A flange
  thinner than `cb_depth` is cut through and the pulley takes the rest.
- **Note for ss**: the worker passes `--top-3d` / `--top-metal` /
  `--bot-metal` whenever flanges are on — it doesn't read
  `flange_top_separate` — so the ss solid includes flanges the cadquery STEP
  leaves out (cadquery keeps only joined printed flanges; `cover_joined`
  tells it which). For ss the rule is simply: `cb_depth` from the outer face
  of its own solid, through whatever it includes. (Whether a *separate* top
  flange belongs in the pulley's STEP at all is a question for the owner.)
- Sizes: `cb_d` stays at least 1 mm inside the tooth root / hub / spoke hub
  (the app warns and has Auto-fix); `cb_depth` is well under the part height.
- Suggested CLI: `--counterbore top|bottom <dia_mm> <depth_mm>`, repeatable.
- **Accept**: `test_spline_retainer.py` — `test_cadquery_step_counterbore_is_the_stl`,
  `…_bore_and_counterbore_stay_nominal`, `…_counterbore_under_flanges`
  (joined top, separate top, metal bottom), `…_both_faces`;
  `test_hex_bore.py::test_cadquery_step_hex_with_a_captured_nut` (two rings).

## 4. Set screws on a hex bar — REQUIRED with §1

A spline takes no set screw (the app sends none). A **hex bar** takes one or
two, each landing on a flat:

| | first screw | second screw |
|---|---|---|
| captured nut | 0° (the +X flat) | **180°** (the opposite flat) — as today |
| threaded / heat-set insert | 0° | **120°** (the next-but-one flat) — today small_step uses 90°, which is a hex's corner |

- The +X flat of the hole is a plane at x = `bore_mm` / 2 — the bore
  radius — so each hole is placed as on a round bore at that angle and ends
  on a flat plane; a captured nut's pocket sits against the flat as against
  a round bore.
- Suggested CLI: `--screw-step <deg>` (default 90; the worker sends 120 for
  a hex with a non-captured screw), or an explicit angle list.
- Reference: `step_exporter._std_screw_step`. **Accept**:
  `test_hex_bore.py::test_a_hex_bar_takes_set_screws_on_its_flats` (the STL:
  holes only at the angles above), `…::test_cadquery_step_hex_with_a_captured_nut`.

## 5. The app side, once small_step has 1–4

In PulleyWebApp-ss (these are small; the PulleyWebApp session can do them):

1. `app.py::_run_ss_worker`: drop the `_has_spline` refusal.
2. `step_worker_ss._generate_pulley_bytes`: pass `spline=params.get('spline')`
   to `generate_dxf`; add the `--counterbore` flags from `spline['retainer']`;
   the hex screw spacing flag; bump `SMALL_STEP_MIN_VERSION`.
3. `fuzz_pulley.py::_splines_on()` — splines and hex bars are drawn only for
   the cadquery backend today; include small_step.
4. The STEP tests above are `cadquery`-only (`importorskip('cadquery')` +
   `PULLEY_STEP_BACKEND=cadquery`); give them a small_step run with the same
   tolerances.

## 6. Changed inputs — nothing to build

- `bore_mm` for a spline or hex bar is the **fitted** hole (a few hundredths
  over nominal) — use it as given.
- The spoke hub OD in the job is now fitted round the bore's **reach** (a
  spline's major, a hex's corners), not its minor — as given.
- A ring can be on either face or both (`retainer['faces']`).

## 7. Not needed for STEP

- The **sample splined shaft** and **splined washer**: STL / SVG / DXF only;
  the download window offers no STEP for them. (If wanted later: the shaft
  is an extruded `splines.shaft_path` with a round groove cut at each ringed
  face; the washer a disc less `splines.path`.)
- The Spline card, the 3D preview's shaft / rings / washers, Auto-fix, the
  Dimensions rows, undo, the hidden 2D button — all app-side.

## Suggested order

§1 (the loop as a bore, no flanges) → §2 (through flanges; fixes keyways
too) → §3 (counterbores) → §4 (hex screws) → §5. Each step has cadquery
tests to match.
