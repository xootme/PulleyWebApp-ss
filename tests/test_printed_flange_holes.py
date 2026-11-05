"""test_printed_flange_holes.py — a captured nut's screw hole and pocket stay open through a top
flange printed in place. The STL unions the flanges onto the pulley; cut before that, the hole
where it crossed the top flange's band was filled by the flange, so the screw couldn't pass
(the night of 2026-11-01's random design [3]: 7.3 of the hole's 86.8 mm3; the owner,
2026-11-02, with SmallStep). generate_pulley_stl now leaves them uncut (cuts_out) and
app._printed_as_one cuts them from the union."""
import io
import struct

import pytest

# The night's [3], as the random test sent it (its screw by diameter alone)
NIGHT_3 = {
    'family': 'STD', 'pitch': '2M', 'teeth': '54', 'bore': '11.2', 'print_extra': '0.15',   # 0.39 that night: over its cap now
    'clearance_preset': 'TIGHT', 'backlash_preset': 'NONE', 'belt_height': '13.2',
    'clearance_height': '0.4', 'hub_flat_depth': '2.61', 'hub_od': '28.7', 'hub_height': '5.4',
    'hub_screw_dia': '3.0', 'hub_screw_count': '1', 'hub_captured_nut': '1',
    'spokes_enabled': '1', 'spokes_hub_od': '28.7', 'spokes_rim_depth': '6.9', 'spokes_width': '4.4',
    'spokes_fillet_tip': '1.2', 'spokes_fillet_base': '1.2', 'spokes_count': '6', 'spokes_height': '3.8',
    'flange_enabled': '1', 'flange_3dprint': '1', 'flange_angle': '8.7', 'flange_rim_radius': '4.5',
    'flange_height': '3.5', 'flange_plate_height': '0.9', 'flange_bend_radius': '1.1',
    'flange_top_separate': '0', 'sv': '1',
}


def _stl_and_cuts(client, monkeypatch, q):
    import trimesh
    import app as A
    seen = []
    real = A.generate_pulley_stl

    def spy(*a, **k):
        seen.append(k.get('cuts_out'))
        return real(*a, **k)
    monkeypatch.setattr(A, 'generate_pulley_stl', spy)
    r = client.get('/download/stl', query_string=q)
    assert r.status_code == 200, r.data[:200]
    d = r.data[:84 + 50 * struct.unpack('<I', r.data[80:84])[0]]      # less the CCT trailer
    return trimesh.load(io.BytesIO(d), file_type='stl'), seen[-1]


def test_a_captured_nuts_hole_is_open_through_a_top_flange_printed_in_place(client, monkeypatch):
    import trimesh
    mesh, cuts = _stl_and_cuts(client, monkeypatch, NIGHT_3)
    assert mesh.is_watertight
    assert len(cuts) == 2                                     # the screw hole and the nut pocket
    for cut in cuts:
        left = trimesh.boolean.intersection([mesh, cut], engine='manifold').volume
        assert left == pytest.approx(0.0, abs=0.01), f'{left:.2f} mm3 of a {cut.volume:.1f} mm3 cut is filled'


@pytest.mark.parametrize('q', [{'flange_top_separate': '1'}, {'flange_3dprint': '0'}, {'flange_enabled': '0'}],
                         ids=['separate top flange', 'metal flanges', 'no flanges'])
def test_the_pulley_cuts_its_own_holes_otherwise(client, monkeypatch, q):
    """Only a top flange printed in place defers them; every other STL is cut as before."""
    _mesh, cuts = _stl_and_cuts(client, monkeypatch, {**NIGHT_3, **q})
    assert cuts is None
