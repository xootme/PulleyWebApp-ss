"""
tests/run_tests.py — PulleyWebApp test runner with live SSE dashboard.

Usage:
    python tests/run_tests.py [--flask-port 5099] [--dash-port 5098]
                               [--skip-slow] [--no-browser] [--exit-when-done]

Thin project wrapper around cct_common.test_dashboard's generic engine
(dashboard server, pytest streaming, --exit-when-done, failed/skipped-name
logging). Everything PulleyWebApp-ss-specific lives here: which test files
map to which dashboard group, how to start app.py, the async job-queue
test suite (run inline against the live server, since it can't run inside
the pytest subprocess), nightly-random-config repro links, and the
connected-CAD-addin warning banner.
"""
import importlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # cct_common is vendored at PulleyWebApp-ss/cct_common/
from cct_common.test_dashboard import InlineBatch, RunnerConfig, run  # noqa: E402

VENV_PY = ROOT / '.venv314' / 'Scripts' / 'python.exe'

PYTEST_GROUPS = {
    'test_api':             'API Endpoints',
    'test_exporters':       'Exporters',
    'test_belt':            'Belt Geometry',
    'test_invalid_inputs':  'Input Validation',
    'test_priority':        'Priority Logic',
    'test_spokes':          'Spoke Geometry',
    'test_3d':              '3D Generation',
    'test_flange_geometry': 'Flange Geometry',
    'test_flange':          'Flange Export',
    'test_benchmarks':      'Benchmarks',
    'test_repro':                  'Regression',
    'test_dual_pulley_bore_spoke': 'Regression',
    'test_nub_socket_merge':       'STEP Geometry',
    'test_keyway_screw':           'STEP Geometry',
    'test_addin_helpers':          'Addin Helpers',
    'test_downloads':              'Download Routes',
    'test_nightly_random':  'Nightly Random',
}

SLOW_TESTS = {
    'test_idle_timeout_drops_active_session',
    'test_stale_queued_session_removed',
    'test_heartbeat_prevents_idle_timeout',
    'test_state_persistence_across_restart',
}

_ADDIN_CONNECTED_KEYS = [
    ('fusion_connected',     'Fusion 360'),
    ('solidworks_connected', 'SolidWorks'),
    ('freecad_connected',    'FreeCAD'),
]


def _server_cmd(port: int) -> list:
    return [str(VENV_PY if VENV_PY.exists() else sys.executable),
            'app.py', '--port', str(port), '--no-debug']


def _connected_addins_warning() -> list:
    """Warn on the dashboard when a connected CAD addin would make app.py
    mirror downloads to its watch folder (a 204) instead of the real 200
    the tests expect — PULLEY_TESTING forces the real download either way,
    so tests still pass, but that means the addin-mirror path itself isn't
    being exercised this run."""
    cfg_path = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')),
                            'CheapCADTools', 'config.json')
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except Exception:
        return []
    connected = [name for key, name in _ADDIN_CONNECTED_KEYS if cfg.get(key)]
    if not connected:
        return []
    return [f'{", ".join(connected)} addin connected — download tests are bypassing the '
            f'addin mirror (forced browser download via PULLEY_TESTING); the real 204 '
            f'mirror-to-watch-folder path is NOT exercised this run. Close the addin to test it.']


def _pre_generate_nightly_configs():
    """Generate nightly random configs in THIS process (not inside the
    pytest subprocess) and save to logs/nightly_random/ so the pytest
    fixture just loads the file. No-op unless PULLEY_NIGHTLY=1."""
    if os.environ.get('PULLEY_NIGHTLY') != '1':
        return
    import random as _random
    sys.path.insert(0, str(ROOT))
    from tests.test_nightly_random import _make_config, _save_configs
    seed = int(datetime.now().strftime('%Y%m%d%H'))
    r = _random.Random(seed)
    run_id = datetime.now().strftime('%H%M%S')
    print(f'Nightly   : generating 5 random configs (seed={seed})…')
    try:
        configs = [_make_config(r, i) for i in range(5)]
        path = _save_configs(run_id, configs)
        print(f'Nightly   : configs saved -> {path}')
    except Exception as e:
        print(f'Nightly   : config generation failed: {e}')


def _nightly_repro_configs() -> list:
    if os.environ.get('PULLEY_NIGHTLY') != '1':
        return []
    log_dir = ROOT / 'logs' / 'nightly_random'
    files = sorted(log_dir.glob('*.json')) if log_dir.exists() else []
    if not files:
        return []
    try:
        data = json.loads(files[-1].read_text(encoding='utf-8'))
        return data.get('configs', [])
    except Exception:
        return []


def _queue_test_batches(base_url: str) -> list:
    """The async job/session queue system needs a LIVE Flask server, so it
    can't run inside the pytest subprocess the way every other test does --
    each test method is wrapped in a reset-before / reset-after against the
    real running server, mirroring the original run_queue_test()."""
    import requests as _req

    os.environ['PULLEY_TEST_URL'] = base_url
    import tests.test_queue_pytest as tmod
    tmod.BASE_URL = base_url
    importlib.reload(tmod)
    tmod.BASE_URL = base_url

    def _reset():
        try:
            _req.post(f'{tmod.BASE_URL}/api/test/reset', timeout=5)
        except Exception:
            pass

    def _make_run_fn(cls, method_name):
        def _run():
            _reset()
            try:
                getattr(cls(), method_name)()
            finally:
                _reset()
        return _run

    groups = [
        ('Queue System — Functional', tmod.TestSessionBasics),
        ('Queue System — Timeouts',   tmod.TestSessionTimeouts),
        ('Queue System — Stress',     tmod.TestStress),
        ('Trial Downloads',           tmod.TestTrialDownloads),
    ]
    batches = []
    for group_name, cls in groups:
        tests = []
        for method in sorted(m for m in dir(cls) if m.startswith('test_')):
            tests.append((method, _make_run_fn(cls, method), method in SLOW_TESTS))
        batches.append(InlineBatch(group=group_name, tests=tests))
    return batches


def main():
    cfg = RunnerConfig(
        title='PulleyWebApp — Test Dashboard',
        root=ROOT,
        venv_py=VENV_PY,
        pytest_groups=PYTEST_GROUPS,
        always_ignore=['--ignore=tests/test_queue_pytest.py'],
        optional_test_files=[('PULLEY_NIGHTLY', 'tests/test_nightly_random.py')],
        slow_tests=SLOW_TESTS,
        server_cmd=_server_cmd,
        server_env={'PULLEY_TESTING': '1'},
        inline_batches=_queue_test_batches,
        pre_discovery_hook=_pre_generate_nightly_configs,
        warnings_fn=_connected_addins_warning,
        repro_group='Nightly Random',
        repro_configs_fn=_nightly_repro_configs,
        flask_port=5099,
        dash_port=5098,
    )
    sys.exit(run(cfg))


if __name__ == '__main__':
    main()
