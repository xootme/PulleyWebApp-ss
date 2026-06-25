import sys, os, math, re
sys.path.insert(0, ".")
from exporters.dxf_exporter import generate_dxf
from geometry.pulley_geometry import PULLEY_SPECS, PROFILE_KEY_PREFIX, getOuterDiameter

family, pitch, teeth = "GT", "5M", 34
bore = 14.4; pr_ex = 0.23; cl_mm = 0.2

key = PROFILE_KEY_PREFIX.get(family, "") + pitch
spec = PULLEY_SPECS[key]
pld = spec.get("pitch_line_diff", spec.get("pitchLineDiff", 0.0))
R_OD = getOuterDiameter(teeth, spec["pitch"], pld + pr_ex - cl_mm) / 2.0
ht = spec["tooth_ht"]
R_tr = R_OD - ht
R_rim = R_tr - 3.5
hub_r = 18.5 / 2.0
print(f"R_OD={R_OD:.3f}  tooth_ht={ht}  R_tr={R_tr:.3f}  R_rim={R_rim:.3f}  hub_r={hub_r:.3f}")

spoke_half = math.degrees(math.asin(min(7.1 / 2.0 / hub_r, 1.0)))
gap = 360.0 / 7 - 2 * spoke_half
print(f"spoke half-width at hub: {spoke_half:.1f} deg  gap: {gap:.1f} deg")

dxf = generate_dxf(
    family=family, pitch=pitch, num_teeth=teeth,
    bore_mm=bore, clearance_mm=cl_mm, backlash_mm=0.0, print_extra_mm=pr_ex,
    spoke_count=7, spoke_width_mm=7.1, spoke_hub_od_mm=18.5,
    rim_depth_mm=3.5, fillet_tip_mm=0.5, fillet_base_mm=1.8,
    flat_depth_mm=0.0, keyway_w_mm=0.0, keyway_h_mm=0.0,
)
if isinstance(dxf, bytes):
    dxf = dxf.decode()

# Count SPOKES layer arcs
spokes_section = ""
for line in dxf.split("\n"):
    spokes_section += line + "\n"

arc_blocks = re.findall(r"ARC\n[\s\S]+?(?=\n  0\n|\Z)", dxf)
print(f"\nTotal ARC entities: {len(arc_blocks)}")

for i, a in enumerate(arc_blocks):
    layer = re.search(r"\n  8\n(.+)", a)
    cx = re.search(r"\n 10\n\s*([\-0-9.eE+]+)", a)
    cy = re.search(r"\n 20\n\s*([\-0-9.eE+]+)", a)
    r_m = re.search(r"\n 40\n\s*([\-0-9.eE+]+)", a)
    a0 = re.search(r"\n 50\n\s*([\-0-9.eE+]+)", a)
    a1 = re.search(r"\n 51\n\s*([\-0-9.eE+]+)", a)
    if cx and cy and r_m:
        lay = layer.group(1).strip() if layer else "?"
        print(f"  arc[{i}] layer={lay} cx={float(cx.group(1)):.3f} cy={float(cy.group(1)):.3f} "
              f"r={float(r_m.group(1)):.3f} a0={float(a0.group(1)) if a0 else '?':.1f} a1={float(a1.group(1)) if a1 else '?':.1f}")
