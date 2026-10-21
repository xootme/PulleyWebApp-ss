"""
release.py — the unattended pulley release, for CCT_Controller's nightly.py (nightly.json "release").

nightly.py runs it after the night's full tests pass, with cwd = the clean worktree of the
tested commit (detached HEAD), when the owner queued "release tonight":

    .venv314/Scripts/python.exe tools/release.py              release (CCT_NIGHTLY=1)
    .venv314/Scripts/python.exe tools/release.py --dry-run    preconditions and the plan only
    .venv314/Scripts/python.exe tools/release.py --smoke      smoke-test what is live now

CCT_RELEASE_VERSION names the version (else +0.0.1); CCT_RELEASE_NOTE is the owner's words.
CCT_Build_and_Deploy.md §3, unattended:
  1. the checkout is clean, and origin/token-model is already in it (else the push would
     carry untested commits, or fail);
  2. "Version x.y.z" (app.py and tests/browser/admin_ui.js) committed here and pushed to
     origin token-model; the owner's own working tree is never touched;
  3. pulley-test deployed and smoke-tested; on failure prod is left alone;
  4. pulley deployed and smoke-tested through cheapcadtools.com; on failure prod goes back
     to the revision(s) it served before;
  5. prints `RELEASED <version> pulley-test=<rev> pulley=<rev>` and exits 0 only then.
gcloud runs on its stored login (configuration cheapcadtools); no prompts, no secrets.
The Worker and the accounts/backup jobs are separate releases and are not touched.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = os.environ.get('CCT_RELEASE_BRANCH', 'token-model')
REGION = 'us-central1'
SERVICES = {   # service -> the address its smoke test uses
    'pulley-test': 'https://pulley-test-925396938485.us-central1.run.app',
    'pulley': 'https://cheapcadtools.com/tools/pulleys',
}
WEBSITE = Path(r'C:\Users\cmyer\Documents\CCT_WebSite\site')
CCT = Path(r'C:\Users\cmyer\Documents\CCT_Controller\bin\cct.py')
VERSION_FILES = [   # (file, pattern whose group 2 is the version)
    ('app.py', r"^(APP_VERSION\s*=\s*')(\d+\.\d+\.\d+)(')"),
    ('tests/browser/admin_ui.js', r"('Timing Pulleys )(\d+\.\d+\.\d+)(')"),
]
UA = 'CCT-PulleyRelease/1.0'


class Fail(Exception):
    pass


def say(msg):
    print(f'[release] {msg}', flush=True)


def run(cmd, *, cwd=ROOT, check=True, quiet=False):
    """Runs cmd; returns its stdout. Fail (with the output's tail) when it exits non-zero."""
    if not quiet:
        say('$ ' + ' '.join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if check and p.returncode != 0:
        tail = '\n'.join((p.stdout + p.stderr).strip().splitlines()[-15:])
        raise Fail(f'{Path(str(cmd[0])).name} {cmd[1] if len(cmd) > 1 else ""} exited {p.returncode}:\n{tail}')
    return p.stdout


def git(*args, **kw):
    return run(['git', '-C', ROOT, *args], **kw).strip()


def gcloud(*args, **kw):
    exe = shutil.which('gcloud')
    if not exe:
        raise Fail('gcloud not found on PATH')
    return run([exe, '--configuration=cheapcadtools', *args], **kw)


# --- the version ---------------------------------------------------------------

def current_version():
    path, pat = VERSION_FILES[0]
    m = re.search(pat, (ROOT / path).read_text(encoding='utf-8'), re.M)
    if not m:
        raise Fail(f'no version in {path}')
    return m.group(2)


def next_version(cur):
    want = os.environ.get('CCT_RELEASE_VERSION', '').strip()
    if want:
        if not re.fullmatch(r'\d+\.\d+\.\d+', want):
            raise Fail(f'CCT_RELEASE_VERSION {want!r} is not x.y.z')
        if tuple(map(int, want.split('.'))) <= tuple(map(int, cur.split('.'))):
            raise Fail(f'CCT_RELEASE_VERSION {want} is not above the current {cur}')
        return want
    a, b, c = map(int, cur.split('.'))
    return f'{a}.{b}.{c + 1}'


def bump(new):
    for path, pat in VERSION_FILES:
        f = ROOT / path
        with open(f, encoding='utf-8', newline='') as fh:   # keep the file's line endings
            text = fh.read()
        text, n = re.subn(pat, lambda m: m.group(1) + new + m.group(3), text, count=1, flags=re.M)
        if n != 1:
            raise Fail(f'no version in {path}')
        with open(f, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)


# --- Cloud Run ------------------------------------------------------------------

def service(name):
    return json.loads(gcloud('run', 'services', 'describe', name, '--region', REGION,
                             '--format=json', quiet=True))


def serving(name):
    """{revision: percent} that the service serves now."""
    out = {}
    for t in service(name).get('status', {}).get('traffic', []):
        if t.get('percent') and t.get('revisionName'):
            out[t['revisionName']] = out.get(t['revisionName'], 0) + t['percent']
    return out


def deploy(name):
    gcloud('run', 'deploy', name, '--source', '.', '--region', REGION, '--quiet')
    latest = service(name)['status']['latestReadyRevisionName']
    if serving(name) != {latest: 100}:   # a rollback pins traffic; a release goes back to latest
        gcloud('run', 'services', 'update-traffic', name, '--to-latest', '--region', REGION, '--quiet')
        if serving(name) != {latest: 100}:
            raise Fail(f'{name}: {latest} is not serving all traffic: {serving(name)}')
    say(f'{name}: {latest} serving')
    return latest


def roll_back(name, before):
    spec = ','.join(f'{rev}={pct}' for rev, pct in before.items())
    say(f'{name}: rolling back to {spec}')
    gcloud('run', 'services', 'update-traffic', name, '--to-revisions', spec, '--region', REGION, '--quiet')


# --- smoke tests (CCT_Build_and_Deploy.md §3 step 6) -----------------------------

def http(url, data=None, tries=4):
    """(status, body) — retried while a fresh revision starts."""
    for i in range(tries):
        req = urllib.request.Request(url, data=data, headers={
            'User-Agent': UA, **({'Content-Type': 'application/json'} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == tries - 1:
                return e.code, e.read().decode('utf-8', 'replace')
        except (urllib.error.URLError, TimeoutError) as e:
            if i == tries - 1:
                return 0, str(e)
        time.sleep(15)


def queued_check(label, cmd, cwd, tries=2):
    """A website check, in a test turn like every check (it opens Chrome). Tried twice: a
    fresh revision's cold start can fail one fetch (pulley-test, 2026-10-21), and a flake
    must not roll back a good release."""
    for i in range(tries):
        out = run([sys.executable, CCT, 'test', '--from', 'PulleyWebApp', '--label', label, '--', *cmd],
                  cwd=cwd, check=False)
        lines = out.strip().splitlines()
        if re.search(r'exit 0\b', lines[-1] if lines else ''):
            return True, ''
        say(f'{label}: failed' + (', trying again' if i < tries - 1 else ''))
    return False, '\n'.join(lines[-6:])


def smoke(name, version, revision):
    """Returns the problems found (empty = passed)."""
    base, bad = SERVICES[name], []
    say(f'{name}: smoke test of {revision} at {base}')
    code, body = http(base + '/')
    found = re.search(r'Ver\. (\d+\.\d+\.\d+)', body)
    if code != 200 or not found or found.group(1) != version:
        bad.append(f'page: {code}, version {found.group(1) if found else "none"} (want {version})')
    code, _ = http(base + '/api/v1/describe')
    if code != 200:
        bad.append(f'describe: {code}')
    code, _ = http(base + '/api/v1/check', data=b'{"params": {}}')
    if code != 200:
        bad.append(f'check: {code}')
    code, _ = http(f'{base}/download/result/{secrets.token_hex(16)}/x.zip', tries=1)
    if code != 404:
        bad.append(f'expired result: {code} (want 404)')
    if name == 'pulley':
        env = json.dumps(service(name)['spec']['template']['spec']['containers'][0].get('env', []))
        if 'STRIPE_SECRET_KEY' not in env:   # names only; values never printed
            bad.append('the Stripe settings are missing from the service')
    ok, tail = queued_check(f'release smoke: AI prompts on {name} {version}',
                            ['python', 'tools/verify_ai_prompts.py', '--base', base], WEBSITE)
    if not ok:
        bad.append('verify_ai_prompts:\n' + tail)
    ok, tail = queued_check(f'release smoke: live samples on {name} {version}',
                            ['node', 'tools/verify_live_samples.mjs', '--base', base + '/'], WEBSITE)
    if not ok:
        bad.append('verify_live_samples:\n' + tail)
    errors = gcloud('logging', 'read',
                    f'resource.type=cloud_run_revision AND resource.labels.revision_name={revision} '
                    'AND severity>=ERROR', '--limit', '5', '--freshness=2h',
                    '--format=value(timestamp,textPayload)', quiet=True).strip()
    if errors:
        bad.append('errors in the revision log:\n' + errors)
    for b in bad:
        say(f'{name}: FAIL {b}')
    if not bad:
        say(f'{name}: smoke test passed')
    return bad


# --- the release ----------------------------------------------------------------

def preflight():
    if git('status', '--porcelain'):
        raise Fail('the checkout has uncommitted changes; a release runs from a clean worktree')
    git('fetch', '-q', 'origin', BRANCH)
    p = subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', f'origin/{BRANCH}', 'HEAD'])
    if p.returncode != 0:
        raise Fail(f'origin/{BRANCH} has commits this checkout lacks; they were not tested tonight')
    cur = current_version()
    return cur, next_version(cur), {name: serving(name) for name in SERVICES}


def release():
    cur, new, before = preflight()
    say(f'{cur} -> {new} at {git("rev-parse", "--short", "HEAD")}; serving now: {before}')
    note = os.environ.get('CCT_RELEASE_NOTE', '').strip()
    bump(new)
    git('add', *[p for p, _ in VERSION_FILES])
    git('commit', '-q', '-m', f'Version {new}\n\nReleased by the nightly run'
        + (f' (owner: "{note}")' if note else '') + '.')
    git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
    revs = {}
    for name in SERVICES:
        revs[name] = deploy(name)
        bad = smoke(name, new, revs[name])
        if bad and name == 'pulley':
            roll_back(name, before[name])
            code, body = http(SERVICES[name] + '/')
            back = re.search(r'Ver\. (\d+\.\d+\.\d+)', body)
            say(f'pulley: rolled back; the page shows {back.group(1) if back else f"nothing ({code})"}')
        if bad:
            raise Fail(f'{name} {revs[name]} failed its smoke test'
                       + ('; prod was not touched' if name != 'pulley' else '; prod rolled back'))
    print(f'RELEASED {new} ' + ' '.join(f'{n}={r}' for n, r in revs.items()), flush=True)


def main(args):
    try:
        if '--dry-run' in args:
            cur, new, before = preflight()
            say(f'dry run: would commit "Version {new}" (now {cur}) at {git("rev-parse", "--short", "HEAD")}, '
                f'push it to origin {BRANCH}, deploy {" then ".join(SERVICES)}; serving now: {before}')
            print(f'DRY RUN OK {new}', flush=True)
        elif '--smoke' in args:
            bad = []
            for name in SERVICES:
                live = next(iter(serving(name)))
                bad += smoke(name, current_version(), live)
            print('SMOKE OK' if not bad else 'SMOKE FAILED', flush=True)
            return 1 if bad else 0
        else:
            release()
        return 0
    except Fail as e:
        say(f'FAILED: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
