"""A retaining ring's McMaster-Carr number links to McMaster's own "find a
part" (the owner, 2026-10-06): their homepage with the number after #, which
opens the ring's page and its CAD download. We link to their models; their
terms forbid passing them on, so the assembly STEP models the ring itself
(cct_common.ring_outline)."""
import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / 'templates' / 'index.html').read_text(encoding='utf-8')
SPLINE = {'family': 'HTD', 'pitch': '8M', 'teeth': '24', 'bore': '23', 'belt_height': '10', 'feature_build': '1',
          'bore_shape': 'spline', 'spline_type': 'straight', 'spline_n': '6', 'spline_minor': '23',
          'spline_major': '26', 'spline_width': '6', 'spline_ring_top': '1', 'hub_od': '40', 'hub_height': '8'}


def test_the_link_is_mcmasters_find_a_part():
    m = re.search(r'function mcmasterUrl\(part\) \{ return `([^`]+)`; \}', PAGE)
    assert m and m.group(1) == 'https://www.mcmaster.com/#${encodeURIComponent(part)}'


def test_both_places_that_name_a_ring_link_it():
    """The Dimensions table's ring row and the spline card's ring line, not
    plain text."""
    assert "_withMcMaster(`${c.ring}, ${c.ring_face} face`, c.ring_mcmaster)" in PAGE
    assert "_withMcMaster(`${rt.faces.length === 2 ? '2 × ' : ''}${rt.ring}`, rt.mcmaster," in PAGE
    assert '(McMaster ${' not in PAGE                      # no number left as plain text


def test_the_server_gives_the_number(client):
    p = client.get('/api/dimensions', query_string=SPLINE).get_json()['pulleys'][0]
    assert p['ring_mcmaster'] == '98541A128'                 # DIN 471 26, McMaster's listing
