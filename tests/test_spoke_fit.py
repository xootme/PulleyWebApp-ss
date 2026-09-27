"""
test_spoke_fit.py — spoke settings that fit the pulley (geometry/spoke_fit.py).

Covers:
  * fit_spokes: settings that fit are left alone; ones that don't get the
    smallest change that makes them buildable, in the documented order;
    pulleys too small for any spokes say so
  * the builder's report of a requested fillet it silently left out (the bug
    behind "tip fillet not valid, picture shows 0, why no warning")
  * a property sweep: every fitted result really builds
  * _parse_spoke_params builds with the fitted values; /api/spoke-fit
"""
import math

import pytest

from exporters.png_exporter import _spoke_void_polygons
from geometry.pulley_geometry import pulley_outline_segments
from geometry.spoke_fit import (
    HUB_WALL, MIN_RIM, _valid, describe, fit_spokes,
)

GOOD = dict(hub_od=18, rim_depth=3, width=5, fillet_tip=1.5, fillet_base=2, count=6)


def r_root(family, pitch, teeth, pe=0.0):
    _, _, _, wrapped = pulley_outline_segments(family, pitch, teeth, 0.0, 0.0, pe)
    return min(math.hypot(x, y) for x, y in wrapped)


def changed(fit):
    return {k for k, _, _ in fit.changes}


# ============================================================================
# 1.  fit_spokes
# ============================================================================

class TestFitSpokes:

    def test_settings_that_fit_are_left_alone(self):
        fit = fit_spokes(r_root('HTD', '5M', 40), 8, **GOOD)
        assert fit.ok and fit.possible and not fit.changes
        assert fit.fitted == {k: float(v) if k != 'count' else v for k, v in GOOD.items()}

    def test_reported_bug_only_the_tip_fillet_changes(self):
        """HTD 3M 30T, Fillet Tip 6: the builder drew no tip fillet and the page
        showed no warning. Now the tip alone is reduced to one that is drawn."""
        fit = fit_spokes(r_root('HTD', '3M', 30), 8, hub_od=16, rim_depth=2, width=5,
                         fillet_tip=6, fillet_base=1.5, count=4)
        assert not fit.ok and fit.possible
        assert changed(fit) == {'fillet_tip'}
        assert 0.25 <= fit.fitted['fillet_tip'] < 6

    def test_room_is_made_by_shrinking_hub_and_rim_together(self):
        fit = fit_spokes(r_root('HTD', '3M', 24), 8, **GOOD)
        assert fit.possible and not fit.ok
        f = fit.fitted
        assert MIN_RIM <= f['rim_depth'] < GOOD['rim_depth']
        assert 8 + 2 * HUB_WALL <= f['hub_od'] < GOOD['hub_od']

    def test_hub_grows_to_clear_a_large_bore(self):
        fit = fit_spokes(r_root('HTD', '5M', 40), 30, **GOOD)
        assert fit.possible and changed(fit) == {'hub_od'}
        assert fit.fitted['hub_od'] > 30

    def test_too_small_for_spokes(self):
        fit = fit_spokes(r_root('GT', '2M', 16), 5, **GOOD)
        assert not fit.ok and not fit.possible
        assert 'too small for spokes' in describe(fit)[0]

    def test_sliver_fillets_are_reported_as_zero(self):
        fit = fit_spokes(r_root('HTD', '3M', 24), 8, **GOOD)
        for k in ('fillet_tip', 'fillet_base'):
            v = fit.fitted[k]
            assert v == 0.0 or v >= 0.25, (k, v)

    def test_describe_lists_each_change(self):
        fit = fit_spokes(r_root('HTD', '3M', 30), 8, hub_od=16, rim_depth=2, width=5,
                         fillet_tip=6, fillet_base=1.5, count=4)
        lines = describe(fit)
        assert len(lines) == 1 and lines[0].startswith('Fillet Tip: 6 → ')


# ============================================================================
# 2.  the builder's report of a dropped fillet
# ============================================================================

class TestDroppedFilletReport:

    def test_oversized_tip_fillet_is_reported(self):
        r = r_root('HTD', '3M', 30)
        report = {}
        _spoke_void_polygons(8.0, r - 2, 4, 5, 6, 1.5, report=report)
        assert report.get('tip_dropped')

    def test_fitting_fillets_report_nothing(self):
        report = {}
        _spoke_void_polygons(9.0, r_root('HTD', '5M', 40) - 3, 6, 5, 1.5, 2, report=report)
        assert report == {}

    def test_report_is_optional_and_does_not_change_the_output(self):
        r = r_root('HTD', '3M', 30) - 2
        assert _spoke_void_polygons(8.0, r, 4, 5, 6, 1.5) == \
            _spoke_void_polygons(8.0, r, 4, 5, 6, 1.5, report={})


# ============================================================================
# 3.  property sweep: whatever is fitted, builds
# ============================================================================

PULLEYS = [('HTD', '5M', 40), ('HTD', '3M', 30), ('HTD', '3M', 20), ('GT', '2M', 30),
           ('HTD', '8M', 24), ('GT', '2M', 60)]
SETTINGS = [
    GOOD,
    dict(hub_od=16, rim_depth=2, width=5, fillet_tip=6, fillet_base=1.5, count=4),
    dict(hub_od=30, rim_depth=6, width=10, fillet_tip=4, fillet_base=6, count=10),
    dict(hub_od=10, rim_depth=1, width=2, fillet_tip=0, fillet_base=0, count=3),
    dict(hub_od=12, rim_depth=2, width=3, fillet_tip=0.7, fillet_base=3.2, count=7),
]


@pytest.mark.parametrize('family,pitch,teeth', PULLEYS)
@pytest.mark.parametrize('s', SETTINGS, ids=range(len(SETTINGS)))
def test_every_fitted_result_builds(family, pitch, teeth, s):
    r = r_root(family, pitch, teeth)
    fit = fit_spokes(r, 5, **s)
    if not fit.possible:
        return
    assert _valid(round(r, 4), 5.0, fit.fitted)
    f = fit.fitted
    for k in ('rim_depth', 'width', 'fillet_tip', 'fillet_base', 'count'):
        assert f[k] <= s[k] + 1e-9, (k, f[k], s[k])        # only ever reduced
    assert f['count'] >= 2


# ============================================================================
# 4.  the app: parser and route
# ============================================================================

BUG_ARGS = {
    'family': 'HTD', 'pitch': '3M', 'teeth': '20', 'bore': '8', 'p2_teeth': '30',
    'p2_bore': '8', 'p2_spokes_enabled': '1', 'p2_spokes_hub_od': '16',
    'p2_spokes_rim_depth': '2', 'p2_spokes_width': '5', 'p2_spokes_fillet_tip': '6',
    'p2_spokes_fillet_base': '1.5', 'p2_spokes_count': '4',
}


class TestAppWiring:

    def test_parser_builds_with_the_fitted_values(self):
        from app import _parse_spoke_params
        en, hub, rim, w, tip, base, n, _, _ = _parse_spoke_params(BUG_ARGS, 'p2_')
        assert en and (hub, rim, w, base, n) == (16, 2, 5, 1.5, 4)
        assert 0.25 <= tip < 6

    def test_parser_leaves_spokes_out_when_none_fit(self):
        from app import _parse_spoke_params
        args = {'family': 'GT', 'pitch': '2M', 'teeth': '16', 'bore': '5',
                'spokes_enabled': '1', 'spokes_hub_od': '18', 'spokes_rim_depth': '3',
                'spokes_width': '5', 'spokes_fillet_tip': '1.5', 'spokes_fillet_base': '2',
                'spokes_count': '6'}
        assert _parse_spoke_params(args, '')[0] is False

    def test_parser_passes_fitting_settings_through(self):
        from app import _parse_spoke_params
        args = {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'bore': '8',
                'spokes_enabled': '1', 'spokes_hub_od': '18', 'spokes_rim_depth': '3',
                'spokes_width': '5', 'spokes_fillet_tip': '1.5', 'spokes_fillet_base': '2',
                'spokes_count': '6'}
        assert _parse_spoke_params(args, '')[:7] == (True, 18, 3, 5, 1.5, 2, 6)

    def test_route_reports_changes(self, client):
        r = client.get('/api/spoke-fit', query_string={
            'family': 'HTD', 'pitch': '3M', 'teeth': '30', 'bore': '8',
            'spokes_hub_od': '16', 'spokes_rim_depth': '2', 'spokes_width': '5',
            'spokes_fillet_tip': '6', 'spokes_fillet_base': '1.5', 'spokes_count': '4'})
        d = r.get_json()
        assert r.status_code == 200 and d['possible'] and not d['ok']
        assert len(d['changes']) == 1 and d['changes'][0].startswith('Fillet Tip')
        assert d['fitted']['fillet_tip'] < 6

    def test_route_ok_when_it_fits(self, client):
        d = client.get('/api/spoke-fit', query_string={
            'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'bore': '8',
            'spokes_hub_od': '18', 'spokes_rim_depth': '3', 'spokes_width': '5',
            'spokes_fillet_tip': '1.5', 'spokes_fillet_base': '2',
            'spokes_count': '6'}).get_json()
        assert d['ok'] and d['changes'] == []

    def test_formerly_failing_export_now_builds(self, client):
        """fillet_base reaching past fillet_tip used to make the export fail
        (the 7-spoke GT-2M-67T repro in _check_spoke_fillet_order)."""
        r = client.get('/download/svg', query_string={
            'family': 'GT', 'pitch': '2M', 'teeth': '67', 'bore': '5',
            'spokes_enabled': '1', 'spokes_hub_od': '12', 'spokes_rim_depth': '2',
            'spokes_width': '3', 'spokes_fillet_tip': '0.7', 'spokes_fillet_base': '3.2',
            'spokes_count': '7'})
        assert r.status_code == 200, r.data[:200]
