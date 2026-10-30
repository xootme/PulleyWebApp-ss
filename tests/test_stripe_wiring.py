"""
test_stripe_wiring.py — card payments (cct_common.payments' Stripe path) as
Pulley serves them, against a stand-in Stripe API. cct_common's own
tests/test_payments.py covers the mechanics (our price, signature, amount
mismatch, refunds); these cover what Pulley adds and the gaps:

  - the env names Pulley reads (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET) and
    the packs its buy page offers by card;
  - behind cheapcadtools.com's Worker, Stripe sends the buyer back to
    https://cheapcadtools.com/account/buy/... (the Worker routes /account/ and
    /api/ to Pulley) — and a forged X-Forwarded-Host without the edge secret
    can't move that return anywhere else;
  - the same signed event delivered twice (Stripe retries) credits once;
  - a card payment that settles later (async) credits on the second event only;
  - an event for an order we never made, signed correctly, credits nothing.
"""
import hashlib
import hmac
import json
import re
from pathlib import Path

import pytest
from flask import Flask

from cct_common.account_routes import COOKIE_NAME, register_account_routes
from cct_common.accounts import AccountStore
from cct_common.payments import register_payment_routes
from cct_common.tokens import TokenStore
from edge_proxy import EdgeAwareProxyFix

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000.0
SK, WHSEC, EDGE = 'sk_test_pulley', 'whsec_pulley', 'edge-secret'


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeStripe:
    """Stripe's checkout API, as far as payments.py uses it."""
    def __init__(self):
        self.sessions, self.created = {}, []

    def post(self, url, auth=None, data=None, timeout=None, **_):
        assert url == 'https://api.stripe.com/v1/checkout/sessions' and auth == (SK, '')
        sid = f'cs_test_{len(self.sessions) + 1}'
        self.created.append(data)
        self.sessions[sid] = {'id': sid, 'mode': 'payment', 'payment_status': 'unpaid',
                              'amount_total': int(data['line_items[0][price_data][unit_amount]']),
                              'currency': 'usd', 'payment_intent': f'pi_{sid}',
                              'url': f'https://checkout.stripe.com/c/pay/{sid}'}
        return _Resp(200, self.sessions[sid])

    def get(self, url, auth=None, timeout=None, **_):
        return _Resp(200, self.sessions[re.search(r'/checkout/sessions/(\w+)$', url).group(1)])


@pytest.fixture
def settings(monkeypatch):
    """Pulley's own payment settings (app._payment_settings) from the env."""
    import app as appmod
    monkeypatch.setenv('STRIPE_SECRET_KEY', SK)
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', WHSEC)
    for k in ('PAYPAL_CLIENT_ID', 'PAYPAL_CLIENT_SECRET', 'TOKEN_PACKS'):
        monkeypatch.delenv(k, raising=False)
    return appmod._payment_settings()


@pytest.fixture
def shop(tmp_path, settings):
    """Accounts and payments as Pulley registers them, behind the edge proxy."""
    tokens = TokenStore(str(tmp_path / 'accounts.sqlite3'), clock=lambda: NOW)
    accounts = AccountStore(tokens, signup_grant=20)
    app = Flask(__name__)
    app.config['TESTING'] = True
    register_account_routes(app, accounts, email_sender=lambda *a: (True, ''), secure_cookies=False)
    api = FakeStripe()
    enabled = register_payment_routes(app, accounts, http=api, clock=lambda: NOW,
                                      app_name='CheapCADTools', **settings)
    app.wsgi_app = EdgeAwareProxyFix(app.wsgi_app, secret=EDGE)
    acct = tokens.get_or_create_account('buyer@example.com', signup_grant=20)
    c = app.test_client()
    c.set_cookie(COOKIE_NAME, accounts.create_session(acct, kind='web'))
    return {'client': c, 'api': api, 'tokens': tokens, 'acct': acct, 'enabled': enabled}


VIA_EDGE = {'X-CCT-Edge': EDGE, 'X-Forwarded-Host': 'cheapcadtools.com', 'X-Forwarded-Proto': 'https',
            'X-Forwarded-For': '203.0.113.9, 10.0.0.1'}


def _webhook(c, event, *, secret=WHSEC, ts=NOW):
    payload = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), f'{int(ts)}.'.encode() + payload, hashlib.sha256).hexdigest()
    return c.post('/api/payments/stripe/webhook', data=payload,
                  headers={'Stripe-Signature': f't={int(ts)},v1={sig}', 'Content-Type': 'application/json'})


def _checkout(shop, pack='stripe-500', headers=VIA_EDGE):
    r = shop['client'].post('/api/payments/stripe/checkout', json={'pack': pack}, headers=headers)
    assert r.status_code == 200, r.data
    return list(shop['api'].sessions.values())[-1]


def test_pulley_reads_the_stripe_env_names(settings):
    assert settings['stripe'].secret_key == SK and settings['stripe'].webhook_secret == WHSEC


def test_the_buy_panel_gets_the_publishable_key(monkeypatch):
    """The in-page buy panel (cct_common 0.35.0, off): Stripe's public key from
    STRIPE_PUBLISHABLE_KEY; none set, the panel offers PayPal only."""
    import app as appmod
    monkeypatch.setenv('STRIPE_PUBLISHABLE_KEY', 'pk_test_panel')
    assert appmod._payment_settings()['stripe'].publishable_key == 'pk_test_panel'
    monkeypatch.delenv('STRIPE_PUBLISHABLE_KEY')
    assert appmod._payment_settings()['stripe'].publishable_key == ''


def test_no_stripe_key_no_card_payments(monkeypatch):
    import app as appmod
    monkeypatch.delenv('STRIPE_SECRET_KEY', raising=False)
    assert not appmod._payment_settings()['stripe'].secret_key   # register_payment_routes skips it


def test_the_buy_page_offers_the_card_packs(shop):
    assert shop['enabled'] == ['stripe']
    page = shop['client'].get('/account/buy').get_data(as_text=True)
    assert 'Pay by card' in page
    for price, n in (('$5', 100), ('$10', 200), ('$25', 500)):      # 5 cents a token
        assert f"<b>{price}</b>{n} tokens" in page


def test_through_the_edge_stripe_returns_to_cheapcadtools(shop):
    _checkout(shop)
    sent = shop['api'].created[-1]
    assert sent['success_url'] == ('https://cheapcadtools.com/account/buy/stripe-return'
                                   '?session_id={CHECKOUT_SESSION_ID}')
    assert sent['cancel_url'] == 'https://cheapcadtools.com/account/buy'
    assert sent['line_items[0][price_data][unit_amount]'] == '500'
    assert sent['customer_email'] == 'buyer@example.com' and sent['client_reference_id'] == shop['acct']


def test_a_forged_host_without_the_edge_secret_cant_move_the_return(shop):
    """Negative control: the same headers minus the edge secret are not believed."""
    forged = {k: v for k, v in VIA_EDGE.items() if k != 'X-CCT-Edge'} | {'X-Forwarded-Host': 'evil.example'}
    _checkout(shop, headers=forged)
    assert 'evil.example' not in shop['api'].created[-1]['success_url']


def test_the_worker_sends_the_return_and_webhook_to_pulley():
    js = (ROOT / 'site' / 'worker-cloudrun.js').read_text(encoding='utf-8')
    app_paths = re.search(r"const appPaths = \[([^\]]*)\]", js).group(1)
    assert "'/api/'" in app_paths and "'/account/'" in app_paths
    assert re.search(r"appPaths\.some\(.*\)\)\s*\{\s*return toApp\(request, env, PULLEYS", js)


def test_a_retried_webhook_credits_once(shop):
    s = dict(_checkout(shop, 'stripe-1000'), payment_status='paid')
    event = {'id': 'evt_1', 'type': 'checkout.session.completed', 'data': {'object': s}}
    assert _webhook(shop['client'], event).status_code == 200
    assert _webhook(shop['client'], event).status_code == 200      # Stripe retries the same event
    assert shop['tokens'].balance(shop['acct']) == 20 + 200


def test_a_late_settling_card_credits_on_the_second_event_only(shop):
    s = _checkout(shop, 'stripe-2500')
    c = shop['client']
    _webhook(c, {'type': 'checkout.session.completed', 'data': {'object': s}})        # unpaid yet
    assert shop['tokens'].balance(shop['acct']) == 20
    paid = dict(s, payment_status='paid')
    _webhook(c, {'type': 'checkout.session.async_payment_succeeded', 'data': {'object': paid}})
    assert shop['tokens'].balance(shop['acct']) == 20 + 500


def test_a_signed_event_for_an_order_we_never_made_credits_nothing(shop):
    forged = {'id': 'cs_test_not_ours', 'mode': 'payment', 'payment_status': 'paid',
              'amount_total': 2500, 'currency': 'usd', 'payment_intent': 'pi_x'}
    assert _webhook(shop['client'], {'type': 'checkout.session.completed',
                                     'data': {'object': forged}}).status_code == 200
    assert shop['tokens'].balance(shop['acct']) == 20


def test_a_bad_signature_is_refused(shop):
    """Negative control for the retry test: the signature check is live."""
    s = dict(_checkout(shop), payment_status='paid')
    r = _webhook(shop['client'], {'type': 'checkout.session.completed', 'data': {'object': s}},
                 secret='whsec_wrong')
    assert r.status_code == 400 and shop['tokens'].balance(shop['acct']) == 20
