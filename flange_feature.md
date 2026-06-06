# Flange Feature Specification

## Overview

A flange is an annular plate mounted on a pulley that prevents the belt from sliding off axially. The flange begins at the pulley's tooth OD.

---

## Inner Rim Rule (all flange types, top and bottom)

The flange plate extends inward no further than the innermost applicable boundary:

1. **Spokes active** → inner face of the solid rim ring = R_tooth_OD − Rim Depth
2. **No spokes, hub present** → hub OD
3. **No spokes, no hub** → bore diameter

This rule applies to every flange — metal or 3D print, top or bottom. When spokes
are active the flange covers only the solid rim ring; it does not span the spoke web
or hub boss area. The bottom 3D-print flange is merged into the pulley body, so the
*combined* solid spans from the flange OD to the bore, but the flange plate itself
still stops at the inner face of the rim ring.

---

## ☐ 3D Print

Unless stated otherwise, all settings apply to both the top and bottom flanges.

---

## Metal Flanges (3D Print unchecked)

Both a top and a bottom flange are always generated as separate parts.

**Cross-section** (inner rim to outer rim): a flat inner annular section sits at tooth-top height, bends upward at the Bend Radius, then continues at Flange Angle degrees above horizontal to Rim Radius beyond the tooth OD. The outer edge is a rectangular cut with a height equal to Plate Height.

- **Plate Height** — Material/stock thickness of the metal plate. The outer rim of the plate is bent upward to create the flange angle.
- **Flange Angle** — 8–25°, measured from horizontal. The flange flares outward-upward away from the pulley teeth. Vertical rise = Rim Radius × tan(Flange Angle).
- **Bend Radius** — The radius of the bend where the flat inner section transitions to the angled section, measured as horizontal distance from the tooth OD. Default: 1.5 × Plate Height.
- **Rim Radius** — Radial distance the angled section extends beyond the tooth OD.

### Inner rim behavior

Both the top and bottom plates follow the inner rim rule above. When spokes are active, both plates stop at R_tooth_OD − Rim Depth (the inner face of the solid rim ring). When spokes are not active, the bottom plate extends to the bore and the top plate extends to the hub OD.

### Hub intersection warning

A warning and a "Top Flange Cuts Hub" checkbox appear when either condition is true:

- Spokes are active and the hub OD extends inward past the spokes' inner rim, or
- Spokes are not active and the hub OD encroaches within 10 mm of the groove-bottom circle — that is, the top plate's minimum inner hole diameter is max(D_groove_bottom − 10 mm, D_bore), and the hub OD exceeds this boundary.

**Top Flange Cuts Hub** (default: checked) — the flange geometry cuts into the hub solid. If unchecked, the hub cuts into the flange geometry.

### Downloads

Each plate is generated as a separate file — `-upper-flange` and `-lower-flange`.

---

## 3D Print Flanges (3D Print checked)

The 3D-printed flange is a solid wedge of material. Cross-section: starts at the tooth OD, rises at Flange Angle degrees above horizontal for Rim Radius, then terminates in a vertical lip of height Flange Height.

- **Flange Angle** — 8–25° from horizontal. The top flange flares upward; the bottom flange flares downward.
- **Rim Radius** — Radial distance the flange extends beyond the tooth OD.
- **Flange Height** — Height (in Z) of the vertical lip at the outer rim, where the angled section ends.

### Inner rim behavior

Both the top and bottom flanges follow the inner rim rule above. When spokes are active, both flanges stop at R_tooth_OD − Rim Depth (the inner face of the solid rim ring). The flange plate does not extend into the spoke web or hub boss area.

### Bottom flange

The bottom flange plate stops at the inner face of the rim ring (or hub OD, or bore — per the inner rim rule). It is permanently merged into the pulley body. The *combined* solid spans from the flange OD to the bore, but this is a consequence of the union with the pulley body, not the flange plate geometry itself.

### ☐ Generate Top Flange as Separate Part (default: checked)

- **If checked:** The top flange is exported as a separate file with the suffix `-upper-flange`. If the hub intersects the flange, the hub cuts the flange. In the 3D preview, the top flange is displayed 20 mm above the top of the pulley hub.
- **If unchecked:** The top flange is merged into the pulley body as one combined solid from the flange OD inward to the bore.

---

## Gluing Nubs (3D Print, separate top flange only)

### ☐ Add Gluing Nubs

Nubs are cylindrical pegs extending from the underside of the top flange into matching sockets in the pulley body. Their purpose is to increase bonding strength between the top flange and the pulley. A socket is always cut into the pulley body for every nub.

**Nub placement:** The nub outer edge sits at R_groove_bottom − min(tooth_height, 3 mm), so nub centers lie on a circle of radius R_groove_bottom − min(tooth_height, 3 mm) − nub_diameter / 2. If a nub extends inward past the spokes' inner rim, the nub is clipped at that boundary. If a nub extends inward past the spoke outer rim (the inner face of the solid rim ring, at R_OD − Rim Depth), the nub is clipped at that boundary as well.

- **Number of Nubs** — Equally spaced around the nub center circle. If adjacent nubs overlap, they are merged into one shape (boolean union).
- **Nub Height** — Defines the socket depth cut into the pulley body. The physical nub on the flange is shorter by the Socket Fit Allowance height component, leaving a small gap at the base of the socket when fully seated. Minimum: 1 mm. Maximum: 1/3 of belt height.
- **Nub Diameter** — Diameter of each nub and its matching socket.
- **Socket Fit Allowance** — A single value subtracted from both the nub diameter and nub height (but not from the socket dimensions), so the printed nub slides into the socket with clearance.

### ⚠️ Nubs and Spokes: Height Constraint

When **spokes are enabled**, the nub height must be **at least equal to the spoke height** to fully connect the flange to the spoke structure.

**Why:** 
- Spokes extend upward from the hub by *Spoke Height* mm
- The flange sits on top of the hub at height = *Hub Height*
- Nubs extend downward from the flange
- For nubs to reach and grip the spokes, they must traverse the spoke height

**Example (from bug report):**
- Hub height: 10 mm
- Spoke height: 6 mm (spokes reach to 10+6=16 mm from ground)
- Flange nub height: 2 mm (nubs only go from 10 mm down to 8 mm)
- **Result:** Gap between nubs (8 mm) and spokes (16 mm) → nubs don't connect ❌

**Solution:** Set **Nub Height ≥ Spoke Height** when both are enabled.

For the example above: increase Nub Height from 2 mm to **at least 6 mm** to fully engage the spokes.
