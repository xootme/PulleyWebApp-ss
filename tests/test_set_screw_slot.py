"""test_set_screw_slot.py — a set-screw hole has a rim.

A hole wider than both the hub wall it crosses and the bore radius it opens
into breaks out of the hub inside and out at once: a slot, not a hole
(Sprocket's ADR-036, the owner 2026-10-02, from the small_step refusal study;
carried here from the bug hunt). The Dimensions panel warns, and its Auto-fix
closes the rim with a bigger Hub OD (the spokes' hub too, which the hub
follows), else a smaller screw of the same kind; never the bore.
"""
import pytest

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '30', 'bore': '8', 'belt_height': '10',
        'clearance_height': '6', 'feature_build': '1', 'hub_height': '12',
        'hub_screw_count': '1', 'hub_screw_hold': 'thread'}


def _dims(client, q):
    r = client.get('/api/dimensions', query_string=q)
    assert r.status_code == 200, r.data[:200]
    return r.get_json()


def _slot(d):
    return [w for w in d['warnings'] if 'cuts a slot' in w]


def _applied(q, d):
    from agent import _fix_params
    return {**q, **{k: str(v) for k, v in _fix_params(d['fix']['set']).items()}}


def test_the_study_shape_warns_and_a_bigger_hub_closes_it(client):
    """M10 (Ø9.19 tapped) in a Ø16 hub on an 8 mm bore: wider than the 4 mm
    wall and the 4 mm bore radius."""
    q = {**BASE, 'hub_od': '16', 'hub_screw_size': 'M10'}
    d = _dims(client, q)
    assert len(_slot(d)) == 1 and 'Ø9.19' in _slot(d)[0] and 'Ø26.5' in _slot(d)[0], d['warnings']
    assert d['fix']['set']['hub1_od'] == pytest.approx(26.5)
    assert not _slot(_dims(client, _applied(q, d)))


def test_wider_than_the_wall_alone_is_a_sound_hole(client):
    """M3 (Ø2.73) in a Ø16 hub on a 12 mm bore: wider than the 2 mm wall but
    not the 6 mm bore radius, so it has a rim — no warning."""
    d = _dims(client, {**BASE, 'bore': '12', 'hub_od': '16', 'hub_screw_size': 'M3'})
    assert not _slot(d), d['warnings']
    d = _dims(client, {**BASE, 'hub_od': '20', 'hub_screw_size': 'M3'})
    assert not _slot(d), d['warnings']


def test_where_no_hub_fits_a_smaller_screw_does(client):
    """A 14T pulley can't take the Ø23 hub an M8 needs: the largest smaller
    metric screw whose hole has a rim (M4) instead; inch stays inch."""
    q = {**BASE, 'teeth': '14', 'hub_od': '12', 'hub_screw_size': 'M8'}
    d = _dims(client, q)
    assert _slot(d) and d['fix']['set'] == {'hub1_screw_size': 'M4', **{k: v for k, v in d['fix']['set'].items()
                                                                          if k == 'clearance_height'}}
    assert not _slot(_dims(client, _applied(q, d)))
    q = {**BASE, 'teeth': '12', 'bore': '4', 'hub_od': '10', 'hub_screw_size': '1/4-20'}
    d = _dims(client, q)
    assert d['fix']['set']['hub1_screw_size'] == '#4-40'
    assert not _slot(_dims(client, _applied(q, d)))


def test_a_nut_held_screw_counts_its_clearance_hole(client):
    """Held by a nut, the hole is the ISO 273 clearance (Ø11 for M10), wider
    than the tapped hole, so the hub it needs is bigger too."""
    d = _dims(client, {**BASE, 'hub_od': '16', 'hub_screw_size': 'M10', 'hub_screw_hold': 'nut'})
    assert _slot(d) and 'Ø11.00' in _slot(d)[0]
    assert d['fix']['set']['hub1_od'] == pytest.approx(30.0)


def test_on_spokes_the_fix_moves_the_spokes_hub_too(client):
    q = {**BASE, 'teeth': '40', 'hub_od': '16', 'hub_screw_size': 'M10', 'spokes_enabled': '1',
         'spokes_hub_od': '16', 'spokes_rim_depth': '3', 'spokes_width': '5', 'spokes_count': '4'}
    d = _dims(client, q)
    assert _slot(d)
    assert d['fix']['set']['hub1_od'] == d['fix']['set']['spokes1_hub_od'] == pytest.approx(26.5)
    assert not _slot(_dims(client, _applied(q, d)))


def test_pulley_2_gets_its_own_and_the_api_names_it(client):
    q = {**BASE, 'hub_od': '20', 'hub_screw_size': 'M3', 'dual': 'true', 'p2_teeth': '30',
         'center_distance': '120', 'p2_bore': '8', 'p2_hub_od': '16', 'p2_hub_height': '12',
         'p2_hub_screw_size': 'M10', 'p2_hub_screw_count': '1', 'p2_hub_screw_hold': 'thread'}
    d = _dims(client, q)
    assert len(_slot(d)) == 1 and _slot(d)[0].startswith('Pulley 2: ')
    assert 'hub2_od' in d['fix']['set'] and 'hub1_od' not in d['fix']['set']
    from agent import _fix_params
    assert _fix_params(d['fix']['set'])['p2_hub_od'] == pytest.approx(26.5)


def test_2d_and_no_screws_are_quiet(client):
    q = {**BASE, 'hub_od': '16', 'hub_screw_size': 'M10'}
    assert not _slot(_dims(client, {**q, 'feature_build': '0'}))
    assert not _slot(_dims(client, {**q, 'hub_screw_count': '0'}))
