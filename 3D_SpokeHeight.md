# 3D Spoke Height — Research Notes

## Goal
Spoke voids in the 3D preview/STL should be centred at `belt_height / 2`.  
`spoke_height` controls the total axial depth of the voids; equal solid material
is left on the toothed face and the flat/hub face.

Example: belt_height=10, spoke_height=5 → voids from Z=2.5 to Z=7.5.

---

## What Works (Full Height — spoke_height == belt_height)

1. Build `spoke_poly` in 2D:  
   Start with `outer_poly` (full pulley cross-section), subtract each spoke gap
   polygon from `_spoke_void_polygons()` using Shapely `.difference()`.  
   Apply `simplify(0.05, preserve_topology=True).buffer(0)` to each gap polygon
   before subtracting to avoid near-duplicate arc vertices failing earcut.

2. Extrude `spoke_disk = extrude_polygon(spoke_poly, belt_height_mm)` → watertight ✓

3. `voids_full = diff(body.copy(), spoke_disk)` → spoke gap volumes, watertight ✓

4. `body = diff(body, voids_full)` → spokes visible in 3D preview ✓

Debug confirmed:  
`spoke_disk.wt=True  body.wt=True  voids_full.faces=(544,3)  wt=True  final_body.faces=26572`

---

## What Does NOT Work (Partial Height — spoke_height < belt_height)

Every approach tried produces a 200 response with ~26556 faces on the final body
(close to the working 26572), but the spoke holes are not visible. The boolean
operations return silently without error but the holes are gone.

### Approach 1 — clip_box intersection
Compute `voids_full` (full height), then intersect with a box of height `spk_h`.
Result: silently returns empty or incorrect mesh. Spokes invisible.

### Approach 2 — Z-scale
`scale_m[2,2] = spk_h / belt_height_mm` applied to `voids_full`, then subtract.  
Debug: voids_full.faces=(544,3) before AND after scale (topology unchanged).  
Final body.faces=26556. Spokes invisible. The manifold diff runs but holes vanish.

### Approach 3 — translate to top face
`voids_full.apply_translation([0,0, belt_height - spk_h])` then subtract.  
Spokes ARE visible from the flat/hub face — but with solid fill at the floor
(equal to `belt_height - spk_h`). This proves translation alone works for depth
but puts voids on one face only, not centred.

### Approach 4 — Z-scale then centre-translate
Scale voids to spk_h, then translate by `(belt_height - spk_h) / 2`.  
Spokes invisible again. The scale step apparently kills the boolean.

### Approach 5 — direct slab (latest, also failed)
Build `slab_solid = extrude(outer_poly, spk_h)` translated to `slab_offset`.  
Build `slab_spoke = extrude(spoke_poly, spk_h)` translated to `slab_offset`.  
`voids_slab = diff(slab_solid, slab_spoke)`, then `body = diff(body, voids_slab)`.  
Spokes invisible. Did not add debug prints for this attempt.

---

## Key Observations

- The translate-only approach (Approach 3) proved the manifold diff CAN cut
  voids into the body at a non-zero Z offset. Spokes were visible.  
- The Z-scale appears to silently break the manifold input validity, even though
  `is_watertight` reports True after scaling.
- `spoke_poly` extruded to `belt_height` is watertight; it is unknown whether
  extruding to a shorter height (e.g. spk_h=5) produces a watertight mesh.

---

## Promising Next Steps

### Option A — "Add back" caps onto the working full-height result
Instead of trying to clip the voids, compute full-height voids (works), then
UNION solid caps above and below the spoke zone back onto the body:

```python
body_voided = diff(body, voids_full)          # full height spokes — works
cap_h = (belt_height - spk_h) / 2.0
if cap_h > 0:
    top_cap = extrude(outer_poly, cap_h)
    top_cap.apply_translation([0, 0, spk_h + cap_h])   # above spoke zone
    bot_cap = extrude(outer_poly, cap_h)
    # bot_cap sits at Z=0 by default                    # below spoke zone
    body = union([body_voided, top_cap, bot_cap])
```
This fills the outer ring above and below the spoke zone, leaving voids only in
the middle. Avoids Z-scaling entirely.

### Option B — debug slab approach with prints
Add prints for `slab_solid.wt`, `slab_spoke.wt`, `voids_slab.faces`,
`voids_slab.wt` before the final diff. If `voids_slab` is empty or non-watertight
that explains the failure.

### Option C — subtract individual gap shapes at spk_h directly
Instead of diff(slab_solid, slab_spoke), loop over each gap polygon, extrude it
to `spk_h + 2*eps`, translate to `slab_offset - eps`, and subtract each from body.
This avoids the polygon-with-holes extrusion problem entirely. The gap polygon
extrusions are simple shapes (no interior rings).

---

## Related Code Locations

| File | Function | Notes |
|------|----------|-------|
| `exporters/step_exporter.py` | `generate_pulley_stl` | Single pulley STL; spoke block ~line 424 |
| `exporters/step_exporter.py` | `_build_pulley_mesh` | Dual pulley preview; spoke block ~line 920 |
| `exporters/png_exporter.py` | `_spoke_void_polygons` | Returns tessellated fillet polygon point lists |
| `app.py` | `_parse_spoke_params` | Parses spoke params including `spokes_height` |

## Parameter Flow
`spokes_height` → `_parse_spoke_params` → `sp_h` → `generate_pulley_stl_preview(spoke_height_mm=sp_h)` → `generate_pulley_stl(spoke_height_mm=...)`.

Empty string input is handled: `float(args.get(...) or 0.0)` — 0.0 means full height.
