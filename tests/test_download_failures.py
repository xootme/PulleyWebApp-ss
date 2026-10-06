"""test_download_failures.py — this app keeps its failed downloads for the
admin page's Download failures tab (cct_common.download_failures; the owner,
2026-10-05, after a STEP download failed on the live site and nothing said why).

A failed STEP job, a download route's error answer and the page's own report
(a request cut off, a lost job) each leave one row, with the design."""
import pytest

DESIGN = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10'}


@pytest.fixture
def store():
    import app as A
    assert A._failure_store is not None
    A._failure_store.clear()
    yield A._failure_store
    A._failure_store.clear()


def test_a_failed_step_job_is_kept(client, store, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')             # the job runs in the request
    import app as A

    def broken(*_a, **_k):
        raise RuntimeError('small_step worker failed (rc=-9): killed')
    monkeypatch.setattr(A, '_run_ss_worker', broken)
    r = client.post('/api/download/step-async', json=dict(DESIGN))
    assert r.status_code == 200, r.data[:200]
    assert client.get(r.get_json()['status_url']).get_json()['status'] == 'failed'
    [row] = store.list()
    assert row['source'] == 'job' and row['route'] == 'job:step' and 'killed' in row['error']
    assert row['params']['teeth'] == '20' and row['duration_ms'] is not None
    # the page's report of the same job is not a second row
    client.post('/api/download-failure', json={'route': '/api/download/step-async',
                                               'job_id': row['job_id'], 'error': 'Generation failed'})
    assert len(store.list()) == 1


def test_a_download_route_error_is_kept(client, store):
    r = client.get('/download/stl', query_string={**DESIGN, 'with_supports': '1'})
    assert r.status_code == 400
    [row] = store.list()
    assert (row['source'], row['route'], row['fmt'], row['status']) == ('server', '/download/stl', 'stl', 400)
    assert 'Print supports' in row['error'] and row['params']['family'] == 'HTD'


def test_a_working_download_leaves_nothing(client, store):
    assert client.get('/download/svg', query_string=DESIGN).status_code == 200
    assert store.list() == []


def test_the_page_reports_a_cut_off_download(client, store):
    r = client.post('/api/download-failure', json={
        'route': '/api/download/bundle', 'fmt': 'bundle', 'status': 504,
        'error': 'start: Server returned 504', 'duration_ms': 120500,
        'params': {'name': 'HTD-5M-20T', 'files': [{'path': '/download/step', 'params': DESIGN}]}})
    assert r.get_json() == {'kept': True}
    [row] = store.list()
    assert row['source'] == 'page' and row['status'] == 504 and row['app'] == 'pulleys'


def test_the_page_reports_failures_and_stops_polling_a_lost_job():
    """The page half (templates/index.html): a start that fails, a failed or
    lost job, and a poll that keeps failing are reported; a lost job (404) or a
    minute of failed polls ends the wait instead of polling for ever."""
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent / 'templates' / 'index.html').read_text(encoding='utf-8')
    assert page.count('_reportDlFailure(') >= 5
    assert "if (r.status === 404) return { status: 'failed'" in page
    assert 'if (++_pollErrors < 60)' in page
    js = (Path(__file__).resolve().parent.parent / 'static' / 'cct_download.js').read_text(encoding='utf-8')
    assert 'root.cctReportDownloadFailure = reportFailure' in js
