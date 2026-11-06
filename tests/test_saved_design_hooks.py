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
    body = _body('async function _applyDesign() {')     # what cctApplyDesign runs, once the page has started
    for step in ('migrateParams(p);', '_applyUrlParams(p);', 'await onPitchChange();'):
        assert step in body, step


def test_its_name_and_picture():
    assert 'window.cctDesignName = () =>' in PAGE and 'window.cctDesignThumbnail = async () =>' in PAGE
    thumb = PAGE[PAGE.index('window.cctDesignThumbnail = async () =>'):]
    thumb = thumb[:thumb.index('\n};')]
    # the 3D view drawn this frame (its buffer is kept), else the 2D drawing
    assert 'threeRenderer.render(threeScene, threeCamera);' in thumb and "getElementById('preview-img')" in thumb


def test_the_accounts_printer_settings_are_defaults():
    """cct_common 0.41.0's printer settings (off until the owner turns printer_settings on):
    tokens.js calls cctApplyPrinterSettings once, on a fresh page. Each value fills one this
    browser still has at the tool's default, never one the person changed here, on both
    pulleys, and only after the start-up restore (designs_ui.js drives it end to end)."""
    body = _body('async function _applyPrinter() {')
    assert "sel.value === 'mm' && s.units !== sel.value" in body
    assert "['print_extra', 'p2_print_extra'].forEach((id, i) => {" in body
    assert 'if (Number(el.value) === 0) el.value = Number(mm);' in body
    # each pulley's own cap (the owner, 2026-10-31: the account's value clamped without a word)
    assert 'const caps = await _compensationCaps();' in body
    assert 'cctTokens.clampCompensation(raw, caps[i])' in body
    assert "fetch('/api/compensation-cap?' + q)" in _body('async function _compensationCaps() {')
    hook = 'window.cctApplyPrinterSettings = s => { _printerPending = s || null; if (_pageStarted) _applyPrinter(); };'
    assert hook in PAGE
    start = PAGE[PAGE.index('window.onload = async () => {'):]
    start = start[:start.index('\n};')]
    assert start.index('const restored = designInUrl() ? false : await loadSettings();') < start.index('_pageStarted = true;') \
        < start.index('if (_printerPending) await _applyPrinter();')


def test_a_saved_design_opens_after_the_start_up_restore():
    """tokens.js opens ?saved= on the window's load, while the start-up may still be restoring
    this browser's design (loadSplines, then loadSettings): cctApplyDesign waits until the page
    has started, or the restore puts the old design back over it (designs_ui on msi, 2026-10-31)."""
    assert "window.cctApplyDesign = params => new Promise(done => {" in PAGE
    assert "  if (_pageStarted) _applyDesign();" in PAGE
    body = _body('async function _applyDesign() {')
    assert '_applyUrlParams(p);' in body and 'await onPitchChange();' in body and 'd.done();' in body
    start = PAGE[PAGE.index('window.onload = async () => {'):]
    start = start[:start.index('\n};')]
    assert start.index('const restored = designInUrl() ? false : await loadSettings();') < start.index('_pageStarted = true;') \
        < start.index('if (_designPending) await _applyDesign();')
