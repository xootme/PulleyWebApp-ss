"""
build_release.py — One-command Windows release builder for PulleyApp.

Pipeline:
  1. Clean previous build artefacts.
  2. PyArmor obfuscates Python source (app.py, geometry/, exporters/).
  3. Copy launcher.py into the obfuscated output.
  4. PyInstaller bundles everything into dist/PulleyApp/.
  5. Zip dist/PulleyApp/ → releases/PulleyApp_<version>.zip
  6. Publish to GitHub: create release vYYYYMMDD, upload asset as PulleyApp.zip.
     PULLEY_APP_URL on Render is a permanent "latest" URL — never needs updating:
     https://github.com/xootme/PulleyApp-releases/releases/latest/download/PulleyApp.zip

Prerequisites (run once):
  pip install pyarmor pyinstaller
  pyarmor reg C:/Users/cmyer/Documents/PayArmor/pyarmor-regfile-11621.zip
  GitHub token stored in Windows Credential Manager as "git:https://github.com"

Run from the repo root:
  .venv312/Scripts/python packaging/build_release.py
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
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ── Config ────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent   # repo root
BUILD_DIR   = ROOT / 'build' / 'obfuscated'
DIST_DIR    = ROOT / 'dist' / 'PulleyApp'
RELEASES    = ROOT / 'releases'
VERSION     = datetime.now().strftime('%Y%m%d')        # e.g. 20260504
PYTHON      = sys.executable                           # must be .venv312 Python

GITHUB_REPO    = 'xootme/PulleyApp-releases'
ASSET_NAME     = 'PulleyApp.zip'       # fixed name → "latest" URL never changes
RUNTIME_ASSET  = 'CCT_Render_Runtime.zip'     # shared across all CheapCADTools apps
RUNTIME_TAG    = 'runtime-v1'          # bump only when deps change
RUNTIME_VER    = '1'
RENDER_SERVICE = 'srv-d7bve2a8qa3s738n68ig'

# Packages that go into the shared runtime (not bundled in the app)
RUNTIME_PACKAGES = [
    'cadquery', 'OCP', 'cadquery_ocp',
    'casadi',
    'trimesh',
    'shapely',
    'scipy',
    'numpy',
    'vtkmodules',
]
RUNTIME_LIBS = [         # *.libs companion directories
    'cadquery_ocp.libs', 'shapely.libs', 'scipy.libs', 'numpy.libs',
]
RUNTIME_PYD_FILES = [    # single .pyd files that live directly in site-packages
    'manifold3d',
]

# Python source directories/files to obfuscate
OBFUSCATE_TARGETS = [
    ROOT / 'app.py',
    ROOT / 'geometry',
    ROOT / 'exporters',
]


def run(cmd, **kwargs):
    print(f'\n>>> {" ".join(str(c) for c in cmd)}')
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f'[FAILED] exit code {result.returncode}')
        sys.exit(result.returncode)


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _get_github_token():
    """Read the GitHub OAuth token from Windows Credential Manager."""
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ('Flags', ctypes.wintypes.DWORD),
            ('Type', ctypes.wintypes.DWORD),
            ('TargetName', ctypes.wintypes.LPWSTR),
            ('Comment', ctypes.wintypes.LPWSTR),
            ('LastWritten', ctypes.c_int64),
            ('CredentialBlobSize', ctypes.wintypes.DWORD),
            ('CredentialBlob', ctypes.POINTER(ctypes.c_byte)),
            ('Persist', ctypes.wintypes.DWORD),
            ('AttributeCount', ctypes.wintypes.DWORD),
            ('Attributes', ctypes.c_void_p),
            ('TargetAlias', ctypes.wintypes.LPWSTR),
            ('UserName', ctypes.wintypes.LPWSTR),
        ]

    advapi = ctypes.windll.advapi32
    ptr = ctypes.c_void_p()
    if not advapi.CredReadW('git:https://github.com', 1, 0, ctypes.byref(ptr)):
        return None
    cred = ctypes.cast(ptr, ctypes.POINTER(CREDENTIAL)).contents
    blob = bytes(cred.CredentialBlob[i] for i in range(cred.CredentialBlobSize))
    advapi.CredFree(ptr)
    return blob.decode('utf-16-le')


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


def step6_publish(zip_path):
    print('\n── Step 6: Publish to GitHub ────────────────────────────────')
    token = _get_github_token()
    if not token:
        print('  [SKIP] No GitHub token in Credential Manager — upload manually.')
        return

    tag = f'v{VERSION}'

    # Delete old release + tag (always clean both so create never hits an existing tag)
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

    # Create fresh release
    rel = _gh_request('POST', f'repos/{GITHUB_REPO}/releases', token=token, data={
        'tag_name': tag,
        'name': f'PulleyApp {VERSION[:4]}-{VERSION[4:6]}-{VERSION[6:]}',
        'body': '',
        'draft': False,
        'prerelease': False,
    })
    release_id = rel['id']
    print(f'  Created release {tag} (id={release_id})')

    # Upload zip as fixed asset name
    upload_url = f'https://uploads.github.com/repos/{GITHUB_REPO}/releases/{release_id}/assets?name={ASSET_NAME}'
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


# ── Build steps ───────────────────────────────────────────────────────────────

def step1_clean():
    print('\n── Step 1: Clean ────────────────────────────────────────────')
    for d in [BUILD_DIR, ROOT / 'dist', ROOT / 'build' / 'PulleyApp']:
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
    print('\n── Step 3: Copy launcher ────────────────────────────────────')
    src = ROOT / 'packaging' / 'launcher.py'
    dst = BUILD_DIR / 'launcher.py'
    shutil.copy2(src, dst)
    print(f'  Copied {src.name} → {dst}')


def step4_pyinstaller():
    print('\n── Step 4: PyInstaller bundle ───────────────────────────────')
    spec = ROOT / 'packaging' / 'PulleyApp.spec'
    run([
        PYTHON, '-m', 'PyInstaller',
        '--distpath', str(ROOT / 'dist'),
        '--workpath', str(ROOT / 'build' / 'PulleyApp'),
        '--noconfirm',
        str(spec),
    ], cwd=str(ROOT))


def step5_zip():
    print('\n── Step 5: Zip release ──────────────────────────────────────')
    # Write version.txt into the bundle so the addin can detect updates
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


def step6b_build_runtime():
    """Build the shared CheapCADTools-Runtime.zip if it doesn't exist yet.

    The runtime contains all heavy scientific packages (cadquery, OCP, casadi,
    trimesh, numpy, scipy, shapely, manifold3d) that are shared across tools.
    Bump RUNTIME_TAG / RUNTIME_VER when upgrading these deps.
    """
    print('\n── Step 6b: Build runtime ───────────────────────────────────')
    runtime_zip = RELEASES / RUNTIME_ASSET
    if runtime_zip.exists():
        print(f'  [SKIP] {RUNTIME_ASSET} already exists — delete to rebuild.')
        return runtime_zip

    import site as _site
    sp = next((p for p in _site.getsitepackages() if p.endswith('site-packages')), None)
    if not sp:
        print('  [SKIP] Could not locate site-packages.')
        return None
    sp = Path(sp)

    RELEASES.mkdir(exist_ok=True)
    pkg_root = Path('site-packages')  # arc prefix inside the zip

    print(f'  Building {RUNTIME_ASSET} from {sp} ...')
    with zipfile.ZipFile(runtime_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr('version.txt', RUNTIME_VER)

        # Package directories
        for pkg in RUNTIME_PACKAGES:
            src = sp / pkg
            if not src.exists():
                print(f'  [WARN] {pkg} not found, skipping')
                continue
            for f in src.rglob('*'):
                if f.is_file():
                    zf.write(f, pkg_root / pkg / f.relative_to(src))

        # *.libs companion directories (DLLs alongside package)
        for libs in RUNTIME_LIBS:
            src = sp / libs
            if not src.exists():
                print(f'  [WARN] {libs} not found, skipping')
                continue
            for f in src.rglob('*'):
                if f.is_file():
                    zf.write(f, pkg_root / libs / f.relative_to(src))

        # Single .pyd files directly in site-packages
        for name in RUNTIME_PYD_FILES:
            for pyd in sp.glob(f'{name}*.pyd'):
                zf.write(pyd, pkg_root / pyd.name)

    size_mb = runtime_zip.stat().st_size / 1_048_576
    print(f'  Created {RUNTIME_ASSET}  ({size_mb:.1f} MB)')
    return runtime_zip


def step6c_publish_runtime(runtime_zip):
    """Upload runtime zip to GitHub under a stable tag (runtime-v1).

    Only re-uploads if the asset doesn't exist yet on GitHub.
    """
    print('\n── Step 6c: Publish runtime ─────────────────────────────────')
    if not runtime_zip or not runtime_zip.exists():
        print('  [SKIP] No runtime zip to publish.')
        return

    token = _get_github_token()
    if not token:
        print('  [SKIP] No GitHub token.')
        return

    # Check if the runtime release + asset already exists
    try:
        rel = _gh_request('GET', f'repos/{GITHUB_REPO}/releases/tags/{RUNTIME_TAG}', token=token)
        assets = _gh_request('GET', f'repos/{GITHUB_REPO}/releases/{rel["id"]}/assets', token=token)
        if any(a['name'] == RUNTIME_ASSET for a in assets):
            print(f'  [SKIP] {RUNTIME_ASSET} already on GitHub — delete release to re-upload.')
            return
        release_id = rel['id']
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # Create the runtime release
        rel = _gh_request('POST', f'repos/{GITHUB_REPO}/releases', token=token, data={
            'tag_name': RUNTIME_TAG,
            'name': f'CCT Render Runtime v{RUNTIME_VER}',
            'body': 'Shared scientific Python runtime for all CheapCADTools desktop apps.',
            'draft': False, 'prerelease': False,
        })
        release_id = rel['id']
        print(f'  Created runtime release {RUNTIME_TAG} (id={release_id})')

    upload_url = (f'https://uploads.github.com/repos/{GITHUB_REPO}'
                  f'/releases/{release_id}/assets?name={RUNTIME_ASSET}')
    zip_bytes = runtime_zip.read_bytes()
    print(f'  Uploading {len(zip_bytes)/1_048_576:.1f} MB ...')
    req = urllib.request.Request(upload_url, data=zip_bytes, method='POST', headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'PulleyApp-build-script',
        'Content-Type': 'application/octet-stream',
    })
    with urllib.request.urlopen(req) as resp:
        asset = json.loads(resp.read())
    print(f'  [OK] {asset["browser_download_url"]}')


def step7_update_render():
    print('\n── Step 7: Update Render env vars ───────────────────────────')
    render_key = _get_render_token()
    changelog_path = ROOT / 'packaging' / 'changelog.txt'
    changelog = changelog_path.read_text(encoding='utf-8').strip() if changelog_path.exists() else ''

    if not render_key:
        print('  [SKIP] No render:cheapcadtools credential found.')
        print(f'  Set manually on Render:')
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

    # Fetch existing vars so PUT doesn't wipe unrelated ones
    get_req = urllib.request.Request(f'{base}/env-vars', headers=headers)
    with urllib.request.urlopen(get_req) as resp:
        existing = json.loads(resp.read())

    env_map = {e['envVar']['key']: e['envVar']['value'] for e in existing}
    env_map['PULLEY_APP_VERSION']    = VERSION
    env_map['PULLEY_APP_CHANGELOG']  = changelog
    env_map['PULLEY_RUNTIME_VERSION'] = RUNTIME_VER
    env_map['PULLEY_RUNTIME_URL']    = (
        f'https://github.com/{GITHUB_REPO}/releases/download/{RUNTIME_TAG}/{RUNTIME_ASSET}'
    )
    payload = [{'key': k, 'value': v} for k, v in env_map.items()]

    put_req = urllib.request.Request(f'{base}/env-vars',
                                     data=json.dumps(payload).encode(),
                                     headers=headers, method='PUT')
    try:
        with urllib.request.urlopen(put_req) as resp:
            resp.read()
        print(f'  [OK] PULLEY_APP_VERSION   = {VERSION}')
        print(f'  [OK] PULLEY_APP_CHANGELOG = {changelog[:60]}...' if len(changelog) > 60 else f'  [OK] PULLEY_APP_CHANGELOG = {changelog}')
    except urllib.error.HTTPError as e:
        print(f'  [WARN] Render update failed: {e.code}')


def _get_render_token():
    try:
        advapi = ctypes.windll.advapi32

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ('Flags', ctypes.wintypes.DWORD),
                ('Type', ctypes.wintypes.DWORD),
                ('TargetName', ctypes.wintypes.LPWSTR),
                ('Comment', ctypes.wintypes.LPWSTR),
                ('LastWritten', ctypes.c_int64),
                ('CredentialBlobSize', ctypes.wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_byte)),
                ('Persist', ctypes.wintypes.DWORD),
                ('AttributeCount', ctypes.wintypes.DWORD),
                ('Attributes', ctypes.c_void_p),
                ('TargetAlias', ctypes.wintypes.LPWSTR),
                ('UserName', ctypes.wintypes.LPWSTR),
            ]

        ptr = ctypes.c_void_p()
        if not advapi.CredReadW('render:cheapcadtools', 1, 0, ctypes.byref(ptr)):
            return None
        cred = ctypes.cast(ptr, ctypes.POINTER(CREDENTIAL)).contents
        blob = bytes(cred.CredentialBlob[i] for i in range(cred.CredentialBlobSize))
        advapi.CredFree(ptr)
        return blob.decode('utf-16-le')
    except Exception:
        return None


if __name__ == '__main__':
    print(f'Building PulleyApp {VERSION} with Python {sys.version.split()[0]}')
    print(f'Root: {ROOT}')
    step1_clean()
    step2_pyarmor()
    step3_copy_launcher()
    step4_pyinstaller()
    zip_path = step5_zip()
    runtime_zip = step6b_build_runtime()
    step6c_publish_runtime(runtime_zip)
    step6_publish(zip_path)
    step7_update_render()
    print(f'\n[OK] Release ready: releases/PulleyApp_{VERSION}.zip')
    print(f'  Launcher exe:  dist/PulleyApp/PulleyApp.exe')
