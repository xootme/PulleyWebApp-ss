"""
colors.py — each part's colour in a STEP file (the owner, 2026-10-06: colour
the parts). They are the 3D view's (templates/index.html: pulleyMat,
pulley2Mat, beltMat, SPLINE_MATS), so a downloaded part looks as it did on
screen; tests/test_step_colors.py keeps the two in step.

small_step paints them: `--color` on `combined` (a pulley STEP alone), and
"color" per part in an `extrude` spec or an `assemble` manifest. A part is
coloured in ONE place — its own file or the manifest, never both (small_step
refuses that by name) — so a pulley made for an assembly carries no colour of
its own and the manifest paints it.
"""

PULLEY = {1: "#4A9FD4", 2: "#C0392B"}   # pulleyMat, pulley2Mat
BELT = "#1A1A1A"                        # beltMat
SHAFT = "#9AA3AD"                       # SPLINE_MATS.shaft
RING = "#1E293B"                        # SPLINE_MATS.rings
WASHER = "#5B9BD5"                      # SPLINE_MATS.washers
