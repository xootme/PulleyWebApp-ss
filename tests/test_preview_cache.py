"""test_preview_cache.py — the 3D preview is cached for an hour (ETag +
max-age, cct_common.flask_caching), so the page stamps each request with the
build: a new build asks for new URLs and gets its own shapes, instead of the
browser showing the last build's for up to an hour (the shaped retaining ring,
2026-10-06: the server sent it, the view kept the old annulus).
"""
import re
from pathlib import Path

Q = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10', 'feature_build': '1'}


def test_the_preview_is_cached_per_url(client):
    r = client.get('/api/preview-stl', query_string=Q)
    assert r.status_code == 200 and 'max-age' in r.headers.get('Cache-Control', '')


def test_a_build_stamp_is_a_new_cache_entry(client):
    """Same design, another build: a different ETag, so no stale hit — and the
    stamp changes nothing in the shape."""
    a = client.get('/api/preview-stl', query_string=dict(Q, b='2026-10-06 14:00'))
    b = client.get('/api/preview-stl', query_string=dict(Q, b='2026-10-06 15:00'))
    assert a.status_code == b.status_code == 200
    assert a.headers['ETag'] != b.headers['ETag']
    assert a.data == b.data


def test_the_page_stamps_its_3d_preview_with_the_build():
    page = (Path(__file__).parent.parent / 'templates' / 'index.html').read_text(encoding='utf-8')
    body = re.search(r'async function update3DPreview\(\) \{(.*?)\n\}', page, re.S).group(1)
    assert 'params.b = BUILD_TIME' in body
    assert body.index('params.b = BUILD_TIME') < body.index("'/api/preview-stl?'")
