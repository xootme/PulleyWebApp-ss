"""harnesses.py — every browser harness (tests/browser/*.js), side by side, for the nightly run
(tools/nightly_test.py) and by hand. The owner, 2026-10-25: the night runs them all, and one
that fails fails the night (dims_ui's regression sat live from 10-08 to 10-23 because only
pytest ran).

    python C:\\Users\\cmyer\\Documents\\CCT_Controller\\bin\\cct.py test --from PulleyWebApp --label harnesses
        -- C:\\Users\\cmyer\\Documents\\PulleyWebApp-ss\\.venv312\\Scripts\\python.exe
        C:\\Users\\cmyer\\Documents\\PulleyWebApp-ss\\tools\\harnesses.py [--only NAME] [--lanes N]

Lanes (CCT_TEST_CPUS // 2, 1 to 4; Sprocket's tools/test_all.py) each start a dev server of
their own on a free port, with its own log folder and temp folder, run with the interpreter
that runs this (the app's packages are in .venv312 too, the only venv a test host builds),
and take harnesses off one queue, longest first. Each harness's Chrome has its own profile
and a debug port it picks itself. tokens_ui (tokens on, a fresh account store) and admin_ui
(a seeded database) get a server of their own. A harness passes when it exits 0, prints a
result and no FAIL line; one that runs past TIMEOUT_S is stopped and fails. The summary is
the last lines; exits 0 only when every harness passed.
"""
import argparse
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'tools'))
import cpu_cap  # noqa: E402
import nightly_test  # noqa: E402

# Longest first, so the lanes finish together. 'tokens_off' is tokens_ui.js with tokens off.
HARNESSES = ['roundtrip_ui', 'download_design_ui', 'turn_ui', 'help_ui', 'busy_ui', 'import_ui',
             'tokens_ui', 'tokens_off', 'admin_ui', 'bore_ui', 'dims_ui', 'screw_ui', 'undo_ui', 'parity_ui']
TIMEOUT_S = 30 * 60
# Nothing a harness starts may reach a real service or account: whatever the shell has is dropped.
SCRUB = ('RESEND', 'STRIPE', 'PAYPAL', 'SENDGRID', 'DATABASE_URL', 'GOOGLE_CLIENT', 'BACKUP_', 'ADMIN_EMAILS',
         'TOKENS_', 'PROVISION_SECRET', 'PULLEY_LICENCE')
_print = threading.Lock()


def environment():
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in SCRUB)}
    env.update(PYTHONIOENCODING='utf-8', QUEUE_DISABLED='1', PYTHON=PY)   # PYTHON: download_design_ui
    if not env.get('SMALL_STEP_BIN') and Path(nightly_test.SMALL_STEP).is_file():   # what the day runs use
        env['SMALL_STEP_BIN'] = nightly_test.SMALL_STEP
    return env


def lanes():
    """One harness per two CPUs of the turn, 1 to 4 (a headless Chrome drawing WebGL in software
    plus a share of its server each)."""
    return max(1, min(4, cpu_cap.test_cpus() // 2))


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def start_server(folder: Path, env, extra=None):
    """A dev server on a free port logging into `folder`. Returns (process, base URL); the base
    is None if it never answered."""
    port = free_port()
    log = open(folder / 'server.log', 'w', encoding='utf-8')
    server = subprocess.Popen([PY, 'app.py', '--port', str(port), '--no-debug'], cwd=ROOT,
                              stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              env={**env, 'PULLEY_LOG_DIR': str(folder), **(extra or {})})
    server.log = log
    base = f'http://127.0.0.1:{port}'
    for _ in range(240):
        try:
            urllib.request.urlopen(base + '/', timeout=5)
            return server, base
        except OSError:
            if server.poll() is not None:
                break
            time.sleep(0.5)
    return server, None


def stop(proc):
    """The process and all it started (the venv's python.exe is a launcher; node starts Chrome)."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
    if getattr(proc, 'log', None):
        proc.log.close()


def run(label, cmd, env, results):
    t0 = time.time()
    with _print:
        print(f'\n=== {label}: {" ".join(cmd)}', flush=True)
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, text=True, encoding='utf-8', errors='replace',
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    timer = threading.Timer(TIMEOUT_S, stop, [proc])
    timer.start()
    out = proc.communicate()[0]
    timed_out = not timer.is_alive()
    timer.cancel()
    lines = out.splitlines()
    bad = [ln for ln in lines if ln.startswith('FAIL')]
    tail = next((ln.strip() for ln in reversed(lines) if ln.strip()), '(no output)')
    passed = proc.returncode == 0 and bool(lines) and not bad and not timed_out
    secs = time.time() - t0
    with _print:
        print(f'\n=== {label} done ({secs:.0f} s)\n' + '\n'.join(lines[-40:]), flush=True)
    note = f'stopped after {TIMEOUT_S // 60} min' if timed_out else tail[:110]
    results.append((label, passed, f'{note} ({secs:.0f} s)'))


def harness(name, base, folder: Path, env, results):
    """One harness; tokens_ui and admin_ui on a server of their own in a fresh folder."""
    if name == 'tokens_off':
        return run(name, ['node', 'tests/browser/tokens_ui.js', base, str(folder), PY, 'off'], env, results)
    if name not in ('tokens_ui', 'admin_ui'):
        return run(name, ['node', f'tests/browser/{name}.js', base], env, results)
    own = Path(tempfile.mkdtemp(prefix=f'{name}-', dir=folder))
    extra = {'TOKENS_ENABLED': '1'}
    if name == 'tokens_ui':
        extra.update(GOOGLE_CLIENT_ID='test', GOOGLE_CLIENT_SECRET='test')
        args = [str(own), PY]
    else:
        db = own / 'cct.sqlite3'
        seed = subprocess.run([PY, 'tests/browser/admin_seed.py', str(db)], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        if seed.returncode:
            results.append((name, False, f'admin_seed.py failed: {(seed.stderr or seed.stdout).strip()[-200:]}'))
            return
        (own / 'cookies.json').write_text(seed.stdout, encoding='utf-8')
        extra.update(DATABASE_URL=str(db), ADMIN_EMAILS='admin@example.com')
        args = [str(own / 'cookies.json')]
    server, own_base = start_server(own, env, extra)
    try:
        if own_base is None:
            results.append((name, False, 'its dev server never answered'))
            return
        run(name, ['node', f'tests/browser/{name}.js', own_base, *args], env, results)
    finally:
        stop(server)


def run_all(names, n=None):
    """Every harness in `names`, `n` (else lanes()) at once. Returns [(name, passed, note)]."""
    todo = queue.Queue()
    for h in names:
        todo.put(h)
    n = max(1, min(n or lanes(), len(names)))
    results = []
    print(f'harnesses: {len(names)}, {n} at once', flush=True)
    top = Path(tempfile.mkdtemp(prefix='pulley-harnesses-'))

    def lane(i):
        folder = top / f'lane{i}'
        (folder / 'tmp').mkdir(parents=True)
        # Chrome profiles and the server's temp files land here, removed afterwards
        env = {**environment(), 'TMP': str(folder / 'tmp'), 'TEMP': str(folder / 'tmp'), 'TMPDIR': str(folder / 'tmp')}
        server, base = start_server(folder, env)
        try:
            if base is None:
                results.append((f'server {i}', False, 'its dev server never answered'))
                return
            while True:
                try:
                    h = todo.get_nowait()
                except queue.Empty:
                    return
                harness(h, base, folder, env, results)
        finally:
            stop(server)

    threads = [threading.Thread(target=lane, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    while not todo.empty():                       # every lane's server failed
        results.append((todo.get(), False, 'not run: no dev server answered'))
    shutil.rmtree(top, ignore_errors=True)
    return results


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', action='append', help='this harness only (repeatable)')
    ap.add_argument('--lanes', type=int, help='harnesses at once (default: CCT_TEST_CPUS // 2, 1 to 4)')
    a = ap.parse_args(argv)
    unknown = [h for h in a.only or [] if h not in HARNESSES]
    if unknown:
        ap.error(f'no harness {", ".join(unknown)} (have: {", ".join(HARNESSES)})')
    names = [h for h in HARNESSES if not a.only or h in a.only]
    t0 = time.time()
    results = run_all(names, a.lanes)
    print('\n=== HARNESSES')
    order = {h: i for i, h in enumerate(HARNESSES)}
    for label, passed, note in sorted(results, key=lambda r: order.get(r[0], 99)):
        print(f'{"PASS" if passed else "FAIL"}  {label:20s} {note}')
    bad = [r for r in results if not r[1]]
    print(f'{len(results) - len(bad)} of {len(results)} harnesses passed in {(time.time() - t0) / 60:.1f} min', flush=True)
    return 1 if bad or len(results) < len(names) else 0


if __name__ == '__main__':
    sys.exit(main())
