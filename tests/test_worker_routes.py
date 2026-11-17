"""site/worker-cloudrun.js, the cheapcadtools.com Worker: each tool's path goes to its own
Cloud Run service with the tool's prefix taken off, a bare slug gets a 301 to its trailing slash
(the tools use relative URLs), the MCP gateway and Pulley's root paths go to theirs, and the rest
to GreenGeeks. The Worker runs in node with fetch stubbed, so the table below is what it does."""
import json
import shutil
import subprocess

import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = 'https://{}-925396938485.us-central1.run.app'
PROBE = """
import worker from './worker.mjs';
const seen = [];
globalThis.fetch = async (target, init) => {
  seen.push({target: typeof target === 'string' ? target : target.url,
             edge: init && init.headers ? init.headers.get('X-CCT-Edge') : null});
  return new Response('ok');
};
const out = [];
for (const path of JSON.parse(process.argv[2])) {
  seen.length = 0;
  const r = await worker.fetch(new Request('https://cheapcadtools.com' + path), {EDGE_SECRET: 'edge'});
  out.push({path, status: r.status, location: r.headers.get('Location'), ...(seen[0] || {})});
}
console.log(JSON.stringify(out));
"""

ROUTES = [   # path -> (the service, the path it gets) or a 301's Location
    ('/tools/printed-bearings', 'https://cheapcadtools.com/tools/printed-bearings/'),
    ('/tools/printed-bearings/', ('printedbearing', '/')),
    ('/tools/printed-bearings/api/v1/describe?x=1', ('printedbearing', '/api/v1/describe?x=1')),
    ('/tools/printed-bearings/static/app.js', ('printedbearing', '/static/app.js')),
    ('/tools/gear-belt-chain', 'https://cheapcadtools.com/tools/gear-belt-chain/'),
    ('/tools/gear-belt-chain/', ('gearbeltchain', '/')),
    ('/tools/planetary-gears/api/health', ('planetarygear', '/api/health')),
    ('/tools/worm-gears/', ('wormgear', '/')),
    ('/tools/bevel-gears/', ('bevelgear', '/')),
    ('/tools/gears/', ('gear', '/')),
    ('/tools/sprocket/', ('sprocket', '/')),
    ('/tools/pulleys', ('pulley', '/')),
    ('/tools/pulleys/api/v1/describe', ('pulley', '/api/v1/describe')),
    ('/api/health', ('pulley', '/api/health')),
    ('/admin', ('pulley', '/admin')),
    ('/mcp', ('mcp', '/mcp')),
    ('/', None),                                   # None: the website (GreenGeeks), as sent
    ('/blog/', None),
    ('/tools/printed-bearing', None),              # a near miss of the slug
]


@pytest.fixture(scope='module')
def answers(tmp_path_factory):
    node = shutil.which('node')
    if not node:
        pytest.skip('no node')
    tmp = tmp_path_factory.mktemp('worker')
    shutil.copy(ROOT / 'site' / 'worker-cloudrun.js', tmp / 'worker.mjs')
    (tmp / 'probe.mjs').write_text(PROBE, encoding='utf-8')
    out = subprocess.run([node, str(tmp / 'probe.mjs'), json.dumps([p for p, _ in ROUTES])], cwd=tmp,
                         capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert out.returncode == 0, out.stderr
    return {a['path']: a for a in json.loads(out.stdout)}


@pytest.mark.parametrize('path,want', ROUTES, ids=[p for p, _ in ROUTES])
def test_each_path_goes_to_its_tool(answers, path, want):
    got = answers[path]
    if want is None:
        assert got['target'] == 'https://cheapcadtools.com' + path, got
        assert got['edge'] is None, 'the website never gets the edge secret'
    elif isinstance(want, str):
        assert (got['status'], got['location']) == (301, want), got
        assert 'target' not in got, 'a redirect fetches nothing'
    else:
        service, sub = want
        assert got['target'] == RUN.format(service) + sub, got
        assert got['edge'] == 'edge', 'the app trusts the forwarding headers only with the edge secret'
