"""
test_sample_designs_step.py — every pulley in cheapcadtools.com's sample
designs, made as the Download window makes its STEP, checked against its STL.

A deploy check (web_provisioning.md): the samples are the designs people open
first, and the owner's printed drives are among them. On 2026-10-12 CCT_WebSite
found, packaging them for GrabCAD, a GT2 30T whose top flange came out inside
out and an HTD 3M whose STEP was refused although the page showed no warning.
No test had made either design.

For each sample pulley:
  - the page shows no warning, so STEP must make it (a refusal is a promise
    the page can't keep);
  - every solid valid AND of positive volume, the total the STL's, with a
    separate top flange's own STL added (tests/step_checks.py).

The samples come from CCT_WebSite's site/data/samples.generated.json
(CCT_SAMPLES_JSON to point elsewhere): the site's list, never a copy that
drifts. Needs OCP (.venv312) and a small_step binary; skipped without them.
"""
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

from step_checks import STEP_REL, problems, reference_volume

SAMPLES = Path(os.environ.get(
    'CCT_SAMPLES_JSON',
    r'C:\Users\cmyer\Documents\CCT_WebSite\site\data\samples.generated.json'))

# Known faults, each with the small_step version that fixes it (None: not fixed
# yet): expected to fail (strictly) while the binary is older, so a fix shows
# up as a failure until its entry goes.
GAP57 = 'small_step 0.12 refuses a captured-nut screw wider than its D-flat (gap 57)'
HUB = "small_step 0.12 grows a captured nut's hub 0.5 mm past the app's"
KNOWN = {
    ('gt2-30t-45t-printed-three-spoke-pulleys', 1): ((0, 13, 0), HUB),
    ('htd-3m-40t-60t-printed-d-flat-six-spoke-pulleys', 1): ((0, 13, 0), GAP57),
    ('htd-3m-40t-60t-printed-d-flat-six-spoke-pulleys', 2): ((0, 13, 0), GAP57),
    ('gt2-20t-30t-stepper-pulleys-d-flat-captured-nut', 1): ((0, 13, 0), GAP57),
    ('gt2-16t-24t-zero-backlash-pulleys', 2): ((0, 13, 0), HUB),
    ('xl-20t-30t-imperial-pulleys-inch-set-screws', 1): ((0, 13, 0), HUB),
    # A hub wider than the teeth extended down through the top flange's band (its
    # skirt): small_step left the ring between the flange's cone and the hub empty.
    ('gt2-16t-24t-zero-backlash-pulleys', 1): ((0, 13, 1), 'small_step before 0.13.1 leaves the top'
                                                ' flange band under a skirted hub short (-1.03 %)'),
}


def _binary_version():
    try:
        from app import _SS_BIN
        from exporters.step_worker_ss import _binary_version as bv
        return bv(_SS_BIN) if os.path.isfile(_SS_BIN) else (0, 0, 0)
    except Exception:
        return (0, 0, 0)


def _cases():
    if not SAMPLES.is_file():
        return []
    version = _binary_version()
    out = []
    for s in json.loads(SAMPLES.read_text(encoding='utf-8')).get('samples', []):
        if s.get('app') != 'pulley':
            continue
        q = dict(parse_qsl(s['link_query']))
        for n in ((1, 2) if q.get('dual') == 'true' else (1,)):
            known = KNOWN.get((s['slug'], n))
            marks = ([pytest.mark.xfail(strict=True, reason=known[1])]
                     if known and (known[0] is None or version < known[0]) else [])
            out.append(pytest.param(s['slug'], n, q, marks=marks, id=f"{s['slug']}-p{n}"))
    return out


def _ready():
    try:
        import OCP  # noqa: F401
        from app import _SS_BIN
    except ImportError:
        return False
    return os.path.isfile(_SS_BIN)


pytestmark = pytest.mark.skipif(not _ready(), reason='needs OCP (.venv312) and a small_step binary')


@pytest.mark.parametrize('slug,n,query', _cases())
def test_sample_pulley_step_is_its_stl(client, slug, n, query):
    warnings = client.get('/api/dimensions', query_string=query).get_json().get('warnings') or []
    r = client.get('/download/assembly-step', query_string=dict(query, parts=f'p{n}'))
    if warnings and r.status_code != 200:
        pytest.skip(f'the page warns, and STEP refuses: {warnings[0][:80]}')
    assert r.status_code == 200, f'the page shows no warning but STEP refuses: {r.data[:300]!r}'
    assert problems(r.data, reference_volume(client, query, n), STEP_REL) == []


def test_the_samples_are_found():
    """The deploy check reads the site's list; an empty one would pass silently."""
    if not _ready():
        pytest.skip('needs OCP (.venv312) and a small_step binary')
    assert len(_cases()) >= 20, f'{SAMPLES}: {len(_cases())} sample pulleys'
