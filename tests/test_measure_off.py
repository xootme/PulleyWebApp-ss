"""Measure first (cct_common 0.62.0; Controller campaigns/2026-11-11-measure-first.md): the GA4
tag ({{ cct_analytics() }} in the page's <head>) and the ?ref= cookie (cct_src) are business
settings, analytics_tag and source_capture, and both ship OFF: the privacy policy of Nov 8 says
the tools have no analytics, so the owner turns them on in the admin once its new text is live.
Never this app.

The page is the real one, from an app imported with tokens on (the account routes are what
register the tag and the cookie), in a process of its own: the suite's app is imported with
tokens off. With the switches on, in that run's own database, the same page carries both, so the
"off" checks can see them. As Planetary's tests/test_measure_off.py."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = """
import json
import app
from cct_common import measure
from cct_common.settings import settings_for
c = app.app.test_client()

def look():
    r = c.get("/", query_string={"ref": "site-header"})
    html = r.get_data(as_text=True)
    cookie = [h for h in r.headers.getlist("Set-Cookie") if h.startswith(measure.COOKIE + "=")]
    c.delete_cookie(measure.COOKIE)
    return {"status": r.status_code, "tag": measure.TAG_ID in html, "gtag": "googletagmanager" in html,
            "cookie": cookie}

state = app._accounts_state
out = {"healthy": state.healthy, "off": look()}
s = settings_for(state.tokens.path)
s.set("analytics_tag", True, by="test_measure_off")
s.set("source_capture", True, by="test_measure_off")
out["on"] = look()
print(json.dumps(out))
"""


def _env(tmp, tokens):
    env = {k: v for k, v in os.environ.items()
           if k not in ('DATABASE_URL', 'CCT_MODE', 'CCT_ACCOUNTS_MODE', 'RESEND_API_KEY', 'K_SERVICE',
                        'TOKENS_ENABLED')}
    env.update(PULLEY_LOG_DIR=str(tmp), PULLEY_LOG_DIR_FOR='measure', PULLEY_TESTING='1',
               QUEUE_DISABLED='1', PYTHONIOENCODING='utf-8', CCT_SERVER_USE='0')
    if tokens:
        env['TOKENS_ENABLED'] = '1'
    return env


@pytest.fixture(scope='module')
def page(tmp_path_factory):
    out = subprocess.run([sys.executable, '-c', PAGE], cwd=ROOT, env=_env(tmp_path_factory.mktemp('measure'), True),
                         capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got['healthy'], got
    return got


def test_switches_off_no_gtag_and_a_ref_sets_no_cookie(page):
    assert page['off'] == {'status': 200, 'tag': False, 'gtag': False, 'cookie': []}


def test_the_page_carries_both_once_the_owner_turns_them_on(page):
    """The probe above can see them: the page includes the tag, and its server keeps the ?ref=
    (the off test is no test without this)."""
    on = page['on']
    assert on['status'] == 200 and on['tag'] and on['gtag'], on
    assert len(on['cookie']) == 1 and on['cookie'][0].startswith('cct_src=ref=site-header;') \
        and 'HttpOnly' in on['cookie'][0]


def test_without_accounts_the_page_renders_with_neither(tmp_path):
    """Tokens off (the suites, local dev): no account routes, so no cct_analytics; the template's
    guard renders nothing rather than failing the page."""
    code = ("import app; r = app.app.test_client().get('/?ref=site-header'); h = r.get_data(as_text=True); "
            "print('PAGE', r.status_code, 'googletagmanager' in h, "
            "any(c.startswith('cct_src=') for c in r.headers.getlist('Set-Cookie')))")
    out = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=_env(tmp_path, False), capture_output=True,
                         text=True, encoding='utf-8', errors='replace', timeout=300)
    assert 'PAGE 200 False False' in out.stdout, out.stdout + out.stderr[-2000:]


def test_accounts_come_up_with_both_switches_off(tmp_path):
    """This app's accounts set-up (accounts_setup.init_accounts) leaves both as cct_common ships
    them: off. Nothing here turns either on."""
    import flask
    from accounts_setup import init_accounts
    from cct_common import measure
    from cct_common.settings import setting
    app = flask.Flask(__name__)
    state = init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                          email_sender=lambda *a: (True, ''))
    app.add_url_rule('/p', 'p', lambda: flask.render_template_string('<head>{{ cct_analytics() }}</head>'))
    assert state.healthy
    assert setting(state.tokens.path, 'analytics_tag') is False
    assert setting(state.tokens.path, 'source_capture') is False
    r = app.test_client().get('/p?ref=site-header')
    assert r.status_code == 200 and 'googletagmanager' not in r.get_data(as_text=True)
    assert not [h for h in r.headers.getlist('Set-Cookie') if h.startswith(measure.COOKIE + '=')]
