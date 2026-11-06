"""Pure STL (cct_common 0.57.0; the owner approved proposal (c), 2026-11-05): behind the
pure_stl business setting, off until the owner turns it on. Off, every STL is today's (the
settings trailer after the triangles). On, every STL this app makes is exactly 84 + 50·n bytes
with a "CCT pulleys <version>" header, and the Download window's zip carries <stem>.cct.json
beside each STL. Import (/api/import) reads all of it: the trailer for good, a .cct.json, a zip,
and a pure STL's header key, which opens the signed-in account's saved design; a pure STL
without a usable key says NO_KEY."""
import io
import time
import types
import zipfile

import pytest

import app as app_module
from app import APP_VERSION, IMPORT_NONE, app
from cct_common import account_routes
from cct_common.core import flatten_params, parse_meta
from cct_common.download_bundle import build_zip
from cct_common.imports import NO_KEY
from cct_common.saved_designs import SavedDesigns
from cct_common.settings import settings_for
from cct_common.stl import extract_stl, is_strict_stl, settings_sidecar, stl_header
from cct_common.tokens import TokenStore

TOOL = 'pulleys'
ONE = 'family=HTD&pitch=5M&teeth=20&bore=8&belt_height=10'
DUAL = ONE + '&p2_teeth=30&p2_bore=8&center_distance=120&dual=true'
PRINTED = ONE + '&flange_enabled=1&flange_3dprint=1&flange_rim_radius=2.5&flange_height=1.2&flange_angle=15'
SPLINED = ('family=HTD&pitch=5M&teeth=40&bore=23.0105&belt_height=12&bore_shape=spline&spline_type=straight'
           '&spline_n=6&spline_minor=23&spline_major=26&spline_width=6&spline_ring_top=1&spline_washer=1')
# Every route that makes an STL: (its id, path, query); the add-in's POST route on its own below
STL_ROUTES = [
    ('one pulley', '/download/stl', ONE),
    ('the belt', '/download/belt-stl', DUAL),
    ('a printed flange', '/download/flange-stl', PRINTED + '&flange_which=top'),
    ('the flange assembly', '/download/flange-assembly', PRINTED),
    ('a spline shaft', '/download/spline-stl', SPLINED + '&part=shaft'),
]
IDS = [r[0] for r in STL_ROUTES]


@pytest.fixture
def shop(tmp_path, monkeypatch):
    """The accounts database the routes find the business settings in (cct_common.stl),
    as Cloud Run's; `on(key)` sets one, `who["acct"]` is the signed-in account."""
    tokens = TokenStore(str(tmp_path / 'accounts.db'), app=TOOL, clock=time.time)
    acct = tokens.get_or_create_account('a@b.com', signup_grant=0)
    who = {'acct': None}
    monkeypatch.setitem(app.extensions, 'cct_accounts', {'accounts': types.SimpleNamespace(tokens=tokens)})
    monkeypatch.setattr(account_routes, 'current_account_id', lambda: who['acct'])
    settings = settings_for(tokens.path)
    return types.SimpleNamespace(tokens=tokens, acct=acct, who=who,
                                 on=lambda k, v=True: settings.set(k, v, by='test'))


def _addin_stl(client, monkeypatch):
    """The add-ins' STL (POST /api/download/stl), its weekly trial let through."""
    monkeypatch.setattr(app_module, 'register_trial_download', lambda *a: (True, 0, 2))
    r = client.post('/api/download/stl', json={'machine_id': 'm1', 'params': dict(
        family='HTD', pitch='5M', teeth='20', bore='8', belt_height='10')})
    assert r.status_code == 200, r.data[:300]
    return r.data


def _get(client, path, query):
    r = client.get(f'{path}?{query}')
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.data


@pytest.mark.parametrize('label, path, query', STL_ROUTES, ids=IDS)
def test_every_stl_route_is_strict_when_pure_stl_is_on(client, shop, label, path, query):
    """On: exactly 84 + 50·n bytes, the header naming this tool and version (never "solid"),
    no trailer; a bare .stl stays a bare .stl."""
    shop.on('pure_stl')
    data = _get(client, path, query)
    assert is_strict_stl(data)
    assert data[:80].startswith(f'CCT {TOOL} {APP_VERSION}'.encode()) and extract_stl(data) is None


def test_the_addins_stl_is_strict_when_pure_stl_is_on(client, shop, monkeypatch):
    shop.on('pure_stl')
    data = _addin_stl(client, monkeypatch)
    assert is_strict_stl(data) and extract_stl(data) is None


@pytest.mark.parametrize('label, path, query', STL_ROUTES, ids=IDS)
def test_every_stl_route_keeps_todays_file_while_pure_stl_is_off(client, shop, label, path, query):
    """Off (as today, the setting there but false): the trailer carries the settings."""
    data = _get(client, path, query)
    assert not is_strict_stl(data) and extract_stl(data)['tool'] == TOOL


def test_the_addins_stl_keeps_todays_file_while_pure_stl_is_off(client, shop, monkeypatch):
    data = _addin_stl(client, monkeypatch)
    assert flatten_params(extract_stl(data))['teeth'] == '20'


def test_the_download_windows_zip_puts_the_settings_beside_the_stl(shop):
    """The Download window's zip (cct_common's build_zip, each file from its route in-process)."""
    shop.on('pure_stl')
    zipped = build_zip(app, [('/download/stl', dict(x.split('=') for x in ONE.split('&')))])
    with zipfile.ZipFile(io.BytesIO(zipped)) as z:
        names = z.namelist()
        stl = next(n for n in names if n.endswith('.stl'))
        side = parse_meta(z.read(stl[:-4] + '.cct.json').decode())
        assert is_strict_stl(z.read(stl)) and flatten_params(side)['teeth'] == '20'


def _import(client, name, data):
    return client.post('/api/import', data={'file': (io.BytesIO(data), name)}, content_type='multipart/form-data')


def _mesh(client):
    """A strict STL's triangles, the header and count included (the 84 + 50·n bytes)."""
    data = _get(client, '/download/stl', ONE)
    return data[:84 + 50 * int.from_bytes(data[80:84], 'little')]


def test_import_reads_a_legacy_stl(client):
    """Every STL made before pure_stl carries the trailer, and imports for good."""
    r = _import(client, 'old.stl', _get(client, '/download/stl', ONE.replace('teeth=20', 'teeth=21')))
    assert r.status_code == 200 and r.get_json()['params']['teeth'] == '21'


@pytest.mark.parametrize('route, name', [('/download/step', 'p.step'), ('/download/dxf', 'p.dxf'),
                                         ('/download/svg', 'p.svg')], ids=['STEP', 'DXF', 'SVG'])
def test_import_reads_the_other_files_as_before(client, route, name):
    r = _import(client, name, _get(client, route, ONE.replace('teeth=20', 'teeth=22')))
    assert r.status_code == 200 and r.get_json()['params']['teeth'] == '22'


def test_import_opens_a_pure_stls_saved_design(client, shop):
    """A pure STL's header key, with saved_designs on: this account's saved design, its params
    as My designs' Open applies them."""
    shop.on('pure_stl')
    shop.on('saved_designs')
    shop.who['acct'] = shop.acct
    key = '0123456789abcdef0123456789abcdef'
    params = {'family': 'HTD', 'pitch': '5M', 'teeth': '23', 'bore': '6'}
    SavedDesigns(shop.tokens.path).keep(shop.acct, TOOL, key, params, name='mine')
    r = _import(client, 'mine.stl', stl_header(TOOL, APP_VERSION, key) + _mesh(client)[80:])
    assert r.status_code == 200 and r.get_json()['params'] == params


def test_import_turns_away_another_tools_saved_design(client, shop):
    shop.on('saved_designs')
    shop.who['acct'] = shop.acct
    key = 'f' * 32
    SavedDesigns(shop.tokens.path).keep(shop.acct, 'gears', key, {'teeth': '30', 'module': '1'}, name='gear')
    r = _import(client, 'gear.stl', stl_header('gears', '1.2', key) + _mesh(client)[80:])
    assert r.status_code == 400 and r.get_json()['error'] == IMPORT_NONE


@pytest.mark.parametrize('signed_in', [False, True], ids=['signed out', "another account's key"])
def test_import_of_a_pure_stl_without_a_usable_key_says_where_the_settings_are(client, shop, signed_in):
    shop.on('pure_stl')
    shop.on('saved_designs')
    shop.who['acct'] = shop.acct if signed_in else None
    for data in (stl_header(TOOL, APP_VERSION) + _mesh(client)[80:],                  # no key at all
                 stl_header(TOOL, APP_VERSION, 'e' * 32) + _mesh(client)[80:]):       # none of this account's
        r = _import(client, 'pure.stl', data)
        assert r.status_code == 400 and r.get_json()['error'] == NO_KEY


def test_import_reads_a_cct_json(client):
    name, data = settings_sidecar('p', {'family': 'HTD', 'pitch': '5M', 'teeth': '19'}, tool=TOOL,
                                  version=APP_VERSION)
    r = _import(client, name, data)
    assert r.status_code == 200 and r.get_json()['params']['teeth'] == '19'


@pytest.mark.parametrize('pure', [False, True], ids=['trailer', 'pure'])
def test_import_reads_a_zip(client, shop, pure):
    """The Download window's zip, as it came: its .cct.json (pure), else the STL inside."""
    if pure:
        shop.on('pure_stl')
    zipped = build_zip(app, [('/download/stl', dict(x.split('=') for x in ONE.replace('teeth=20', 'teeth=25')
                                                     .split('&')))])
    r = _import(client, 'pulley.zip', zipped)
    assert r.status_code == 200 and r.get_json()['params']['teeth'] == '25'


@pytest.mark.parametrize('name', ['design.dxf', 'f', 'pulley.txt'], ids=["another kind's name", 'no name', 'renamed'])
def test_import_reads_a_file_by_what_is_in_it_whatever_its_name(client, name):
    """cct_common 0.58.1: when the name's reader finds nothing, import_meta reads by content, as
    Import did before 0.57.0 (a renamed download, or one the browser gave no extension)."""
    for route in ('/download/stl', '/download/step', '/download/svg'):
        r = _import(client, name, _get(client, route, ONE.replace('teeth=20', 'teeth=24')))
        assert r.status_code == 200 and r.get_json()['params']['teeth'] == '24', (route, r.get_data()[:200])


def test_import_still_refuses_a_file_without_settings(client):
    r = _import(client, 'plain.stl', b'\0' * 80 + (0).to_bytes(4, 'little'))
    assert r.status_code == 400 and r.get_json()['error'] == IMPORT_NONE
