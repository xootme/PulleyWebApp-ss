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

import uuid

from accounts_setup import _redact, describe_db, init_accounts, make_backup_alert, make_email_sender


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


# ── inactivity: reminder emails and the daily run ─────────────────────────

def test_free_token_reminder_email():
    from accounts_setup import make_inactivity_notify
    sent = []
    notify = make_inactivity_notify(lambda *a: sent.append(a) or (True, ''),
                                    app_name='CheapCAD Tools', site_url='https://example.com/p')
    assert notify('a@example.com', 'free_tokens', 1_893_456_000, 7) is True
    to, subject, body = sent[0]
    assert to == 'a@example.com' and '7 free' in subject and 'expire' in subject
    assert 'https://example.com/p' in body and 'Tokens you bought never expire.' in body


def test_account_closing_reminder_email_and_failed_send():
    from accounts_setup import make_inactivity_notify
    sent = []
    notify = make_inactivity_notify(lambda *a: sent.append(a) or (False, 'down'),
                                    app_name='CheapCAD Tools', site_url='https://example.com/p')
    assert notify('a@example.com', 'account', 1_893_456_000, 0) is False   # not sent -> nothing happens
    assert 'will be closed' in sent[0][1] and 'no purchased tokens' in sent[0][2]


def test_daily_run_survives_a_failure(caplog):
    from accounts_setup import start_housekeeping
    runs, done = [], threading.Event()

    class Accounts:
        def housekeeping(self, notify):
            runs.append(notify)
            if len(runs) == 1:
                raise RuntimeError('db locked')
            done.set()
            return {'reminded': 1}

    stop = start_housekeeping(Accounts(), 'NOTIFY', logging.getLogger('t'),
                              interval_s=0.05, first_delay_s=0)
    try:
        assert done.wait(5)
    finally:
        stop.set()
    assert runs[:2] == ['NOTIFY', 'NOTIFY']
    assert 'db locked' in caplog.text


def test_inactivity_run_with_the_real_store_sends_this_apps_email(tmp_path):
    """cct_common's housekeeping() driving this app's reminder email: after
    ~2 idle years the reminder goes out, 30 days later the free tokens go."""
    from accounts_setup import make_inactivity_notify
    from cct_common.accounts import FREE_TOKEN_IDLE_S, REMINDER_LEAD_S, AccountStore
    from cct_common.tokens import TokenStore

    class Clock:
        t = 1_000_000.0
        def __call__(self):
            return self.t

    clock = Clock()
    tokens = TokenStore(str(tmp_path / 'a.sqlite3'), clock=clock)
    accounts = AccountStore(tokens, signup_grant=10)
    acct = accounts.sign_in('email', 'a@example.com', 'a@example.com', email_verified=True)
    tokens.credit(acct, 5, ref='order:1')
    sent = []
    notify = make_inactivity_notify(lambda *a: sent.append(a) or (True, ''),
                                    app_name='CheapCAD Tools', site_url='https://example.com/p')
    clock.t += FREE_TOKEN_IDLE_S - REMINDER_LEAD_S + 1
    accounts.housekeeping(notify)
    assert len(sent) == 1 and '10 free CheapCAD Tools tokens expire' in sent[0][1]
    clock.t += REMINDER_LEAD_S
    accounts.housekeeping(notify)
    assert tokens.balance(acct) == 5                  # only the bought tokens remain


# ── Postgres (DATABASE_URL, Cloud Run) ────────────────────────────────────

def test_describe_db_never_shows_a_postgres_password():
    url = 'postgresql://app:s3cret-pw@ep-x.neon.tech/cct?sslmode=require'
    assert describe_db(url) == 'Postgres ep-x.neon.tech/cct'
    assert describe_db('/data/accounts.sqlite3') == '/data/accounts.sqlite3'


def test_driver_errors_are_redacted_before_logging():
    url = 'postgresql://app:s3cret-pw@ep-x.neon.tech/cct'
    text = _redact(f'connection to {url} failed; password "s3cret-pw" rejected', url)
    assert 's3cret-pw' not in text and 'Postgres ep-x.neon.tech/cct' in text
    # a SQLite path is left alone
    assert _redact('bad file /a/b.sqlite3', '/a/b.sqlite3') == 'bad file /a/b.sqlite3'


PG = os.environ.get('CCT_TEST_POSTGRES', '').strip()


@pytest.fixture
def pg_url():
    """A Postgres URL on a schema made for this test alone (skipped unless
    CCT_TEST_POSTGRES points at a server, e.g. the local cct-pg container)."""
    if not PG:
        pytest.skip('CCT_TEST_POSTGRES not set')
    import psycopg
    from cct_common.sqlite_db import close_pool
    schema = 't_' + uuid.uuid4().hex[:12]
    with psycopg.connect(PG, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
    url = f"{PG}{'&' if '?' in PG else '?'}options=-csearch_path%3D{schema}"
    try:
        yield url
    finally:
        close_pool(url)
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')


def test_database_url_keeps_the_ledger_in_postgres(tmp_path, pg_url):
    app = flask.Flask(__name__)
    sent = []
    state = init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                          email_sender=lambda *a: sent.append(a) or (True, ''),
                          signup_grant=7, database_url=pg_url,
                          backup_dir=str(tmp_path / 'backups'), backup_first_delay_s=0)
    assert state.healthy and state.tokens.is_postgres
    assert not (tmp_path / 'accounts.sqlite3').exists()      # nothing on local disk
    assert state.backup_stop is None and not (tmp_path / 'backups').exists()
    c = app.test_client()
    c.post('/api/account/login-link', json={'email': 'a@example.com'})
    c.post('/account/login', data={'token': _token_from(sent[-1][2])})
    assert c.get('/api/account').get_json()['balance'] == 7


def test_designs_register_in_postgres(pg_url):
    from charging import DesignStore
    from cct_common.tokens import TokenStore
    TokenStore(pg_url)
    designs = DesignStore(pg_url)
    design = {'teeth': 20, 'pitch': 'GT2', 'bore': 5}
    did = designs.register('acct1', design)
    assert designs.register('acct1', dict(design)) == did      # INSERT OR IGNORE on Postgres
    assert designs.get(did) is not None and designs.get('nope') is None
    designs.purge()
    assert designs.get(did) is not None                         # fresh: kept


# ── new accounts per IP per day ───────────────────────────────────────────

def test_signups_are_limited_per_ip(tmp_path):
    app = flask.Flask(__name__)
    app.config['TESTING'] = True
    sent = []
    init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                  email_sender=lambda *a: sent.append(a) or (True, ''),
                  signups_per_ip_per_day=2)
    c = app.test_client()
    env = {'REMOTE_ADDR': '203.0.113.7'}

    def signup(email):
        c.post('/api/account/login-link', json={'email': email}, environ_overrides=env)
        return c.post('/account/login', data={'token': _token_from(sent[-1][2])},
                      environ_overrides=env)

    assert signup('a@example.com').status_code == 303
    assert signup('b@example.com').status_code == 303
    third = signup('c@example.com')          # made, signed in, told why it has no tokens
    assert third.status_code == 200 and b'starts with none' in third.data
    assert c.get('/api/account').get_json()['balance'] == 0
    assert signup('a@example.com').status_code == 303       # existing accounts still sign in


def test_oauth_providers_follow_their_settings(tmp_path):
    app = flask.Flask(__name__)
    init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                  email_sender=lambda *a: (True, ''),
                  oauth_clients={'google': ('gid', 'gsecret'), 'microsoft': ('', ''),
                                 'github': ('ghid', '')})
    got = app.test_client().get('/api/account/providers').get_json()['providers']
    assert [p['id'] for p in got] == ['google']
    r = app.test_client().get('/account/oauth/google/start?next=/')
    assert r.status_code == 302 and 'accounts.google.com' in r.headers['Location']


def test_real_app_limits_two_signups_and_trusts_one_proxy_hop():
    # The per-IP limits read request.remote_addr, which ProxyFix sets from
    # the X-Forwarded-For entry the nearest PROXY_HOPS proxies appended —
    # never the visitor-controlled leading entries.
    import app as app_module
    from werkzeug.middleware.proxy_fix import ProxyFix
    assert isinstance(app_module.app.wsgi_app, ProxyFix)
    assert app_module.app.wsgi_app.x_for == int(os.environ.get('PROXY_HOPS', '1'))
    src = open(app_module.__file__, encoding='utf-8').read()
    assert "os.environ.get('TOKENS_SIGNUPS_PER_IP_PER_DAY', '2')" in src
