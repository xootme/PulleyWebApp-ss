"""test_account_features_off.py — cct_common 0.36 to 0.54 on this app's account
routes, with every new business setting off (Controller task
pulleywebapp-sync-cct-common-0-36-1-newsletter-opt-in-welcome): pulley hosts the
account routes on cheapcadtools.com, so these are the checks that nothing a
user sees changes until the owner turns a setting on.

- /account/ is a 404 (account_page off), signed in or not;
- a new account's sign-in goes straight on, with no welcome page, and the
  welcome form keeps nothing (newsletter_opt_in off); the newsletter save
  route is a 404;
- sign-in with no next lands on "/", as today, not on /account/;
- the Brevo webhook refuses without BREVO_WEBHOOK_SECRET (and with a wrong one);
- the daily housekeeping runs the reconcile harmlessly with no key, or with
  Brevo's SMTP key (what pulley has mounted today), and nothing is sent;
- the printer settings route is a 404 (printer_settings off);
- a bug report keeps its app and, only with my_reports on, its account.
"""
import flask
import pytest

from accounts_setup import init_accounts
from cct_common.account_routes import COOKIE_NAME
from cct_common.settings import settings_for


@pytest.fixture
def state(tmp_path):
    app = flask.Flask(__name__)
    st = init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                       email_sender=lambda *a: (True, ''))
    st.app = app
    return st


def _client(state, email=None):
    c = state.app.test_client()
    if email:
        acct = state.accounts.sign_in('email', email, email, email_verified=True)
        c.set_cookie(COOKIE_NAME, state.accounts.create_session(acct, kind='web'))
        return c, acct
    return c, None


def test_every_new_setting_starts_off(state):
    s = settings_for(state.tokens.path)
    for key in ('account_page', 'newsletter_opt_in', 'my_account_button', 'saved_designs', 'bookmarks',
                'projects', 'share_links', 'my_reports', 'printer_settings', 'data_download', 'rewards',
                'receipts', 'referrals', 'inline_checkout', 'price_in_money'):
        assert s.get(key) is not True, key
    assert s.get('newsletter_list_id') is None


def test_the_account_page_is_a_404(state):
    assert _client(state)[0].get('/account/').status_code == 404
    c, _ = _client(state, 'page@example.com')
    assert c.get('/account/').status_code == 404
    assert c.get('/account').status_code in (404, 308)        # /account redirects to /account/ at most


def test_a_new_account_signs_in_with_no_welcome_page_and_lands_on_home(state):
    c = state.app.test_client()
    token = state.accounts.create_login_link('new@example.com')
    r = c.post('/account/login', data={'token': token})
    assert r.status_code == 303 and r.headers['Location'].endswith('/')   # "/", not /account/
    assert b'Welcome' not in r.data and b'newsletter' not in r.data
    acct = state.tokens.account_by_email('new@example.com')
    r = c.post('/account/welcome', data={'newsletter': '1', 'next': '/'})  # a forged form keeps nothing
    assert r.status_code == 303
    from cct_common.newsletter import Newsletter
    assert Newsletter(state.tokens.path).state(acct)['subscribed'] is False


def test_the_newsletter_and_printer_saves_are_404s(state):
    c, _ = _client(state, 'saves@example.com')
    assert c.post('/api/account/newsletter', json={'subscribed': True}).status_code == 404
    assert c.post('/api/account/printer', json={'units': 'in'}).status_code == 404
    body = c.get('/api/account').get_json()
    assert 'newsletter' not in body and 'printer' not in body and not body.get('account_page')


@pytest.mark.parametrize('secret', [None, 'short', 'a-wrong-secret-of-enough-length'])
def test_the_brevo_webhook_refuses_without_its_secret(state, monkeypatch, secret):
    monkeypatch.delenv('BREVO_WEBHOOK_SECRET', raising=False)
    if secret == 'a-wrong-secret-of-enough-length':
        monkeypatch.setenv('BREVO_WEBHOOK_SECRET', 'the-real-secret-of-enough-length')
    elif secret:
        monkeypatch.setenv('BREVO_WEBHOOK_SECRET', secret)
    c = state.app.test_client()
    ev = {'event': 'unsubscribed', 'email': 'someone@example.com'}
    r = c.post('/api/newsletter/brevo-webhook', json=ev,
               headers={'Authorization': f'Bearer {secret or ""}'})
    assert r.status_code == 404
    from cct_common.newsletter_sync import NewsletterSync
    assert NewsletterSync(state.tokens.path).waiting() == 0          # nothing queued


@pytest.mark.parametrize('key', [None, 'xsmtpsib-not-an-api-key'])
def test_the_daily_housekeeping_reconciles_harmlessly_and_sends_nothing(state, monkeypatch, key):
    import cct_common.newsletter_sync as ns
    monkeypatch.delenv('BREVO_API_KEY', raising=False)
    if key:
        monkeypatch.setenv('BREVO_API_KEY', key)

    def no_network(*a, **k):
        raise AssertionError('Brevo was called')
    monkeypatch.setattr(ns.Brevo, '__init__', no_network)
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', no_network)
    state.accounts.sign_in('email', 'hk@example.com', 'hk@example.com', email_verified=True)
    done = state.accounts.housekeeping(None)
    assert 'newsletter' not in done                                  # not set up: no reconcile counts
    assert ns.NewsletterSync(state.tokens.path).reconcile() is None


def test_a_report_is_kept_in_the_shared_store_as_this_apps(tmp_path, monkeypatch):
    import app as app_module
    from cct_common.reports import ReportStore
    store = ReportStore(str(tmp_path / 'bugs.sqlite3'))
    monkeypatch.setattr(app_module, '_bug_store', store)
    r = app_module.app.test_client().post('/api/report-bug', json={
        'type': 'bug', 'seeing': 'x', 'should_see': 'y', 'state': {}})
    assert r.status_code == 200, r.data
    row = store.get(r.get_json()['report_id'])
    assert row['app'] == 'pulleys' and row['account_id'] is None and row['seeing'] == 'x'


def test_the_reporters_account_goes_with_it_only_with_my_reports(state, tmp_path, monkeypatch):
    import app as app_module
    from cct_common.reports import ReportStore
    monkeypatch.setattr(app_module, '_bug_store', ReportStore(str(tmp_path / 'bugs.sqlite3')))
    monkeypatch.setattr(app_module, '_accounts_state', state)
    acct = state.accounts.sign_in('email', 'rep@example.com', 'rep@example.com', email_verified=True)
    cookie = f'{COOKIE_NAME}={state.accounts.create_session(acct, kind="web")}'

    def reporter():
        with state.app.test_request_context('/api/report-bug', headers={'Cookie': cookie}):
            return app_module._reporter()

    assert reporter() is None                                  # my_reports off: no account kept
    settings_for(state.tokens.path).set('my_reports', True, by='test')
    assert reporter() == acct
    with state.app.test_request_context('/api/report-bug'):
        assert app_module._reporter() is None                  # signed out
