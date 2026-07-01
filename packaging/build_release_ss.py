"""
build_release_ss.py — One-command Windows release builder for PulleyWebApp-ss.

Pipeline:
  1. Clean previous build artefacts.
  2. PyArmor obfuscates Python source (app.py, geometry/, exporters/).
  3. Copy launcher_ss.py into the obfuscated output (not obfuscated — entry point).
  4. PyInstaller bundles everything into dist/PulleyApp/ using PulleyApp_ss.spec.
     small_step.exe is bundled automatically by the spec.
  5. Write version.txt; zip dist/PulleyApp/ → releases/PulleyApp_<version>.zip
  6. Publish to GitHub: create release vYYYYMMDD, upload asset as PulleyApp.zip.
     PULLEY_APP_URL on Render is a permanent "latest" URL — never needs updating:
     https://github.com/xootme/PulleyApp-releases/releases/latest/download/PulleyApp.zip
  7. Update Render env vars (PULLEY_APP_VERSION, PULLEY_APP_CHANGELOG) and trigger
     a redeploy so the running service picks them up immediately.

Key differences from build_release.py (non-ss):
  - Uses build/obfuscated_ss/ and packaging/PulleyApp_ss.spec
  - Copies launcher_ss.py (sets SMALL_STEP_BIN, QUEUE_DISABLED=1)
  - No shared runtime zip — trimesh/shapely/manifold3d are bundled directly by
    PyInstaller (no cadquery/OCP/casadi dependency in the -ss build)

Prerequisites (run once):
  pip install pyarmor pyinstaller
  pyarmor reg C:/Users/cmyer/Documents/PayArmor/pyarmor-regfile-11621.zip
  GitHub token stored in Windows Credential Manager as "git:https://github.com"

Run from the repo root (registered Windows dev machine only — never CI/CD):
  .venv314/Scripts/python packaging/build_release_ss.py
"""

import ctypes
import ctypes.wintypes
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ── Config ────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent   # repo root
BUILD_DIR   = ROOT / 'build' / 'obfuscated_ss'
DIST_DIR    = ROOT / 'dist' / 'PulleyApp'
RELEASES    = ROOT / 'releases'
VERSION     = (ROOT / 'version.txt').read_text(encoding='utf-8').strip()
PYTHON      = sys.executable                           # must be .venv314 Python

GITHUB_REPO  = 'xootme/PulleyApp-releases'
ASSET_NAME   = 'PulleyApp.zip'   # fixed name → "latest" URL never changes
RENDER_SERVICE = 'srv-d7bve2a8qa3s738n68ig'

OBFUSCATE_TARGETS = [
    ROOT / 'app.py',
    ROOT / 'geometry',
    ROOT / 'exporters',
]

LAUNCHER_SRC = ROOT / 'packaging' / 'launcher_ss.py'
SPEC_FILE    = ROOT / 'packaging' / 'PulleyApp_ss.spec'


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd, **kwargs):
    print(f'\n>>> {" ".join(str(c) for c in cmd)}')
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f'[FAILED] exit code {result.returncode}')
        sys.exit(result.returncode)


# ── Credential helpers ────────────────────────────────────────────────────────

def _read_credential(target: str) -> str | None:
    """Read a password from Windows Credential Manager by target name."""
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ('Flags',             ctypes.wintypes.DWORD),
            ('Type',              ctypes.wintypes.DWORD),
            ('TargetName',        ctypes.wintypes.LPWSTR),
            ('Comment',           ctypes.wintypes.LPWSTR),
            ('LastWritten',       ctypes.c_int64),
            ('CredentialBlobSize',ctypes.wintypes.DWORD),
            ('CredentialBlob',    ctypes.POINTER(ctypes.c_byte)),
            ('Persist',           ctypes.wintypes.DWORD),
            ('AttributeCount',    ctypes.wintypes.DWORD),
            ('Attributes',        ctypes.c_void_p),
            ('TargetAlias',       ctypes.wintypes.LPWSTR),
            ('UserName',          ctypes.wintypes.LPWSTR),
        ]
    try:
        advapi = ctypes.windll.advapi32
        ptr = ctypes.c_void_p()
        if not advapi.CredReadW(target, 1, 0, ctypes.byref(ptr)):
            return None
        cred = ctypes.cast(ptr, ctypes.POINTER(CREDENTIAL)).contents
        blob = bytes(cred.CredentialBlob[i] for i in range(cred.CredentialBlobSize))
        advapi.CredFree(ptr)
        return blob.decode('utf-16-le')
    except Exception:
        return None


def _get_github_token() -> str | None:
    return _read_credential('git:https://github.com')


def _get_render_token() -> str | None:
    return _read_credential('render:cheapcadtools')


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _gh_request(method, path, data=None, headers=None, token=None):
    url = f'https://api.github.com/{path.lstrip("/")}'
    req_headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'PulleyApp-build-script',
    }
    if headers:
        req_headers.update(headers)
    body = json.dumps(data).encode() if data else None
    if body:
        req_headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


# ── Build steps ───────────────────────────────────────────────────────────────

def step1_clean():
    print('\n── Step 1: Clean ────────────────────────────────────────────')
    for d in [BUILD_DIR, ROOT / 'dist', ROOT / 'build' / 'PulleyApp_ss']:
        if d.exists():
            shutil.rmtree(d)
            print(f'  Removed {d}')
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    print('  Done.')


def step2_pyarmor():
    print('\n── Step 2: PyArmor obfuscation ──────────────────────────────')
    targets = [str(t) for t in OBFUSCATE_TARGETS]
    run([
        PYTHON, '-m', 'pyarmor.cli', 'gen',
        '--output', str(BUILD_DIR),
        '--platform', 'windows.x86_64',
        *targets,
    ])


def step3_copy_launcher():
    print('\n── Step 3: Copy launcher_ss.py ──────────────────────────────')
    dst = BUILD_DIR / 'launcher_ss.py'
    shutil.copy2(str(LAUNCHER_SRC), str(dst))
    print(f'  Copied {LAUNCHER_SRC.name} → {dst}')


def step4_pyinstaller():
    print('\n── Step 4: PyInstaller bundle ───────────────────────────────')
    run([
        PYTHON, '-m', 'PyInstaller',
        '--distpath', str(ROOT / 'dist'),
        '--workpath', str(ROOT / 'build' / 'PulleyApp_ss'),
        '--noconfirm',
        '--clean',
        str(SPEC_FILE),
    ], cwd=str(ROOT))


def step5_zip() -> Path:
    print('\n── Step 5: Zip release ──────────────────────────────────────')
    version_file = DIST_DIR / 'version.txt'
    version_file.write_text(VERSION)
    RELEASES.mkdir(exist_ok=True)
    zip_path = RELEASES / f'PulleyApp_{VERSION}.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in DIST_DIR.rglob('*'):
            zf.write(file, file.relative_to(DIST_DIR.parent))
    size_mb = zip_path.stat().st_size / 1_048_576
    print(f'  Created {zip_path.name}  ({size_mb:.1f} MB)')
    return zip_path


def step6_publish(zip_path: Path):
    print('\n── Step 6: Publish to GitHub ────────────────────────────────')
    token = _get_github_token()
    if not token:
        print('  [SKIP] No GitHub token in Credential Manager — upload manually.')
        return

    tag = f'v{VERSION}'

    # Delete existing release + tag so create never hits a duplicate
    try:
        existing = _gh_request('GET', f'repos/{GITHUB_REPO}/releases/tags/{tag}', token=token)
        _gh_request('DELETE', f'repos/{GITHUB_REPO}/releases/{existing["id"]}', token=token)
        print(f'  Deleted existing release {tag}')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    for _ref in (f'tags/{tag}', f'heads/{tag}'):
        try:
            _gh_request('DELETE', f'repos/{GITHUB_REPO}/git/refs/{_ref}', token=token)
            print(f'  Deleted ref {_ref}')
        except urllib.error.HTTPError:
            pass

    rel = _gh_request('POST', f'repos/{GITHUB_REPO}/releases', token=token, data={
        'tag_name': tag,
        'name': f'PulleyApp {VERSION}',
        'body': '',
        'draft': False,
        'prerelease': False,
    })
    release_id = rel['id']
    print(f'  Created release {tag} (id={release_id})')

    upload_url = (f'https://uploads.github.com/repos/{GITHUB_REPO}'
                  f'/releases/{release_id}/assets?name={ASSET_NAME}')
    zip_bytes = zip_path.read_bytes()
    req = urllib.request.Request(upload_url, data=zip_bytes, method='POST', headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'PulleyApp-build-script',
        'Content-Type': 'application/octet-stream',
    })
    size_mb = len(zip_bytes) / 1_048_576
    print(f'  Uploading {size_mb:.1f} MB as {ASSET_NAME} ...')
    with urllib.request.urlopen(req) as resp:
        asset = json.loads(resp.read())
    print(f'  [OK] {asset["browser_download_url"]}')
    print(f'\n  Permanent download URL (set once in Render PULLEY_APP_URL):')
    print(f'  https://github.com/{GITHUB_REPO}/releases/latest/download/{ASSET_NAME}')


def step7_update_render():
    print('\n── Step 7: Update Render env vars ───────────────────────────')
    render_key = _get_render_token()
    changelog_path = ROOT / 'packaging' / 'changelog.txt'
    changelog = changelog_path.read_text(encoding='utf-8').strip() if changelog_path.exists() else ''

    if not render_key:
        print('  [SKIP] No render:cheapcadtools credential found.')
        print('  Set manually on Render:')
        print(f'    PULLEY_APP_VERSION   = {VERSION}')
        print(f'    PULLEY_APP_CHANGELOG = {changelog!r}')
        return

    headers = {
        'Authorization': f'Bearer {render_key}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'PulleyApp-build-script',
    }
    base = f'https://api.render.com/v1/services/{RENDER_SERVICE}'

    get_req = urllib.request.Request(f'{base}/env-vars', headers=headers)
    with urllib.request.urlopen(get_req) as resp:
        existing = json.loads(resp.read())

    env_map = {e['envVar']['key']: e['envVar']['value'] for e in existing}
    env_map['PULLEY_APP_VERSION']   = VERSION
    env_map['PULLEY_APP_CHANGELOG'] = changelog
    payload = [{'key': k, 'value': v} for k, v in env_map.items()]

    put_req = urllib.request.Request(
        f'{base}/env-vars',
        data=json.dumps(payload).encode(),
        headers=headers, method='PUT',
    )
    try:
        with urllib.request.urlopen(put_req) as resp:
            resp.read()
        print(f'  [OK] PULLEY_APP_VERSION   = {VERSION}')
        if changelog:
            preview = changelog[:60] + ('...' if len(changelog) > 60 else '')
            print(f'  [OK] PULLEY_APP_CHANGELOG = {preview}')
    except urllib.error.HTTPError as e:
        print(f'  [WARN] Render env var update failed: {e.code}')
        return

    deploy_req = urllib.request.Request(
        f'{base}/deploys',
        data=json.dumps({'clearCache': 'do_not_clear'}).encode(),
        headers=headers, method='POST',
    )
    try:
        with urllib.request.urlopen(deploy_req) as resp:
            body = resp.read()
            deploy = json.loads(body) if body.strip() else {}
        deploy_id = deploy.get('id', '?') if deploy else 'triggered'
        print(f'  [OK] Render redeploy triggered (id={deploy_id})')
        print(f'       Service will be live with new version in ~2 minutes.')
    except urllib.error.HTTPError as e:
        print(f'  [WARN] Render redeploy trigger failed: {e.code} — redeploy manually in dashboard')


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print(f'Building PulleyApp-ss {VERSION} with Python {sys.version.split()[0]}')
    print(f'Root:   {ROOT}')
    print(f'Spec:   {SPEC_FILE}')
    print(f'Launch: {LAUNCHER_SRC}')
    print()

    if not SPEC_FILE.exists():
        print(f'ERROR: spec file not found: {SPEC_FILE}')
        sys.exit(1)
    if not LAUNCHER_SRC.exists():
        print(f'ERROR: launcher not found: {LAUNCHER_SRC}')
        sys.exit(1)

    step1_clean()
    step2_pyarmor()
    step3_copy_launcher()
    step4_pyinstaller()
    zip_path = step5_zip()
    step6_publish(zip_path)
    step7_update_render()
    print(f'\n[OK] Release ready: {zip_path}')
    print(f'     Launcher exe:   {DIST_DIR / "PulleyApp.exe"}')
