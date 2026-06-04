"""
step_worker.py
Standalone script invoked as a subprocess by app.py to generate STEP files
using cadquery (requires Python 3.12 + cadquery-ocp).

Usage:
    python step_worker.py <json-params>

Pass export_type='flange' in the JSON to call generate_flange_step() instead
of the default generate_pulley_step().

Writes STEP bytes to stdout; progress JSON lines to stderr; errors to stderr.
"""
import sys
import json
import os

# Ensure project root is on sys.path when run as a subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def progress_callback(pct):
    """Write progress JSON to stderr so parent process can parse it."""
    sys.stderr.write(json.dumps({'progress': pct}) + '\n')
    sys.stderr.flush()

def main():
    params = json.loads(sys.argv[1])
    export_type = params.pop('export_type', 'pulley')

    if export_type == 'assembly':
        from exporters.step_exporter import generate_pulley_assembly_step
        step_bytes = generate_pulley_assembly_step(params)
    elif export_type == 'all':
        from exporters.step_exporter import generate_all_parts_step
        kw2      = params.pop('kw2', None)
        belt_kw  = params.pop('belt_kw', None)
        step_bytes = generate_all_parts_step(params, kw2, belt_kw, progress_callback=progress_callback)
    elif export_type == 'flange':
        from exporters.step_exporter import generate_flange_step
        step_bytes = generate_flange_step(
            family           = params['family'],
            pitch            = params['pitch'],
            num_teeth        = params['num_teeth'],
            bore_mm          = params['bore_mm'],
            belt_height_mm   = params['belt_height_mm'],
            clearance_mm     = params.get('clearance_mm', 0.0),
            print_extra_mm   = params.get('print_extra_mm', 0.0),
            flange_3dprint   = params.get('flange_3dprint', True),
            flange_angle_deg = params.get('flange_angle_deg', 15.0),
            rim_radius_mm    = params.get('rim_radius_mm', 3.0),
            flange_height_mm = params.get('flange_height_mm', 1.5),
            plate_height_mm  = params.get('plate_height_mm', 1.0),
            bend_radius_mm   = params.get('bend_radius_mm', 0.0),
            which            = params.get('which', 'top'),
            hub_od_mm        = params.get('hub_od_mm', 0.0),
            spokes_enabled   = params.get('spokes_enabled', False),
            spoke_hub_od_mm  = params.get('spoke_hub_od_mm', 0.0),
            rim_depth_mm     = params.get('rim_depth_mm', 0.0),
            nubs_enabled     = params.get('nubs_enabled', False),
            nub_count        = params.get('nub_count', 4),
            nub_dia_mm       = params.get('nub_dia_mm', 3.0),
            nub_height_mm    = params.get('nub_height_mm', 2.0),
            nub_allowance_mm = params.get('nub_allowance_mm', 0.2),
        )
    else:
        from exporters.step_exporter import generate_pulley_step
        step_bytes = generate_pulley_step(
            family            = params['family'],
            pitch             = params['pitch'],
            num_teeth         = params['num_teeth'],
            bore_mm           = params['bore_mm'],
            belt_height_mm    = params['belt_height_mm'],
            clearance_mm      = params.get('clearance_mm', 0.0),
            backlash_mm       = params.get('backlash_mm', 0.0),
            print_extra_mm    = params.get('print_extra_mm', 0.0),
            hub_od_mm         = params.get('hub_od_mm', 0.0),
            hub_height_mm     = params.get('hub_height_mm', 0.0),
            screw_dia_mm      = params.get('screw_dia_mm', 0.0),
            screw_count       = params.get('screw_count', 0),
            captured_nut      = params.get('captured_nut', False),
            flat_depth_mm     = params.get('flat_depth_mm', 0.0),
            keyway_w_mm       = params.get('keyway_w_mm', 0.0),
            keyway_h_mm       = params.get('keyway_h_mm', 0.0),
            spoke_count       = params.get('spoke_count', 0),
            spoke_width_mm    = params.get('spoke_width_mm', 0.0),
            spoke_hub_od_mm   = params.get('spoke_hub_od_mm', 0.0),
            rim_depth_mm      = params.get('rim_depth_mm', 0.0),
            fillet_tip_mm     = params.get('fillet_tip_mm', 0.0),
            fillet_base_mm    = params.get('fillet_base_mm', 0.0),
            spoke_height_mm   = params.get('spoke_height_mm', 0.0),
            export_fmt        = params.get('export_fmt', 'STEP'),
            flange_enabled    = params.get('flange_enabled', False),
            flange_3dprint    = params.get('flange_3dprint', True),
            flange_angle_deg  = params.get('flange_angle_deg', 15.0),
            flange_rim_radius_mm = params.get('flange_rim_radius_mm', 3.0),
            flange_height_mm  = params.get('flange_height_mm', 1.5),
            flange_top_separate = params.get('flange_top_separate', True),
            nubs_enabled      = params.get('nubs_enabled', False),
            nub_count         = params.get('nub_count', 4),
            nub_dia_mm        = params.get('nub_dia_mm', 3.0),
            nub_height_mm     = params.get('nub_height_mm', 2.0),
            nub_allowance_mm  = params.get('nub_allowance_mm', 0.2),
        )

    sys.stdout.buffer.write(step_bytes)

if __name__ == '__main__':
    main()
