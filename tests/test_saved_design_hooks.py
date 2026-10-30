"""test_saved_design_hooks.py — the page's side of My designs (cct_common 0.34.0
saved designs, off until the owner turns saved_designs on): what a saved design
keeps, its name and picture, and how it reopens. tests/browser/designs_ui.js
drives it end to end with the setting on; these pin the page's code."""
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / 'templates' / 'index.html').read_text(encoding='utf-8')


def _body(start):
    i = PAGE.index(start)
    return PAGE[i:PAGE.index('\n}', i) + 2]


def test_a_saved_design_is_the_whole_design():
    """cctFullDesign is what tokens.js keeps: designParams(), the whole design as a
    link or a file carries it (the hubs in the 2D view too, pulley 2 with a drive)."""
    assert 'const d = Object.assign({}, designParams());' in _body('function cctFullDesign() {')


def test_open_applies_a_design_as_import_does():
    body = PAGE[PAGE.index('window.cctApplyDesign = async params => {'):]
    body = body[:body.index('\n};') + 3]
    for step in ('migrateParams(p);', '_applyUrlParams(p);', 'await onPitchChange();'):
        assert step in body, step


def test_its_name_and_picture():
    assert 'window.cctDesignName = () =>' in PAGE and 'window.cctDesignThumbnail = async () =>' in PAGE
    thumb = PAGE[PAGE.index('window.cctDesignThumbnail = async () =>'):]
    thumb = thumb[:thumb.index('\n};')]
    # the 3D view drawn this frame (its buffer is kept), else the 2D drawing
    assert 'threeRenderer.render(threeScene, threeCamera);' in thumb and "getElementById('preview-img')" in thumb
