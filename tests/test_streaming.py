"""Large responses go out in pieces, with no Content-Length (cct_common 0.59.0's
stream_large_responses, called at the end of app.py).

Cloud Run answers 500 in place of any response over 32 MiB sent with a Content-Length;
Worm lost a user's 47.7 MiB zip three times on 2026-11-06, and a pulley drive's preview STL
reaches 32 MiB at 360 + 360 teeth. The hook's threshold is lowered here so a small design
stands in for a big one: the same bytes arrive, a result link and a gzipped download stream
too, and a charged result is paid only once its last piece is sent, never on a cut stream."""
import gzip
import time

import pytest

from test_charging import DESIGN, paid  # noqa: F401  (the fixture)

PREVIEW = '/api/preview-stl?family=HTD&pitch=5M&teeth=40&bore=8&belt_height=10'
PIECE = 4096


@pytest.fixture
def low(monkeypatch):
    """Everything over 1000 bytes streams, in 4 KiB pieces."""
    from app import _stream_large
    monkeypatch.setattr(_stream_large, 'over', 1000)
    monkeypatch.setattr(_stream_large, 'piece', PIECE)


def _pieces(r):
    try:
        return list(r.response)
    finally:
        r.close()


def test_the_hook_is_on_with_cloud_runs_margin():
    from app import _stream_large
    assert _stream_large.over == 16 * 2**20 and _stream_large.piece == 2**20


def test_a_small_preview_keeps_its_content_length(client):
    r = client.get(PREVIEW)
    assert r.status_code == 200 and int(r.headers['Content-Length']) == len(r.data) > 1000


def test_a_large_preview_streams_the_same_bytes(client, low):
    whole = client.get(PREVIEW + '&streamed=0')
    r = client.get(PREVIEW, buffered=False)
    assert r.status_code == 200 and 'Content-Length' not in r.headers
    pieces = _pieces(r)
    assert len(pieces) > 1 and max(map(len, pieces)) <= PIECE
    assert b''.join(pieces) == whole.data


def test_a_result_link_streams(client, low, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = client.post('/api/download/step-async', json={'family': 'HTD', 'pitch': '5M', 'teeth': '20',
                                                      'bore': '8', 'belt_height': '10'})
    url = client.get(r.get_json()['status_url']).get_json()['output_file']
    got = client.get(url, buffered=False)
    assert got.status_code == 200 and 'Content-Length' not in got.headers
    assert 'attachment' in got.headers['Content-Disposition']
    body = b''.join(_pieces(got))
    assert body.startswith(b'ISO-10303-21') and body.rstrip().endswith(b'END-ISO-10303-21;')


def test_a_gzipped_download_streams_after_compression(client, low):
    """Flask-Compress runs before the hook: the compressed body is what streams."""
    from app import _stream_large
    q = '/download/svg?family=HTD&pitch=5M&teeth=40&bore=8'
    r = client.get(q, headers={'Accept-Encoding': 'gzip'}, buffered=False)
    assert r.status_code == 200 and r.headers.get('Content-Encoding') == 'gzip'
    assert 'Content-Length' not in r.headers
    streamed = gzip.decompress(b''.join(_pieces(r)))
    _stream_large.over = 10**9
    whole = client.get(q, headers={'Accept-Encoding': 'gzip'})
    assert whole.headers.get('Content-Length') and gzip.decompress(whole.data) == streamed
    assert b'<svg' in streamed


# -- a charged result: paid for what arrives (cct_common.charging "delivery") --

def _paid_result(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    body = dict(DESIGN, design_id=paid.design_id)
    body.pop('p2_teeth'), body.pop('p2_bore')
    r = paid.client.post('/api/download/step-async', headers=paid.auth, json=body)
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'done' and paid.balance() == 16
    return status['output_file']


def test_a_streamed_result_fetched_in_full_stays_paid(paid, low, monkeypatch):
    from charging import charges
    got = paid.client.get(_paid_result(paid, monkeypatch), buffered=False)
    assert 'Content-Length' not in got.headers and len(_pieces(got)) > 1
    assert charges.sweep_undelivered(now=time.time() + 10**6) == 0   # claimed after the last piece
    assert paid.balance() == 16


def test_a_cut_stream_is_never_claimed(paid, low, monkeypatch):
    from charging import charges
    got = paid.client.get(_paid_result(paid, monkeypatch), buffered=False)
    assert 'Content-Length' not in got.headers
    assert len(next(iter(got.response))) == PIECE
    got.close()                                                       # the client went away
    charges.sweep_undelivered(now=time.time() + 10**6)
    assert paid.balance() == 20
    assert 'refund' in [h['kind'] for h in paid.tokens.history(paid.acct)]
