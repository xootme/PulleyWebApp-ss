"""
step_worker.py
Standalone script invoked as a subprocess by app.py to generate STEP files
using cadquery (requires Python 3.12 + cadquery-ocp).

Usage:
    python step_worker.py <json-params>

Writes STEP bytes to stdout; errors to stderr.
"""
import sys
import json
import os

# Ensure project root is on sys.path when run as a subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    params = json.loads(sys.argv[1])

    from exporters.step_exporter import generate_pulley_step

    step_bytes = generate_pulley_step(
        family        = params['family'],
        pitch         = params['pitch'],
        num_teeth     = params['num_teeth'],
        bore_mm       = params['bore_mm'],
        belt_height_mm= params['belt_height_mm'],
        clearance_mm  = params.get('clearance_mm', 0.0),
        backlash_mm   = params.get('backlash_mm', 0.0),
        print_extra_mm= params.get('print_extra_mm', 0.0),
        hub_od_mm     = params.get('hub_od_mm', 0.0),
        hub_height_mm = params.get('hub_height_mm', 0.0),
        screw_dia_mm  = params.get('screw_dia_mm', 0.0),
        screw_count   = params.get('screw_count', 0),
        captured_nut  = params.get('captured_nut', False),
        flat_depth_mm = params.get('flat_depth_mm', 0.0),
        keyway_w_mm   = params.get('keyway_w_mm', 0.0),
        keyway_h_mm   = params.get('keyway_h_mm', 0.0),
        spoke_count   = params.get('spoke_count', 0),
        spoke_width_mm= params.get('spoke_width_mm', 0.0),
        fillet_tip_mm = params.get('fillet_tip_mm', 0.0),
        fillet_base_mm= params.get('fillet_base_mm', 0.0),
        rim_depth_mm  = params.get('rim_depth_mm', 0.0),
    )

    sys.stdout.buffer.write(step_bytes)

if __name__ == '__main__':
    main()
