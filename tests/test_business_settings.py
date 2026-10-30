"""test_business_settings.py — the business settings win (cct_common.settings;
Controller's campaigns/2026-10-27-signup-grant-10.md, phase 3): the signup
grant, the add-in daily limit, signups per IP, the prices, the packs and the
buy page are the shared database's, edited on the admin page, and this app
passes none of its own. They start at today's values, so nothing a user sees
changes until the owner edits one."""
import os
import re
import subprocess
import sys
from pathlib import Path

import flask
import pytest

from accounts_setup import init_accounts
from cct_common.payments import PayPalConfig, StripeConfig
from cct_common.settings import settings_for

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def state(tmp_path):
    return init_accounts(flask.Flask(__name__), log_dir=str(tmp_path), enabled=True, live=False,
                         email_sender=lambda *a: (True, ''),
                         payments={'paypal': PayPalConfig('id', 'secret'), 'stripe': StripeConfig('')})


def _new(state, email):
    acct = state.accounts.sign_in('email', email, email, email_verified=True)
    return state.tokens.balance(acct), acct


def test_a_new_signup_gets_the_settings_grant(state):
    first, a = _new(state, 'a@example.com')
    assert first == 20                                     # today's value, as before
    settings_for(state.tokens.path).set('signup_grant', 10, by='test')
    assert _new(state, 'b@example.com')[0] == 10           # the owner's last step
    assert state.tokens.balance(a) == 20                   # an existing balance is untouched
    assert _new(state, 'a@example.com') == (20, a)         # signing in again grants nothing


def test_the_other_settings_are_read_where_used(state):
    path = state.tokens.path
    assert (state.accounts.device_daily_budget, state.accounts.signups_per_ip_per_day) == (100, 2)
    assert state.tokens.tier_prices() == {'2d': 2, 'stl': 3, 'step': 4}
    settings_for(path).set('device_daily_budget', 50, by='test')
    settings_for(path).set('signups_per_ip_per_day', 5, by='test')
    settings_for(path).set('tier_prices', {'2d': 1, 'stl': 3, 'step': 5}, by='test')
    assert (state.accounts.device_daily_budget, state.accounts.signups_per_ip_per_day) == (50, 5)
    assert state.tokens.tier_prices() == {'2d': 1, 'stl': 3, 'step': 5}


def test_the_buy_page_sells_the_settings_packs(tmp_path, monkeypatch):
    from app import _payment_settings          # what the app passes: providers, no packs of its own
    monkeypatch.setenv('PAYPAL_CLIENT_ID', 'id')
    monkeypatch.setenv('PAYPAL_CLIENT_SECRET', 'secret')
    app, sent = flask.Flask(__name__), []
    st = init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                       email_sender=lambda *a: sent.append(a) or (True, ''),
                       payments=_payment_settings())
    c = app.test_client()
    c.post('/api/account/login-link', json={'email': 'a@example.com'})
    c.post('/account/login', data={'token': re.search(r'token=([^&\s]+)', sent[-1][2]).group(1)})
    page = c.get('/account/buy').get_data(as_text=True)
    assert '40 tokens' in page and '100 tokens' in page            # today's PayPal packs
    settings_for(st.tokens.path).set(
        'packs', {'paypal': [{'cents': 300, 'tokens': 60}], 'stripe': []}, by='test')
    page = c.get('/account/buy').get_data(as_text=True)
    assert '60 tokens' in page and '40 tokens' not in page


def test_the_app_passes_no_settings_of_its_own(tmp_path):
    """Built as it runs live (tokens on), in a fresh process: no grant, limits,
    packs or buy page of this app's own, so the settings win; the page's offer
    answers today's values; the old env vars do nothing."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('CCT_SERVER_USE', 'K_SERVICE', 'RESEND_API_KEY', 'CCT_MODE', 'CCT_ACCOUNTS_MODE')}
    env.update(TOKENS_ENABLED='1', DATABASE_URL=str(tmp_path / 'accounts.sqlite3'),
               PULLEY_LOG_DIR=str(tmp_path), PULLEY_TESTING='1', QUEUE_DISABLED='1',
               PYTHONIOENCODING='utf-8',
               TOKENS_SIGNUP_GRANT='7', TOKENS_BUY_URL='https://example.com/buy',
               TOKENS_DEVICE_DAILY_BUDGET='9', TOKENS_SIGNUPS_PER_IP_PER_DAY='9',
               TOKEN_PACKS='paypal:2=9')
    code = ("import app, json; s = app._accounts_state; c = app.app.test_client(); "
            "o = c.get('/api/offer').get_json(); "
            "print('GIVEN', sorted(s.accounts._given), app.charges._buy_url); "
            "print('OFFER', json.dumps([o['signup_grant'], o['tier_prices'], o['buy_url'], o['packs']], "
            "sort_keys=True)); "
            "print('BUY', app.charges.buy_url)")
    out = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True,
                         encoding='utf-8', timeout=180)
    said = out.stdout + out.stderr[-2000:]
    assert 'GIVEN [] None' in out.stdout, said
    assert ('OFFER [20, {"2d": 2, "step": 4, "stl": 3}, "https://cheapcadtools.com/account/buy", '
            in out.stdout), said
    assert '"tokens": 40' in out.stdout and '"tokens": 9' not in out.stdout, said
    assert 'BUY https://cheapcadtools.com/account/buy' in out.stdout, said


def test_the_page_prices_the_download_window_from_the_offer():
    """The Download window's tier prices are cctTokens.price (api/offer), read
    before it opens; the page holds no prices of its own."""
    page = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')
    assert "tokens: tokensOn ? cctTokens.price(t.fmts[0][0]) : null" in page
    assert 'if (tokensOn) await cctTokens.loadOffer();' in page
    tiers = page[page.index('const _DL_TIERS_3D = ['):page.index('const _DL_TIERS_2D')]
    assert 'tokens:' not in tiers
