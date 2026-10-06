"""test_step_colors.py — STEP parts carry the 3D view's colours (the owner,
2026-10-06): exporters/colors.py, painted by small_step (5bcf1a3: `--color` on
`combined`). Colour changes no volume, face or validity, so the checks are on
the entity graph — the COLOUR_RGB values and that styled items reach the solids.

Without a binary: the table is the page's, and the command carries --color only
when the binary lists it. With a colour-capable small_step (bin/small_step_linux
on Linux, or SMALL_STEP_BIN): each pulley STEP is painted its colour.
"""
import re
from pathlib import Path

import pytest

from exporters import colors
from exporters import step_worker_ss as W

PAGE = (Path(__file__).parent.parent / 'templates' / 'index.html').read_text(encoding='utf-8')
BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10', 'feature_build': '1'}


def _rgb(hexcolor):
    h = hexcolor.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _page_color(pattern):
    return '#' + re.search(pattern, PAGE).group(1).upper()


# ── the table is the 3D view's ───────────────────────────────────────────────

def test_the_colours_are_the_3d_views():
    assert colors.PULLEY[1] == _page_color(r'pulleyMat *= *new THREE\.MeshStandardMaterial\(\{ *color: *0x([0-9a-fA-F]{6})')
    assert colors.PULLEY[2] == _page_color(r'pulley2Mat *= *new THREE\.MeshStandardMaterial\(\{ *color: *0x([0-9a-fA-F]{6})')
    assert colors.BELT == _page_color(r'beltMat *= *new THREE\.MeshStandardMaterial\(\{ *color: *0x([0-9a-fA-F]{6})')
    mats = re.search(r'SPLINE_MATS *= *\{([^}]*)\}', PAGE).group(1)
    page = {k: '#' + v.upper() for k, v in re.findall(r'(\w+): *0x([0-9a-fA-F]{6})', mats)}
    assert (page['shaft'], page['rings'], page['washers']) == (colors.SHAFT, colors.RING, colors.WASHER)


# ── the command line ─────────────────────────────────────────────────────────

P = dict(family='HTD', pitch='5M', num_teeth=20, bore_mm=8.0, belt_height_mm=10.0, color='#4A9FD4')


def _cmd(params, flags=('--color',)):
    orig = W._has_flag
    W._has_flag = lambda _bin, flag: flag in flags
    try:
        return W._build_pulley_cmd(dict(params), 'ss', 'x.dxf')
    finally:
        W._has_flag = orig


def test_the_colour_goes_on_the_command():
    cmd = _cmd(P)
    assert cmd[cmd.index('--color') + 1] == '#4A9FD4'
    assert '--color' not in _cmd(P, flags=())                       # a binary without it isn't sent it
    assert '--color' not in _cmd({k: v for k, v in P.items() if k != 'color'})   # made for an assembly


# ── the STEP file ────────────────────────────────────────────────────────────

def _binary():
    import app as A
    if A._SS_AVAILABLE:
        return A._SS_BIN
    from exporters.assembly import ss_binary
    return ss_binary()


needs_color = pytest.mark.skipif(not (_binary() and W._has_flag(_binary(), '--color')),
                                 reason='no small_step with --color')


def _colours(data: bytes):
    return [tuple(float(x) for x in m) for m in
            re.findall(rb"COLOUR_RGB\('[^']*',([-0-9.Ee+]+),([-0-9.Ee+]+),([-0-9.Ee+]+)\)", data)]


def _close(a, b):
    return all(abs(x - y) < 1e-6 for x, y in zip(a, b))


@needs_color
@pytest.mark.parametrize('n', [1, 2])
def test_a_pulley_step_is_its_colour(client, monkeypatch, n):
    monkeypatch.setenv('PULLEY_STEP_BACKEND', '')
    q = dict(BASE, **({'dual': 'true', 'p2_teeth': '30', 'p2_bore': '8', 'pulley': '2'} if n == 2 else {}))
    r = client.get('/download/step', query_string=q)
    assert r.status_code == 200, r.data[:300]
    cols = _colours(r.data)
    assert len(cols) == 1 and _close(cols[0], _rgb(colors.PULLEY[n]))
    solids = r.data.count(b'MANIFOLD_SOLID_BREP')
    assert r.data.count(b'STYLED_ITEM(') == solids >= 1            # every solid painted, once


@needs_color
def test_a_flanged_pulleys_flanges_are_painted_too(client, monkeypatch):
    monkeypatch.setenv('PULLEY_STEP_BACKEND', '')
    q = dict(BASE, flange_enabled='1', flange_3dprint='0', flange_plate_height='1')    # metal: separate bodies
    r = client.get('/download/step', query_string=q)
    assert r.status_code == 200, r.data[:300]
    assert r.data.count(b'MANIFOLD_SOLID_BREP') > 1
    assert r.data.count(b'STYLED_ITEM(') == r.data.count(b'MANIFOLD_SOLID_BREP')
    assert len(_colours(r.data)) == 1                               # one chain, shared by every body
