import sys
sys.path.insert(0, ".")
from exporters.dxf_exporter import generate_dxf

dxf = generate_dxf(
    family="GT", pitch="5M", num_teeth=34,
    bore_mm=14.4, clearance_mm=0.2, backlash_mm=0.0, print_extra_mm=0.23,
    spoke_count=7, spoke_width_mm=7.1, spoke_hub_od_mm=18.5,
    rim_depth_mm=3.5, fillet_tip_mm=0.5, fillet_base_mm=1.8,
    flat_depth_mm=0.0, keyway_w_mm=0.0, keyway_h_mm=0.0,
)
if isinstance(dxf, bytes):
    dxf = dxf.decode()

out = r"C:\Users\cmyer\Documents\PulleyWebApp-ss\logs\debug_spoke_fail.dxf"
with open(out, "w") as f:
    f.write(dxf)
print(f"Saved to: {out}")
