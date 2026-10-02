"""Token charging on the real download routes (ADR-008, charging.py).

conftest's app is imported with tokens off, and Flask won't take new routes
once it has served requests, so these tests switch charging on by pointing
the `charges` singleton at an enabled accounts state and registering the
account store the routes look up. Requests authenticate with an add-in
device token (Authorization: Bearer)."""
import os
import time
from types import SimpleNamespace

import pytest

from accounts_setup import AccountsState
from charging import DesignStore, charges, design_matches
from cct_common.accounts import AccountStore
from cct_common.tokens import TokenStore

DESIGN = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10',
          'p2_teeth': '40', 'p2_bore': '10'}
Q = 'family=HTD&pitch=5M&teeth=20&bore=8&belt_height=10'


@pytest.fixture
def paid(client, tmp_path, monkeypatch):
    from app import app
    tokens = TokenStore(str(tmp_path / 'accounts.sqlite3'))
    accounts = AccountStore(tokens, signup_grant=20)
    state = AccountsState(enabled=True, healthy=True, tokens=tokens, accounts=accounts)
    monkeypatch.setattr(charges, 'state', state)
    monkeypatch.setattr(charges, 'designs', DesignStore(tokens.path))
    monkeypatch.setitem(app.extensions, 'cct_accounts', {'accounts': accounts})
    acct = accounts.sign_in('email', 'a@example.com', 'a@example.com', email_verified=True)
    auth = {'Authorization': f'Bearer {accounts.create_session(acct, kind="device")}'}
    design_id = charges.designs.register(acct, DESIGN)
    return SimpleNamespace(client=client, tokens=tokens, acct=acct, auth=auth,
                           design_id=design_id, balance=lambda: tokens.balance(acct))


def _get(paid, path, **extra):
    q = Q + ''.join(f'&{k}={v}' for k, v in extra.items())
    return paid.client.get(f'{path}?{q}', headers=paid.auth)


# ── the gate ──────────────────────────────────────────────────────────────

def test_signed_out_download_is_refused(paid):
    r = paid.client.get(f'/download/svg?{Q}')
    assert r.status_code == 401 and r.get_json()['code'] == 'SIGN_IN_REQUIRED'
    assert paid.balance() == 20


def test_unhealthy_accounts_refuse_with_503(paid, monkeypatch):
    monkeypatch.setattr(charges.state, 'healthy', False)
    r = _get(paid, '/download/svg')
    assert r.status_code == 503 and r.get_json()['code'] == 'ACCOUNTS_UNAVAILABLE'


# ── prices, unlocks and refunds ───────────────────────────────────────────

@pytest.mark.parametrize('path,cost', [('/download/svg', 2), ('/download/dxf', 2),
                                       ('/download/stl', 3), ('/download/step', 4)])
def test_each_format_costs_its_tier(paid, path, cost):
    r = _get(paid, path)
    assert r.status_code == 200, r.data[:200]
    assert r.headers['X-CCT-Tokens-Charged'] == str(cost)
    assert r.headers['X-CCT-Tokens-Balance'] == str(20 - cost)
    assert paid.balance() == 20 - cost


def test_redownload_is_free(paid):
    _get(paid, '/download/stl')
    r = _get(paid, '/download/stl')
    assert r.status_code == 200 and r.headers['X-CCT-Tokens-Charged'] == '0'
    assert paid.balance() == 17


def test_step_unlocks_the_designs_other_formats(paid):
    did = paid.design_id
    assert _get(paid, '/download/step', design_id=did).headers['X-CCT-Tokens-Charged'] == '4'
    for path in ('/download/stl', '/download/svg', '/download/dxf'):
        r = _get(paid, path, design_id=did)
        assert r.status_code == 200 and r.headers['X-CCT-Tokens-Charged'] == '0', path
    assert paid.balance() == 16


def test_pulley_2_of_the_design_is_covered_too(paid):
    did = paid.design_id
    _get(paid, '/download/step', design_id=did)
    r = paid.client.get(f'/download/svg?family=HTD&pitch=5M&teeth=40&bore=10&belt_height=10'
                        f'&pulley=2&design_id={did}', headers=paid.auth)
    assert r.status_code == 200 and r.headers['X-CCT-Tokens-Charged'] == '0'


def test_upgrade_pays_the_difference(paid):
    did = paid.design_id
    assert _get(paid, '/download/svg', design_id=did).headers['X-CCT-Tokens-Charged'] == '2'
    assert _get(paid, '/download/step', design_id=did).headers['X-CCT-Tokens-Charged'] == '2'
    assert paid.balance() == 16


def test_design_id_of_another_design_unlocks_nothing(paid):
    did = paid.design_id                                    # registered with teeth=20
    _get(paid, '/download/step', design_id=did)
    r = paid.client.get(f'/download/svg?family=HTD&pitch=5M&teeth=30&bore=8&belt_height=10'
                        f'&design_id={did}', headers=paid.auth)
    assert r.status_code == 200 and r.headers['X-CCT-Tokens-Charged'] == '2'


def test_made_up_design_id_is_priced_as_its_own_design(paid):
    r = _get(paid, '/download/svg', design_id='0' * 32)
    assert r.headers['X-CCT-Tokens-Charged'] == '2'


def test_failed_export_is_refunded(paid):
    r = paid.client.get('/download/step?family=BOGUS&pitch=5M&teeth=20&bore=8&belt_height=10',
                        headers=paid.auth)
    assert r.status_code >= 400
    assert paid.balance() == 20
    kinds = [row['kind'] for row in paid.tokens.history(paid.acct)]
    assert kinds[:2] == ['refund', 'spend']


def test_not_enough_tokens_is_402_and_nothing_is_generated(paid):
    paid.tokens.credit(paid.acct, -18, kind='adjust')     # balance 2
    r = _get(paid, '/download/step')
    body = r.get_json()
    assert r.status_code == 402 and body['code'] == 'NOT_ENOUGH_TOKENS'
    assert (body['needed'], body['balance']) == (4, 2)
    assert paid.balance() == 2


# ── add-in API and background jobs ────────────────────────────────────────

def test_addin_api_charges_tokens_instead_of_the_trial_limit(paid, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'register_trial_download',
                        lambda *a: pytest.fail('trial limit must not apply with tokens on'))
    r = paid.client.post('/api/download/stl', headers=paid.auth, json={
        'machine_id': 'm1',
        'params': {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10'}})
    assert r.status_code == 200, r.data[:200]
    assert r.headers['X-CCT-Tokens-Charged'] == '3' and paid.balance() == 17


def test_async_step_job_charges_once_it_runs(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')             # run the job in the request
    body = dict(DESIGN, design_id=paid.design_id)
    body.pop('p2_teeth'), body.pop('p2_bore')
    r = paid.client.post('/api/download/step-async', headers=paid.auth, json=body)
    assert r.status_code == 200, r.data[:200]
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'done'
    assert paid.balance() == 16


def test_async_step_job_failure_is_refunded(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = paid.client.post('/api/download/step-async', headers=paid.auth,
                         json={'family': 'BOGUS', 'pitch': '5M', 'teeth': '20', 'bore': '8'})
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'failed'
    assert paid.balance() == 20


def test_async_step_job_refused_up_front_when_unaffordable(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    paid.tokens.credit(paid.acct, -17, kind='adjust')     # balance 3, a STEP is 4
    r = paid.client.post('/api/download/step-async', headers=paid.auth, json=dict(DESIGN))
    assert r.status_code == 402 and 'job_id' not in r.get_json()


# ── the design match rules ────────────────────────────────────────────────

def test_design_matches_flange_aliases_pulley2_names_and_number_formats():
    design = {'family': 'HTD', 'teeth': '20', 'p2_teeth': '40', 'hub_flat_depth': '0.5',
              'hub_od': '', 'belt_height': '10.0'}
    assert design_matches({'family': 'HTD', 'teeth': '40', 'flat_depth': '.5',
                           'hub_od': '0', 'belt_height': '10', 'flange_which': 'top'}, design)
    assert not design_matches({'teeth': '30'}, design)
    assert not design_matches({'spokes_count': '5'}, design)   # not in the design at all


def test_off_by_default_leaves_routes_as_they_were(client):
    # With charging off (conftest's app), downloads need no sign-in.
    assert charges.enabled is False
    assert client.get(f'/download/svg?{Q}').status_code == 200


# ── price check (/api/tokens/quote) and design registration (/api/design) ──

@pytest.fixture
def mini(tmp_path):
    """A small app with its own Charges, so attach() can add its routes."""
    import flask
    from accounts_setup import init_accounts
    from charging import Charges

    ch = Charges()
    app = flask.Flask(__name__)

    @app.route('/download/stl')
    @ch.charged('stl')
    def download_stl():
        return 'solid x'

    state = init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                          email_sender=lambda *a: (True, ''))
    ch.attach(app, state, buy_url='https://shop.example/tokens')
    acct = state.accounts.sign_in('email', 'a@example.com', 'a@example.com', email_verified=True)
    auth = {'Authorization': f'Bearer {state.accounts.create_session(acct, kind="device")}'}
    return SimpleNamespace(c=app.test_client(), auth=auth, tokens=state.tokens, acct=acct)


def test_quote_prices_a_download_exactly_as_the_download_does(mini):
    did = mini.c.post('/api/design', headers=mini.auth, json={'design': DESIGN}).get_json()['design_id']
    params = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'design_id': did}
    q = mini.c.post('/api/tokens/quote', headers=mini.auth,
                    json={'path': '/download/stl', 'params': params}).get_json()
    assert (q['cost'], q['tier'], q['balance'], q['held_tier']) == (3, 'stl', 20, None)
    assert q['buy_url'] == 'https://shop.example/tokens'
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    assert mini.c.get(f'/download/stl?{qs}', headers=mini.auth).headers['X-CCT-Tokens-Charged'] == '3'
    q = mini.c.post('/api/tokens/quote', headers=mini.auth,
                    json={'path': '/download/stl', 'params': params}).get_json()
    assert (q['cost'], q['held_tier'], q['balance']) == (0, 'stl', 17)


def test_quote_and_design_need_sign_in_and_a_charged_route(mini):
    assert mini.c.post('/api/tokens/quote', json={'path': '/download/stl'}).status_code == 401
    assert mini.c.post('/api/design', json={'design': DESIGN}).status_code == 401
    r = mini.c.post('/api/tokens/quote', headers=mini.auth, json={'path': '/not/a/download', 'params': {}})
    assert r.status_code == 400
    assert mini.c.post('/api/design', headers=mini.auth, json={'design': {}}).status_code == 400
    assert mini.c.post('/api/design', headers=mini.auth, json={'design': 'x'}).status_code == 400


def test_402_carries_the_buy_link(mini):
    mini.tokens.credit(mini.acct, -18, kind='adjust')
    r = mini.c.get('/download/stl?family=HTD&pitch=5M&teeth=20&bore=8', headers=mini.auth)
    assert r.status_code == 402 and r.get_json()['buy_url'] == 'https://shop.example/tokens'


# ── the download window's zip (bundles.py) ────────────────────────────────

P = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10'}


def _bundle(paid, files, design_id=None):
    return paid.client.post('/api/download/bundle', headers=paid.auth, json={
        'design_id': design_id or paid.design_id, 'name': 'HTD-5M-20T', 'files': files})


def _zip_names(client, status):
    import io
    import zipfile
    r = client.get(status['output_file'])
    assert r.status_code == 200 and r.mimetype == 'application/zip'
    return sorted(zipfile.ZipFile(io.BytesIO(r.data)).namelist())


def test_bundle_is_one_charge_at_the_highest_tier(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = _bundle(paid, [{'path': '/download/step', 'params': P},
                       {'path': '/download/stl', 'params': P},
                       {'path': '/download/svg', 'params': dict(P, include_data='1')}])
    assert r.status_code == 200, r.data[:300]
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'done', status
    names = _zip_names(paid.client, status)
    assert [n.rsplit('.', 1)[1] for n in names] == ['step', 'stl', 'svg']
    spends = [h for h in paid.tokens.history(paid.acct) if h['kind'] == 'spend']
    assert [(s['amount'], s['fmt']) for s in spends] == [(-4, 'step')]
    assert paid.balance() == 16


def test_bundle_of_drawings_costs_the_2d_price(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = _bundle(paid, [{'path': '/download/svg', 'params': P}, {'path': '/download/dxf', 'params': P}])
    assert paid.client.get(r.get_json()['status_url']).get_json()['status'] == 'done'
    assert paid.balance() == 18


def test_bundle_refuses_files_from_another_design(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = _bundle(paid, [{'path': '/download/svg', 'params': dict(P, teeth='30')}])
    assert r.status_code == 400 and paid.balance() == 20


def test_bundle_refuses_non_downloads_and_unknown_designs(paid):
    assert _bundle(paid, [{'path': '/api/account', 'params': {}}]).status_code == 400
    assert _bundle(paid, []).status_code == 400
    assert _bundle(paid, [{'path': '/download/svg', 'params': P}], design_id='0' * 32).status_code == 400


def test_bundle_refused_up_front_when_unaffordable(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    paid.tokens.credit(paid.acct, -17, kind='adjust')     # balance 3, a STEP is 4
    r = _bundle(paid, [{'path': '/download/step', 'params': P}])
    assert r.status_code == 402 and 'job_id' not in r.get_json()


def test_bundle_failure_refunds_everything(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    bad = dict(P, family='BOGUS')
    paid.designs = charges.designs
    did = charges.designs.register(paid.acct, dict(DESIGN, family='BOGUS'))
    r = _bundle(paid, [{'path': '/download/svg', 'params': dict(P, family='BOGUS')},
                       {'path': '/download/step', 'params': bad}], design_id=did)
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'failed'
    assert paid.balance() == 20


def test_forged_internal_header_gets_no_free_download(paid):
    r = paid.client.get(f'/download/svg?{Q}', headers={'X-CCT-Internal': 'guess'})
    assert r.status_code == 401


def test_bundle_with_tokens_off_needs_no_sign_in(client, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = client.post('/api/download/bundle', json={
        'name': 'x', 'files': [{'path': '/download/svg', 'params': P}]})
    status = client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'done'
    assert _zip_names(client, status)[0].endswith('.svg')


# ── daily limits on add-in/agent tokens ───────────────────────────────────

@pytest.fixture
def agent(paid, monkeypatch):
    """A device token with a daily limit of 6, and a record of limit emails."""
    accounts = charges.state.accounts
    token = accounts.create_session(paid.acct, kind='device', label='Claude MCP', daily_budget=6)
    emails = []
    monkeypatch.setattr(charges, 'limit_notify', lambda *a: emails.append(a) or True)
    paid.agent = {'Authorization': f'Bearer {token}'}
    paid.emails = emails
    return paid


def test_agent_stops_at_its_daily_limit_and_emails_once(agent):
    ok = agent.client.get(f'/download/step?{Q}', headers=agent.agent)
    assert ok.status_code == 200 and ok.headers['X-CCT-Tokens-Charged'] == '4'
    r = agent.client.get('/download/stl?family=HTD&pitch=5M&teeth=30&bore=8&belt_height=10',
                         headers=agent.agent)
    body = r.get_json()
    assert r.status_code == 429 and body['code'] == 'DAILY_LIMIT_REACHED'
    assert (body['budget'], body['spent'], body['needed']) == (6, 4, 3)
    assert body['settings_url'].endswith('/account/devices')
    assert agent.balance() == 16                                   # nothing charged
    agent.client.get('/download/stl?family=HTD&pitch=5M&teeth=31&bore=8&belt_height=10',
                     headers=agent.agent)
    assert len(agent.emails) == 1                                 # one email, not one per refusal
    email, label, budget, url = agent.emails[0]
    assert (email, label, budget) == ('a@example.com', 'Claude MCP', 6)


def test_browser_session_has_no_limit(agent):
    web = charges.state.accounts.create_session(agent.acct, kind='web')
    c = agent.client.application.test_client()
    c.set_cookie('cct_session', web)
    for teeth in (20, 21, 22):                                    # 12 tokens, past any agent limit of 6
        r = c.get(f'/download/step?family=HTD&pitch=5M&teeth={teeth}&bore=8&belt_height=10')
        assert r.status_code == 200
    assert agent.balance() == 8


def test_agent_limit_refuses_background_jobs_and_zips_up_front(agent, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    agent.client.get(f'/download/step?{Q}', headers=agent.agent)  # 4 of 6 used
    r = agent.client.post('/api/download/step-async', headers=agent.agent,
                          json={'family': 'HTD', 'pitch': '5M', 'teeth': '33', 'bore': '8'})
    assert r.status_code == 429 and 'job_id' not in r.get_json()
    did = charges.designs.register(agent.acct, dict(DESIGN, teeth='34'))
    r = agent.client.post('/api/download/bundle', headers=agent.agent, json={
        'design_id': did, 'name': 'x',
        'files': [{'path': '/download/step', 'params': dict(P, teeth='34')}]})
    assert r.status_code == 429


SPLINED = {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'bore': '23.0105', 'belt_height': '12',
           'bore_shape': 'spline', 'spline_type': 'straight', 'spline_n': '6', 'spline_minor': '23',
           'spline_major': '26', 'spline_width': '6', 'spline_ring_top': '1', 'spline_ring_bottom': '0',
           'spline_washer': '1'}


def test_bundle_takes_a_splined_designs_shaft_and_washer(paid, monkeypatch):
    """The Download window sends the sample shaft and washer as
    /download/spline-* with part=shaft / washer — a choice of part, not a
    design setting. It was refused as "isn't part of this design" (bug
    30ae49ecd8, 2026-10-02) because `part` wasn't a route-only key."""
    monkeypatch.setenv('PULLEY_TESTING', '1')
    did = charges.designs.register(paid.acct, SPLINED)
    r = _bundle(paid, [{'path': '/download/stl', 'params': SPLINED},
                       {'path': '/download/spline-stl', 'params': dict(SPLINED, part='shaft')},
                       {'path': '/download/spline-dxf', 'params': dict(SPLINED, part='washer')}], did)
    assert r.status_code == 200, r.data[:300]
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'done', status
    assert len(_zip_names(paid.client, status)) == 3
    # still refused when the part's other settings belong to another design
    r = _bundle(paid, [{'path': '/download/spline-stl', 'params': dict(SPLINED, part='shaft', spline_n='8')}], did)
    assert r.status_code == 400 and "isn't part of this design" in r.get_json()['error']


def test_design_matches_ignores_the_spline_part():
    from charging import canonical_design
    design = canonical_design(SPLINED)
    assert design_matches(dict(SPLINED, part='shaft'), design)
    assert design_matches(dict(SPLINED, part='washer', pulley='2'), design)
    assert 'part' not in canonical_design(dict(SPLINED, part='shaft'))


# ── Paid only for what arrives (cct_common.charging "delivery"; the owner,
# 2026-10-02: a download failed and was still charged) ──────────────────

def _drop_result(output_file):
    """Delete a stored result, as an expired link or another server would."""
    import shutil
    import app as appmod
    import results
    token = output_file.split('/')[3]
    shutil.rmtree(os.path.join(results._root(appmod._LOG_DIR), token), ignore_errors=True)


def test_bundle_whose_zip_is_gone_is_refunded(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = _bundle(paid, [{'path': '/download/stl', 'params': P}])
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'done' and paid.balance() == 17     # held while it waits
    _drop_result(status['output_file'])
    assert paid.client.get(status['output_file']).status_code == 404
    assert paid.balance() == 20
    assert 'refund' in [h['kind'] for h in paid.tokens.history(paid.acct)]


def test_async_step_fetched_in_full_stays_paid(paid, monkeypatch):
    from charging import charges
    monkeypatch.setenv('PULLEY_TESTING', '1')
    body = dict(DESIGN, design_id=paid.design_id)
    body.pop('p2_teeth'), body.pop('p2_bore')
    r = paid.client.post('/api/download/step-async', headers=paid.auth, json=body)
    status = paid.client.get(r.get_json()['status_url']).get_json()
    got = paid.client.get(status['output_file'])
    assert got.status_code == 200 and len(got.data) > 1000
    got.close()
    assert charges.sweep_undelivered(now=time.time() + 10**6) == 0  # nothing left pending
    assert paid.balance() == 16


def test_bundle_nobody_fetches_is_refunded_by_the_sweep(paid, monkeypatch):
    from charging import charges
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = _bundle(paid, [{'path': '/download/stl', 'params': P}])
    assert paid.client.get(r.get_json()['status_url']).get_json()['status'] == 'done'
    assert charges.sweep_undelivered(now=time.time()) == 0       # not overdue yet
    assert paid.balance() == 17
    assert charges.sweep_undelivered(now=time.time() + 10**6) == 1
    assert paid.balance() == 20
