"""The app under gunicorn as it runs live (gunicorn.conf.py: preload_app), with workers
forked over and over (recycled every few requests) while a master thread holds the app's
_request_count_lock half the time: its metrics sampler holds it a moment each minute, and
this makes the moment long enough to land a fork in. Every request must answer.

Linux only (WSL), from a Linux copy of the repo, with a venv that has gunicorn:
  python tools/fork_probe.py [--requests 120] [--port 5291]
Exit 0 when every request answered. Before cct_common.forksafe in app.py (2026-10-28) a
worker forked inside the hold hung on every request until WORKER TIMEOUT."""
import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WSGI = '''import threading, time
import app as _pulley
app = _pulley.app
def _hold():
    while True:
        with _pulley._request_count_lock:
            time.sleep(0.02)
        time.sleep(0.02)
threading.Thread(target=_hold, daemon=True).start()
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--requests', type=int, default=120)
    ap.add_argument('--port', type=int, default=5291)
    a = ap.parse_args()
    if not hasattr(os, 'fork'):
        sys.exit('needs os.fork: run it in WSL')
    tmp = Path(tempfile.mkdtemp(prefix='fork_probe_'))
    (tmp / 'probe_wsgi.py').write_text(WSGI)
    log = tmp / 'gunicorn.log'
    env = dict(os.environ, QUEUE_DISABLED='1', PYTHONPATH=f'{tmp}{os.pathsep}{ROOT}')
    server = subprocess.Popen(
        [sys.executable, '-m', 'gunicorn', 'probe_wsgi:app', '-c', str(ROOT / 'gunicorn.conf.py'),
         '-w', '4', '--timeout', '4', '--max-requests', '3', '--max-requests-jitter', '2',
         '-b', f'127.0.0.1:{a.port}', '--log-file', str(log)],
        cwd=ROOT, env=env, start_new_session=True)
    url = f'http://127.0.0.1:{a.port}/api/spec?family=HTD&pitch=5M'
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(url, timeout=2).read()
                break
            except OSError:
                time.sleep(0.5)
        else:
            sys.exit(f'gunicorn did not start; see {log}')
        ok, failed, t0 = 0, 0, time.time()
        for _ in range(a.requests):
            try:
                ok += urllib.request.urlopen(url, timeout=8).status == 200
            except OSError:
                failed += 1
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        server.wait(30)
    timeouts = log.read_text(errors='replace').count('WORKER TIMEOUT')
    print(f'{ok} of {a.requests} answered, {failed} failed, {timeouts} WORKER TIMEOUTs '
          f'({time.time() - t0:.0f} s)')
    sys.exit(0 if ok == a.requests else 1)


if __name__ == '__main__':
    main()
