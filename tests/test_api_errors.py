"""
test_api_errors.py — errors are JSON ({"error": ...}) and never carry a traceback.

The SVG/DXF/STL/STEP routes used to answer failures with plain text, several
with the full Python traceback in the body — server internals on the public
site. Errors now go through app._api_error (JSON, which is what the page and
API clients read as d.error); tracebacks go to the server log.
"""
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / 'app.py'


def test_unknown_profile_is_a_json_error(client):
    r = client.get('/download/svg?family=HTD&pitch=99M&teeth=20&bore=8')
    assert r.status_code == 400 and r.is_json
    assert 'Unknown profile' in r.get_json()['error']


@pytest.mark.parametrize('path,target', [
    ('/download/stl', 'generate_pulley_stl'),
    ('/api/preview-stl', 'generate_pulley_stl_preview'),
])
def test_a_failed_export_is_json_without_a_traceback(client, monkeypatch, path, target):
    import app as app_module

    def boom(*a, **k):
        raise RuntimeError('geometry exploded')
    monkeypatch.setattr(app_module, target, boom)
    r = client.get(f'{path}?family=HTD&pitch=5M&teeth=20&bore=8&belt_height=10')
    assert r.status_code == 400 and r.is_json
    body = r.get_data(as_text=True)
    assert 'geometry exploded' in r.get_json()['error']
    assert 'Traceback' not in body and 'File "' not in body


def test_no_route_sends_a_traceback():
    src = APP.read_text(encoding='utf-8')
    # a traceback may be logged, never returned
    bad = [ln for ln in src.splitlines()
           if 'format_exc' in ln and ('return' in ln or "'trace'" in ln or 'Response(' in ln)]
    assert not bad, bad
    assert not re.search(r"return f'[^'\n]*', 4\d\d\n", src), 'plain-text error response'
