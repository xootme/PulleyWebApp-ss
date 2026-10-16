"""
test_brand.py — the business name reads "CheapCADTools" everywhere a visitor
sees it (the owner, 2026-10-15). Two words only where a signup demands a first
and last name; the domain, URLs and code names are unchanged.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = re.compile(r'Cheap ?CAD Tools', re.I)
# What visitors and customers see: pages, help, emails, the desktop launcher.
SHOWN = ['app.py', 'accounts_setup.py', 'packaging/launcher.py',
         *[str(p.relative_to(ROOT)) for p in (ROOT / 'templates').glob('*.html')],
         *[str(p.relative_to(ROOT)) for p in (ROOT / 'static').glob('*.html')]]


def test_the_page_title_names_cheapcadtools(client):
    html = client.get('/').get_data(as_text=True)
    assert '<title>Timing Pulley Generator — CheapCADTools</title>' in html
    assert not OLD.search(html)


def test_no_old_brand_in_what_people_see():
    old = [f'{f}:{n}' for f in SHOWN
           for n, line in enumerate((ROOT / f).read_text(encoding='utf-8').splitlines(), 1)
           if OLD.search(line)]
    assert old == []
