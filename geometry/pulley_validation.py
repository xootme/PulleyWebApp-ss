"""
pulley_validation.py
Geometry constraint rules shared by server routes, /api/validate, and test scripts.

Rules:
  bore_max (no spokes) = groove_diameter - 1 mm
  bore_max (spokes)    = spoke_void_outer_diameter - 1 mm
  hub_od_min           = bore + 1 mm  (when hub is enabled)

  groove_diameter     = OD - 2 * tooth_ht
  spoke_void_outer_d  = groove_diameter - 2 * rim_depth
"""
from geometry.pulley_geometry import PULLEY_SPECS, PROFILE_KEY_PREFIX, getOuterDiameter


def groove_diameter(family, pitch, num_teeth, clearance_mm=0.0, print_extra_mm=0.0):
    """Return (groove_d_mm, tooth_ht_mm, od_mm)."""
    key  = PROFILE_KEY_PREFIX.get(family, '') + pitch
    spec = PULLEY_SPECS[key]
    pld  = spec.get('pitch_line_diff', spec.get('pitchLineDiff', 0.0))
    od   = getOuterDiameter(num_teeth, spec['pitch'], pld + print_extra_mm - clearance_mm)
    return od - 2 * spec['tooth_ht'], spec['tooth_ht'], od


def validate_params(family, pitch, num_teeth, bore_mm,
                    clearance_mm=0.0, print_extra_mm=0.0,
                    spokes_enabled=False, rim_depth_mm=0.0,
                    hub_od_mm=0.0):
    """
    Return list of (field, message) error tuples. Empty list = valid.

    Parameters mirror _parse_stl_params / _parse_hub_params / _parse_spoke_params.
    """
    errors = []

    try:
        groove_d, tooth_ht, od = groove_diameter(
            family, pitch, num_teeth, clearance_mm, print_extra_mm)
    except KeyError:
        errors.append(('family', f'Unknown profile {family}/{pitch}'))
        return errors

    if spokes_enabled and rim_depth_mm > 0.0:
        void_od  = groove_d - 2 * rim_depth_mm
        bore_max = void_od - 1.0
        if bore_mm > bore_max:
            errors.append(('bore', (
                f'Bore {bore_mm:.2g} mm exceeds limit {bore_max:.1f} mm '
                f'(spoke void OD {void_od:.1f} mm − 1 mm)'
            )))
    else:
        bore_max = groove_d - 1.0
        if bore_mm > bore_max:
            errors.append(('bore', (
                f'Bore {bore_mm:.2g} mm exceeds limit {bore_max:.1f} mm '
                f'(groove diameter {groove_d:.1f} mm − 1 mm)'
            )))

    if hub_od_mm > 0.0 and hub_od_mm < bore_mm + 1.0:
        errors.append(('hub_od', (
            f'Hub OD {hub_od_mm:.2g} mm must be ≥ bore + 1 mm = {bore_mm + 1.0:.1f} mm'
        )))

    return errors
