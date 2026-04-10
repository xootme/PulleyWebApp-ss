"""
pulley_geometry.py
Pure-Python timing belt pulley groove geometry engine.
Extracted from SketchTimingPulley (Fusion 360 add-in) — no Autodesk API dependencies.

Usage:
    from geometry.pulley_geometry import (
        PULLEY_SPECS, PROFILE_KEY_PREFIX,
        generate_profile_groove, _build_groove_points,
        _filter_min_spacing, getPitchDiameter, getOuterDiameter,
        wrap_groove_to_pulley
    )
"""
import math
from typing import List, Tuple


# ==========================================
# PULLEY PROFILE PARAMETER TABLE
# All dimensions are in mm.
# HTD/GT: ISO 13050 Tables 9 & 14 (curvilinear arc-flank geometry)
# STD/S-series: ISO 13050 Table 32 (S8M/S14M); estimated for S2M/S3M/S5M
# Imperial: ANSI/RMA IP-24 (trapezoidal, 40° included angle)
# T-series: ISO 17396:2017 (trapezoidal, 50° included angle)
# AT-series: ISO 17396 (trapezoidal, 50° included angle)
# ==========================================
PULLEY_SPECS = {
    "3M": {
        # ISO 13050 Table 9: belt ht=1.21mm. Hg ≈ belt_ht across all ranges → tight_clear=0.
        # Backlash tight unresolvable from tabulated curvilinear params; tight=0 (exact ISO groove).
        # Loose: clear=5%×Hg; backlash=2.5%×R1 (mid-range R1≈0.91)
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.02},
        "pitch": 3.0,
        "tooth_ht": 1.21,
        "pitch_line_diff": 0.381,
        "min_teeth": 10,
        "clearances": {
            "TIGHT":  0.000,
            "STANDARD":  0.000,
            "LOOSE":     0.060,
        },
        "ranges": [
            {"min": 10, "max": 13, "Hg": 1.190, "X": 0.029, "R1": 0.991, "phi": 15.0, "R2": 0.181},
            {"min": 14, "max": 25, "Hg": 1.179, "X": 0.112, "R1": 0.889, "phi": 9.0, "R2": 0.229},
            {"min": 26, "max": 80, "Hg": 1.219, "X": 0.028, "R1": 0.927, "phi": 8.0, "R2": 0.191},
            {"min": 81, "max": 999, "Hg": 1.234, "X": 0.074, "R1": 0.925, "phi": 4.0, "R2": 0.301},
        ]
    },
    "5M": {
        # ISO 13050 Table 9: belt ht=2.08mm. All Hg ranges < 2.08mm → tip crush → tight_clear=0.
        # Backlash tight unresolvable from tabulated curvilinear params; tight=0 (exact ISO groove).
        # Loose: clear=5%×Hg≈2.03; backlash=2.5%×R1 (mid-range R1≈1.38)
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.03},
        "pitch": 5.0,
        "tooth_ht": 2.08,
        "pitch_line_diff": 0.572,
        "min_teeth": 12,
        "clearances": {
            "TIGHT":  0.000,
            "STANDARD":  0.000,
            "LOOSE":     0.100,
        },
        "ranges": [
            {"min": 12, "max": 16, "Hg": 1.989, "X": 0.307, "R1": 1.265, "phi": 10.0, "R2": 0.432},
            {"min": 17, "max": 25, "Hg": 2.009, "X": 0.320, "R1": 1.270, "phi": 6.0, "R2": 0.508},
            {"min": 26, "max": 80, "Hg": 2.052, "X": 0.081, "R1": 1.438, "phi": 2.0, "R2": 0.488},
            {"min": 81, "max": 999, "Hg": 2.056, "X": 0.028, "R1": 1.552, "phi": 5.0, "R2": 0.569},
        ]
    },
    "8M": {
        # ISO 13050 Table 9: belt ht=3.38mm. 28-89T Hg=3.604 → gap 0.22mm → tight=−0.22.
        # Backlash tight unresolvable from tabulated curvilinear params; tight=0 (exact ISO groove).
        # Loose: clear=5%×Hg≈3.60; backlash=2.5%×R1 (28-89T R1=2.629)
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.07},
        "pitch": 8.0,
        "tooth_ht": 3.38,
        "pitch_line_diff": 0.686,
        "min_teeth": 15,
        "clearances": {
            "TIGHT": -0.220,
            "STANDARD":  0.000,
            "LOOSE":     0.180,
        },
        "ranges": [
            {"min": 22, "max": 27, "Hg": 3.295, "X": 0.000, "R1": 2.675, "phi": 11.3, "R2": 0.874},
            {"min": 28, "max": 89, "Hg": 3.604, "X": 0.000, "R1": 2.629, "phi": 7.0, "R2": 1.024},
            {"min": 90, "max": 999, "Hg": 3.630, "X": 0.000, "R1": 2.639, "phi": 6.6, "R2": 1.008},
        ]
    },
    "14M": {
        # ISO 13050 Table 9: belt ht=6.02mm. All Hg ranges > 6.02mm → gap 0.18–0.31mm → tight=−0.18.
        # Backlash tight unresolvable from tabulated curvilinear params; tight=0 (exact ISO groove).
        # Loose: clear=5%×Hg≈6.20; backlash=2.5%×R1 (37-57T R1=4.737)
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.12},
        "pitch": 14.0,
        "tooth_ht": 6.02,
        "pitch_line_diff": 1.397,
        "min_teeth": 18,
        "clearances": {
            "TIGHT": -0.180,
            "STANDARD":  0.000,
            "LOOSE":     0.310,
        },
        "ranges": [
            {"min": 28, "max": 32, "Hg": 6.327, "X": 0.000, "R1": 4.859, "phi": 7.1, "R2": 1.544},
            {"min": 33, "max": 36, "Hg": 6.328, "X": 0.000, "R1": 4.834, "phi": 5.2, "R2": 1.613},
            {"min": 37, "max": 57, "Hg": 6.198, "X": 0.000, "R1": 4.737, "phi": 9.3, "R2": 1.654},
            {"min": 58, "max": 89, "Hg": 6.198, "X": 0.000, "R1": 4.669, "phi": 8.9, "R2": 1.902},
            {"min": 90, "max": 153, "Hg": 6.328, "X": 0.000, "R1": 4.636, "phi": 6.9, "R2": 1.704},
            {"min": 154, "max": 999, "Hg": 6.327, "X": 0.000, "R1": 4.597, "phi": 8.6, "R2": 1.770},
        ]
    },
    "20M": {
        # ISO 13050 Table 9: belt ht=8.68mm. Hg≈8.65–8.70 ≈ belt_ht → tight_clear≈0.
        # Backlash tight unresolvable from tabulated curvilinear params; tight=0 (exact ISO groove).
        # Loose: clear=5%×Hg≈8.67; backlash=2.5%×R1=6.185
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.16},
        "pitch": 20.0,
        "tooth_ht": 8.68,
        "pitch_line_diff": 2.159,
        "min_teeth": 24,
        "clearances": {
            "TIGHT":  0.000,
            "STANDARD":  0.000,
            "LOOSE":     0.430,
        },
        "ranges": [
            {"min": 32, "max": 45, "Hg": 8.649, "X": 0.544, "R1": 6.185, "phi": 15.0, "R2": 2.184},
            {"min": 46, "max": 100, "Hg": 8.661, "X": 0.544, "R1": 6.185, "phi": 10.0, "R2": 2.540},
            {"min": 101, "max": 999, "Hg": 8.700, "X": 0.544, "R1": 6.185, "phi": 18.0, "R2": 2.540},
        ]
    },
    # ==========================================
    # STD / S-series — ISO 13050 Table 32 (S8M, S14M) / estimated (S2M, S3M, S5M)
    # Bg:  distance along pitch line between the two R1 flank origin points
    # R1:  flank arc radius (centre = flank origin at ±Bg/2 on pitch line)
    # R2:  tip fillet (external tangency with R1, tangent to OD land)
    # R4:  root corner fillet (internal tangency with R1, external tangency with R5)
    # R5:  groove floor arc — convex dome, centre at (0, -(Hg+R5)), peak at (0, -Hg)
    #      S8M/S14M: ISO 13050 Table 32.  S2M/S3M/S5M: scaled by pitch ratio from S8M/S14M
    # ==========================================
    "S2M": {
        # Scaled params — no ISO Table 27 data. Belt_ht estimated ≥ Hg → tip crush → tight_clear=0.
        # Bg=belt_S (scaled 0.65×pitch) → tight_backlash=0. Loose: clear=5%×0.71; bl=2.5%×R1=1.33
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.03},
        "pitch": 2.0, "tooth_ht": 0.71, "pitch_line_diff": 0.118, "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.040},
        "Bg": 1.30, "R1": 1.33, "R2": 0.19, "R4": 0.10, "R5": 1.01,
    },
    "S3M": {
        # Scaled params — no ISO Table 27 data. Belt_ht estimated ≥ Hg → tip crush → tight_clear=0.
        # Bg=belt_S (scaled 0.65×pitch) → tight_backlash=0. Loose: clear=5%×1.06; bl=2.5%×R1=1.99
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.05},
        "pitch": 3.0, "tooth_ht": 1.06, "pitch_line_diff": 0.197, "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.050},
        "Bg": 1.95, "R1": 1.99, "R2": 0.28, "R4": 0.15, "R5": 1.52,
    },
    "S5M": {
        # Scaled params — no ISO Table 27 data. Belt_ht estimated ≥ Hg → tip crush → tight_clear=0.
        # Bg=belt_S (scaled 0.65×pitch) → tight_backlash=0. Loose: clear=5%×1.77; bl=2.5%×R1=3.31
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.08},
        "pitch": 5.0, "tooth_ht": 1.77, "pitch_line_diff": 0.377, "min_teeth": 12,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.090},
        "Bg": 3.25, "R1": 3.31, "R2": 0.47, "R4": 0.25, "R5": 2.52,
    },
    "S8M": {
        # ISO 13050 Table 27: belt_ht=3.05mm > Hg=2.83mm → tip crush → tight_clear=0.
        # ISO Table 32: Bg1=5.20=belt_S → tight_backlash=0. Loose: clear=5%×2.83; bl=2.5%×R1=5.30
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.13},
        "pitch": 8.0, "tooth_ht": 2.83, "pitch_line_diff": 0.686, "min_teeth": 22,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.140},
        "Bg": 5.20, "R1": 5.30, "R2": 0.75, "R4": 0.40, "R5": 4.04,
    },
    "S14M": {
        # ISO 13050 Table 27: belt_ht=5.30mm > Hg=4.95mm → tip crush → tight_clear=0.
        # ISO Table 32: Bg1=9.10=belt_S → tight_backlash=0. Loose: clear=5%×4.95; bl=2.5%×R1=9.28
        "backlash": {"TIGHT": 0.00, "STANDARD": 0.00, "LOOSE": 0.23},
        "pitch": 14.0, "tooth_ht": 4.95, "pitch_line_diff": 1.397, "min_teeth": 28,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.250},
        "Bg": 9.10, "R1": 9.28, "R2": 1.31, "R4": 0.70, "R5": 7.07,
    },
    # ==========================================
    # Gates PowerGrip GT2/GT3 (curvilinear, same profile)
    # Parameters from droftarts OpenSCAD / Gates engineering data
    # ==========================================
    "GT2M": {
        # GT: STANDARD = Gates designed-in clearance. Tight=groove→belt (est. gap≈0.025×pitch=0.05).
        # Loose: clear=STD+5%×Hg=0.048; backlash=STD+2.5%×R1=0.555
        "backlash": {"TIGHT": -0.050, "STANDARD": 0.030, "LOOSE": 0.04},
        "pitch": 2.0, "tooth_ht": 0.764, "pitch_line_diff": 0.254, "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.010, "LOOSE": 0.050},
        "ranges": [{"min": 10, "max": 999, "Hg": 0.764, "X": 0.000, "R1": 0.555, "phi": 14.0, "R2": 0.150}]
    },
    "GT3M": {
        # GT: STANDARD = Gates designed-in clearance. Tight=groove→belt (est. gap≈0.025×pitch=0.075).
        # Loose: clear=STD+5%×Hg=0.067; backlash=STD+2.5%×R1=0.955
        "backlash": {"TIGHT": -0.075, "STANDARD": 0.030, "LOOSE": 0.05},
        "pitch": 3.0, "tooth_ht": 1.143, "pitch_line_diff": 0.381, "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.010, "LOOSE": 0.070},
        "ranges": [{"min": 10, "max": 999, "Hg": 1.143, "X": 0.000, "R1": 0.955, "phi": 14.0, "R2": 0.250}]
    },
    "GT5M": {
        # GT: STANDARD = Gates designed-in clearance. Tight=groove→belt (est. gap≈0.025×pitch=0.125).
        # Loose: clear=STD+5%×Hg=0.109; backlash=STD+2.5%×R1=1.542
        "backlash": {"TIGHT": -0.125, "STANDARD": 0.050, "LOOSE": 0.09},
        "pitch": 5.0, "tooth_ht": 1.778, "pitch_line_diff": 0.5715, "min_teeth": 12,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.020, "LOOSE": 0.110},
        "ranges": [{"min": 12, "max": 999, "Hg": 1.778, "X": 0.000, "R1": 1.542, "phi": 14.0, "R2": 0.400}]
    },
    "GT8M": {
        # ISO 13050 Table 1/6: belt bg=5.20, pulley bg=5.40 → gap 0.20mm → tight_bl=−0.20.
        # Belt hg=pulley hg=3.43mm → tight_clear=0. Loose: clear=STD+5%×Hg; bl=STD+2.5%×R1=2.471
        "backlash": {"TIGHT": -0.200, "STANDARD": 0.080, "LOOSE": 0.14},
        "pitch": 8.0, "tooth_ht": 2.845, "pitch_line_diff": 0.686, "min_teeth": 20,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.030, "LOOSE": 0.170},
        "ranges": [{"min": 20, "max": 999, "Hg": 2.845, "X": 0.000, "R1": 2.471, "phi": 14.0, "R2": 0.640}]
    },
    "GT14M": {
        # ISO 13050 Table 1/6: belt bg=9.10, pulley bg=9.45 → gap 0.35mm → tight_bl=−0.35.
        # Belt hg=pulley hg=6.00mm → tight_clear=0. Loose: clear=STD+5%×Hg; bl=STD+2.5%×R1=4.324
        "backlash": {"TIGHT": -0.350, "STANDARD": 0.120, "LOOSE": 0.23},
        "pitch": 14.0, "tooth_ht": 4.978, "pitch_line_diff": 1.397, "min_teeth": 24,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.050, "LOOSE": 0.300},
        "ranges": [{"min": 24, "max": 999, "Hg": 4.978, "X": 0.000, "R1": 4.324, "phi": 14.0, "R2": 1.120}]
    },
    # ==========================================
    # Imperial trapezoidal profiles — ISO 5294:2012 Table 2
    # tooth_ht   = hg  (groove depth = belt tooth height)
    # land_width = pitch - bw - 2*hg*tan(Phi)  (bw=ISO floor width, Phi=half-angle)
    # tip_radius = rt  (OD corner fillet, ISO minimum value)
    # root_radius= rb  (root fillet, ISO maximum; capped where rb+rt > hg)
    # tooth_angle_deg = 2*Phi  (full included angle between flanks)
    # pitch_line_diff = a = (ISO 2a)/2
    #
    # Belt tooth dimensions (ht, hs, Lr, h_p) derived from:
    #   Fenner Drives — Timing Belt Technical Handbook
    #     https://www.fennerdrives.com/uploadedFiles/PowerTwist/Timing%20Belt%20Technical%20Handbook.pdf
    #   Gates Corporation — PowerGrip Drive Design Manual
    #     https://www.gates.com/us/en/knowledge-center/resource-library/powergrip-drive-design-manual.html
    "MXL": {
        # Belt ht=0.51mm (Gates); Hg=0.690 → gap 0.18mm → tight_cl=−0.18.
        # groove_open=1.342, belt_root=1.14 → tight_bl=−0.20.
        # Loose: clear=5%×0.690; bl=2.5%×w_center (w_center=1.091, Phi=20°)
        "backlash": {"TIGHT": -0.200, "STANDARD": 0.000, "LOOSE": 0.03},
        "pitch": 2.032,
        "tooth_ht": 0.690,         # ISO hg
        "pitch_line_diff": 0.254,  # ISO a = 2a/2 = 0.508/2
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.180, "STANDARD": 0.000, "LOOSE": 0.040},
        # ISO 5294:2012 Table 2: bw=0.84, hg=0.69, Phi=20, rb=0.25, rt=0.13
        "land_width": 0.690,       # pitch - bw - 2*hg*tan(20°) = 2.032 - 0.84 - 0.502; groove_opening = 1.342
        "tip_radius": 0.130,       # rt (ISO minimum)
        "root_radius": 0.250,      # rb (ISO maximum)
        "tooth_angle_deg": 40.0,   # 2*Phi = 2*20°
        # Legacy fields
        "tooth_base_width": 1.14,
        "ranges": [{"min": 10, "max": 999, "Hg": 0.690, "X": 0.0, "R1": 0.0, "phi": 40.0, "R2": 0.250}],
    },
    "XL": {
        # Belt ht=1.27mm (Gates); Hg=1.650 → gap 0.38mm → tight_cl=−0.38.
        # groove_open=2.859, belt_root=2.57 → tight_bl=−0.29.
        # Loose: clear=5%×1.650; bl=2.5%×w_center (w_center=2.090, Phi=25°)
        "backlash": {"TIGHT": -0.290, "STANDARD": 0.000, "LOOSE": 0.05},
        "pitch": 5.08,
        "tooth_ht": 1.650,         # ISO hg
        "pitch_line_diff": 0.254,  # ISO a = 2a/2 = 0.508/2
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.380, "STANDARD": 0.000, "LOOSE": 0.080},
        # ISO 5294:2012 Table 2: bw=1.32, hg=1.65, Phi=25, rb=0.41, rt=0.64
        "land_width": 2.221,       # pitch - bw - 2*hg*tan(25°) = 5.08 - 1.32 - 1.539; groove_opening = 2.859
        "tip_radius": 0.640,       # rt (ISO minimum)
        "root_radius": 0.410,      # rb (ISO maximum)
        "tooth_angle_deg": 50.0,   # 2*Phi = 2*25°
        # Legacy fields
        "tooth_base_width": 2.57,
        "ranges": [{"min": 10, "max": 999, "Hg": 1.650, "X": 0.0, "R1": 0.0, "phi": 50.0, "R2": 0.410}],
    },
    "L": {
        # Belt ht=1.91mm (Gates); Hg=2.670 → gap 0.76mm → tight_cl=−0.76.
        # groove_open=4.994, belt_root=4.65 → tight_bl=−0.34.
        # Loose: clear=5%×2.670; bl=2.5%×w_center (w_center=4.022, Phi=20°)
        "backlash": {"TIGHT": -0.340, "STANDARD": 0.000, "LOOSE": 0.10},
        "pitch": 9.525,
        "tooth_ht": 2.670,         # ISO hg
        "pitch_line_diff": 0.381,  # ISO a = 2a/2 = 0.762/2
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.760, "STANDARD": 0.000, "LOOSE": 0.130},
        # ISO 5294:2012 Table 2: bw=3.05, hg=2.67, Phi=20, rb=1.19, rt=1.17
        "land_width": 4.531,       # pitch - bw - 2*hg*tan(20°) = 9.525 - 3.05 - 1.944; groove_opening = 4.994
        "tip_radius": 1.170,       # rt (ISO minimum)
        "root_radius": 1.190,      # rb (ISO maximum)
        "tooth_angle_deg": 40.0,   # 2*Phi = 2*20°
        # Legacy fields
        "tooth_base_width": 4.65,
        "ranges": [{"min": 10, "max": 999, "Hg": 2.670, "X": 0.0, "R1": 0.0, "phi": 40.0, "R2": 1.190}],
    },
    "H": {
        # Belt ht=2.29mm (Gates); Hg=3.050 → gap 0.76mm → tight_cl=−0.76.
        # groove_open=6.410, belt_root=6.12 → tight_bl=−0.29.
        # Loose: clear=5%×3.050; bl=2.5%×w_center (w_center=5.300, Phi=20°)
        "backlash": {"TIGHT": -0.290, "STANDARD": 0.000, "LOOSE": 0.13},
        "pitch": 12.7,
        "tooth_ht": 3.050,         # ISO hg
        "pitch_line_diff": 0.686,  # ISO a = 2a/2 = 1.372/2
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.760, "STANDARD": 0.000, "LOOSE": 0.150},
        # ISO 5294:2012 Table 2: bw=4.19, hg=3.05, Phi=20, rb_max=1.60, rt=1.60
        # NOTE: rb_max+rt=3.20 > hg=3.05; rb capped at hg-rt-0.10=1.35 to preserve straight flank
        "land_width": 6.290,       # pitch - bw - 2*hg*tan(20°) = 12.7 - 4.19 - 2.220; groove_opening = 6.410
        "tip_radius": 1.600,       # rt (ISO minimum)
        "root_radius": 1.350,      # rb capped at hg-rt-0.10 (ISO max=1.60, physically incompatible with rt)
        "tooth_angle_deg": 40.0,   # 2*Phi = 2*20°
        # Legacy fields
        "tooth_base_width": 6.12,
        "ranges": [{"min": 10, "max": 999, "Hg": 3.050, "X": 0.0, "R1": 0.0, "phi": 40.0, "R2": 1.350}],
    },
    "XH": {
        # Belt ht=6.35mm (Gates); Hg=7.140 → gap 0.79mm → tight_cl=−0.79.
        # groove_open=13.097, belt_root=12.57 → tight_bl=−0.53.
        # Loose: clear=5%×7.140; bl=2.5%×w_center (w_center=10.498, Phi=20°)
        "backlash": {"TIGHT": -0.530, "STANDARD": 0.000, "LOOSE": 0.26},
        "pitch": 22.225,
        "tooth_ht": 7.140,         # ISO hg
        "pitch_line_diff": 1.397,  # ISO a = 2a/2 = 2.794/2
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.790, "STANDARD": 0.000, "LOOSE": 0.360},
        # ISO 5294:2012 Table 2: bw=7.90, hg=7.14, Phi=20, rb=1.98, rt=2.39
        "land_width": 9.128,       # pitch - bw - 2*hg*tan(20°) = 22.225 - 7.90 - 5.197; groove_opening = 13.097
        "tip_radius": 2.390,       # rt (ISO minimum)
        "root_radius": 1.980,      # rb (ISO maximum)
        "tooth_angle_deg": 40.0,   # 2*Phi = 2*20°
        # Legacy fields
        "tooth_base_width": 12.57,
        "ranges": [{"min": 10, "max": 999, "Hg": 7.140, "X": 0.0, "R1": 0.0, "phi": 40.0, "R2": 1.980}],
    },
    "XXH": {
        # Belt ht=9.53mm (Gates); Hg=10.310 → gap 0.78mm → tight_cl=−0.78.
        # groove_open=19.675, belt_root=19.05 → tight_bl=−0.63.
        # Loose: clear=5%×10.310; bl=2.5%×w_center (w_center=15.922, Phi=20°)
        "backlash": {"TIGHT": -0.630, "STANDARD": 0.000, "LOOSE": 0.40},
        "pitch": 31.75,
        "tooth_ht": 10.310,        # ISO hg
        "pitch_line_diff": 1.524,  # ISO a = 2a/2 = 3.048/2
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.780, "STANDARD": 0.000, "LOOSE": 0.520},
        # ISO 5294:2012 Table 2: bw=12.17, hg=10.31, Phi=20, rb=3.96, rt=3.18
        "land_width": 12.075,      # pitch - bw - 2*hg*tan(20°) = 31.75 - 12.17 - 7.505; groove_opening = 19.675
        "tip_radius": 3.180,       # rt (ISO minimum)
        "root_radius": 3.960,      # rb (ISO maximum)
        "tooth_angle_deg": 40.0,   # 2*Phi = 2*20°
        # Legacy fields
        "tooth_base_width": 19.05,
        "ranges": [{"min": 10, "max": 999, "Hg": 10.310, "X": 0.0, "R1": 0.0, "phi": 40.0, "R2": 3.960}],
    },
    # ==========================================
    # Metric trapezoidal T profiles
    # Geometry from user-supplied ISO/DIN dimensions:
    #   Sr (root width), ht (tooth height), rr (root radius), ra (tip radius), 2beta (tooth angle)
    # Backlash baselines align with Megadyne/Megalinear average standard values where available
    # (T5=0.6, T10=1.2, T20=2.4 mm).
    # Pitch line differential values follow existing sample table defaults.
    # ==========================================
    "T2.5": {
        # ISO 17396:2017 — ≤20 grooves: br=1.75+0.05, hg_min=0.75+0.05, 2Phi=50, rb_max=0.2, rt=0.3±0.05
        # Belt ht=0.70mm (ISO 17396 Table 1) → Hg=0.75 > ht → gap 0.05mm → tight_cl=−0.05.
        # Belt Sr=1.50mm → groove_open=br=1.75 → tight_bl=−0.25.
        # Loose: clear=5%×0.75; bl=2.5%×w_center=1.400 (br=1.75, Phi=25°)
        "backlash": {"TIGHT": -0.250, "STANDARD": 0.000, "LOOSE": 0.04},
        "pitch": 2.5,
        "tooth_ht": 0.75,          # hg min
        "pitch_line_diff": 0.25,
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.050, "STANDARD": 0.000, "LOOSE": 0.040},
        "land_width": 2.5 - 1.75,  # pitch - br = 0.75
        "tip_radius": 0.30,        # rt (nominal)
        "root_radius": 0.20,       # rb max
        "tooth_angle_deg": 50.0,   # 2Phi = 50° included → half-angle 25°
    },
    "T5": {
        # ISO 17396:2017 — ≤20 grooves: br=2.96±0.05, hg_min=1.25±0.05, 2Phi=50, rb_max=0.4, rt=0.6±0.05
        # Belt ht=1.20mm (ISO 17396 Table 1) → Hg=1.25 > ht → gap 0.05mm → tight_cl=−0.05.
        # Belt Sr=2.65mm → groove_open=br=2.96 → tight_bl=−0.31.
        # Loose: clear=5%×1.25; bl=2.5%×w_center=2.377 (br=2.96, Phi=25°)
        "backlash": {"TIGHT": -0.310, "STANDARD": 0.000, "LOOSE": 0.06},
        "pitch": 5.0,
        "tooth_ht": 1.25,          # hg min
        "pitch_line_diff": 0.50,
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.050, "STANDARD": 0.000, "LOOSE": 0.060},
        "land_width": 5.0 - 2.96,  # pitch - br = 2.04
        "tip_radius": 0.60,        # rt (nominal)
        "root_radius": 0.40,       # rb max
        "tooth_angle_deg": 50.0,   # 2Phi = 50° included → half-angle 25°
    },
    "T10": {
        # ISO 17396:2017 — ≤20 grooves: br=6.02±0.10, hg_min=2.60±0.10, 2Phi=50, rb_max=0.6, rt=0.8±0.10
        # Belt ht=2.50mm (ISO 17396 Table 1) → Hg=2.60 > ht → gap 0.10mm → tight_cl=−0.10.
        # Belt Sr=5.30mm → groove_open=br=6.02 → tight_bl=−0.72.
        # Loose: clear=5%×2.60; bl=2.5%×w_center=4.808 (br=6.02, Phi=25°)
        "backlash": {"TIGHT": -0.720, "STANDARD": 0.000, "LOOSE": 0.12},
        "pitch": 10.0,
        "tooth_ht": 2.60,          # hg min
        "pitch_line_diff": 0.60,
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.100, "STANDARD": 0.000, "LOOSE": 0.130},
        "land_width": 10.0 - 6.02, # pitch - br = 3.98
        "tip_radius": 0.80,        # rt (nominal)
        "root_radius": 0.60,       # rb max
        "tooth_angle_deg": 50.0,   # 2Phi = 50° included → half-angle 25°
    },
    "T20": {
        # ISO 17396:2017 — ≤20 grooves: br=11.65±0.15, hg_min=5.20±0.13, 2Phi=50, rb_max=0.8, rt=1.2±0.10
        # Belt ht=5.00mm (ISO 17396 Table 1) → Hg=5.20 > ht → gap 0.20mm → tight_cl=−0.20.
        # Belt Sr=10.15mm → groove_open=br=11.65 → tight_bl=−1.50.
        # Loose: clear=5%×5.20; bl=2.5%×w_center=9.225 (br=11.65, Phi=25°)
        "backlash": {"TIGHT": -1.500, "STANDARD": 0.000, "LOOSE": 0.23},
        "pitch": 20.0,
        "tooth_ht": 5.20,          # hg min
        "pitch_line_diff": 1.00,
        "min_teeth": 10,
        "clearances": {"TIGHT": -0.200, "STANDARD": 0.000, "LOOSE": 0.260},
        "land_width": 20.0 - 11.65, # pitch - br = 8.35
        "tip_radius": 1.20,        # rt (nominal)
        "root_radius": 0.80,       # rb max
        "tooth_angle_deg": 50.0,   # 2Phi = 50° included → half-angle 25°
    },
    # ==========================================
    # Metric trapezoidal AT profiles — ISO 17396:2017
    # bh: groove width at floor (ISO primary dimension — NOT the OD opening)
    # groove_opening_at_OD = bh + 2*hg*tan(25°)  →  land_width = pitch - groove_opening_at_OD
    # tip_radius / root_radius: nominal corner radii = midpoint of ISO tolerance range
    # tooth_angle_deg: full included angle between flanks (50° for all AT)
    # ==========================================
    "AT3": {
        # ISO 17396:2017 — bh=1.65 (floor width), hg=1.00, 2Phi=50, rb=0.10–0.25, rt=0.05–0.20
        # bh is floor width; groove_opening_at_OD = bh + 2*hg*tan(25°) = 1.65 + 0.933 = 2.583
        # ISO 17396 Table 2: belt ht=1.10 > hg=1.00 → tip crush → tight_cl=0.
        # Belt Sh=1.50 (root width at OD) vs bh=1.65 → tight_bl=−0.15.
        # Loose: clear=5%×1.00; bl=2.5%×w_center
        "backlash": {"TIGHT": -0.150, "STANDARD": 0.000, "LOOSE": 0.03},
        "pitch": 3.0,
        "tooth_ht": 1.00,
        "pitch_line_diff": 0.40,
        "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.050},
        "land_width": 3.0 - (1.65 + 2*1.00*math.tan(math.radians(25))),  # pitch - groove_OD_opening
        "tip_radius": 0.125,        # rt nominal = (0.05+0.20)/2
        "root_radius": 0.175,       # rb nominal = (0.10+0.25)/2
        "tooth_angle_deg": 50.0,    # 2Phi
    },
    "AT5": {
        # ISO 17396:2017 — bh=2.70 (floor width), hg=1.10, 2Phi=50, rb=0.20–0.40, rt=0.10–0.70
        # bh is floor width; groove_opening_at_OD = bh + 2*hg*tan(25°) = 2.70 + 1.026 = 3.726
        # ISO 17396 Table 2: belt ht=1.20 > hg=1.10 → tip crush → tight_cl=0.
        # Belt Sh=2.50 (root width at OD) vs bh=2.70 → tight_bl=−0.20.
        # Loose: clear=5%×1.10; bl=2.5%×w_center
        "backlash": {"TIGHT": -0.200, "STANDARD": 0.000, "LOOSE": 0.06},
        "pitch": 5.0,
        "tooth_ht": 1.10,
        "pitch_line_diff": 0.60,
        "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.060},
        "land_width": 5.0 - (2.70 + 2*1.10*math.tan(math.radians(25))),  # pitch - groove_OD_opening
        "tip_radius": 0.40,         # rt nominal = (0.10+0.70)/2
        "root_radius": 0.30,        # rb nominal = (0.20+0.40)/2
        "tooth_angle_deg": 50.0,    # 2Phi
    },
    "AT10": {
        # ISO 17396:2017 — bh=5.40 (floor width), hg=2.35, 2Phi=50, rb=0.30–0.50, rt=0.20–1.20
        # bh is floor width; groove_opening_at_OD = bh + 2*hg*tan(25°) = 5.40 + 2.192 = 7.592
        # ISO 17396 Table 2: belt ht=2.50 > hg=2.35 → tip crush → tight_cl=0.
        # Belt Sh=5.00 (root width at OD) vs bh=5.40 → tight_bl=−0.40.
        # Loose: clear=5%×2.35; bl=2.5%×w_center
        "backlash": {"TIGHT": -0.400, "STANDARD": 0.000, "LOOSE": 0.11},
        "pitch": 10.0,
        "tooth_ht": 2.35,
        "pitch_line_diff": 0.90,
        "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.120},
        "land_width": 10.0 - (5.40 + 2*2.35*math.tan(math.radians(25))),  # pitch - groove_OD_opening
        "tip_radius": 0.70,         # rt nominal = (0.20+1.20)/2
        "root_radius": 0.40,        # rb nominal = (0.30+0.50)/2
        "tooth_angle_deg": 50.0,    # 2Phi
    },
    "AT20": {
        # ISO 17396:2017 — bh=10.80 (floor width), hg=4.65, 2Phi=50, rb=0.20–1.50, rt=0.20–2.50
        # bh is floor width; groove_opening_at_OD = bh + 2*hg*tan(25°) = 10.80 + 4.337 = 15.137
        # ISO 17396 Table 2: belt ht=5.00 > hg=4.65 → tip crush → tight_cl=0.
        # Belt Sh=10.00 (root width at OD) vs bh=10.80 → tight_bl=−0.80.
        # Loose: clear=5%×4.65; bl=2.5%×w_center
        "backlash": {"TIGHT": -0.800, "STANDARD": 0.000, "LOOSE": 0.22},
        "pitch": 20.0,
        "tooth_ht": 4.65,
        "pitch_line_diff": 1.25,
        "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.230},
        "land_width": 20.0 - (10.80 + 2*4.65*math.tan(math.radians(25))),  # pitch - groove_OD_opening
        "tip_radius": 1.35,         # rt nominal = (0.20+2.50)/2
        "root_radius": 0.85,        # rb nominal = (0.20+1.50)/2
        "tooth_angle_deg": 50.0,    # 2Phi
    },
    # ==========================================
    # RPP (R-series) parabolic flank profiles — ISO 13050 Section 8, Table 23
    # GH: groove depth from OD to bottom
    # XA: x distance from centreline to parabola vertex
    # XB: length of reference line from XA in +x direction
    # YB: height from XB point toward tip (defines x' axis with XA)
    # Xc, Yc: point in x'-y' frame where parabola transitions to rt fillet
    # K: parabola coefficient (y' = K * x'^2)
    # rt: tip fillet radius connecting (Xc,Yc) to OD surface
    #
    # Backlash / Clearance note:
    # ISO 13050 does not publish separate backlash or clearance values for RPP.
    # Standard=0: groove drawn exactly per ISO 13050 Table 23 geometry.
    # Tight: derived by comparing ISO groove opening (Table 22 bt) to belt root width (Table 18 Sr).
    #   For profiles where belt tooth crushes into groove (ht > GH), tight_clear=0.
    #   For R8M, belt ht < GH → gap 0.22mm → tight_clear=−0.22mm.
    # Loose: clearance=5%×GH, backlash=2.5%×GH (groove depth as parabolic function scale).
    # In practice, low-tooth-count RPP pulleys may exhibit ~0.25mm additional tangential
    # backlash vs the ISO baseline. Use the Custom field to match a specific commercial pulley.
    # ==========================================
    "R3M": {
        # ISO 13050 Table 18: belt_ht=1.27 > GH=1.15 → crush → tight_cl=0.
        # Table 18 belt Sr=1.95; groove_open≈2.06 (Table 22 bt) → tight_bl=−0.11.
        # Loose: clear=5%×GH; bl=2.5%×GH (parabolic function scale)
        "backlash": {"TIGHT": -0.110, "STANDARD": 0.000, "LOOSE": 0.03},
        "pitch": 3.0,
        "tooth_ht": 1.15,
        "pitch_line_diff": 0.381,
        "min_teeth": 8,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.060},
        "ranges": [
            {"min":  8, "max": 15,
             "GH": 1.15, "XA": 0.39, "XB": 4.00, "YB": 0.08,
             "Xc": 0.54, "Yc": 0.94, "K": 3.2100, "rt": 0.28},
            {"min": 16, "max": 30,
             "GH": 1.15, "XA": 0.40, "XB": 4.00, "YB": 0.00,
             "Xc": 0.53, "Yc": 0.93, "K": 3.2850, "rt": 0.30},
            {"min": 31, "max": 9999,
             "GH": 1.20, "XA": 0.40, "XB": 4.00, "YB": 0.00,
             "Xc": 0.53, "Yc": 0.93, "K": 3.3940, "rt": 0.40},
        ],
    },
    "R5M": {
        # ISO 13050 Table 18: belt_ht=2.15 > GH=2.06 → crush → tight_cl=0.
        # Table 18 belt Sr=3.30; groove_open≈3.48 (Table 22 bt) → tight_bl=−0.18.
        # Loose: clear=5%×GH; bl=2.5%×GH (parabolic function scale)
        "backlash": {"TIGHT": -0.180, "STANDARD": 0.000, "LOOSE": 0.05},
        "pitch": 5.0,
        "tooth_ht": 2.06,
        "pitch_line_diff": 0.570,
        "min_teeth": 10,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.100},
        "ranges": [
            {"min": 10, "max": 21,
             "GH": 2.06, "XA": 0.63, "XB": 4.00, "YB": 0.06,
             "Xc": 0.97, "Yc": 1.70, "K": 1.7900, "rt": 0.63},
            {"min": 22, "max": 9999,
             "GH": 2.06, "XA": 0.70, "XB": 4.00, "YB": 0.00,
             "Xc": 0.95, "Yc": 1.66, "K": 1.8290, "rt": 0.50},
        ],
    },
    "R8M": {
        # ISO 13050 Table 18: belt_ht=3.25 < GH=3.47 → gap 0.22mm → tight_cl=−0.22.
        # Table 18 belt Sr=5.49; groove_open≈5.90 (Table 22 bt) → tight_bl=−0.41.
        # Loose: clear=5%×GH; bl=2.5%×GH (parabolic function scale)
        "backlash": {"TIGHT": -0.410, "STANDARD": 0.000, "LOOSE": 0.09},
        "pitch": 8.0,
        "tooth_ht": 3.47,
        "pitch_line_diff": 0.686,
        "min_teeth": 22,
        "clearances": {"TIGHT": -0.220, "STANDARD": 0.000, "LOOSE": 0.170},
        "ranges": [
            {"min": 22, "max": 27,
             "GH": 3.47, "XA": 0.92, "XB": 4.00, "YB": 0.11,
             "Xc": 1.75, "Yc": 2.61, "K": 0.8477, "rt": 0.90},
            {"min": 28, "max": 9999,
             "GH": 3.47, "XA": 0.92, "XB": 4.00, "YB": 0.00,
             "Xc": 1.75, "Yc": 2.61, "K": 0.8477, "rt": 0.95},
        ],
    },
    "R14M": {
        # ISO 13050 Table 18: belt_ht=6.13 > GH=6.04 → crush → tight_cl=0.
        # Table 18 belt Sr=9.61; groove_open≈10.45 (Table 22 bt) → tight_bl=−0.84.
        # Loose: clear=5%×GH; bl=2.5%×GH (parabolic function scale)
        "backlash": {"TIGHT": -0.840, "STANDARD": 0.000, "LOOSE": 0.15},
        "pitch": 14.0,
        "tooth_ht": 6.04,
        "pitch_line_diff": 1.397,
        "min_teeth": 28,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.300},
        "ranges": [
            {"min": 28, "max": 9999,
             "GH": 6.04, "XA": 1.64, "XB": 4.00, "YB": 0.00,
             "Xc": 3.21, "Yc": 4.93, "K": 0.4799, "rt": 1.60},
        ],
    },
    "R20M": {
        # ISO 13050 Table 18: belt_ht=8.75 > GH=8.50 → crush → tight_cl=0.
        # Table 18 belt Sr=13.75; groove_open≈14.85 (Table 22 bt) → tight_bl=−1.10.
        # Loose: clear=5%×GH; bl=2.5%×GH (parabolic function scale)
        "backlash": {"TIGHT": -1.100, "STANDARD": 0.000, "LOOSE": 0.21},
        "pitch": 20.0,
        "tooth_ht": 8.50,
        "pitch_line_diff": 2.160,
        "min_teeth": 30,
        "clearances": {"TIGHT": 0.000, "STANDARD": 0.000, "LOOSE": 0.430},
        "ranges": [
            {"min": 30, "max": 9999,
             "GH": 8.50, "XA": 2.50, "XB": 4.00, "YB": 0.00,
             "Xc": 4.40, "Yc": 6.80, "K": 0.3490, "rt": 2.42},
        ],
    },
}

HTD_PITCHES = list(PULLEY_SPECS.keys())

def getPitchDiameter(numTeeth, pitch_mm):
    return (numTeeth * pitch_mm) / math.pi

def getOuterDiameter(numTeeth, pitch_mm, offset_mm):
    return getPitchDiameter(numTeeth, pitch_mm) - 2.0 * offset_mm

def getTeethFromOD(outerDia_mm, pitch_mm, offset_mm):
    return round((outerDia_mm + 2.0 * offset_mm) * math.pi / pitch_mm)

PROFILE_PITCHES = {
    'HTD': ['3M', '5M', '8M', '14M', '20M'],
    'GT':  ['2M', '3M', '5M', '8M', '14M'],
    'STD': ['2M', '3M', '5M', '8M', '14M'],
    'T': ['T2.5', 'T5', 'T10', 'T20'],
    'AT': ['AT3', 'AT5', 'AT10', 'AT20'],
    'Imperial': ['MXL', 'XL', 'L', 'H', 'XH', 'XXH'],
    'RPP': ['3M', '5M', '8M', '14M', '20M'],
}
PROFILE_KEY_PREFIX = {'HTD': '', 'GT': 'GT', 'STD': 'S', 'T': '', 'AT': '', 'Imperial': '', 'RPP': 'R'}
PROFILE_DEFAULT_KEY = {'HTD': '3M', 'GT': 'GT3M', 'STD': 'S3M', 'T': 'T5', 'AT': 'AT5', 'Imperial': 'MXL', 'RPP': 'R8M'}

# ==========================================
class Point:
    def __init__(self, x: float, y: float):
        self.x, self.y = x, y
    def __repr__(self): return f"Point({self.x:.3f}, {self.y:.3f})"
    def to_tuple(self): return (self.x, self.y)

class Line:
    def __init__(self, p1: Point, p2: Point):
        self.p1, self.p2 = p1, p2
    def __repr__(self): return f"Line({self.p1} -> {self.p2})"
    def to_points(self, res: int = 20) -> List[Point]:
        pts = []
        for i in range(res + 1):
            t = i / res
            pts.append(Point(self.p1.x + t * (self.p2.x - self.p1.x),
                             self.p1.y + t * (self.p2.y - self.p1.y)))
        return pts

class Arc:
    def __init__(self, center: Point, radius: float, start_angle: float, end_angle: float, cw: bool = False):
        self.center = center
        self.radius = radius
        self.start_angle = start_angle
        self.end_angle = end_angle
        self.cw = cw
    def __repr__(self):
        return f"Arc(C={self.center}, R={self.radius:.3f}, {'CW' if self.cw else 'CCW'})"
    def to_points(self, resolution: int = 20) -> List[Point]:
        pts = []
        a1, a2 = self.start_angle, self.end_angle
        if self.cw:
            while a2 > a1: a2 -= 2 * math.pi
        else:
            while a2 < a1: a2 += 2 * math.pi
        step = (a2 - a1) / resolution
        for i in range(resolution + 1):
            a = a1 + i * step
            pts.append(Point(self.center.x + self.radius * math.cos(a),
                             self.center.y + self.radius * math.sin(a)))
        return pts

class HTDContainer:
    def __init__(self, profile_name: str, clearance: float, print_extra: float,
                 number_of_teeth: int = None, pitch: float = None, pitch_line_diff: float = None):
        self.profile_name  = profile_name
        self.clearance     = clearance
        self.print_extra   = print_extra
        self.number_of_teeth = number_of_teeth
        self.pitch         = pitch
        self.pitch_line_diff = pitch_line_diff
        self.primitives    = []
    def add(self, primitive):
        self.primitives.append(primitive)
    def to_point_cloud(self, arc_resolution=15) -> List[Tuple[float, float]]:
        cloud = []
        for prim in self.primitives:
            pts = prim.to_points(arc_resolution)
            if not cloud:
                cloud.extend([p.to_tuple() for p in pts])
            else:
                cloud.extend([p.to_tuple() for p in pts[1:]])
        return cloud


def _decimate_polyline(points: List[Tuple[float, float]], max_points: int) -> List[Tuple[float, float]]:
    if max_points < 2 or len(points) <= max_points:
        return points
    stride = (len(points) - 1) / float(max_points - 1)
    reduced: List[Tuple[float, float]] = []
    for i in range(max_points):
        idx = int(round(i * stride))
        reduced.append(points[idx])
    reduced[0] = points[0]
    reduced[-1] = points[-1]
    return reduced


def _filter_min_spacing(points: List[Tuple[float, float]], min_d: float) -> List[Tuple[float, float]]:
    """Remove consecutive points closer than min_d, always keeping first and last."""
    if len(points) <= 2:
        return points
    result = [points[0]]
    for p in points[1:-1]:
        if math.hypot(p[0] - result[-1][0], p[1] - result[-1][1]) >= min_d:
            result.append(p)
    result.append(points[-1])
    return result


def _build_groove_points(groove_prims, profile_family: str) -> List[Tuple[float, float]]:
    if profile_family in ('Imperial', 'T', 'AT'):
        line_res = 1
        arc_res = 5
        max_points = 110
    elif profile_family == 'RPP':
        line_res = 1
        arc_res = 5
        max_points = 200
    else:
        line_res = 1
        arc_res = 15
        max_points = 450

    groove_points: List[Tuple[float, float]] = []
    for prim in groove_prims:
        if isinstance(prim, Line):
            pts = [prim.p1, prim.p2] if line_res <= 1 else prim.to_points(line_res)
        elif isinstance(prim, Arc):
            pts = prim.to_points(arc_res)
        else:
            pts = prim.to_points(arc_res)
        tuples = [p.to_tuple() for p in pts]
        if not groove_points:
            groove_points.extend(tuples)
        else:
            groove_points.extend(tuples[1:])

    return _decimate_polyline(groove_points, max_points)


def generate_std_groove(profile_name, number_of_teeth, radial_clearance, print_extra, backlash):
    """
    ISO 13050 Table 32 STD (S-series) groove profile.

    Coordinate system: x=0 = groove centreline, y=0 = OD surface, y<0 = into groove.

    Profile order (left to right):
        land → R2 (tip fillet, CW) → R1 (flank, CCW) → R4 (root fillet, CCW)
        → R5 (floor dome, CW, peak at x=0) → R4 → R1 → R2 → land

    S8M/S14M: ISO 13050 Table 32 geometry with R5 convex floor arc.
    S2M/S3M/S5M: estimated parameters, R5=None, flat bottom used.
    """
    spec   = PULLEY_SPECS[profile_name]
    pitch  = spec["pitch"]
    Hg_nom = spec["tooth_ht"]
    aa     = spec["pitch_line_diff"]
    Bg     = spec["Bg"]
    R1     = spec["R1"]
    R2     = spec["R2"]
    R4     = spec["R4"]
    R5     = spec.get("R5")

    offset  = print_extra / 2.0
    bs      = backlash / 2.0
    od_y    = -offset
    Hg_eff  = Hg_nom + (radial_clearance or 0) + offset

    r1 = max(1e-4, R1 + offset)
    r2 = max(1e-4, R2 - offset)
    r4 = max(1e-4, R4 + offset)
    r5 = (max(1e-4, R5 - offset) if R5 is not None else None)

    # ---- Left flank origin: on pitch line at +Bg/2 ----
    C1x =  Bg / 2.0 + bs
    C1y =  od_y + aa

    # ---- Left tip fillet R2: tangent to OD, external tangency with R1 ----
    C2y = od_y - r2
    C2x = C1x - math.sqrt(max(0.0, (r1 + r2)**2 - (C2y - C1y)**2))

    d_12  = math.hypot(C2x - C1x, C2y - C1y)
    T12x  = C1x + r1 * (C2x - C1x) / max(d_12, 1e-9)
    T12y  = C1y + r1 * (C2y - C1y) / max(d_12, 1e-9)

    # ---- Left root fillet R4 ----
    if r5 is not None:
        # R5 convex floor arc: centre at (0, od_y - Hg_eff - r5)
        C5x = 0.0
        C5y = od_y - Hg_eff - r5

        # R4 centre: internally tangent to R1 (dist = r1-r4),
        #            externally tangent to R5 (dist = r4+r5)
        # Use right-of-line intersection (groove interior side)
        A = (C1x, C1y);  ra = r1 - r4
        B = (C5x, C5y);  rb = r4 + r5
        dx = B[0] - A[0];  dy = B[1] - A[1]
        d  = math.hypot(dx, dy)
        h  = (ra**2 - rb**2 + d**2) / (2.0 * d)
        p  = math.sqrt(max(0.0, ra**2 - h**2))
        ux = dx / d;  uy = dy / d
        vx = -uy;     vy = ux          # left-perp (CCW 90°)
        C4x = A[0] + h * ux - p * vx  # right-of-line = M - p*v
        C4y = A[1] + h * uy - p * vy

        d_14  = math.hypot(C4x - C1x, C4y - C1y)
        T14x  = C1x + r1 * (C4x - C1x) / max(d_14, 1e-9)
        T14y  = C1y + r1 * (C4y - C1y) / max(d_14, 1e-9)

        d_45  = math.hypot(C5x - C4x, C5y - C4y)
        T45x  = C4x + r4 * (C5x - C4x) / max(d_45, 1e-9)
        T45y  = C4y + r4 * (C5y - C4y) / max(d_45, 1e-9)
    else:
        # Flat bottom: R4 internally tangent to R1, tangent to flat
        C4y = od_y - Hg_eff + r4
        C4x = C1x - math.sqrt(max(0.0, (r1 - r4)**2 - (C4y - C1y)**2))

        d_14  = math.hypot(C4x - C1x, C4y - C1y)
        T14x  = C1x + r1 * (C4x - C1x) / max(d_14, 1e-9)
        T14y  = C1y + r1 * (C4y - C1y) / max(d_14, 1e-9)

    # ---- Arc angles ----
    a_R2_s = math.pi / 2.0
    a_R2_e = math.atan2(T12y - C2y, T12x - C2x)

    a_R1_s = math.atan2(T12y - C1y, T12x - C1x)
    a_R1_e = math.atan2(T14y - C1y, T14x - C1x)

    a_R4_s = math.atan2(T14y - C4y, T14x - C4x)
    a_R4_e = (math.atan2(T45y - C4y, T45x - C4x)
               if r5 is not None else -math.pi / 2.0)

    container = HTDContainer(
        f"{profile_name} ({number_of_teeth}T)",
        radial_clearance or 0, print_extra, None, pitch, aa
    )

    # Land — left
    container.add(Line(Point(-pitch / 2.0, od_y), Point(C2x, od_y)))
    # R2 tip fillet — left: CW
    container.add(Arc(Point(C2x, C2y), r2, a_R2_s, a_R2_e, cw=True))
    # R1 flank — left: CCW
    container.add(Arc(Point(C1x, C1y), r1, a_R1_s, a_R1_e, cw=False))
    # R4 root fillet — left: CCW
    container.add(Arc(Point(C4x, C4y), r4, a_R4_s, a_R4_e, cw=False))

    if r5 is not None:
        # R5 floor dome: CW from T45_left through peak (0, od_y-Hg_eff) to T45_right
        a_R5_s = math.atan2(T45y - C5y,  T45x - C5x)
        a_R5_e = math.atan2(T45y - C5y, -T45x - C5x)
        container.add(Arc(Point(C5x, C5y), r5, a_R5_s, a_R5_e, cw=True))
    else:
        bot_y = od_y - Hg_eff
        container.add(Line(Point(C4x, bot_y), Point(-C4x, bot_y)))

    # Right side — mirror (angles: π - angle, same cw directions)
    container.add(Arc(Point(-C4x, C4y), r4,
                      math.pi - a_R4_e, math.pi - a_R4_s, cw=False))
    container.add(Arc(Point(-C1x, C1y), r1,
                      math.pi - a_R1_e, math.pi - a_R1_s, cw=False))
    container.add(Arc(Point(-C2x, C2y), r2,
                      math.pi - a_R2_e, math.pi - a_R2_s, cw=True))
    container.add(Line(Point(-C2x, od_y), Point(pitch / 2.0, od_y)))

    return container

def generate_htd_groove(
    profile_name: str,
    number_of_teeth: int,
    radial_clearance: float = None,
    print_extra: float = 0.0,
    backlash: float = 0.0
) -> HTDContainer:
    """
    Generates a single HTD or GT pulley groove profile (ISO 13050 curvilinear arc-flank geometry).
    radial_clearance: delta in mm from the ISO Table 14 Hg for the current tooth-count range.
                      0.0 (or None) = nominal table Hg.
                      Negative = shallower groove (tight / minimum profile band).
                      Positive = deeper groove (loose / maximum profile band).
    backlash: total tangential backlash in mm; widens groove by backlash/2 per side.
    """
    if profile_name not in PULLEY_SPECS:
        raise ValueError(f"Profile '{profile_name}' not found in PULLEY_SPECS.")

    spec = PULLEY_SPECS[profile_name]
    pitch = spec["pitch"]

    range_params = None
    for r in spec["ranges"]:
        if r["min"] <= number_of_teeth <= r["max"]:
            range_params = r
            break
    if range_params is None:
        range_params = spec["ranges"][-1]

    X     = range_params["X"]
    theta = math.radians(range_params["phi"])

    # radial_clearance is a delta from the ISO table Hg for this tooth-count range.
    hg_base = range_params["Hg"] + (radial_clearance if radial_clearance is not None else 0.0)

    offset       = print_extra / 2.0
    arc_rad_eff  = max(1e-4, range_params["R1"] + offset)
    root_rad_eff = max(1e-4, range_params["R2"] - offset)

    od_y   = -offset
    hg_eff = hg_base + offset

    C1_y = -hg_eff + arc_rad_eff
    T1_x = X    + arc_rad_eff  * math.cos(theta)
    T1_y = C1_y - arc_rad_eff  * math.sin(theta)

    C2_y = od_y - root_rad_eff
    T2_y = C2_y + root_rad_eff * math.sin(theta)
    T2_x = T1_x + (T2_y - T1_y) * math.tan(theta)
    C2_x = T2_x + root_rad_eff * math.cos(theta)

    container = HTDContainer(
        profile_name=f"{profile_name} ({number_of_teeth}T)",
        clearance=radial_clearance,
        print_extra=print_extra,
        number_of_teeth=None,
        pitch=pitch,
        pitch_line_diff=spec["pitch_line_diff"]
    )

    bs = backlash / 2.0
    container.add(Line(Point(-pitch/2,       od_y),  Point(-C2_x - bs,  od_y)))
    container.add(Arc( Point(-C2_x - bs,     C2_y),  root_rad_eff, math.pi/2,           theta,              cw=True))
    container.add(Line(Point(-T2_x - bs,     T2_y),  Point(-T1_x - bs,  T1_y)))
    container.add(Arc( Point(-X    - bs,     C1_y),  arc_rad_eff,  math.pi + theta,     1.5*math.pi,        cw=False))
    container.add(Line(Point(-X    - bs,  -hg_eff),  Point( X    + bs, -hg_eff)))
    container.add(Arc( Point( X    + bs,     C1_y),  arc_rad_eff,  1.5*math.pi,         2*math.pi - theta,  cw=False))
    container.add(Line(Point( T1_x + bs,     T1_y),  Point( T2_x + bs,  T2_y)))
    container.add(Arc( Point( C2_x + bs,     C2_y),  root_rad_eff, math.pi - theta,     math.pi/2,          cw=True))
    container.add(Line(Point( C2_x + bs,     od_y),  Point( pitch/2,    od_y)))

    return container


def _generate_trapezoidal_groove(
    depth: float,
    groove_opening: float,
    tip_radius: float,
    root_radius: float,
    tooth_angle_deg: float,
) -> List[Tuple[float, float]]:
    """
    Generate a symmetric trapezoidal groove cross-section algebraically.
    y=0 at OD surface, y increases into the groove (positive = deeper).

    Profile (left to right):
      OD_corner_arc -> straight_flank -> root_corner_arc
      -> bottom_flat -> root_corner_arc -> straight_flank -> OD_corner_arc

    tooth_angle_deg : full included angle between the two flanks (e.g. 50 for XL).
    groove_opening  : total groove width at the OD surface (= pitch - land_width).
    """
    alpha = math.radians(tooth_angle_deg / 2.0)   # half-angle from vertical
    Rt = max(0.0, tip_radius)
    Rr = max(0.0, root_radius)
    half_open = groove_opening / 2.0

    def _append_unique(points: List[Tuple[float, float]], pt: Tuple[float, float], tol: float = 1e-9) -> None:
        if not points:
            points.append(pt)
            return
        px, py = points[-1]
        if abs(px - pt[0]) > tol or abs(py - pt[1]) > tol:
            points.append(pt)

    # --- Key tangent points (left side) ---
    # OD corner fillet: centre on the bisector of the angle where OD meets the flank,
    # so the fillet is tangent to both surfaces without shrinking the floor width (bw).
    # half_open is the sharp-corner half-opening (anchors the flank / bw).
    # half_open_c is the fillet-centre x-offset (further out by Rt*(1-sin α)/cos α).
    if Rt > 0.0 and abs(math.cos(alpha)) > 1e-9:
        half_open_c = half_open + Rt * (1.0 - math.sin(alpha)) / math.cos(alpha)
    else:
        half_open_c = half_open

    xt_od = -half_open_c + Rt * math.cos(alpha)   # T_tf x (left side)
    yt_od = Rt * (1.0 - math.sin(alpha))           # T_tf y

    # Root corner arc: center (-half_flat, depth-Rr), tangent to y=depth at x=-half_flat.
    # Tangent with flank (flank runs down-right at angle alpha from vertical):
    yt_rt = depth - Rr * (1.0 - math.sin(alpha))
    xt_rt = xt_od + (yt_rt - yt_od) * math.tan(alpha)

    # Half-width of the flat at the groove bottom (derived from geometry):
    half_flat = -xt_rt - Rr * math.cos(alpha)

    STEPS = 8
    pts: List[Tuple[float, float]] = []

    # Left OD corner arc (or sharp OD corner when Rt == 0)
    if Rt > 0.0:
        for i in range(STEPS + 1):
            a = -math.pi / 2.0 + i * (math.pi / 2.0 - alpha) / STEPS
            _append_unique(pts, (-half_open_c + Rt * math.cos(a), Rt + Rt * math.sin(a)))
    else:
        _append_unique(pts, (-half_open, 0.0))

    # Left straight flank
    _append_unique(pts, (xt_rt, yt_rt))

    # Left root corner arc (or sharp root corner when Rr == 0)
    if Rr > 0.0:
        for i in range(1, STEPS + 1):
            a = (math.pi - alpha) + i * (math.pi / 2.0 - (math.pi - alpha)) / STEPS
            _append_unique(pts, (-half_flat + Rr * math.cos(a), (depth - Rr) + Rr * math.sin(a)))
    else:
        _append_unique(pts, (-half_flat, depth))

    # Bottom flat
    _append_unique(pts, (half_flat, depth))

    # Right root corner arc
    if Rr > 0.0:
        for i in range(1, STEPS + 1):
            a = math.pi / 2.0 + i * (alpha - math.pi / 2.0) / STEPS
            _append_unique(pts, (half_flat + Rr * math.cos(a), (depth - Rr) + Rr * math.sin(a)))

    # Right straight flank
    _append_unique(pts, (-xt_rt, yt_rt))

    # Right OD corner arc
    if Rt > 0.0:
        for i in range(1, STEPS + 1):
            a = (alpha - math.pi) + i * (-math.pi / 2.0 - (alpha - math.pi)) / STEPS
            _append_unique(pts, (half_open_c + Rt * math.cos(a), Rt + Rt * math.sin(a)))
    else:
        _append_unique(pts, (half_open, 0.0))

    return pts


def generate_imperial_groove(
    profile_name: str,
    number_of_teeth: int,
    radial_clearance: float = None,
    print_extra: float = 0.0,
    backlash: float = 0.0,
) -> HTDContainer:
    """
    Generates a single Imperial, T-series, or AT-series pulley groove profile
    (trapezoidal flank geometry per ANSI/RMA IP-24, ISO 5296 / DIN 7721, ISO 17396).
    radial_clearance: if None, uses the ISO/ANSI standard clearance for the profile.
    backlash: total tangential backlash in mm; widens groove by backlash/2 per side.
    """
    if profile_name not in PULLEY_SPECS:
        raise ValueError(f"Profile '{profile_name}' not found in PULLEY_SPECS.")

    spec = PULLEY_SPECS[profile_name]
    pitch = spec["pitch"]

    offset = print_extra / 2.0
    od_y = -offset
    bs = backlash / 2.0

    if radial_clearance is None:
        radial_clearance = spec["clearances"]["STANDARD"]

    depth = max(1e-4, spec["tooth_ht"] + radial_clearance + offset)

    if "Sr" in spec:
        alpha_r = math.radians(spec["tooth_angle_deg"] / 2.0)
        tip_r = 0.0
        groove_opening = spec["Sr"] + 2.0 * tip_r * (1.0 - math.sin(alpha_r)) / math.cos(alpha_r)
        profile_pts = _generate_trapezoidal_groove(
            depth=depth,
            groove_opening=groove_opening,
            tip_radius=tip_r,
            root_radius=0.0,
            tooth_angle_deg=spec["tooth_angle_deg"],
        )
    elif "land_width" in spec:
        groove_opening = pitch - spec["land_width"]
        profile_pts = _generate_trapezoidal_groove(
            depth=depth,
            groove_opening=groove_opening,
            tip_radius=spec.get("tip_radius", 0.0) + offset,
            root_radius=spec.get("root_radius", 0.0) + offset,
            tooth_angle_deg=spec["tooth_angle_deg"],
        )
    else:
        raise ValueError(f"Profile '{profile_name}' has neither 'Sr' nor 'land_width' — cannot generate groove.")

    effective_backlash = max(0.0, backlash)
    if profile_pts:
        profile_w = profile_pts[-1][0] - profile_pts[0][0]
        max_backlash = max(0.0, pitch - profile_w - 0.01)
        effective_backlash = min(effective_backlash, max_backlash)

    shifted_pts: List[Tuple[float, float]] = []
    bs = effective_backlash / 2.0
    for x, y in profile_pts:
        if x < 0:
            sx = x - bs
        elif x > 0:
            sx = x + bs
        else:
            sx = x
        shifted_pts.append((sx, od_y - y))

    container = HTDContainer(
        profile_name=f"{profile_name} ({number_of_teeth}T)",
        clearance=radial_clearance,
        print_extra=print_extra,
        number_of_teeth=None,
        pitch=pitch,
        pitch_line_diff=spec["pitch_line_diff"],
    )

    left_x, left_y = shifted_pts[0]
    right_x, right_y = shifted_pts[-1]
    container.add(Line(Point(-pitch / 2.0, od_y), Point(left_x, left_y)))
    for i in range(len(shifted_pts) - 1):
        x0, y0 = shifted_pts[i]
        x1, y1 = shifted_pts[i + 1]
        container.add(Line(Point(x0, y0), Point(x1, y1)))
    container.add(Line(Point(right_x, right_y), Point(pitch / 2.0, od_y)))
    return container


def generate_rpp_groove(
    profile_name: str,
    number_of_teeth: int,
    radial_clearance: float = None,
    print_extra: float = 0.0,
    backlash: float = 0.0,
) -> HTDContainer:
    """
    ISO 13050 Section 8 RPP (R-series) parabolic flank groove profile.

    Coordinate system: x=0 = groove centreline, y=0 = nominal OD surface, y<0 = into groove.

    Right flank: parabola y'=K*x'^2 in rotated frame (x' from XA toward YB),
                 followed by rt tip fillet arc to the OD circle.
    Root: arc of radius R_OD-GH_eff centred at pulley centre.
    Left flank: mirror of right.
    """
    if profile_name not in PULLEY_SPECS:
        raise ValueError(f"Profile '{profile_name}' not found in PULLEY_SPECS.")

    spec  = PULLEY_SPECS[profile_name]
    pitch = spec["pitch"]
    aa    = spec["pitch_line_diff"]

    rng = None
    for rv in spec["ranges"]:
        if rv["min"] <= number_of_teeth <= rv["max"]:
            rng = rv
            break
    if rng is None:
        rng = spec["ranges"][-1]

    GH = rng["GH"];  XA = rng["XA"];  XB = rng["XB"];  YB = rng["YB"]
    Xc = rng["Xc"];  Yc = rng["Yc"];  K  = rng["K"];   rt = rng["rt"]

    if radial_clearance is None:
        radial_clearance = spec["clearances"]["STANDARD"]

    offset  = print_extra / 2.0
    bs      = backlash / 2.0
    od_y    = -offset
    GH_eff  = GH + radial_clearance + offset
    rt_eff  = max(1e-4, rt - offset)

    # Pulley OD circle (used for root arc and fillet-OD intersection)
    PD    = number_of_teeth * pitch / math.pi
    R_OD  = PD / 2.0 - aa - offset
    od_cx = 0.0
    od_cy = od_y - R_OD   # pulley centre y in groove coords

    # Root arc radius — needed before vertex placement
    r_root = R_OD - GH_eff

    # ---- Parabola vertex: placed on the root arc circle to eliminate the gap
    # between the root arc endpoint and the parabola start point.
    # (XA, od_y-GH_eff) is only on the circle when XA=0; for XA>0 the naive
    # vertex lies outside the circle by ~XA²/(2·r_root), causing a visible kink.
    ox = XA + bs                                          # vertex x
    oy = od_cy + math.sqrt(max(0.0, r_root**2 - ox**2))  # vertex y on root arc

    # ---- x' axis = CW tangent to root arc at vertex → zero angular kink ----
    ux_v = ((oy - od_cy) / r_root, -ox / r_root)
    uy_v = (-ux_v[1], ux_v[0])       # 90° CCW of ux

    def to_g(xp, yp):
        """x'-y' frame → groove coordinates."""
        return (ox + xp*ux_v[0] + yp*uy_v[0],
                oy + xp*ux_v[1] + yp*uy_v[1])

    # ---- Find x' where fillet (rt_eff) is exactly tangent to flat OD (y=od_y) ----
    # Condition: fc_y = od_y - rt_eff
    # tp_y(xp) + rt_eff * ny(xp) = od_y - rt_eff
    # Solved by bisection; result is close to table Xc but geometrically exact.
    def _fc_y_at(xp):
        tx = ux_v[0] + 2.0*K*xp*uy_v[0]
        ty = ux_v[1] + 2.0*K*xp*uy_v[1]
        ny = -tx / math.hypot(tx, ty)
        tpy = oy + xp*ux_v[1] + K*xp**2*uy_v[1]
        return tpy + rt_eff * ny

    target_fc_y = od_y - rt_eff
    xp_lo, xp_hi = 0.0, XB
    while _fc_y_at(xp_hi) < target_fc_y:
        xp_hi *= 2.0
    for _ in range(60):
        xp_mid = (xp_lo + xp_hi) / 2.0
        if _fc_y_at(xp_mid) < target_fc_y:
            xp_lo = xp_mid
        else:
            xp_hi = xp_mid
    xp_tan = (xp_lo + xp_hi) / 2.0

    # ---- Parabola: right flank, x' from 0 to xp_tan ----
    PARA_RES = 20
    para_pts = [to_g(xp_tan * i / PARA_RES, K * (xp_tan * i / PARA_RES) ** 2)
                for i in range(PARA_RES + 1)]

    tp_x, tp_y = to_g(xp_tan, K * xp_tan ** 2)

    # ---- rt fillet: tangent to parabola at tp, tangent to flat OD at (fc_x, od_y) ----
    dydxp = 2.0 * K * xp_tan
    tx_o  = ux_v[0] + dydxp * uy_v[0];  ty_o = ux_v[1] + dydxp * uy_v[1]
    t_mag = math.hypot(tx_o, ty_o);       tx_o /= t_mag;  ty_o /= t_mag
    nx_o  =  ty_o;  ny_o = -tx_o

    fc_x = tp_x + rt_eff * nx_o
    fc_y = od_y - rt_eff               # exact by construction (bisection above)
    od_ex = fc_x                        # fillet meets OD directly above centre

    # Fillet arc CW from tp to (fc_x, od_y) = top of fillet circle
    FIL_RES = 8
    a0 = math.atan2(tp_y - fc_y, tp_x - fc_x)
    a1 = math.pi / 2.0                 # top of circle → (fc_x, fc_y + rt_eff) = (fc_x, od_y)
    while a1 > a0: a1 -= 2.0 * math.pi
    fil_pts = [(fc_x + rt_eff * math.cos(a0 + (a1 - a0) * i / FIL_RES),
                fc_y + rt_eff * math.sin(a0 + (a1 - a0) * i / FIL_RES))
               for i in range(FIL_RES + 1)]
    # fil_pts[-1] = (fc_x, od_y) exactly — no snap line required

    # ---- Root arc: left XA vertex to right XA vertex (CW, decreasing angle) ----
    ROOT_RES = 12
    a_r = math.atan2(oy - od_cy, ox)
    a_l = math.pi - a_r
    root_pts = [(od_cx + r_root * math.cos(a_l + (a_r - a_l) * i / ROOT_RES),
                 od_cy + r_root * math.sin(a_l + (a_r - a_l) * i / ROOT_RES))
                for i in range(ROOT_RES + 1)]

    # ---- Mirror for left flank ----
    def mir(pts): return [(-x, y) for x, y in pts]

    # ---- Assemble groove (starts and ends exactly at y=od_y) ----
    groove: List[Tuple[float, float]] = []
    def ext(pts):
        if not groove: groove.extend(pts)
        else:          groove.extend(pts[1:])

    ext(mir(fil_pts)[::-1])    # left fillet  (od_y → parabola base)
    ext(mir(para_pts)[::-1])   # left parabola (down to root)
    ext(root_pts)              # root arc
    ext(para_pts)              # right parabola (root → OD)
    ext(fil_pts)               # right fillet  (→ od_y, tangent to OD)

    groove_full = groove       # already starts/ends at od_y — no snap line needed

    # ---- Build HTDContainer ----
    container = HTDContainer(
        profile_name=f"{profile_name} ({number_of_teeth}T)",
        clearance=radial_clearance,
        print_extra=print_extra,
        number_of_teeth=None,
        pitch=pitch,
        pitch_line_diff=aa,
    )
    container.add(Line(Point(-pitch / 2.0, od_y), Point(-od_ex, od_y)))   # left land
    for i in range(len(groove_full) - 1):
        x0, y0 = groove_full[i];  x1, y1 = groove_full[i + 1]
        container.add(Line(Point(x0, y0), Point(x1, y1)))
    container.add(Line(Point(od_ex, od_y), Point(pitch / 2.0, od_y)))     # right land
    return container


def generate_profile_groove(
    profile_family: str,
    profile_key: str,
    number_of_teeth: int,
    radial_clearance: float = None,
    print_extra: float = 0.0,
    backlash: float = 0.0,
) -> HTDContainer:
    if profile_family in ('Imperial', 'T', 'AT'):
        return generate_imperial_groove(profile_key, number_of_teeth, radial_clearance, print_extra, backlash)
    if profile_family == 'STD':
        return generate_std_groove(profile_key, number_of_teeth, radial_clearance, print_extra, backlash)
    if profile_family == 'RPP':
        return generate_rpp_groove(profile_key, number_of_teeth, radial_clearance, print_extra, backlash)
    return generate_htd_groove(profile_key, number_of_teeth, radial_clearance, print_extra, backlash)

# ==========================================
# Belt tooth profiles
# ==========================================

# ISO 13050:2014 Table 9 — H-series belt tooth dimensions
H_BELT_SPECS = {
    "H3M": {
        "pitch":  3.0, "hs": 2.4,  "ht": 1.21, "aa": 0.381,
        "P1": (-1.14,  0.00), "r1": 0.30,
        "P2": (-0.83, -0.30), "P3": (-0.83, -0.35),
        "r2": 0.86,            "P4": ( 0.00, -1.21),
    },
    "H5M": {
        "pitch":  5.0, "hs": 3.8,  "ht": 2.08, "aa": 0.572,
        "P1": (-1.85,  0.00), "r1": 0.41,
        "P2": (-1.44, -0.42), "P3": (-1.44, -0.53),
        "r2": 1.50,            "P4": ( 0.00, -2.08),
    },
    "H8M": {
        "pitch":  8.0, "hs": 6.0,  "ht": 3.38, "aa": 0.686,
        "P1": (-3.30,  0.00), "r1": 0.76,
        "P2": (-2.55, -0.65), "P3": (-2.47, -1.17),
        "r2": 2.59,            "P4": ( 0.00, -3.38),
    },
    "H14M": {
        "pitch": 14.0, "hs": 10.0, "ht": 6.02, "aa": 1.397,
        "P1": (-5.78,  0.00), "r1": 1.42,
        "P2": (-4.36, -1.29), "P3": (-4.30, -1.97),
        "r2": 4.46,            "P4": ( 0.00, -6.02),
    },
    "H20M": {
        "pitch": 20.0, "hs": 13.2, "ht": 8.68, "aa": 2.159,
        "P1": (-8.34,  0.00), "r1": 2.03,
        "P2": (-6.32, -1.84), "P3": (-6.22, -2.90),
        "r2": 6.40,            "P4": ( 0.00, -8.68),
    },
}

# ISO 13050:2014 Table 27 — S-series belt tooth dimensions
# S8M/S14M: Table 27. S2M/S3M/S5M: scaled from S8M by pitch ratio.
S_BELT_SPECS = {
    "S2M":  {"pitch":  2.0, "hs": 1.325, "ht": 0.763, "aa": 0.118,
             "Bg": 1.30, "R1": 1.33, "rr": 0.19, "ra": 0.20, "R5": 1.01},
    "S3M":  {"pitch":  3.0, "hs": 1.988, "ht": 1.144, "aa": 0.197,
             "Bg": 1.95, "R1": 1.99, "rr": 0.28, "ra": 0.30, "R5": 1.52},
    "S5M":  {"pitch":  5.0, "hs": 3.313, "ht": 1.906, "aa": 0.377,
             "Bg": 3.25, "R1": 3.31, "rr": 0.47, "ra": 0.50, "R5": 2.52},
    "S8M":  {"pitch":  8.0, "hs": 5.30,  "ht": 3.05,  "aa": 0.686,
             "Bg": 5.20, "R1": 5.30, "rr": 0.80, "ra": 0.80, "R5": 4.04},
    "S14M": {"pitch": 14.0, "hs": 10.20, "ht": 5.30,  "aa": 1.397,
             "Bg": 9.10, "R1": 9.28, "rr": 1.40, "ra": 1.40, "R5": 7.07},
}

# ── R-series (RPP) belt tooth — ISO 13050:2014 Table 18 ──────────────────────
# Two-lobe parabolic profile.  Coordinate system: y=0 at toothed face (OD),
# y>0 toward belt back, y<0 into tooth lobes.
# C  = parabolic flank coefficient  Y = C·X²  (lobe-frame origin = lobe tip)
# S  = distance between the two ±S/2 points where flanks meet y=0
# ht = lobe tip depth below y=0
# hs = belt section height from lobe tip to back face
# rr = root fillet radius (centre at y=-rr, tangent to y=0 and flank)
# aa = pitch line differential above back face
R_BELT_SPECS = {
    "R3M":  {"pitch":  3.0, "S": 1.95, "hs": 2.40, "ht": 1.27, "rr": 0.380, "aa": 0.381, "C": 3.0567},
    "R5M":  {"pitch":  5.0, "S": 3.30, "hs": 3.80, "ht": 2.15, "rr": 0.630, "aa": 0.570, "C": 1.7950},
    "R8M":  {"pitch":  8.0, "S": 5.49, "hs": 5.40, "ht": 3.25, "rr": 1.000, "aa": 0.686, "C": 1.0954},
    "R14M": {"pitch": 14.0, "S": 9.61, "hs": 9.70, "ht": 6.13, "rr": 1.750, "aa": 1.397, "C": 0.6250},
    "R20M": {"pitch": 20.0, "S":13.75, "hs":14.50, "ht": 8.75, "rr": 2.500, "aa": 2.160, "C": 0.0438},
}

# ── G-series (GT/GT3) belt tooth — community-derived / Gates catalog data ─────
# Modified curvilinear arc-flank profile.  Same construction as H-series but
# shallower teeth (GT has better load distribution, less tooth engagement depth).
# Coordinate system identical to H_BELT_SPECS:
#   y = 0   at toothed face (OD land between teeth)
#   y < 0   into tooth  (tip at y = -ht)
#   y = aa  at pitch line (tensile cord)
#   y = hs-ht  at belt back face
#
# Parameters confirmed from Gates Light Power & Precision catalog sprocket tables:
#   aa  = (PD - OD) / 2  (same for GT and HTD at each pitch)
#   hs  = overall belt thickness (Table 15 for 2/3/5 mm; estimated for 8/14 mm)
# Tooth geometry (P1–P4, r1, r2) derived from PULLEY_SPECS GT groove parameters
# and scaled from H_BELT_SPECS to match GT ht values.
G_BELT_SPECS = {
    # 2mm pitch — GT2 (community-measured). r1=R2=0.150, r2=R1=0.555 from PULLEY_SPECS GT2M.
    "G2M":  {"pitch":  2.0, "hs": 1.52, "ht": 0.764, "aa": 0.254,
             "P1": (-0.76,  0.00), "r1": 0.20,
             "P2": (-0.55, -0.20), "P3": (-0.55, -0.23),
             "r2": 0.57,            "P4": ( 0.00, -0.764)},
    # 3mm pitch — GT3. r1≈R2=0.250, r2≈R1=0.955; P1-P3 from H3M, P4 at GT ht.
    "G3M":  {"pitch":  3.0, "hs": 2.41, "ht": 1.143, "aa": 0.381,
             "P1": (-1.14,  0.00), "r1": 0.28,
             "P2": (-0.83, -0.28), "P3": (-0.83, -0.33),
             "r2": 0.86,            "P4": ( 0.00, -1.143)},
    # 5mm pitch — GT3. r1≈R2=0.400, r2≈R1=1.542; P1-P3 from H5M, P4 at GT ht.
    "G5M":  {"pitch":  5.0, "hs": 3.81, "ht": 1.778, "aa": 0.5715,
             "P1": (-1.85,  0.00), "r1": 0.38,
             "P2": (-1.44, -0.38), "P3": (-1.44, -0.48),
             "r2": 1.50,            "P4": ( 0.00, -1.778)},
    # 8mm pitch — GT3 (est.; catalog 17195). Belt_y preserved from H8M.
    "G8M":  {"pitch":  8.0, "hs": 5.47, "ht": 2.845, "aa": 0.686,
             "P1": (-3.30,  0.00), "r1": 0.64,
             "P2": (-2.55, -0.64), "P3": (-2.47, -1.07),
             "r2": 2.47,            "P4": ( 0.00, -2.845)},
    # 14mm pitch — GT3 (est.; catalog 17195). Belt_y preserved from H14M.
    "G14M": {"pitch": 14.0, "hs": 8.96, "ht": 4.978, "aa": 1.397,
             "P1": (-5.78,  0.00), "r1": 1.12,
             "P2": (-4.36, -1.12), "P3": (-4.30, -1.80),
             "r2": 4.32,            "P4": ( 0.00, -4.978)},
}

# ── T-series belt tooth dimensions (ISO 5296) ────────────────────────────────
# S   = S_r : root width at OD surface (belt tooth wider at OD, narrows toward tip)
# beta2     : full included angle (40°)
# rr        : OD-corner fillet radius (at land/flank junction)
# ra        : tooth-tip fillet radius
# aa        : pitch line differential
T_BELT_SPECS = {
    "T2.5": {"pitch":  2.5, "ht": 0.70, "hs": 1.30, "aa": 0.25,
             "S": 1.50,  "beta2": 40.0, "rr": 0.2, "ra": 0.2, "s_dim": "OD"},
    "T5":   {"pitch":  5.0, "ht": 1.20, "hs": 2.20, "aa": 0.50,
             "S": 2.65,  "beta2": 40.0, "rr": 0.4, "ra": 0.4, "s_dim": "OD"},
    "T10":  {"pitch": 10.0, "ht": 2.50, "hs": 4.50, "aa": 0.60,
             "S": 5.30,  "beta2": 40.0, "rr": 0.6, "ra": 0.6, "s_dim": "OD"},
    "T20":  {"pitch": 20.0, "ht": 5.00, "hs": 8.00, "aa": 1.00,
             "S": 10.15, "beta2": 40.0, "rr": 0.8, "ra": 0.8, "s_dim": "OD"},
}

# ── AT-series belt tooth dimensions (ISO 17396) ───────────────────────────────
# S   = S_h : head/floor width at tooth TIP
#             land_width = pitch - (S_h + 2*ht*tan(beta/2))
# beta2     : full included angle (50°)
AT_BELT_SPECS = {
    "AT3":  {"pitch":  3.0, "ht": 1.10, "hs": 1.90, "aa": 0.40,
             "S":  1.50, "beta2": 50.0, "rr": 0.1, "ra": 0.3, "s_dim": "floor"},
    "AT5":  {"pitch":  5.0, "ht": 1.20, "hs": 2.70, "aa": 0.60,
             "S":  2.50, "beta2": 50.0, "rr": 0.6, "ra": 0.4, "s_dim": "floor"},
    "AT10": {"pitch": 10.0, "ht": 2.50, "hs": 4.50, "aa": 0.90,
             "S":  5.00, "beta2": 50.0, "rr": 1.2, "ra": 0.6, "s_dim": "floor"},
    "AT20": {"pitch": 20.0, "ht": 5.00, "hs": 8.00, "aa": 1.25,
             "S": 10.00, "beta2": 50.0, "rr": 2.5, "ra": 1.6, "s_dim": "floor"},
}

# ── Imperial belt tooth dimensions ────────────────────────────────────────────
# S     : tooth width at OD surface (ISO 5296-1:1989 Table 2; XXL interpolated)
# beta2 : full included angle (ISO 5296-1:1989 Table 2)
# ht    : tooth height OD→tip (ISO 5296-1:1989 Table 2)
# rr    : OD-corner fillet (ISO 5296-1:1989 Table 2  r_r)
# ra    : tooth-tip fillet  (ISO 5296-1:1989 Table 2  r_a)
# aa    : pitch line differential (ISO 5294:2012)
# hs    : total section height = ht + belt body (Fenner/Gates handbooks)
# s_dim : "OD" — S measured at OD surface (same as T-belt S_r convention)
IMPERIAL_BELT_SPECS = {
    "MXL": {"pitch":  2.032, "beta2": 40.0,
            "S":  1.14, "ht": 0.51, "rr": 0.13, "ra": 0.13,
            "aa": 0.254, "hs": 1.65, "s_dim": "OD"},
    "XL":  {"pitch":  5.080, "beta2": 50.0,
            "S":  2.57, "ht": 1.27, "rr": 0.38, "ra": 0.38,
            "aa": 0.254, "hs": 3.84, "s_dim": "OD"},
    "L":   {"pitch":  9.525, "beta2": 40.0,
            "S":  4.65, "ht": 1.91, "rr": 0.51, "ra": 0.51,
            "aa": 0.381, "hs": 6.48, "s_dim": "OD"},
    "H":   {"pitch": 12.700, "beta2": 40.0,
            "S":  6.12, "ht": 2.29, "rr": 1.02, "ra": 1.02,
            "aa": 0.686, "hs": 8.64, "s_dim": "OD"},
    "XH":  {"pitch": 22.225, "beta2": 40.0,
            "S": 12.57, "ht": 6.35, "rr": 1.57, "ra": 1.19,
            "aa": 1.397, "hs": 17.53, "s_dim": "OD"},
    "XXH": {"pitch": 31.750, "beta2": 40.0,
            "S": 19.05, "ht": 9.53, "rr": 2.29, "ra": 1.52,
            "aa": 1.524, "hs": 25.41, "s_dim": "OD"},
}

# Families that have belt tooth profiles
BELT_FAMILIES = frozenset({'HTD', 'GT', 'STD', 'RPP', 'T', 'AT', 'Imperial'})


# ── Belt profile sampling helpers ─────────────────────────────────────────────

def _bsamp_line(p0, p1, res=4):
    return [(p0[0] + i/res*(p1[0]-p0[0]), p0[1] + i/res*(p1[1]-p0[1]))
            for i in range(res+1)]

def _bsamp_arc(center, radius, a1, a2, ccw=True, res=16):
    if ccw:
        while a2 < a1: a2 += 2*math.pi
    else:
        while a2 > a1: a2 -= 2*math.pi
    step = (a2-a1) / max(1, res)
    return [(center[0]+radius*math.cos(a1+i*step),
             center[1]+radius*math.sin(a1+i*step)) for i in range(res+1)]

def _bextend(path, pts):
    path.extend(pts if not path else pts[1:])


def _build_ta_belt_tooth(spec):
    """
    Build one T/AT belt tooth profile: list of (x,y) from (-pitch/2,0) to (pitch/2,0).
    y=0 at OD surface, y<0 into tooth (toward tip).
    Uses _generate_trapezoidal_groove (y=0 at OD, y>0 into groove) then negates y.
    """
    pitch    = spec["pitch"]
    ht       = spec["ht"]
    beta2    = spec["beta2"]
    beta_rad = math.radians(beta2 / 2.0)

    if spec["s_dim"] == "floor":
        # AT: S_h is floor width → derive groove opening at OD
        groove_opening = spec["S"] + 2.0 * ht * math.tan(beta_rad)
    else:
        # T: S_r is opening at OD directly
        groove_opening = spec["S"]

    raw = _generate_trapezoidal_groove(
        depth=ht,
        groove_opening=groove_opening,
        tip_radius=spec["rr"],
        root_radius=spec["ra"],
        tooth_angle_deg=beta2,
    )
    # raw: y=0 at OD, y>0 into tooth — negate y and add land segments
    path = [(-pitch / 2.0, 0.0)]
    lx = raw[0][0]
    if lx > -pitch / 2.0 + 1e-9:
        path.append((lx, 0.0))
    for x, y in raw:
        path.append((x, -y))
    rx = raw[-1][0]
    if rx < pitch / 2.0 - 1e-9:
        path.append((rx, 0.0))
    path.append((pitch / 2.0, 0.0))
    return path


# ── H-series belt tooth ───────────────────────────────────────────────────────

def _belt_h_arc_centers(spec):
    P1x, P1y = spec["P1"];  r1 = spec["r1"]
    P3x, P3y = spec["P3"];  P4x, P4y = spec["P4"];  r2 = spec["r2"]
    C1 = (P1x, P1y - r1)
    mx, my   = (P3x+P4x)/2, (P3y+P4y)/2
    dx, dy   = P4x-P3x, P4y-P3y
    chord    = math.hypot(dx, dy)
    px, py   = -dy/chord, dx/chord
    h        = math.sqrt(max(0.0, r2**2 - (chord/2)**2))
    Ca = (mx+h*px, my+h*py);  Cb = (mx-h*px, my-h*py)
    return C1, (Ca if abs(Ca[0]) <= abs(Cb[0]) else Cb)


def _build_h_belt_tooth(spec, res=32):
    pitch = spec["pitch"]
    P1x, _ = spec["P1"];  P2x, P2y = spec["P2"]
    P3x, P3y = spec["P3"];  P4x, P4y = spec["P4"]
    r1, r2 = spec["r1"], spec["r2"]
    C1, C2 = _belt_h_arc_centers(spec)
    a_r1_s = math.atan2(0-C1[1], P1x-C1[0])
    a_r1_e = math.atan2(P2y-C1[1], P2x-C1[0])
    a_r2_s = math.atan2(P3y-C2[1], P3x-C2[0])
    a_r2_e = math.atan2(P4y-C2[1], P4x-C2[0])
    lh = []
    _bextend(lh, _bsamp_line((-pitch/2, 0), (P1x, 0)))
    _bextend(lh, _bsamp_arc(C1, r1, a_r1_s, a_r1_e, ccw=False, res=res))
    _bextend(lh, _bsamp_line((P2x, P2y), (P3x, P3y)))
    _bextend(lh, _bsamp_arc(C2, r2, a_r2_s, a_r2_e, ccw=True, res=res*2))
    rh = [(-x, y) for x, y in reversed(lh)]
    tooth = list(lh);  _bextend(tooth, rh[1:])
    return tooth


def generate_h_belt_profile(pitch_key: str, n_teeth: int = 3, res: int = 32):
    """
    Build closed polygon for an H-series belt cross-section (n_teeth teeth).
    Returns (pts, geo): pts = list of (x,y); geo = dict with C1, C2, P1-P4, belt_y, aa.
    """
    spec = H_BELT_SPECS[pitch_key]
    pitch, hs, ht = spec["pitch"], spec["hs"], spec["ht"]
    one_tooth = _build_h_belt_tooth(spec, res=res)
    half_w = n_teeth * pitch / 2.0
    bottom = []
    for i in range(n_teeth):
        off = (i - (n_teeth-1)/2.0) * pitch
        shifted = [(x+off, y) for x, y in one_tooth]
        bottom.extend(shifted if i==0 else shifted[1:])
    belt_y = hs - ht
    pts = list(bottom);  pts.append((half_w, belt_y));  pts.append((-half_w, belt_y))
    C1, C2 = _belt_h_arc_centers(spec)
    return pts, {"C1": C1, "C2": C2, "P1": spec["P1"], "P2": spec["P2"],
                 "P3": spec["P3"], "P4": spec["P4"], "belt_y": belt_y, "aa": spec["aa"]}


# ── G-series (GT/GT3) belt tooth ─────────────────────────────────────────────
# Uses the same arc-flank construction as H-series; just shallower teeth.

def generate_g_belt_profile(pitch_key: str, n_teeth: int = 3, res: int = 32):
    """
    Build closed polygon for a G-series (GT/GT3) belt cross-section (n_teeth teeth).
    Returns (pts, geo): pts = list of (x,y); geo = dict with belt_y, aa, r1, r2.
    """
    spec = G_BELT_SPECS[pitch_key]
    pitch, hs, ht = spec["pitch"], spec["hs"], spec["ht"]
    one_tooth = _build_h_belt_tooth(spec, res=res)   # same arc construction
    half_w = n_teeth * pitch / 2.0
    bottom = []
    for i in range(n_teeth):
        off = (i - (n_teeth-1)/2.0) * pitch
        shifted = [(x+off, y) for x, y in one_tooth]
        bottom.extend(shifted if i==0 else shifted[1:])
    belt_y = hs - ht
    pts = list(bottom);  pts.append((half_w, belt_y));  pts.append((-half_w, belt_y))
    C1, C2 = _belt_h_arc_centers(spec)
    return pts, {"C1": C1, "C2": C2, "P1": spec["P1"], "P2": spec["P2"],
                 "P3": spec["P3"], "P4": spec["P4"], "belt_y": belt_y,
                 "aa": spec["aa"], "r1": spec["r1"], "r2": spec["r2"]}


# ── S-series belt tooth ───────────────────────────────────────────────────────

def _build_s_belt_tooth(belt_spec, res=24):
    """One S-belt tooth cell (x,y), flat tip (no R5 dome)."""
    pitch = belt_spec["pitch"];  aa = belt_spec["aa"];  ht = belt_spec["ht"]
    Bg = belt_spec["Bg"];  R1 = belt_spec["R1"]
    rr = belt_spec["rr"];  ra = belt_spec["ra"]
    r1 = max(1e-4, R1);  r2 = max(1e-4, rr);  r4 = max(1e-4, ra)

    C1x = Bg / 2.0;  C1y = aa
    C2y = -r2
    C2x = C1x - math.sqrt(max(0.0, (r1+r2)**2 - (C2y-C1y)**2))
    d_12 = math.hypot(C2x-C1x, C2y-C1y)
    T12x = C1x + r1*(C2x-C1x)/max(d_12, 1e-9)
    T12y = C1y + r1*(C2y-C1y)/max(d_12, 1e-9)

    C4y = -ht + r4
    C4x = C1x - math.sqrt(max(0.0, (r1-r4)**2 - (C4y-C1y)**2))
    d_14 = math.hypot(C4x-C1x, C4y-C1y)
    T14x = C1x + r1*(C4x-C1x)/max(d_14, 1e-9)
    T14y = C1y + r1*(C4y-C1y)/max(d_14, 1e-9)

    a_R2_s = math.pi/2.0;  a_R2_e = math.atan2(T12y-C2y, T12x-C2x)
    a_R1_s = math.atan2(T12y-C1y, T12x-C1x);  a_R1_e = math.atan2(T14y-C1y, T14x-C1x)
    a_R4_s = math.atan2(T14y-C4y, T14x-C4x);  a_R4_e = -math.pi/2.0

    left = []
    _bextend(left, _bsamp_line((-pitch/2, 0), (C2x, 0), res=2))
    _bextend(left, _bsamp_arc((C2x, C2y), r2, a_R2_s, a_R2_e, ccw=False, res=max(4, res//4)))
    _bextend(left, _bsamp_arc((C1x, C1y), r1, a_R1_s, a_R1_e, ccw=True, res=res*2))
    _bextend(left, _bsamp_arc((C4x, C4y), r4, a_R4_s, a_R4_e, ccw=True, res=res))
    path = list(left)
    _bextend(path, _bsamp_line((C4x, -ht), (-C4x, -ht), res=2))
    rh = [(-x, y) for x, y in reversed(left)]
    _bextend(path, rh[1:])
    return path


def generate_s_belt_profile(pitch_key: str, n_teeth: int = 3, res: int = 24):
    """
    Build closed polygon for an S-series belt cross-section (n_teeth teeth).
    Returns (pts, geo): pts = list of (x,y); geo = dict with belt_y, aa, pitch, hs, ht.
    """
    spec = S_BELT_SPECS[pitch_key]
    pitch, hs, ht = spec["pitch"], spec["hs"], spec["ht"]
    one_tooth = _build_s_belt_tooth(spec, res=res)
    half_w = n_teeth * pitch / 2.0
    bottom = []
    for i in range(n_teeth):
        off = (i - (n_teeth-1)/2.0) * pitch
        shifted = [(x+off, y) for x, y in one_tooth]
        bottom.extend(shifted if i==0 else shifted[1:])
    belt_y = hs - ht
    pts = list(bottom);  pts.append((half_w, belt_y));  pts.append((-half_w, belt_y))
    return pts, {"belt_y": belt_y, "aa": spec["aa"],
                 "pitch": pitch, "hs": hs, "ht": ht}


# ── R-series (RPP) belt tooth ─────────────────────────────────────────────────

def _build_r_belt_tooth(spec, res=60):
    """
    One R-belt tooth cell (bx, by) in belt coordinates.
      by = 0   at toothed face (OD land)
      by < 0   into lobes (tip at by = -ht)
    Runs from (-pitch/2, 0) → lobes → (+pitch/2, 0).
    """
    pitch  = spec["pitch"]
    S, ht, rr, C_par = spec["S"], spec["ht"], spec["rr"], spec["C"]
    half_p = pitch / 2.0
    yo     = -ht
    # xo: vertex x so parabola f(x) = yo + C*(x-xo)^2 passes through (S/2, 0)
    xo     = S / 2.0 - math.sqrt(ht / C_par)

    f  = lambda x: yo + C_par * (x - xo) ** 2
    fp = lambda x: 2.0 * C_par * (x - xo)

    # Bisect for rr fillet tangency on right flank
    h = lambda x: (f(x) + rr) ** 2 * (1.0 + fp(x) ** 2) - rr ** 2
    roots, x, pv, step = [], xo + 1e-5, h(xo + 1e-5), 0.0002
    while x <= S / 2.0:
        x2, v = x + step, h(x + step)
        if (v > 0.0) != (pv > 0.0):
            a, b = x, x2
            for _ in range(90):
                m = 0.5 * (a + b)
                if (h(a) > 0.0) != (h(m) > 0.0): b = m
                else:                               a = m
            roots.append(0.5 * (a + b))
        x, pv = x2, v
    x_tan = max(roots) if roots else (xo + S / 2.0) / 2.0
    y_tan = f(x_tan)
    xc    = x_tan + (y_tan + rr) * fp(x_tan)   # fillet centre x
    cy_r  = -rr                                  # fillet centre y

    # Right parabola: vertex → tangency
    right_para = [(xo + (x_tan - xo) * i / (res - 1),
                   f(xo  + (x_tan - xo) * i / (res - 1))) for i in range(res)]
    left_para  = [(-xv, yv) for xv, yv in reversed(right_para)]

    # rr fillet arc: (xc, 0) CW → tangency point
    a_top = math.pi / 2.0
    a_tan = math.atan2(y_tan - cy_r, x_tan - xc)
    if a_tan < a_top: a_tan += 2.0 * math.pi
    right_fillet = [(xc   + rr * math.cos(a_top + (a_tan - a_top) * i / 41.0),
                     cy_r + rr * math.sin(a_top + (a_tan - a_top) * i / 41.0))
                    for i in range(42)]
    left_fillet = [(-xv, yv) for xv, yv in right_fillet]

    # Centre bridge between the two lobe origins (slight dip toward root)
    bridge_rise = 0.18 * (S / 3.30)
    bridge = [(-xo + 2.0 * xo * i / 119.0,
               yo + bridge_rise * (1.0 - ((-xo + 2.0 * xo * i / 119.0) / xo) ** 2) ** 2)
              for i in range(120)]

    # Flat OD lands
    left_land  = [(-half_p + (-xc + half_p) * i / 40.0, 0.0) for i in range(41)]
    right_land = [(xc + (half_p - xc) * i / 40.0,       0.0) for i in range(41)]

    tooth = list(left_land)
    _bextend(tooth, left_fillet[1:])
    _bextend(tooth, left_para[1:])
    _bextend(tooth, bridge[1:])
    _bextend(tooth, right_para[1:])
    _bextend(tooth, list(reversed(right_fillet))[1:])
    _bextend(tooth, right_land[1:])
    return tooth


def generate_r_belt_profile(pitch_key: str, n_teeth: int = 3, res: int = 60):
    """
    Build closed polygon for an R-series belt cross-section (n_teeth teeth).
    Returns (pts, geo): pts = list of (x,y); geo = dict with belt_y, aa, pitch, hs, ht.
    """
    spec = R_BELT_SPECS[pitch_key]
    pitch, hs, ht = spec["pitch"], spec["hs"], spec["ht"]
    one_tooth = _build_r_belt_tooth(spec, res=res)
    half_w = n_teeth * pitch / 2.0
    bottom = []
    for i in range(n_teeth):
        off = (i - (n_teeth - 1) / 2.0) * pitch
        shifted = [(x + off, y) for x, y in one_tooth]
        bottom.extend(shifted if i == 0 else shifted[1:])
    belt_y = hs - ht
    pts = list(bottom)
    pts.append((half_w, belt_y))
    pts.append((-half_w, belt_y))
    return pts, {"belt_y": belt_y, "aa": spec["aa"],
                 "pitch": pitch, "hs": hs, "ht": ht}


# ---------------------------------------------------------------------------
# ── T-series belt tooth profile ──────────────────────────────────────────────

def generate_t_belt_profile(pitch_key: str, n_teeth: int = 3):
    """
    Build closed polygon for a T-series belt cross-section (n_teeth teeth).
    Returns (pts, geo): pts = list of (x,y); geo = dict with belt_y, aa, ht, hs.
    """
    spec  = T_BELT_SPECS[pitch_key]
    pitch = spec["pitch"];  hs = spec["hs"];  ht = spec["ht"];  aa = spec["aa"]
    one_tooth = _build_ta_belt_tooth(spec)
    belt_y = hs - ht
    half_w = n_teeth * pitch / 2.0
    pts = []
    for i in range(n_teeth):
        offset = (i - (n_teeth - 1) / 2.0) * pitch
        shifted = [(x + offset, y) for x, y in one_tooth]
        _bextend(pts, shifted)
    pts.append(( half_w, belt_y))
    pts.append((-half_w, belt_y))
    pts.append(pts[0])
    geo = {"belt_y": belt_y, "aa": aa, "ht": ht, "hs": hs, "pitch": pitch}
    return pts, geo


# ── AT-series belt tooth profile ─────────────────────────────────────────────

def generate_at_belt_profile(pitch_key: str, n_teeth: int = 3):
    """
    Build closed polygon for an AT-series belt cross-section (n_teeth teeth).
    Returns (pts, geo): pts = list of (x,y); geo = dict with belt_y, aa, ht, hs.
    """
    spec  = AT_BELT_SPECS[pitch_key]
    pitch = spec["pitch"];  hs = spec["hs"];  ht = spec["ht"];  aa = spec["aa"]
    one_tooth = _build_ta_belt_tooth(spec)
    belt_y = hs - ht
    half_w = n_teeth * pitch / 2.0
    pts = []
    for i in range(n_teeth):
        offset = (i - (n_teeth - 1) / 2.0) * pitch
        shifted = [(x + offset, y) for x, y in one_tooth]
        _bextend(pts, shifted)
    pts.append(( half_w, belt_y))
    pts.append((-half_w, belt_y))
    pts.append(pts[0])
    geo = {"belt_y": belt_y, "aa": aa, "ht": ht, "hs": hs, "pitch": pitch}
    return pts, geo


# ── Imperial belt tooth profile ───────────────────────────────────────────────

def generate_imperial_belt_profile(pitch_key: str, n_teeth: int = 3):
    """
    Build closed polygon for an Imperial belt cross-section (n_teeth teeth).
    Returns (pts, geo): pts = list of (x,y); geo = dict with belt_y, aa, ht, hs.
    """
    spec  = IMPERIAL_BELT_SPECS[pitch_key]
    pitch = spec["pitch"];  hs = spec["hs"];  ht = spec["ht"];  aa = spec["aa"]
    one_tooth = _build_ta_belt_tooth(spec)
    belt_y = hs - ht
    half_w = n_teeth * pitch / 2.0
    pts = []
    for i in range(n_teeth):
        offset = (i - (n_teeth - 1) / 2.0) * pitch
        shifted = [(x + offset, y) for x, y in one_tooth]
        _bextend(pts, shifted)
    pts.append(( half_w, belt_y))
    pts.append((-half_w, belt_y))
    pts.append(pts[0])
    geo = {"belt_y": belt_y, "aa": aa, "ht": ht, "hs": hs, "pitch": pitch}
    return pts, geo


# Belt-length / centre-distance correction
# ---------------------------------------------------------------------------

def open_belt_length(R_left: float, R_right: float, C: float) -> float:
    """Exact open-belt pitch-line length for a given centre distance C."""
    alpha     = math.asin(max(-1.0, min(1.0, (R_right - R_left) / C)))
    straight  = 2.0 * C * math.cos(alpha)
    arc_left  = R_left  * (math.pi - 2.0 * alpha)
    arc_right = R_right * (math.pi + 2.0 * alpha)
    return straight + arc_left + arc_right


def correct_center_distance(pitch_mm: float, left_teeth: int, right_teeth: int,
                             nominal_center: float):
    """
    Round belt length UP to the nearest whole pitch increment then
    back-calculate the exact centre distance that achieves it.

    Returns (L_target_mm, n_belt_teeth, C_corrected_mm).
    """
    R_left  = left_teeth  * pitch_mm / (2.0 * math.pi)
    R_right = right_teeth * pitch_mm / (2.0 * math.pi)
    C_nom   = max(float(nominal_center), R_left + R_right)
    L_nom   = open_belt_length(R_left, R_right, C_nom)

    n_belt   = math.ceil(L_nom / pitch_mm)
    L_target = n_belt * pitch_mm

    C_lo = C_nom
    C_hi = C_nom * 2.0
    while open_belt_length(R_left, R_right, C_hi) < L_target:
        C_hi *= 2.0
    for _ in range(80):
        C_mid = (C_lo + C_hi) / 2.0
        if open_belt_length(R_left, R_right, C_mid) < L_target:
            C_lo = C_mid
        else:
            C_hi = C_mid
    return L_target, n_belt, (C_lo + C_hi) / 2.0


def center_dist_from_belt_teeth(pitch_mm: float, left_teeth: int, right_teeth: int,
                                 n_belt: int):
    """
    Find the centre distance that gives exactly n_belt pitch lengths.
    Returns None if n_belt is geometrically too small.
    """
    R_left   = left_teeth  * pitch_mm / (2.0 * math.pi)
    R_right  = right_teeth * pitch_mm / (2.0 * math.pi)
    C_min    = R_left + R_right
    L_target = n_belt * pitch_mm
    L_at_min = open_belt_length(R_left, R_right, C_min * 1.0001)
    if L_target < L_at_min:
        return None   # belt too short to fit around both pulleys

    C_lo = C_min * 1.0001
    C_hi = C_lo * 2.0
    while open_belt_length(R_left, R_right, C_hi) < L_target:
        C_hi *= 2.0
    for _ in range(80):
        C_mid = (C_lo + C_hi) / 2.0
        if open_belt_length(R_left, R_right, C_mid) < L_target:
            C_lo = C_mid
        else:
            C_hi = C_mid
    return (C_lo + C_hi) / 2.0


def build_two_pulley_belt(family: str, pitch: str, left_teeth: int, right_teeth: int,
                          center_dist_mm: float, x_offset: float = 0.0):
    """
    Compute belt geometry (mm) for two pulleys sharing the same family/pitch.

    Left pulley centre:  (x_offset, 0)
    Right pulley centre: (x_offset + C, 0)

    Returns
    -------
    belt_ring_poly : [(x,y), ...]   closed strip (belt back + OD surface)
    tooth_polys    : [[(x,y), ...]] one closed polygon per belt tooth
    phi_left       : float  groove-phase offset (rad) for left pulley
    phi_right      : float  groove-phase offset (rad) for right pulley

    Returns ([], [], 0.0, 0.0) for families without belt tooth data.
    """
    if family not in BELT_FAMILIES:
        return [], [], 0.0, 0.0

    if family == 'HTD':
        key = 'H' + pitch
        if key not in H_BELT_SPECS:
            return [], [], 0.0, 0.0
        belt_spec = H_BELT_SPECS[key]
        one_tooth_pts = _build_h_belt_tooth(belt_spec, res=24)
    elif family == 'GT':
        key = 'G' + pitch
        if key not in G_BELT_SPECS:
            return [], [], 0.0, 0.0
        belt_spec = G_BELT_SPECS[key]
        one_tooth_pts = _build_h_belt_tooth(belt_spec, res=24)   # same arc construction
    elif family == 'RPP':
        key = 'R' + pitch
        if key not in R_BELT_SPECS:
            return [], [], 0.0, 0.0
        belt_spec = R_BELT_SPECS[key]
        one_tooth_pts = _build_r_belt_tooth(belt_spec, res=48)
    elif family == 'T':
        if pitch not in T_BELT_SPECS:
            return [], [], 0.0, 0.0
        belt_spec = T_BELT_SPECS[pitch]
        one_tooth_pts = _build_ta_belt_tooth(belt_spec)
    elif family == 'AT':
        if pitch not in AT_BELT_SPECS:
            return [], [], 0.0, 0.0
        belt_spec = AT_BELT_SPECS[pitch]
        one_tooth_pts = _build_ta_belt_tooth(belt_spec)
    elif family == 'Imperial':
        if pitch not in IMPERIAL_BELT_SPECS:
            return [], [], 0.0, 0.0
        belt_spec = IMPERIAL_BELT_SPECS[pitch]
        one_tooth_pts = _build_ta_belt_tooth(belt_spec)
    else:   # STD
        key = 'S' + pitch
        if key not in S_BELT_SPECS:
            return [], [], 0.0, 0.0
        belt_spec = S_BELT_SPECS[key]
        one_tooth_pts = _build_s_belt_tooth(belt_spec, res=24)

    p_mm = belt_spec["pitch"]
    aa   = belt_spec["aa"]
    ht   = belt_spec["ht"]
    hs   = belt_spec["hs"]

    R_left  = left_teeth  * p_mm / (2.0 * math.pi)
    R_right = right_teeth * p_mm / (2.0 * math.pi)

    C = max(float(center_dist_mm), R_right + R_left)
    alpha = math.asin(max(-1.0, min(1.0, (R_right - R_left) / C)))

    ox = x_offset   # left-pulley centre x

    # Tangent points (open-belt geometry)
    L_top = (ox - R_left  * math.sin(alpha),  R_left  * math.cos(alpha))
    L_bot = (ox - R_left  * math.sin(alpha), -R_left  * math.cos(alpha))
    R_top = (ox + C - R_right * math.sin(alpha),  R_right * math.cos(alpha))
    R_bot = (ox + C - R_right * math.sin(alpha), -R_right * math.cos(alpha))

    # ── Pitch-line polygon (CW): top-tang → right arc ↓ → bot-tang → left arc ↑ ──
    def _sline(p0, p1, res=8):
        return [(p0[0] + i/res*(p1[0]-p0[0]),
                 p0[1] + i/res*(p1[1]-p0[1])) for i in range(res+1)]

    def _sarc(cx, cy, R, a1, a2, ccw, res=96):
        if ccw:
            while a2 < a1: a2 += 2*math.pi
        else:
            while a2 > a1: a2 -= 2*math.pi
        step = (a2-a1) / max(1, res)
        return [(cx + R*math.cos(a1+i*step),
                 cy + R*math.sin(a1+i*step)) for i in range(res+1)]

    pitch_line = []

    def _pl_ext(pts):
        pitch_line.extend(pts if not pitch_line else pts[1:])

    _pl_ext(_sline(L_top, R_top))
    a_rt = math.atan2(R_top[1], R_top[0] - (ox+C))
    a_rb = math.atan2(R_bot[1], R_bot[0] - (ox+C))
    _pl_ext(_sarc(ox+C, 0.0, R_right, a_rt, a_rb, ccw=False, res=96))
    _pl_ext(_sline(R_bot, L_bot))
    a_lb = math.atan2(L_bot[1], L_bot[0] - ox)
    a_lt = math.atan2(L_top[1], L_top[0] - ox)
    _pl_ext(_sarc(ox, 0.0, R_left, a_lb, a_lt, ccw=False, res=96))

    # ── Number of belt teeth ──────────────────────────────────────────────────
    s_tangent   = C * math.cos(alpha)
    sweep_right = math.pi + 2.0 * alpha
    sweep_left  = math.pi - 2.0 * alpha
    total_belt  = 2*s_tangent + R_right*sweep_right + R_left*sweep_left
    n_belt = max(0, round(total_belt / p_mm))

    # ── Arc-length table ──────────────────────────────────────────────────────
    n = len(pitch_line)
    S = [0.0] * n
    for i in range(1, n):
        S[i] = S[i-1] + math.hypot(pitch_line[i][0]-pitch_line[i-1][0],
                                    pitch_line[i][1]-pitch_line[i-1][1])
    close_len = math.hypot(pitch_line[0][0]-pitch_line[-1][0],
                            pitch_line[0][1]-pitch_line[-1][1])
    total_s = S[-1] + close_len

    tangents_v = []
    for i in range(n):
        j = (i+1) % n
        dx = pitch_line[j][0] - pitch_line[i][0]
        dy = pitch_line[j][1] - pitch_line[i][1]
        d  = math.hypot(dx, dy)
        tangents_v.append((dx/d, dy/d) if d > 1e-12 else (1.0, 0.0))
    # CW belt → left-hand normal = outward
    normals_v = [(-ty, tx) for tx, ty in tangents_v]

    def _offset_path(dist):
        return [(pitch_line[i][0] + dist*normals_v[i][0],
                 pitch_line[i][1] + dist*normals_v[i][1]) for i in range(n)]

    offset_back = hs - ht - aa
    back_path   = _offset_path(offset_back)
    od_path     = _offset_path(-aa)   # OD-land surface; used to close the tooth chain

    def _interp_at(s):
        s = s % total_s
        lo, hi = 0, n-1
        while hi - lo > 1:
            mid = (lo+hi)//2
            if S[mid] <= s: lo = mid
            else:            hi = mid
        frac = (s-S[lo]) / max(1e-12, S[hi]-S[lo]) if hi > lo else 0.0
        ppx = pitch_line[lo][0] + frac*(pitch_line[hi][0]-pitch_line[lo][0])
        ppy = pitch_line[lo][1] + frac*(pitch_line[hi][1]-pitch_line[lo][1])
        nnx = normals_v[lo][0]  + frac*(normals_v[hi][0] -normals_v[lo][0])
        nny = normals_v[lo][1]  + frac*(normals_v[hi][1] -normals_v[lo][1])
        d   = math.hypot(nnx, nny)
        if d > 1e-12: nnx, nny = nnx/d, nny/d
        return ppx, ppy, nnx, nny

    # Build inner (toothed) surface by concatenating all tooth profiles.
    # Shift tooth chain by +p_mm/2 so the first tooth's left land falls exactly
    # at s=0 (= pitch_line[0] = L_top tangent point).  This makes inner_pts[0]
    # and inner_pts[-1] align with back_path[0] and back_path[-1] at the same
    # belt arc-length, so the polygon closes with perpendicular cross-sections
    # and no diagonal kink above the left pulley.
    inner_pts = []
    for k in range(n_belt):
        s_c = k * p_mm + p_mm * 0.5   # tooth k spans s ∈ [k·p, (k+1)·p]
        tooth_inner = []
        for bx, by in one_tooth_pts:
            ppx, ppy, nnx, nny = _interp_at(s_c + bx)
            d = by - aa
            tooth_inner.append((ppx + d*nnx, ppy + d*nny))
        inner_pts.extend(tooth_inner if k == 0 else tooth_inner[1:])

    # Return outer and inner as separate closed paths.
    # The renderer combines them with fill-rule="evenodd" so no seam is needed:
    # outer (back_path) fills everything inside the belt outline;
    # inner (inner_pts) punches out the groove interiors and belt-loop cavity.
    belt_ring_poly = back_path      # outer boundary
    tooth_polys    = [inner_pts] if inner_pts else []   # inner boundary

    # ── Phase alignment ───────────────────────────────────────────────────────
    # Teeth shifted by +p_mm/2 relative to the original s_c = k·p indexing, so
    # the groove phase on each pulley adjusts by -(p_mm/2)/R (half a tooth angle).
    s_right_start = s_tangent
    s_left_start  = s_tangent + R_right*sweep_right + s_tangent

    a_rt_std = math.atan2(R_top[1], R_top[0] - (ox+C))
    theta_right_start = math.pi/2.0 - a_rt_std
    t_ang_right = 2.0 * math.pi / right_teeth
    phi_right = (theta_right_start - (s_right_start - p_mm * 0.5) / R_right) % t_ang_right

    a_lb_std = math.atan2(L_bot[1], L_bot[0] - ox)
    theta_left_start = math.pi/2.0 - a_lb_std
    t_ang_left = 2.0 * math.pi / left_teeth
    phi_left = (theta_left_start - (s_left_start - p_mm * 0.5) / R_left) % t_ang_left

    return belt_ring_poly, tooth_polys, phi_left, phi_right


def wrap_groove_to_pulley(groove_points, spec, num_teeth, print_extra=0.0):
    """
    Convert flat groove cross-section points into wrapped (X,Y) mm coordinates
    around the pulley OD circle.

    Returns:
        wrapped  : list of (x_mm, y_mm) for one tooth groove, rotated to angle=0
        R_OD_mm  : physical outer radius (mm)
        edge_a   : half-angle (radians) of the OD land arc between adjacent grooves
    """
    R_pitch   = (spec['pitch'] * num_teeth) / (2.0 * math.pi)
    R_OD_phys = R_pitch - spec['pitch_line_diff'] - (print_extra / 2.0)
    wrapped   = []
    for x, y in groove_points:
        r = R_OD_phys + y + (print_extra / 2.0)   # radial distance from centre (mm)
        a = x / R_pitch                             # angular position (rad)
        wrapped.append((r * math.sin(a), r * math.cos(a)))
    wrapped   = _filter_min_spacing(wrapped, 0.002)
    edge_a    = abs(groove_points[-1][0] / R_pitch)
    return wrapped, R_OD_phys, edge_a

