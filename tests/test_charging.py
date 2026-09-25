"""Token charging on the real download routes (ADR-008, charging.py).

conftest's app is imported with tokens off, and Flask won't take new routes
once it has served requests, so these tests switch charging on by pointing
the `charges` singleton at an enabled accounts state and registering the
account store the routes look up. Requests authenticate with an add-in
device token (Authorization: Bearer)."""
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
    accounts = AccountStore(tokens, signup_grant=10)
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
    assert paid.balance() == 10


def test_unhealthy_accounts_refuse_with_503(paid, monkeypatch):
    monkeypatch.setattr(charges.state, 'healthy', False)
    r = _get(paid, '/download/svg')
    assert r.status_code == 503 and r.get_json()['code'] == 'ACCOUNTS_UNAVAILABLE'


# ── prices, unlocks and refunds ───────────────────────────────────────────

@pytest.mark.parametrize('path,cost', [('/download/svg', 1), ('/download/dxf', 1),
                                       ('/download/stl', 2), ('/download/step', 3)])
def test_each_format_costs_its_tier(paid, path, cost):
    r = _get(paid, path)
    assert r.status_code == 200, r.data[:200]
    assert r.headers['X-CCT-Tokens-Charged'] == str(cost)
    assert r.headers['X-CCT-Tokens-Balance'] == str(10 - cost)
    assert paid.balance() == 10 - cost


def test_redownload_is_free(paid):
    _get(paid, '/download/stl')
    r = _get(paid, '/download/stl')
    assert r.status_code == 200 and r.headers['X-CCT-Tokens-Charged'] == '0'
    assert paid.balance() == 8


def test_step_unlocks_the_designs_other_formats(paid):
    did = paid.design_id
    assert _get(paid, '/download/step', design_id=did).headers['X-CCT-Tokens-Charged'] == '3'
    for path in ('/download/stl', '/download/svg', '/download/dxf'):
        r = _get(paid, path, design_id=did)
        assert r.status_code == 200 and r.headers['X-CCT-Tokens-Charged'] == '0', path
    assert paid.balance() == 7


def test_pulley_2_of_the_design_is_covered_too(paid):
    did = paid.design_id
    _get(paid, '/download/step', design_id=did)
    r = paid.client.get(f'/download/svg?family=HTD&pitch=5M&teeth=40&bore=10&belt_height=10'
                        f'&pulley=2&design_id={did}', headers=paid.auth)
    assert r.status_code == 200 and r.headers['X-CCT-Tokens-Charged'] == '0'


def test_upgrade_pays_the_difference(paid):
    did = paid.design_id
    assert _get(paid, '/download/svg', design_id=did).headers['X-CCT-Tokens-Charged'] == '1'
    assert _get(paid, '/download/step', design_id=did).headers['X-CCT-Tokens-Charged'] == '2'
    assert paid.balance() == 7


def test_design_id_of_another_design_unlocks_nothing(paid):
    did = paid.design_id                                    # registered with teeth=20
    _get(paid, '/download/step', design_id=did)
    r = paid.client.get(f'/download/svg?family=HTD&pitch=5M&teeth=30&bore=8&belt_height=10'
                        f'&design_id={did}', headers=paid.auth)
    assert r.status_code == 200 and r.headers['X-CCT-Tokens-Charged'] == '1'


def test_made_up_design_id_is_priced_as_its_own_design(paid):
    r = _get(paid, '/download/svg', design_id='0' * 32)
    assert r.headers['X-CCT-Tokens-Charged'] == '1'


def test_failed_export_is_refunded(paid):
    r = paid.client.get('/download/step?family=BOGUS&pitch=5M&teeth=20&bore=8&belt_height=10',
                        headers=paid.auth)
    assert r.status_code >= 400
    assert paid.balance() == 10
    kinds = [row['kind'] for row in paid.tokens.history(paid.acct)]
    assert kinds[:2] == ['refund', 'spend']


def test_not_enough_tokens_is_402_and_nothing_is_generated(paid):
    paid.tokens.credit(paid.acct, -9, kind='adjust')     # balance 1
    r = _get(paid, '/download/step')
    body = r.get_json()
    assert r.status_code == 402 and body['code'] == 'NOT_ENOUGH_TOKENS'
    assert (body['needed'], body['balance']) == (3, 1)
    assert paid.balance() == 1


# ── add-in API and background jobs ────────────────────────────────────────

def test_addin_api_charges_tokens_instead_of_the_trial_limit(paid, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'register_trial_download',
                        lambda *a: pytest.fail('trial limit must not apply with tokens on'))
    r = paid.client.post('/api/download/stl', headers=paid.auth, json={
        'machine_id': 'm1',
        'params': {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8', 'belt_height': '10'}})
    assert r.status_code == 200, r.data[:200]
    assert r.headers['X-CCT-Tokens-Charged'] == '2' and paid.balance() == 8


def test_async_step_job_charges_once_it_runs(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')             # run the job in the request
    body = dict(DESIGN, design_id=paid.design_id)
    body.pop('p2_teeth'), body.pop('p2_bore')
    r = paid.client.post('/api/download/step-async', headers=paid.auth, json=body)
    assert r.status_code == 200, r.data[:200]
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'done'
    assert paid.balance() == 7


def test_async_step_job_failure_is_refunded(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = paid.client.post('/api/download/step-async', headers=paid.auth,
                         json={'family': 'BOGUS', 'pitch': '5M', 'teeth': '20', 'bore': '8'})
    status = paid.client.get(r.get_json()['status_url']).get_json()
    assert status['status'] == 'failed'
    assert paid.balance() == 10


def test_async_step_job_refused_up_front_when_unaffordable(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    paid.tokens.credit(paid.acct, -8, kind='adjust')     # balance 2
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
    assert (q['cost'], q['tier'], q['balance'], q['held_tier']) == (2, 'stl', 10, None)
    assert q['buy_url'] == 'https://shop.example/tokens'
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    assert mini.c.get(f'/download/stl?{qs}', headers=mini.auth).headers['X-CCT-Tokens-Charged'] == '2'
    q = mini.c.post('/api/tokens/quote', headers=mini.auth,
                    json={'path': '/download/stl', 'params': params}).get_json()
    assert (q['cost'], q['held_tier'], q['balance']) == (0, 'stl', 8)


def test_quote_and_design_need_sign_in_and_a_charged_route(mini):
    assert mini.c.post('/api/tokens/quote', json={'path': '/download/stl'}).status_code == 401
    assert mini.c.post('/api/design', json={'design': DESIGN}).status_code == 401
    r = mini.c.post('/api/tokens/quote', headers=mini.auth, json={'path': '/not/a/download', 'params': {}})
    assert r.status_code == 400
    assert mini.c.post('/api/design', headers=mini.auth, json={'design': {}}).status_code == 400
    assert mini.c.post('/api/design', headers=mini.auth, json={'design': 'x'}).status_code == 400


def test_402_carries_the_buy_link(mini):
    mini.tokens.credit(mini.acct, -9, kind='adjust')
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
    assert [(s['amount'], s['fmt']) for s in spends] == [(-3, 'step')]
    assert paid.balance() == 7


def test_bundle_of_drawings_costs_one(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = _bundle(paid, [{'path': '/download/svg', 'params': P}, {'path': '/download/dxf', 'params': P}])
    assert paid.client.get(r.get_json()['status_url']).get_json()['status'] == 'done'
    assert paid.balance() == 9


def test_bundle_refuses_files_from_another_design(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    r = _bundle(paid, [{'path': '/download/svg', 'params': dict(P, teeth='30')}])
    assert r.status_code == 400 and paid.balance() == 10


def test_bundle_refuses_non_downloads_and_unknown_designs(paid):
    assert _bundle(paid, [{'path': '/api/account', 'params': {}}]).status_code == 400
    assert _bundle(paid, []).status_code == 400
    assert _bundle(paid, [{'path': '/download/svg', 'params': P}], design_id='0' * 32).status_code == 400


def test_bundle_refused_up_front_when_unaffordable(paid, monkeypatch):
    monkeypatch.setenv('PULLEY_TESTING', '1')
    paid.tokens.credit(paid.acct, -8, kind='adjust')     # balance 2
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
    assert paid.balance() == 10


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
