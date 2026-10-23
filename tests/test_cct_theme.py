"""The shared CCT theme (cct_common's static/cct_theme.css), as Sprocket and
Gear Designer load it: a copy in static/, from the same cct_common commit
as the vendored package, linked before style.css so this app's own rules
still win where they differ."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = Path(r"C:\Users\cmyer\Documents\cct_common")


@pytest.fixture
def page(client, monkeypatch):
    # The page itself, not the session queue's redirect (index() checks this per request).
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    return client.get('/').get_data(as_text=True)


def test_page_links_the_theme_before_style_css(page):
    html = page
    assert 'cct_theme.css' in html
    assert html.index('cct_theme.css') < html.index('style.css')


def test_theme_is_served(client):
    r = client.get('/static/cct_theme.css')
    assert r.status_code == 200
    assert b'--cct-accent' in r.data


@pytest.mark.skipif(not UPSTREAM.is_dir(), reason="cct_common checkout not on this machine")
def test_theme_matches_the_vendored_commit():
    text = (ROOT / 'cct_common' / '_VENDORED_FROM.txt').read_text(encoding='utf-8')
    commit = re.search(r'source commit: ([0-9a-f]{40})', text).group(1)
    upstream = subprocess.run(['git', 'show', f'{commit}:static/cct_theme.css'],
                              cwd=UPSTREAM, capture_output=True, check=True).stdout
    assert (ROOT / 'static' / 'cct_theme.css').read_bytes().replace(b'\r\n', b'\n') \
        == upstream.replace(b'\r\n', b'\n')


@pytest.mark.skipif(not UPSTREAM.is_dir(), reason="cct_common checkout not on this machine")
def test_busy_overlay_matches_the_vendored_commit():
    text = (ROOT / 'cct_common' / '_VENDORED_FROM.txt').read_text(encoding='utf-8')
    commit = re.search(r'source commit: ([0-9a-f]{40})', text).group(1)
    upstream = subprocess.run(['git', 'show', f'{commit}:static/cct_busy.js'],
                              cwd=UPSTREAM, capture_output=True, check=True).stdout
    assert (ROOT / 'static' / 'cct_busy.js').read_bytes().replace(b'\r\n', b'\n') \
        == upstream.replace(b'\r\n', b'\n')


def test_page_draws_and_fixes_through_the_busy_overlay(page, client):
    """cct_common 0.25.7 (the owner, 2026-10-22): "Drawing the pulley…" over the
    preview while it is redrawn; Auto-fix and Auto-fit close their warnings at
    once; the warnings wait while a value is part-way typed. 0.25.8: each check
    takes a ticket as it asks, so a refusal of a value changed since never shows."""
    assert client.get('/static/cct_busy.js').status_code == 200
    assert '.cct-busy-card' in client.get('/static/cct_theme.css').get_data(as_text=True)
    assert 'cct_busy.js' in page
    assert page.index('cct_busy.js') < page.index('function updatePreview')
    assert "Loading 3D…'" not in page
    for use in ("CCTBusy.show(previewBox(), busyText())", "CCTBusy.hide(previewBox())",
                "CCTBusy.applyFix(document.getElementById('dims-warn-row')",
                "CCTBusy.applyFix(warn, previewBox()",
                "const ticket = refusals.ticket();", "refusals.show(showWarnings, ticket)",
                "const ticket = _spokeFitGate[n].ticket();",
                "_spokeFitGate[n].show(() => _showSpokeFit(n, warn, fit), ticket)"):
        assert use in page, use


def test_title_bar_privacy_policy_left_of_bug_report(page):
    """The CCT title bar (2026-10-01 audit, row 7): a Privacy Policy icon button
    just left of Report a bug, both drawn by cct_theme.css."""
    body = page
    assert 'class="btn-privacy" href="https://cheapcadtools.com/privacy-policy/"' in body
    assert body.index('class="btn-privacy"') < body.index('class="btn-bug-report"')


def test_own_css_leaves_the_title_bar_buttons_to_the_theme():
    """A copy in style.css loads after the theme and wins, so a shared fix never
    arrives (the bug button's glyph stayed 14 px that way)."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(encoding="utf-8")
    for sel in (".btn-help", ".btn-bug-report", ".btn-privacy"):
        assert not re.search(r"(?m)^" + re.escape(sel) + r"(:hover)?\s*\{", css), sel
