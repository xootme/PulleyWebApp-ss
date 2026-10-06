"""test_download_design.py — every file a design's downloads ask for belongs to
the design that pays for them (bug 30ae49ecd8, 2026-10-02: a splined pulley's
shaft and washer were refused, "/download/spline-stl isn't part of this
design", because their `part` wasn't a route-only key).

Two doors lead to the same check (charging.design_matches):
- the page's Download window: the files it builds for a matrix of designs,
  recorded by tests/browser/download_design_ui.js --write in
  tests/data/download_window_files.json, checked here with the app's rule;
- the agent API: /api/v1/files registers the union of its files' parameters
  as the design and hands out links — each must belong to that design.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from charging import DesignStore, charges, design_matches
from cct_common.accounts import AccountStore
from cct_common.tokens import TokenStore
from accounts_setup import AccountsState
from download_design_check import problems

CASES = Path(__file__).parent / 'data' / 'download_window_files.json'


# ── the Download window (recorded from the real page) ────────────────────────
def test_every_window_file_belongs_to_its_design():
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    assert problems(cases) == []


def test_the_recorded_matrix_still_covers_the_risky_parts():
    """If the recording is regenerated, it must still hold the files that
    carry extra keys: spline parts (part=), flanges (flange_which=), rim
    layers, pulley 2 (pulley=2), belt and drive, STEP."""
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    paths = {f['path'] for c in cases for f in c['files']}
    for p in ('/download/spline-stl', '/download/spline-dxf', '/download/flange-stl', '/download/svg-rim',
              '/download/assembly-step', '/download/belt-stl', '/download/all-dxf'):
        assert p in paths, p
    params = [f['params'] for c in cases for f in c['files']]
    assert any(q.get('part') == 'washer' for q in params) and any(q.get('pulley') == '2' for q in params)
    assert len(cases) >= 12


def test_the_checker_catches_the_original_bug():
    """Negative control: without `part` as a route-only key the shaft file
    is refused — the checker must say so."""
    case = {'name': 'x', 'design': {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'bore_shape': 'spline'},
            'files': [{'path': '/download/spline-stl',
                       'params': {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'bore_shape': 'spline',
                                  'part': 'shaft', 'stray': '1'}}]}
    found = problems([case])
    assert len(found) == 1 and 'stray' in found[0] and 'part' not in found[0]


# ── the agent API (/api/v1/files) ────────────────────────────────────────────
@pytest.fixture
def signed_in(client, tmp_path, monkeypatch):
    from app import app
    tokens = TokenStore(str(tmp_path / 'accounts.sqlite3'))
    accounts = AccountStore(tokens, signup_grant=50)
    state = AccountsState(enabled=True, healthy=True, tokens=tokens, accounts=accounts)
    monkeypatch.setattr(charges, 'state', state)
    monkeypatch.setattr(charges, 'designs', DesignStore(tokens.path))
    monkeypatch.setitem(app.extensions, 'cct_accounts', {'accounts': accounts})
    acct = accounts.sign_in('email', 'a@example.com', 'a@example.com', email_verified=True)
    auth = {'Authorization': f'Bearer {accounts.create_session(acct, kind="device")}'}
    return SimpleNamespace(client=client, auth=auth)


SPLINE = {"bore_shape": "spline", "spline_type": "straight", "spline_n": 6, "spline_minor": 23,
          "spline_major": 26, "spline_width": 6, "spline_ring_top": True, "spline_washer": True}
AGENT_DESIGNS = {
    'one pulley': {"family": "HTD", "pitch": "5M", "teeth": 24, "bore": 8},
    'drive': {"family": "HTD", "pitch": "5M", "teeth": 24, "bore": 8, "dual": True, "p2_teeth": 36,
              "p2_bore": 10, "center_distance": 160},
    'splines on both pulleys': {"family": "HTD", "pitch": "8M", "teeth": 24, "belt_height": 10, **SPLINE,
                                "dual": True, "p2_teeth": 36, "center_distance": 260,
                                **{f"p2_{k}": v for k, v in SPLINE.items()}},
    'printed flanges, separate top': {"family": "HTD", "pitch": "5M", "teeth": 40, "bore": 8,
                                      "flange_enabled": True, "flange_3dprint": True, "flange_top_separate": True},
    'metal flanges on spokes': {"family": "HTD", "pitch": "8M", "teeth": 48, "bore": 12, "spokes_enabled": True,
                                "spokes_rim_depth": 5, "spokes_hub_od": 22, "flange_enabled": True,
                                "flange_3dprint": False},
}


@pytest.mark.parametrize('name', list(AGENT_DESIGNS))
def test_every_agent_file_belongs_to_its_design(signed_in, name):
    r = signed_in.client.post('/api/v1/files', headers=signed_in.auth, json={'params': AGENT_DESIGNS[name]})
    assert r.status_code == 200, r.data[:300]
    body = r.get_json()
    design = charges.designs.get(body['design_id'])
    assert design is not None and body['files']
    from urllib.parse import parse_qsl, urlsplit
    refused = []
    for f in body['files']:
        params = {k: v for k, v in parse_qsl(urlsplit(f['url']).query, keep_blank_values=True)}
        if not design_matches(params, design):
            bad = sorted(k for k, v in params.items() if not design_matches({k: v}, design))
            refused.append((f['part'], f['format'], bad))
    assert refused == [], refused
