"""test_print_compensation_limits.py — 3D Print Compensation is 0 to 1.00 mm, cct_common's range for
every tool, and less for small teeth: each tooth keeps at least half its width, measured half-way
up its height (geometry/print_comp.py; the owner, 2026-10-31 and 2026-11-02; Controller's campaign
2026-10-31-print-compensation). The owner's words for both refusals; the Auto-fix is the cap; the
account's value is clamped to it on a fresh page without a word (designs_ui.js drives that)."""
import re
from pathlib import Path

import pytest

from cct_common.printer_settings import COMPENSATION_MAX_MM, COMPENSATION_MIN_MM
from geometry import print_comp

ROOT = Path(__file__).resolve().parent.parent
BASE = {'family': 'GT', 'pitch': '2M', 'teeth': 10, 'bore': 4, 'belt_height': 6, 'feature_build': 1}


@pytest.mark.parametrize('family, pitch, teeth, cap', [
    ('GT', '2M', 10, 0.18), ('GT', '2M', 20, 0.21), ('HTD', '5M', 12, 0.40), ('HTD', '5M', 20, 0.42),
    ('STD', '2M', 10, 0.17), ('AT', 'AT3', 10, 0.17), ('Imperial', 'MXL', 10, 0.18), ('HTD', '14M', 30, 1.0),
])
def test_the_cap_is_a_quarter_of_the_tooth_at_half_its_height(family, pitch, teeth, cap):
    assert print_comp.cap(family, pitch, teeth) == cap
    assert print_comp.cap(family, pitch, teeth) == min(1.0, int(print_comp.tooth_width_mid(family, pitch, teeth) / 4 * 100 + 1e-9) / 100)


def test_half_of_each_tooth_is_left_at_the_cap():
    """At the cap the tooth, measured where the cap is, keeps at least half its width."""
    import math
    from shapely.geometry import LineString, Point, Polygon
    from exporters.step_exporter import _build_outline_points, _profile_key
    from geometry.pulley_geometry import PULLEY_SPECS
    family, pitch, teeth = 'GT', '2M', 10
    pts = _build_outline_points(family, pitch, teeth)[0]
    r = max(math.hypot(x, y) for x, y in pts) - 1e-4 - 0.5 * PULLEY_SPECS[_profile_key(family, pitch)]['tooth_ht']
    after = _build_outline_points(family, pitch, teeth, print_extra_mm=print_comp.cap(family, pitch, teeth))[0]
    inter = Polygon(after).buffer(0).intersection(LineString(Point(0, 0).buffer(r, resolution=4096).exterior.coords))
    arcs = sorted(s.length for s in getattr(inter, 'geoms', [inter]) if s.length > 1e-6)
    assert arcs[len(arcs) // 2] >= print_comp.tooth_width_mid(family, pitch, teeth) / 2 - 0.01


def _refusal(client, **q):
    r = client.get('/api/dimensions', query_string={**BASE, **q})
    return r.status_code, r.get_json()


def test_above_the_range_in_the_owners_words(client):
    code, body = _refusal(client, print_extra=1.5)
    assert code == 400 and body['error'] == '3D Print Compensation must be 0 to 1.00 mm.'
    assert body['field'] == 'print_extra' and body['fix'] == '1'


def test_above_the_cap_in_the_owners_words_with_the_cap_as_its_fix(client):
    code, body = _refusal(client, print_extra=0.19)
    assert code == 400 and body['error'] == ('3D Print Compensation can be at most 0.18 mm for these teeth: more '
                                             'would leave each tooth less than half its width.')
    assert body['field'] == 'print_extra' and body['fix'] == '0.18'
    assert _refusal(client, print_extra=0.18)[0] == 200


def test_pulley_2_has_its_own_cap(client):
    drive = {'dual': 'true', 'center_distance': 60, 'teeth': 60, 'print_extra': 0.2, 'p2_teeth': 10}
    code, body = _refusal(client, **drive, p2_print_extra=0.5)
    assert code == 400 and body['field'] == 'p2_print_extra' and body['fix'] == '0.18'
    assert body['error'].startswith('Pulley 2 3D Print Compensation can be at most 0.18 mm')
    assert _refusal(client, **drive, p2_print_extra=0.18)[0] == 200


def test_the_downloads_hold_it_too(client):
    r = client.get('/download/svg', query_string={**BASE, 'print_extra': 0.5})
    assert r.status_code == 400 and 'can be at most 0.18 mm' in r.get_json()['error']


def test_the_agents_check_holds_it_too(client):
    agent = {k: v for k, v in BASE.items() if k != 'feature_build'}     # the agents' params: 3D always
    r = client.post('/api/v1/check', json={'params': {**agent, 'print_extra': 0.5}})
    body = r.get_json()
    assert r.status_code == 400 and 'can be at most 0.18 mm for these teeth' in body['error']
    assert client.post('/api/v1/check', json={'params': {**agent, 'print_extra': 0.18}}).status_code == 200


def test_the_page_asks_each_pulleys_cap(client):
    r = client.get('/api/compensation-cap', query_string={'family': 'GT', 'pitch': '2M', 'teeth': 10, 'p2_teeth': 20})
    assert r.get_json() == {'cap': [0.18, 0.21]}


def test_the_range_is_cct_commons():
    import agent
    p = {q.name: q for q in agent._params({'GT': ['2M']}, ['M3'])}['print_extra']
    assert (p.min, p.max) == (COMPENSATION_MIN_MM, COMPENSATION_MAX_MM)
    page = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')
    for el in ('print_extra', 'p2_print_extra'):
        assert re.search(rf'id="{el}" value="0" min="0" max="1" step="0.01"', page), el


def test_the_help_says_so():
    text = re.sub(r'\s+', ' ', (ROOT / 'static' / '2d_advanced_help.html').read_text(encoding='utf-8'))
    assert ('It is 0 to 1.00 mm, the same in every tool, and less for small teeth: each tooth keeps at least '
            'half its width, so it is at most 0.18 mm for a 10-tooth GT2 pulley and 0.40 mm for a 12-tooth '
            'HTD 5M.') in text
    assert print_comp.cap('GT', '2M', 10) == 0.18 and print_comp.cap('HTD', '5M', 12) == 0.40
