"""test_viewer3d.py — the page's 3D view is cct_common's createViewer
(cct_viewer3d.js, 0.30.0) with this page's options. Dragging orbits the
part's z (the owner, 2026-10-29: "fix the drag"; the page's own viewer made
its OrbitControls before setting camera.up, so they orbited +y). The views
and the drag are driven in tests/browser/parity_ui.js; these pin the page's
side of them."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')


def _call(src, start):
    """The text of the call that opens at `start`, to its matching ')'."""
    i, depth = src.index('(', start), 0
    for j in range(i, len(src)):
        depth += {'(': 1, ')': -1}.get(src[j], 0)
        if depth == 0:
            return src[i:j + 1]
    raise AssertionError('unclosed call')


def test_the_view_is_the_shared_core(client):
    assert "await import('/static/cct/cct_viewer3d.js')" in PAGE
    assert client.get('/static/cct/cct_viewer3d.js').status_code == 200
    assert 'new OrbitControls(' not in PAGE and 'new THREE.WebGLRenderer(' not in PAGE


def test_the_controls_orbit_the_parts_z():
    """The core makes the OrbitControls with camera.up +z; the page hands it
    three.js's own OrbitControls, not one that turns another way."""
    call = _call(PAGE, PAGE.index('threeViewer = createViewer('))
    assert re.search(r'\bOrbitControls\s*,', call), call[:200]
    assert 'extends OrbitControls' not in PAGE and 'up.set(0, 1, 0)' not in call


def test_top_and_bottom_keep_the_pages_up():
    call = _call(PAGE, PAGE.index('threeViewer = createViewer('))
    assert "snaps: { top: [[0, 0, 1], [0, 1, 0]], bottom: [[0, 0, -1], [0, -1, 0]] }" in call
    assert "refit: 'always'" in call and 'visibleOnly: true' in call


def test_a_failed_job_is_reported_before_its_id_is_cleared():
    """One failure, one row (cct_common 0.31.1): hideProgressModal() clears
    _currentJobId, so a report after it reached the server without the job's
    id and stood as a second row."""
    poll = PAGE[PAGE.index('function pollJobStatus()'):PAGE.index('function updateProgressDisplay(')]
    failed = poll[poll.index("job.status === 'failed'"):poll.index('showErrorModalWithRetry(')]
    assert failed.index('_reportDlFailure({') < failed.index('hideProgressModal();')
    lost = poll[poll.index('if (++_pollErrors < 60)'):]
    assert lost.index('_reportDlFailure({') < lost.index('hideProgressModal();')


def test_a_new_screw_size_or_hold_starts_the_insert_hole_over():
    """Worm's rule (the owner, 2026-10-30): the size and hold menus clear the
    hole, typed or not, so its typical size fills in again."""
    for n in (1, 2):
        for menu in ('retention', 'screw_size'):
            assert f'<select id="hub{n}_{menu}" onchange="onScrewKindChange({n})">' in PAGE
    body = PAGE[PAGE.index('function onScrewKindChange(n) {'):]
    body = body[:body.index('\n}\n')]
    assert body.index("`hub${n}_screw_hole_dia`).value = ''") < body.index('onHubChange(n)')
