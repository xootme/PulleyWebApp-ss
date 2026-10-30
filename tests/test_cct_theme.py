"""The shared CCT look and page scripts (cct_common 0.29.0): one copy, in the
vendored cct_common/static/, served at /static/cct/<name> (shared_static), so
the vendored commit's files are what the page gets and nobody copies them by
hand. The theme, then the shared layout, then style.css, so this app's own
rules still win where they differ."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHARED = ('cct_theme.css', 'cct_layout.css', 'tokens.js', 'cct_units.js', 'cct_download.js', 'cct_busy.js')


@pytest.fixture
def page(client, monkeypatch):
    # The page itself, not the session queue's redirect (index() checks this per request).
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    return client.get('/').get_data(as_text=True)


def test_page_links_the_theme_then_the_layout_then_style_css(page):
    html = page
    assert (html.index('/static/cct/cct_theme.css') < html.index('/static/cct/cct_layout.css')
            < html.index('/static/style.css'))


def test_page_loads_the_shared_files_from_cct_common(page, client):
    for name in SHARED:
        assert f'/static/cct/{name}' in page, name
        r = client.get(f'/static/cct/{name}')
        assert r.status_code == 200, name
        assert r.data == (ROOT / 'cct_common' / 'static' / name).read_bytes(), name
        assert not (ROOT / 'static' / name).exists(), f'a copy of {name} in static/'
    assert b'--cct-accent' in client.get('/static/cct/cct_theme.css').data
    # this app's routes are at the site's root
    assert '<script src="/static/cct/tokens.js" data-api-base="/"></script>' in page


def test_page_draws_and_fixes_through_the_busy_overlay(page, client):
    """cct_common 0.25.7 (the owner, 2026-10-22): "Drawing the pulley…" over the
    preview while it is redrawn; Auto-fix and Auto-fit close their warnings at
    once; the warnings wait while a value is part-way typed. 0.25.8: each check
    takes a ticket as it asks, so a refusal of a value changed since never shows."""
    assert client.get('/static/cct/cct_busy.js').status_code == 200
    assert '.cct-busy-card' in client.get('/static/cct/cct_theme.css').get_data(as_text=True)
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
