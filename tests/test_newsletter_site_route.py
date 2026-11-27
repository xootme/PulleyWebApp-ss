"""test_newsletter_site_route.py — cct_common 0.67.0's newsletter box for the website
(cct_common.newsletter_site), on this app's own account routes (Controller task
pulleywebapp-newsletter-sign-up-route-for-the-website-sync-th): pulley hosts the account
routes for cheapcadtools.com, so the website's footer box posts here.

- With newsletter_opt_in off (pulley-test today), the subscribe and confirm routes are 404s
  and no email goes;
- on, a sign-up is answered 202 and sends the approved confirm email, whose link opens a
  page with one button; only the button subscribes ("You're subscribed");
- CORS answers exactly the two www origins, never another site.
"""
import re

import flask
import pytest

from accounts_setup import init_accounts
from cct_common.newsletter import Newsletter, site_key
from cct_common.newsletter_site import CONFIRM_BUTTON, DONE_TITLE, SUBJECT
from cct_common.settings import settings_for

WWW = ('https://www.cheapcadtools.com', 'http://www.cheapcadtools.com')


@pytest.fixture
def state(tmp_path):
    sent = []
    app = flask.Flask(__name__)
    st = init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                       email_sender=lambda to, subject, body: (sent.append((to, subject, body)), (True, ''))[1])
    st.app, st.sent = app, sent
    return st


def _on(state):
    settings_for(state.tokens.path).set('newsletter_opt_in', True, by='test')


def test_off_the_routes_are_404s_and_nothing_is_sent(state):
    c = state.app.test_client()
    r = c.post('/api/newsletter/subscribe', json={'email': 'off@example.com', 'website': ''},
               headers={'Origin': WWW[0]})
    assert r.status_code == 404 and not state.sent
    assert c.get('/newsletter/confirm?t=x').status_code == 404
    assert c.post('/newsletter/confirm', data={'t': 'x'}).status_code == 404


def test_on_a_sign_up_is_confirmed_only_by_the_button(state):
    _on(state)
    c = state.app.test_client()
    r = c.post('/api/newsletter/subscribe', json={'email': 'Box@Example.com', 'website': ''},
               headers={'Origin': WWW[0]})
    assert r.status_code == 202 and r.get_json() == {'ok': True}
    [(to, subject, body)] = state.sent
    assert to == 'box@example.com' and subject == SUBJECT
    link = re.search(r'http://localhost/newsletter/confirm\?t=(\S+)', body)
    assert link, body
    news = Newsletter(state.tokens.path)
    page = c.get(f'/newsletter/confirm?t={link.group(1)}')
    assert page.status_code == 200 and CONFIRM_BUTTON in page.get_data(as_text=True)
    assert not news.state(site_key('box@example.com'))['subscribed']       # opening the link: nothing
    done = c.post('/newsletter/confirm', data={'t': link.group(1)})
    assert done.status_code == 200 and DONE_TITLE in done.get_data(as_text=True).replace('&#x27;', "'")
    assert news.state(site_key('box@example.com'))['subscribed']


@pytest.mark.parametrize('origin, allowed', [
    (WWW[0], True), (WWW[1], True),
    ('https://cheapcadtools.com', False), ('https://evil.example', False), (None, False)])
def test_cors_answers_only_the_two_www_origins(state, origin, allowed):
    _on(state)
    c = state.app.test_client()
    headers = {'Origin': origin} if origin else {}
    pre = c.open('/api/newsletter/subscribe', method='OPTIONS', headers=headers)
    post = c.post('/api/newsletter/subscribe', json={'email': 'cors@example.com', 'website': ''},
                  headers=headers)
    for r in (pre, post):
        assert r.headers.get('Access-Control-Allow-Origin') == (origin if allowed else None)
