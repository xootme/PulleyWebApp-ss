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
             'tokens_ui', 'designs_ui', 'tokens_off', 'admin_ui', 'bore_ui', 'dims_ui', 'screw_ui', 'undo_ui',
             'parity_ui', 'link_ui']
# Harnesses cut into one slice a lane, each slice some of the same designs (--first, --designs):
# roundtrip_ui's 20 took 21 min in one lane while the other lane finished the rest in 9 (11-06).
SLICED = {'roundtrip_ui': 20}
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


# A failed harness's whole output, kept (as Gear's tools/test_all.py does): a lane's temp folder
# goes with it, and the printed tail once cut a run's FAIL lines off (2026-10-31, bore_ui on msi).
# logs/ is git-ignored; the last KEEP_RUNS stay.
FAILED_RUNS = ROOT / 'logs' / 'harness_failures'
KEEP_RUNS = 50
SHOW_LINES = 200                    # a failure's output printed in full up to this many lines


# node's own report of a crash: a harness that dies on node's fast-fail leaves only its exit code
# (roundtrip_ui, 0xC0000409 after design 1005, 2026-11-02), so every harness runs with node's
# fatal-error and uncaught-exception reports on, each into a folder of its own; a failed run's
# report is named in its output and summarised (event, top frames), a passed run's folder goes.
NODE_REPORTS = FAILED_RUNS / 'node_reports'
REPORT_FLAGS = '--report-on-fatalerror --report-uncaught-exception'


def report_env(env, label):
    """(env with NODE_OPTIONS asking node for its crash report, the folder it goes in)."""
    folder = NODE_REPORTS / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{os.getpid()}-{threading.get_ident()}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return env, None
    opts = f"{env.get('NODE_OPTIONS', '')} {REPORT_FLAGS} --report-directory={folder}".strip()
    return {**env, 'NODE_OPTIONS': opts}, folder


def report_summary(folder):
    """The lines naming each report node wrote in `folder` (none: an empty list): the file, its
    event, and the top JS and native frames."""
    import json
    lines = []
    for f in sorted(folder.glob('*.json')) if folder else []:
        lines.append(f'node report: {f}')
        try:
            r = json.loads(f.read_text(encoding='utf-8', errors='replace'))
        except (OSError, ValueError):
            continue
        head, js = r.get('header', {}), r.get('javascriptStack', {})
        lines.append(f"  event: {head.get('event')} (trigger {head.get('trigger')})"
                     + (f"; {js.get('message')}" if js.get('message') else ''))
        lines += [f'  js: {s}' for s in (js.get('stack') or [])[:8]]
        lines += [f"  native: {s.get('symbol', s) if isinstance(s, dict) else s}" for s in (r.get('nativeStack') or [])[:8]]
    return lines


def drop_reports(folder):
    """A run's report folder, gone when it is empty; only the last KEEP_RUNS folders stay."""
    try:
        if folder and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
        for old in sorted(p for p in NODE_REPORTS.iterdir() if p.is_dir())[:-KEEP_RUNS]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


def keep_failure(label, code, out):
    try:
        FAILED_RUNS.mkdir(parents=True, exist_ok=True)
        f = FAILED_RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{os.getpid()}-{threading.get_ident()}.txt"
        f.write_text(f'{label}: exit {code}\n{out}', encoding='utf-8')
        for old in sorted(FAILED_RUNS.glob('*.txt'))[:-KEEP_RUNS]:
            old.unlink(missing_ok=True)
        return f
    except OSError:
        return None


def run(label, cmd, env, results):
    t0 = time.time()
    with _print:
        print(f'\n=== {label}: {" ".join(cmd)}', flush=True)
    env, reports = report_env(env, label)
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, text=True, encoding='utf-8', errors='replace',
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    timer = threading.Timer(TIMEOUT_S, stop, [proc])
    timer.start()
    out = proc.communicate()[0]
    timed_out = not timer.is_alive()
    timer.cancel()
    lines = out.splitlines()
    bad = [ln for ln in lines if ln.startswith('FAIL')]
    tail = next((ln.strip() for ln in reversed(lines) if ln.strip()), f'(no output, exit {proc.returncode})')
    passed = proc.returncode == 0 and bool(lines) and not bad and not timed_out
    secs = time.time() - t0
    if passed and reports:
        shutil.rmtree(reports, ignore_errors=True)
    else:
        lines += report_summary(reports)
        out = '\n'.join(lines)
    drop_reports(reports)
    kept = None if passed else keep_failure(label, 'timeout' if timed_out else proc.returncode, out)
    with _print:
        if passed:
            print(f'\n=== {label} done ({secs:.0f} s)\n' + '\n'.join(lines[-40:]), flush=True)
        else:   # a failure's output in full (to SHOW_LINES), so a lane on msi brings it back too
            print(f'\n=== {label} FAILED ({secs:.0f} s), exit {proc.returncode}; its output, {len(lines)} lines'
                  + (f' (the last {SHOW_LINES})' if len(lines) > SHOW_LINES else '')
                  + (f'; kept in {kept}' if kept else '') + ':\n'
                  + '\n'.join([ln for ln in lines[:-SHOW_LINES] if ln.startswith('FAIL')] + lines[-SHOW_LINES:]),
                  flush=True)
    note = f'stopped after {TIMEOUT_S // 60} min' if timed_out else tail[:110]
    results.append((label, passed, f'{note} ({secs:.0f} s)'))


def slices(name, n):
    """(label, extra args) for each run of harness `name` across `n` lanes: a SLICED harness's
    designs in up to n contiguous slices, any other harness whole."""
    total = SLICED.get(name)
    if not total or n < 2:
        return [(name, [])]
    k = min(n, total)
    cuts = [total * i // k for i in range(k + 1)]
    # no '/' in a label: it names the run's report folder and kept-failure file
    return [(f'{name} {i + 1} of {k}', ['--first', str(cuts[i]), '--designs', str(cuts[i + 1] - cuts[i])])
            for i in range(k)]


def harness(name, base, folder: Path, env, results, label=None, args=()):
    """One harness (or `label`, a slice of it with `args`); tokens_ui, designs_ui and admin_ui on a
    server of their own in a fresh folder."""
    if name == 'tokens_off':
        return run(name, ['node', 'tests/browser/tokens_ui.js', base, str(folder), PY, 'off'], env, results)
    if name not in ('tokens_ui', 'designs_ui', 'admin_ui'):
        return run(label or name, ['node', f'tests/browser/{name}.js', base, *args], env, results)
    own = Path(tempfile.mkdtemp(prefix=f'{name}-', dir=folder))
    extra = {'TOKENS_ENABLED': '1'}
    if name in ('tokens_ui', 'designs_ui'):
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
    n = max(1, n or lanes())
    runs = [(h, label, args) for h in names for label, args in slices(h, n)]
    todo = queue.Queue()
    for r in runs:
        todo.put(r)
    n = min(n, len(runs))
    results = []
    print(f'harnesses: {len(names)}, {n} at once'
          + (f' ({len(runs)} runs, {len(runs) - len(names)} more from slices)' if len(runs) > len(names) else ''),
          flush=True)
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
                    h, label, args = todo.get_nowait()
                except queue.Empty:
                    return
                harness(h, base, folder, env, results, label, args)
        finally:
            stop(server)

    threads = [threading.Thread(target=lane, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    while not todo.empty():                       # every lane's server failed
        results.append((todo.get()[1], False, 'not run: no dev server answered'))
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
    for label, passed, note in sorted(results, key=lambda r: (order.get(r[0].split()[0], 99), r[0])):
        print(f'{"PASS" if passed else "FAIL"}  {label:20s} {note}')
    bad = [r for r in results if not r[1]]
    print(f'{len(results) - len(bad)} of {len(results)} harnesses passed in {(time.time() - t0) / 60:.1f} min', flush=True)
    return 1 if bad or len(results) < len(names) else 0


if __name__ == '__main__':
    sys.exit(main())
