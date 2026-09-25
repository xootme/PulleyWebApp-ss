"""Finished background downloads live at unguessable, expiring links
(results.py), replacing /download/<job_id>.step."""
import os
import re
import time

import flask

import results
from results import RESULT_TTL_S, register_result_routes, save_result

P = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10'}


def test_async_step_link_is_random_not_the_job_id(client, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = client.post('/api/download/step-async', json=P)
    job = client.get(r.get_json()['status_url']).get_json()
    assert job['status'] == 'done', job
    m = re.fullmatch(r'/download/result/([A-Za-z0-9_-]+)/([^/]+\.step)', job['output_file'])
    assert m and len(m.group(1)) >= 32 and job['id'] not in job['output_file']
    got = client.get(job['output_file'])
    assert got.status_code == 200 and b'ISO-10303-21' in got.data[:200]
    assert 'attachment' in got.headers['Content-Disposition']


def test_old_job_id_route_is_gone(client, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    job_id = client.post('/api/download/step-async', json=P).get_json()['job_id']
    assert client.get(f'/download/{job_id}.step').status_code == 404


def test_guessed_or_malformed_tokens_are_404(client):
    assert client.get('/download/result/' + 'A' * 32 + '/x.step').status_code == 404
    assert client.get('/download/result/short/x.step').status_code == 404


def _mini(tmp_path):
    app = flask.Flask(__name__)
    register_result_routes(app, log_dir=str(tmp_path))
    return app.test_client()


def test_result_expires_after_an_hour(tmp_path):
    c = _mini(tmp_path)
    url = save_result(str(tmp_path), b'solid x', 'part.stl')
    assert c.get(url).data == b'solid x'
    folder = os.path.dirname(os.path.join(str(tmp_path), 'results', url.split('/')[3], 'x'))
    old = time.time() - RESULT_TTL_S - 5
    os.utime(folder, (old, old))
    save_result(str(tmp_path), b'other', 'other.stl')      # saving purges expired results
    assert c.get(url).status_code == 404


def test_path_tricks_stay_inside_the_result_folder(tmp_path):
    c = _mini(tmp_path)
    (tmp_path / 'secret.txt').write_text('no')
    url = save_result(str(tmp_path), b'x', 'a.step')
    token = url.split('/')[3]
    assert c.get(f'/download/result/{token}/..%2F..%2Fsecret.txt').status_code == 404


def test_legacy_job_files_are_removed_at_startup(tmp_path):
    (tmp_path / 'a1b2c3d4.step').write_bytes(b'old paid file')
    (tmp_path / 'keep-me.step').write_bytes(b'not a job file')
    _mini(tmp_path)
    assert not (tmp_path / 'a1b2c3d4.step').exists()
    assert (tmp_path / 'keep-me.step').exists()
