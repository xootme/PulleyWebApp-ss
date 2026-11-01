"""A hand control for a browser harness: a copy of the tree (git ls-files, as the working tree
has them) with one break in it, and the harness run on its own server against that copy. It
passes when the harness fails, and names the checks that caught the break; a harness that
passes, or never finishes, says the break went unseen. From the repo root, in a test turn:

  .venv314/Scripts/python.exe tools/hand_control.py NAME
  .venv314/Scripts/python.exe tools/hand_control.py --list

The breaks are HAND below: name -> (harness, file, [(the text, its broken form), ...]). They are for
checks that only a browser can catch (a converted wait that must still fail when its state
never comes), so tools/neg_controls.py, which runs pytest, can't run them."""
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harnesses  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
PAGE = 'templates/index.html'
HAND = {
    # the harness sweep (2026-10-31): a wait that is settled() still fails when the state never comes
    'busy: a valid value clears only after the 2 s hold': ('busy_ui', PAGE, [
        ('  else { refusals.cancel(); showWarnings(); }', '  else refusals.show(showWarnings, ticket);')]),
    'bore: the spline\'s fitted figures never come': ('bore_ui', PAGE, [
        ("      const r = await fetch('/api/spline?' + new URLSearchParams(p));", "      return;")]),
    'undo: teeth under the minimum are never clamped': ('undo_ui', PAGE, [   # both clamps: the field's and Teeth's own
        ('        if (cur < min || cur > max) {\n          el.value = Math.min(max, Math.max(min, cur));',
         '        if (false) {\n          el.value = Math.min(max, Math.max(min, cur));'),
        ('  if (parseInt(teethEl.value) < minT) teethEl.value = minT;\n  const teeth = teethEl.value;',
         '  const teeth = teethEl.value;')]),
    # a dropped download poll (Sprocket's lost paid zip, 2026-10-31): polling must go on
    'downloads: a dropped poll stops the download': ('tokens_ui', PAGE, [
        ('      if (++_pollErrors < 60) { _jobPollTimer = setTimeout(pollJobStatus, 1000); return; }',
         '      if (++_pollErrors < 60) { return; }')]),
}
# A harness that needs more than a plain server (as tools/harnesses.py starts it): the server's
# extra settings, and the harness's arguments after the base URL from the server's log folder.
SETUP = {
    'tokens_ui': ({'TOKENS_ENABLED': '1', 'GOOGLE_CLIENT_ID': 'test', 'GOOGLE_CLIENT_SECRET': 'test'},
                  lambda logs: [logs, PY]),
}


def copy_tree(dst):
    files = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout.split(b'\0')
    for f in filter(None, files):
        src = ROOT / f.decode()
        if src.is_file():
            out = dst / f.decode()
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)


def run(name):
    harness, rel, breaks = HAND[name]
    tree = Path(tempfile.mkdtemp(prefix='hand-ctl-'))
    try:
        copy_tree(tree)
        f = tree / rel
        text = f.read_text(encoding='utf-8')
        nl = '\r\n' if '\r\n' in text else '\n'
        for old, new in breaks:
            old, new = old.replace('\n', nl), new.replace('\n', nl)
            if text.count(old) != 1:
                print(f'BROKEN  {name}: the text is in {rel} {text.count(old)} times, not once')
                return False
            text = text.replace(old, new)
        f.write_text(text, encoding='utf-8', newline='')
        s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
        logs = tempfile.mkdtemp(prefix='hand-ctl-logs-')
        env = harnesses.environment()            # the runner's (no secrets; the STEP binary the day runs use)
        env.update(QUEUE_DISABLED='1', PULLEY_LOG_DIR=logs, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        extra, args = SETUP.get(harness, ({}, lambda logs: []))
        srv = subprocess.Popen([PY, 'app.py', '--port', str(port), '--no-debug'], cwd=tree, env={**env, **extra},
                               stdout=open(Path(logs) / 'server.log', 'w'), stderr=subprocess.STDOUT)
        try:
            for _ in range(240):
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2); break
                except Exception:
                    time.sleep(0.5)
            try:
                out = subprocess.run(['node', f'tests/browser/{harness}.js', f'http://127.0.0.1:{port}', *args(logs)], cwd=tree,
                                     env=env, capture_output=True, text=True, encoding='utf-8', timeout=900)
                code, text = out.returncode, out.stdout + out.stderr
            except subprocess.TimeoutExpired:
                code, text = 'timeout', ''
        finally:
            srv.terminate(); srv.wait(30)
        fails = [ln for ln in text.splitlines() if ln.startswith('FAIL') or 'never settled' in ln]
        caught = code not in (0, 'timeout') and bool(fails)
        print(f"{'caught' if caught else 'MISSED'}  {name}  ({harness}, exit {code})")
        for ln in fails[:8]:
            print('    ' + ln[:300])
        return caught
    finally:
        shutil.rmtree(tree, ignore_errors=True)


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args or '--list' in args:
        print('\n'.join(HAND))
        sys.exit(0)
    names = [n for n in HAND if any(a in n for a in args)]
    sys.exit(0 if names and all([run(n) for n in names]) else 1)
