"""
test_help_pictures.py — the help pictures and what points at them.

Covers:
  * every /static/help/*.svg referenced by the page or a help page exists and
    is a well-formed SVG
  * every hover-picture key in index.html matches a real control label, and
    every data-picture points at an existing picture
  * "Apply to both pulleys" has its checkbox in both Advanced sections and
    every field it syncs exists for both pulleys

The behaviour in the browser (the pop-ups, the sync, the spoke-fit warning
and Auto-fit) is driven by tests/browser/help_ui.js.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'static'
INDEX = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')
HELP_PAGES = sorted(STATIC.glob('*_help.html'))


def _refs(text):
    return set(re.findall(r'/static/help/([\w.-]+\.svg)', text))


ALL_REFS = sorted(_refs(INDEX).union(*(_refs(p.read_text(encoding='utf-8'))
                                        for p in HELP_PAGES)))


def test_some_pictures_are_referenced():
    assert len(ALL_REFS) >= 15


@pytest.mark.parametrize('name', ALL_REFS)
def test_referenced_picture_exists_and_is_svg(name):
    path = STATIC / 'help' / name
    assert path.is_file(), f'missing {path}'
    root = ET.parse(path).getroot()
    assert root.tag.endswith('svg')
    assert root.get('viewBox'), f'{name} has no viewBox (it would not scale)'


def _picture_table():
    m = re.search(r'const PICTURES = \{(.*?)\n  \};', INDEX, re.S)
    assert m, 'hover-picture table not found in index.html'
    return dict(re.findall(r"^\s*(\w+):\s*'(/static/help/[\w.-]+\.svg)'", m[1], re.M))


def test_every_hover_key_matches_a_control_label():
    fors = set(re.findall(r'<label for="([\w-]+)"', INDEX))
    for key in _picture_table():
        # the page drops the pulley number: spokes1_x -> spokes_x, hub2_x -> hub_x,
        # flange1_x -> flange_x, p2_x -> x
        candidates = {key, 'p2_' + key}
        for panel in ('spokes', 'hub', 'flange'):
            if key.startswith(panel + '_'):
                candidates |= {key.replace(panel + '_', f'{panel}{n}_', 1) for n in (1, 2)}
        assert candidates & fors, f'no <label for=...> for hover key {key!r}'


def test_every_hover_picture_exists():
    for key, src in _picture_table().items():
        assert (ROOT / src.lstrip('/')).is_file(), (key, src)


def test_every_data_picture_exists():
    srcs = re.findall(r'data-picture="(/static/help/[\w.-]+\.svg)"', INDEX)
    assert srcs, 'no data-picture labels found'
    for src in srcs:
        assert (ROOT / src.lstrip('/')).is_file(), src


def test_apply_to_both_pulleys_is_in_both_advanced_sections():
    for n in (1, 2):
        assert f'id="adv_both_{n}"' in INDEX
        assert f'onAdvBothToggle({n})' in INDEX
    fields = re.search(r'const ADV_FIELDS = \[(.*?)\];', INDEX, re.S)[1]
    for f in re.findall(r"'(\w+)'", fields):
        assert f'id="{f}"' in INDEX and f'id="p2_{f}"' in INDEX, f
