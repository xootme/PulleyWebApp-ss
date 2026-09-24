"""Wiring of the token model's accounts into the app (ADR-008,
accounts_setup.py). The accounts/tokens logic itself is tested in
cct_common; these check how this app switches it on, guards it, and
sends its sign-in email."""
import logging
import os
import re
import threading

import flask
import pytest

from accounts_setup import init_accounts, make_backup_alert, make_email_sender


def _fresh_app(tmp_path, *, enabled=True, live=False, sender=None, grant=10):
    app = flask.Flask(__name__)
    app.config['TESTING'] = True
    sent = []

    def default_sender(to, subject, body):
        sent.append((to, subject, body))
        return True, ''

    state = init_accounts(app, log_dir=str(tmp_path), enabled=enabled, live=live,
                          email_sender=sender or default_sender, signup_grant=grant)
    return app, state, sent


def _token_from(body):
    return re.search(r'token=([^&\s]+)', body).group(1)


def test_disabled_registers_no_account_routes(tmp_path):
    app, state, _ = _fresh_app(tmp_path, enabled=False)
    assert not state.enabled and not state.healthy
    c = app.test_client()
    assert c.get('/api/account').status_code == 404
    assert c.post('/api/account/login-link', json={'email': 'a@example.com'}).status_code == 404
    assert not (tmp_path / 'accounts.sqlite3').exists()


def test_real_app_has_accounts_off_by_default(client, monkeypatch):
    # conftest's app is imported without TOKENS_ENABLED: the live site
    # must not grow sign-in routes until the flag is turned on.
    assert client.get('/api/account').status_code == 404
    assert client.post('/api/account/login-link', json={'email': 'a@example.com'}).status_code == 404


def test_enabled_email_sign_in_end_to_end(tmp_path):
    app, state, sent = _fresh_app(tmp_path, grant=10)
    assert state.healthy and state.problems == ()
    assert (tmp_path / 'accounts.sqlite3').exists()
    c = app.test_client()
    assert c.post('/api/account/login-link', json={'email': 'a@example.com'}).get_json() == {'ok': True}
    to, subject, body = sent[-1]
    assert to == 'a@example.com' and subject == 'Sign in to CheapCAD Tools'
    r = c.post('/account/login', data={'token': _token_from(body)})
    assert r.status_code == 303
    info = c.get('/api/account').get_json()
    assert info['email'] == 'a@example.com' and info['balance'] == 10


def test_signup_grant_is_configurable(tmp_path):
    app, _, sent = _fresh_app(tmp_path, grant=25)
    c = app.test_client()
    c.post('/api/account/login-link', json={'email': 'a@example.com'})
    c.post('/account/login', data={'token': _token_from(sent[-1][2])})
    assert c.get('/api/account').get_json()['balance'] == 25


@pytest.mark.parametrize('live,secure', [(False, False), (True, True)])
def test_cookie_secure_flag_follows_live_mode(tmp_path, live, secure):
    app, _, sent = _fresh_app(tmp_path, live=live)
    c = app.test_client()
    c.post('/api/account/login-link', json={'email': 'a@example.com'})
    r = c.post('/account/login', data={'token': _token_from(sent[-1][2])})
    assert ('Secure' in r.headers['Set-Cookie']) is secure


def test_damaged_database_answers_503_and_touches_nothing(tmp_path):
    db = tmp_path / 'accounts.sqlite3'
    garbage = b'not a database' * 500
    db.write_bytes(garbage)
    app, state, sent = _fresh_app(tmp_path)
    assert state.enabled and not state.healthy and state.problems
    c = app.test_client()
    for r in (c.get('/api/account'),
              c.post('/api/account/login-link', json={'email': 'a@example.com'}),
              c.get('/account/login?token=x')):
        assert r.status_code == 503
        assert r.get_json()['code'] == 'ACCOUNTS_UNAVAILABLE'
    assert sent == []
    assert db.read_bytes() == garbage          # left as found, for restore/forensics


def test_damaged_database_leaves_other_routes_alone(tmp_path):
    (tmp_path / 'accounts.sqlite3').write_bytes(b'not a database' * 500)
    app, _, _ = _fresh_app(tmp_path)

    @app.route('/download/ping')
    def ping():
        return 'ok'

    assert app.test_client().get('/download/ping').data == b'ok'


# ── sign-in email sender ──────────────────────────────────────────────────

def test_sender_uses_resend_when_key_configured():
    calls = []
    send = make_email_sender(lambda *a: calls.append(a) or (True, ''), live=True,
                             has_key=True, logger=logging.getLogger('t'))
    assert send('a@example.com', 's', 'b') == (True, '')
    assert calls == [('a@example.com', 's', 'b')]


def test_sender_in_dev_without_key_logs_the_link(caplog):
    send = make_email_sender(lambda *a: pytest.fail('must not send'), live=False,
                             has_key=False, logger=logging.getLogger('t'))
    with caplog.at_level(logging.WARNING):
        assert send('a@example.com', 's', 'link: http://x/account/login?token=abc') == (True, '')
    assert 'token=abc' in caplog.text


def test_sender_live_without_key_fails_instead_of_pretending():
    send = make_email_sender(lambda *a: pytest.fail('must not send'), live=True,
                             has_key=False, logger=logging.getLogger('t'))
    ok, err = send('a@example.com', 's', 'b')
    assert ok is False and 'RESEND_API_KEY' in err


# ── scheduled backups ─────────────────────────────────────────────────────

def test_backups_run_when_enabled(tmp_path):
    app = flask.Flask(__name__)
    uploaded, done = [], threading.Event()

    def upload(path):
        uploaded.append(path)
        done.set()

    state = init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                          email_sender=lambda *a: (True, ''),
                          backup_dir=str(tmp_path / 'backups'),
                          backup_upload=upload, backup_first_delay_s=0)
    try:
        assert done.wait(10)
    finally:
        state.backup_stop.set()
    hourly = os.listdir(tmp_path / 'backups' / 'hourly')
    assert len(hourly) == 1 and hourly[0].startswith('accounts-')


def test_no_backups_without_a_backup_dir(tmp_path):
    _, state, _ = _fresh_app(tmp_path)
    assert state.healthy and state.backup_stop is None


def test_no_backups_of_a_damaged_database(tmp_path):
    (tmp_path / 'accounts.sqlite3').write_bytes(b'not a database' * 500)
    app = flask.Flask(__name__)
    state = init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                          email_sender=lambda *a: (True, ''),
                          backup_dir=str(tmp_path / 'backups'), backup_first_delay_s=0)
    assert not state.healthy and state.backup_stop is None
    assert not (tmp_path / 'backups').exists()


def test_damaged_database_sends_an_alert(tmp_path):
    (tmp_path / 'accounts.sqlite3').write_bytes(b'not a database' * 500)
    alerts = []
    init_accounts(flask.Flask(__name__), log_dir=str(tmp_path), enabled=True, live=False,
                  email_sender=lambda *a: (True, ''), backup_alert=alerts.append)
    assert len(alerts) == 1 and 'integrity check' in alerts[0] and '503' in alerts[0]


def test_backup_alert_logs_and_emails_when_address_set(caplog):
    sent = []
    alert = make_backup_alert(lambda *a: sent.append(a) or (True, ''),
                              alert_to='ops@example.com', logger=logging.getLogger('t'))
    with caplog.at_level(logging.ERROR):
        alert('No database backup in the last 7200 s')
    assert 'No database backup' in caplog.text
    assert sent == [('ops@example.com', 'CheapCAD Tools: database backup problem',
                     'No database backup in the last 7200 s')]


def test_backup_alert_without_address_only_logs(caplog):
    alert = make_backup_alert(lambda *a: pytest.fail('must not email'),
                              alert_to='', logger=logging.getLogger('t'))
    with caplog.at_level(logging.ERROR):
        alert('backup failed: disk full')
    assert 'disk full' in caplog.text
