"""
test_agent.py — the agent interface (ADR-019): /api/v1/describe, check, files
and quote (agent.py on cct_common.agent_api), and cct_common's MCP gateway
driving them — through an HTTP adapter onto the Flask test client, so the
gateway's own code (downloads, filenames, token headers) runs unchanged.
"""
import asyncio

import pytest

from cct_common.mcp_gateway import Gateway, GatewayError

BASE = 'http://pulleys.test'
DRIVE = {"family": "HTD", "pitch": "5M", "teeth": 24, "belt_height": 10, "dual": True, "p2_teeth": 36,
         "center_distance": 120, "bore_shape": "spline", "spline_type": "straight", "spline_n": 6,
         "spline_minor": 23, "spline_major": 26, "spline_width": 6, "spline_ring_top": True,
         "spline_washer": True, "hub_od": 25, "hub_height": 10,
         "p2_bore_shape": "spline", "p2_spline_type": "hex", "p2_spline_af": 12.7, "p2_spline_series": "inch"}
ROUND = {"family": "GT", "pitch": "2M", "teeth": 20, "bore": 5, "belt_height": 6}


def flask_http(client, headers=None):
    """The gateway's http(method, url, body, headers) onto the test client."""
    def http(method, url, body=None, hdrs=None):
        assert url.startswith(BASE), url
        r = client.open(url[len(BASE):], method=method, json=body, headers={**(headers or {}), **(hdrs or {})})
        return r.status_code, dict(r.headers), r.data
    return http


@pytest.fixture
def gw(client, tmp_path):
    return Gateway({'pulleys': BASE}, http=flask_http(client), token_path=tmp_path / 'token')


# ── the four routes ─────────────────────────────────────────────────────────

def test_describe(client):
    d = client.get('/api/v1/describe').get_json()
    assert d['app'] == 'pulleys' and d['api_version'] == 1 and d['units'] == 'mm'
    names = {p['name'] for p in d['params']}
    for key in ('family', 'pitch', 'teeth', 'bore', 'belt_height', 'dual', 'center_distance', 'bore_shape',
                'spline_type', 'spline_af', 'spline_ring_top', 'hub_od', 'hub_screw_size', 'flange_enabled',
                'spokes_enabled', 'flange_supports_enabled', 'flange_nubs_enabled'):
        assert key in names, key
    pitch = next(p for p in d['params'] if p['name'] == 'pitch')
    assert pitch['choices_by']['family']['Imperial'] == ['MXL', 'XL', 'L', 'H', 'XH', 'XXH']
    assert next(p for p in d['params'] if p['name'] == 'teeth')['per_part'] is True
    assert 'p2_' in d['prefixes'] and {p['id'] for p in d['parts']} >= {'pulley1', 'pulley2', 'belt', 'shaft1'}


def test_bad_params_are_all_listed(client):
    r = client.post('/api/v1/check', json={"params": {"teeth": 5.5, "pitch": "7M", "colour": "red",
                                                      "dual": "maybe", "p2_family": "HTD"}})
    assert r.status_code == 400
    d = r.get_json()
    assert d['code'] == 'BAD_PARAMS' and len(d['details']) == 5
    text = ' '.join(d['details'])
    for words in ("unknown parameter 'colour'", "whole number", "3M, 5M", "true or false",
                  "unknown parameter 'p2_family'"):          # family isn't per pulley
        assert words in text, words


def test_check_and_its_autofix_as_parameters(client):
    """The page's Auto-fix, keyed by parameters an agent can send back."""
    d = client.post('/api/v1/check', json={"params": DRIVE}).get_json()
    assert d['drive']['belt_teeth'] == 79 and d['spline1']['label'] == '6 × 23 × 26 (B 6)'
    assert d['spline2']['label'] == 'hex 1/2" (12.7 mm AF)'
    fix = d['fix']['params']
    assert fix['hub_od'] == 30.5 and (fix['spline_n'], fix['spline_minor'], fix['spline_major']) == (6, 16, 20)
    again = client.post('/api/v1/check', json={"params": dict(DRIVE, **fix)}).get_json()
    assert again['fix'] is None


def test_autofix_face_width_is_not_a_float_tenth_over(client):
    """An L drive: 14.3 mm minimum face for a 10 mm belt is 4.3 mm of extra
    face, as the page says, not the 4.4 that float rounding gave."""
    d = client.post('/api/v1/check', json={"params": {"family": "Imperial", "pitch": "L", "teeth": 20,
                                                      "dual": True, "p2_teeth": 30}}).get_json()
    assert d['pulleys'][0]['face_width_min'] == 14.3
    assert d['fix']['params']['clearance_height'] == 4.3


def test_files_list_the_design(client):
    d = client.post('/api/v1/files', json={"params": DRIVE}).get_json()
    got = {(f['part'], f['format']) for f in d['files']}
    assert ('drive', 'dxf') in got and ('washer1', 'stl') in got and ('shaft2', 'dxf') in got
    assert ('washer2', 'stl') not in got                       # pulley 2 has no ring
    one = client.post('/api/v1/files', json={"params": ROUND, "parts": ["pulley1"], "formats": ["stl"]}).get_json()
    assert [(f['part'], f['format']) for f in one['files']] == [('pulley1', 'stl')]
    r = client.post('/api/v1/files', json={"params": ROUND, "parts": ["washer1"]})
    assert r.status_code == 400 and r.get_json()['code'] == 'NO_FILES'
    r = client.post('/api/v1/files', json={"params": ROUND, "formats": ["obj"]})
    assert r.status_code == 400


def test_quote_without_tokens(client):
    assert client.post('/api/v1/quote', json={"params": ROUND}).get_json() == {"tokens": False, "cost": 0}


# ── the MCP gateway on top ──────────────────────────────────────────────────

def test_gateway_lists_describes_checks(gw):
    apps = gw.list_apps()
    assert apps[0]['app'] == 'pulleys' and 'pulley1' in apps[0]['parts']
    assert gw.describe('pulleys')['title'] == 'Timing Pulley Generator'
    assert gw.check('pulleys', ROUND)['pulleys'][0]['teeth'] == 20
    with pytest.raises(GatewayError, match="unknown parameter"):
        gw.check('pulleys', {"colour": "red"})
    with pytest.raises(GatewayError, match="unknown app"):
        gw.describe('gears')


def test_gateway_export_writes_the_files(gw, tmp_path):
    out = gw.export('pulleys', dict(DRIVE, spline_ring_top=False, spline_washer=False),
                    formats=['stl', 'dxf'], parts=['pulley1', 'shaft1', 'drive'], out_dir=str(tmp_path / 'out'))
    got = {(f['part'], f['format']) for f in out['files']}
    assert got == {('pulley1', 'stl'), ('pulley1', 'dxf'), ('shaft1', 'stl'), ('shaft1', 'dxf'), ('drive', 'dxf')}
    for f in out['files']:
        data = open(f['path'], 'rb').read()
        assert len(data) == f['bytes'] > 100
    assert out['problems'] == []


def test_flange_supports_and_nubs_reach_the_stl(gw, tmp_path):
    """Print supports under a top flange printed in place, and gluing nubs on
    a separate one: listed by describe, and each changes the pulley's STL."""
    def stl(name, **extra):
        p = dict(ROUND, flange_enabled=True, flange_3dprint=True, **extra)
        out = gw.export('pulleys', p, formats=['stl'], parts=['pulley1'], out_dir=str(tmp_path / name))
        assert out['problems'] == []
        return open(out['files'][0]['path'], 'rb').read()
    in_place = stl('a', flange_top_separate=False)
    supported = stl('b', flange_top_separate=False, flange_supports_enabled=True,
                    flange_support_nozzle_dia=0.4)
    separate = stl('c', flange_top_separate=True)
    nubbed = stl('d', flange_top_separate=True, flange_nubs_enabled=True, flange_nub_count=6)
    assert supported != in_place and nubbed != separate


# small_step refuses this one by name (its "gap 65"): the ring's counterbore
# would reach the spoke voids. (Found by fuzz_pulley.py, seed 2026093014.)
GAP65 = {"family": "Imperial", "pitch": "H", "teeth": 29, "bore": 25.9, "belt_height": 24.3,
         "clearance_height": 0.19, "backlash_preset": "NONE", "bore_shape": "spline",
         "spline_type": "straight", "spline_n": 8, "spline_minor": 36, "spline_major": 40,
         "spline_width": 7, "spline_ring_top": True, "spline_ring_bottom": False, "spline_washer": True,
         "spokes_enabled": True, "spokes_hub_od": 28.4, "spokes_rim_depth": 4.1, "spokes_width": 7.0,
         "spokes_fillet_tip": 0.8, "spokes_fillet_base": 2.0, "spokes_count": 4, "spokes_height": 0.5}


def test_gateway_reports_a_file_the_app_refuses(gw, tmp_path, monkeypatch):
    """A STEP small_step refuses: the other files still come, and the refusal
    is reported in its own words, not dropped."""
    from exporters.step_worker_ss import SMALL_STEP_MIN_VERSION   # noqa: F401  (the ss build)
    monkeypatch.setenv('PULLEY_STEP_BACKEND', '')
    out = gw.export('pulleys', GAP65, formats=['step', 'stl'], parts=['pulley1'], out_dir=str(tmp_path))
    assert [(f['part'], f['format']) for f in out['files']] == [('pulley1', 'stl')]
    assert out['problems'] and 'spoke voids' in out['problems'][0], out['problems']


def _stub_http(method, url, body=None, hdrs=None):
    """A canned app for the MCP tests: the SDK runs tools on another thread,
    where the Flask test client can't be used (a real gateway talks HTTP)."""
    import json
    path = url[len(BASE):]
    if path == '/api/v1/describe':
        return 200, {}, json.dumps({'title': 'Timing Pulley Generator', 'summary': 's',
                                    'parts': [{'id': 'pulley1'}]}).encode()
    if path == '/api/v1/check':
        return 200, {}, json.dumps({'pulleys': [{'teeth': body['params']['teeth']}]}).encode()
    if path == '/api/account':
        if not hdrs.get('Authorization'):
            return 401, {}, b'{"error": "sign in required", "code": "SIGN_IN_REQUIRED"}'
        return 200, {}, json.dumps({'email': 'a@b.c', 'balance': 17}).encode()
    if path == '/api/account/device/start':
        return 200, {}, json.dumps({'device_code': 'dc', 'user_code': 'BCDF-GHJK',
                                    'verify_url_complete': BASE + '/account/device?code=BCDF-GHJK'}).encode()
    if path == '/api/account/device/poll':
        return 200, {}, json.dumps({'status': 'approved', 'token': 'tok123'}).encode()
    return 404, {}, b'{"error": "not found"}'


def test_gateway_sign_in_and_balance(tmp_path):
    gw = Gateway({'pulleys': BASE}, token=None, http=_stub_http, token_path=tmp_path / 't' / 'token')
    with pytest.raises(GatewayError, match="not signed in"):
        gw.balance('pulleys')
    with pytest.raises(GatewayError, match="call sign_in first"):
        gw.sign_in_finish('pulleys')
    start = gw.sign_in('pulleys')
    assert start['user_code'] == 'BCDF-GHJK' and 'code=BCDF-GHJK' in start['verify_url']
    assert gw.sign_in_finish('pulleys')['status'] == 'signed in'
    assert (tmp_path / 't' / 'token').read_text() == 'tok123'
    assert gw.balance('pulleys') == {'email': 'a@b.c', 'balance': 17}
    # a new gateway finds the saved token
    assert Gateway({'pulleys': BASE}, http=_stub_http, token_path=tmp_path / 't' / 'token').token == 'tok123'


def test_mcp_server_tools(tmp_path):
    pytest.importorskip('mcp')
    from cct_common.mcp_gateway import build_server
    gw = Gateway({'pulleys': BASE}, http=_stub_http, token_path=tmp_path / 'token')
    server = build_server(gw)
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert tools == {'list_apps', 'describe', 'check', 'quote', 'export', 'sign_in', 'sign_in_finish', 'balance'}
    result = asyncio.run(server.call_tool('check', {'app': 'pulleys', 'params': ROUND}))
    assert 'pulleys' in str(result)
    bad = asyncio.run(server.call_tool('describe', {'app': 'nope'}))
    assert 'unknown app' in str(bad)


# ── paying: one design, one payment (tokens on) ─────────────────────────────

def test_agent_pays_once_for_the_design(paid, tmp_path):
    """files registers the design (every file's parameters) for the signed-in
    device token; the first STL pays the STL tier, which covers the DXF."""
    from app import app
    gw = Gateway({'pulleys': BASE}, token=paid.auth['Authorization'][7:],
                 http=flask_http(paid.client), token_path=tmp_path / 'token')
    q = gw.quote('pulleys', ROUND, formats=['stl', 'dxf'])
    assert q['tokens'] is True and q['cost'] == 3 and q['fmt'] == 'stl'
    before = paid.balance()
    out = gw.export('pulleys', ROUND, formats=['stl', 'dxf'], parts=['pulley1'], out_dir=str(tmp_path / 'o'))
    assert {f['format'] for f in out['files']} == {'stl', 'dxf'} and out['design_id']
    assert before - paid.balance() == 3 == out['tokens_charged']
    again = gw.export('pulleys', ROUND, formats=['dxf'], parts=['pulley1'], out_dir=str(tmp_path / 'o2'))
    assert again['tokens_charged'] == 0 and paid.balance() == before - 3


def test_signed_out_agent_is_told_to_sign_in(paid, tmp_path):
    gw = Gateway({'pulleys': BASE}, token=None, http=flask_http(paid.client), token_path=tmp_path / 'none')
    with pytest.raises(GatewayError, match="SIGN_IN_REQUIRED"):
        gw.quote('pulleys', ROUND, formats=['stl'])
    with pytest.raises(GatewayError, match="no files downloaded"):
        gw.export('pulleys', ROUND, formats=['stl'], parts=['pulley1'], out_dir=str(tmp_path))


from test_charging import paid  # noqa: E402,F401  (the fixture)
