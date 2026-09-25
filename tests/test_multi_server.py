"""Running on several servers (Cloud Run): a download's status poll and its
result link may land on a different server from the one that made the file.
Result files go to Cloud Storage (results.py, RESULTS_BUCKET) and finished
jobs' status to the database (shared_jobs.py, DATABASE_URL)."""
import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import pytest

import results
from cct_common import job_queue
from shared_jobs import SHARED_JOB_TTL_S, SharedJobs

P = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10'}


# ── a stand-in for Cloud Storage's JSON API and the metadata server ───────

class _Resp:
    def __init__(self, status, body=None, content=b''):
        self.status_code, self._body, self.content = status, body, content

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class FakeGcs:
    def __init__(self):
        self.objects = {}          # name -> (bytes, content_type, created)
        self.token_fetches = 0
        self.auth_seen = []

    def get(self, url, params=None, headers=None, timeout=None):
        if url.startswith('http://metadata.google.internal/'):
            assert headers == {'Metadata-Flavor': 'Google'}
            self.token_fetches += 1
            return _Resp(200, {'access_token': f'tok{self.token_fetches}', 'expires_in': 3599})
        self.auth_seen.append(headers.get('Authorization'))
        m = re.fullmatch(r'https://storage.googleapis.com/storage/v1/b/bkt/o/(.+)', url)
        name = unquote(m.group(1))
        if name not in self.objects:
            return _Resp(404)
        data, ctype, created = self.objects[name]
        if params == {'alt': 'media'}:
            return _Resp(200, content=data)
        stamp = datetime.fromtimestamp(created, timezone.utc).isoformat().replace('+00:00', 'Z')
        return _Resp(200, {'name': name, 'timeCreated': stamp, 'contentType': ctype})

    def post(self, url, params=None, data=None, headers=None, timeout=None):
        assert url == 'https://storage.googleapis.com/upload/storage/v1/b/bkt/o'
        assert params['uploadType'] == 'media' and headers['Authorization'].startswith('Bearer ')
        self.objects[params['name']] = (data, headers['Content-Type'], time.time())
        return _Resp(200, {'name': params['name']})


@pytest.fixture
def gcs():
    fake = FakeGcs()
    results.configure(bucket='bkt', http=fake)
    yield fake
    results.configure(bucket=None)


def test_results_go_to_the_bucket_and_are_served_from_it(client, gcs, tmp_path):
    url = results.save_result(str(tmp_path), b'zipdata', 'HTD pulley.zip')
    assert not (tmp_path / 'results').exists()            # nothing on local disk
    [(name, (data, ctype, _))] = gcs.objects.items()
    assert name.startswith('results/') and name.endswith('/HTD pulley.zip')
    assert data == b'zipdata' and ctype == 'application/zip'
    r = client.get(url)
    assert r.status_code == 200 and r.data == b'zipdata'
    assert 'attachment' in r.headers['Content-Disposition']


def test_bucket_links_expire_after_an_hour(client, gcs, tmp_path):
    url = results.save_result(str(tmp_path), b'x', 'a.step')
    name = next(iter(gcs.objects))
    data, ctype, _ = gcs.objects[name]
    gcs.objects[name] = (data, ctype, time.time() - results.RESULT_TTL_S - 5)
    assert client.get(url).status_code == 404


def test_unknown_bucket_link_is_404(client, gcs):
    assert client.get('/download/result/' + 'A' * 32 + '/x.step').status_code == 404


def test_access_token_is_reused_until_it_expires(client, gcs, tmp_path):
    for _ in range(3):
        client.get(results.save_result(str(tmp_path), b'x', 'a.step'))
    assert gcs.token_fetches == 1 and set(gcs.auth_seen) == {'Bearer tok1'}


# ── finished jobs' status, shared through the database ────────────────────

def test_shared_jobs_round_trip_and_expire(tmp_path):
    now = [1000.0]
    store = SharedJobs(str(tmp_path / 'jobs.sqlite3'), clock=lambda: now[0])
    job = job_queue.create_job('step')
    job_queue.start_job(job.id)
    job_queue.finish_job(job.id, output_file='/download/result/abc/x.step')
    store.publish(job)
    got = store.get(job.id)
    assert got['status'] == 'done' and got['output_file'] == '/download/result/abc/x.step'
    store.publish(job)                                     # republish: still one row
    assert store.get(job.id) == got
    now[0] += SHARED_JOB_TTL_S + 1
    assert store.get(job.id) is None
    assert store.get('no-such-job') is None


def test_status_poll_on_another_server_finds_the_finished_job(client, monkeypatch, tmp_path):
    import app as app_module
    monkeypatch.setenv('PULLEY_TESTING', '1')              # run the export in the request
    monkeypatch.setattr(app_module, '_shared_jobs', SharedJobs(str(tmp_path / 'jobs.sqlite3')))
    started = client.post('/api/download/step-async', json=P).get_json()
    # This server forgets the job: the poll now lands on "another server".
    with job_queue._LOCK:
        job_queue._JOBS.pop(started['job_id'])
    job = client.get(started['status_url']).get_json()
    assert job['status'] == 'done' and job['id'] == started['job_id']
    got = client.get(job['output_file'])
    assert got.status_code == 200 and b'ISO-10303-21' in got.data[:200]


def test_without_a_shared_store_a_forgotten_job_is_not_found(client, monkeypatch):
    import app as app_module
    monkeypatch.setenv('PULLEY_TESTING', '1')
    monkeypatch.setattr(app_module, '_shared_jobs', None)
    started = client.post('/api/download/step-async', json=P).get_json()
    with job_queue._LOCK:
        job_queue._JOBS.pop(started['job_id'])
    assert client.get(started['status_url']).status_code == 404


def test_async_routes_and_zip_all_publish_their_jobs():
    # Every place a download job finishes goes through _finish_job, so none
    # can be missed by the shared store.
    import inspect
    import app as app_module
    import bundles
    src = inspect.getsource(app_module)
    assert src.count('_finish_job(job.id') == 4
    assert re.search(r'(?<![_\w])finish_job\(job\.id', src) is None
    assert 'finish_job=_finish_job' in src
    assert 'finish_job(job.id' in inspect.getsource(bundles.register_bundle_routes)
