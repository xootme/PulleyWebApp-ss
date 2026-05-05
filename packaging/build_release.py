"""
build_release.py — One-command Windows release builder for PulleyApp.

Pipeline:
  1. Clean previous build artefacts.
  2. PyArmor obfuscates Python source (app.py, geometry/, exporters/).
  3. Copy launcher.py into the obfuscated output.
  4. PyInstaller bundles everything into dist/PulleyApp/.
  5. Zip dist/PulleyApp/ → releases/PulleyApp_<version>.zip

Prerequisites (run once):
  pip install pyarmor pyinstaller
  pyarmor reg C:/Users/cmyer/Documents/PayArmor/pyarmor-regfile-11621.zip

Run from the repo root:
  .venv312/Scripts/python packaging/build_release.py
"""

import os
import shutil
import subprocess
import sys
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
    RELEASES.mkdir(exist_ok=True)
    zip_path = RELEASES / f'PulleyApp_{VERSION}.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in DIST_DIR.rglob('*'):
            zf.write(file, file.relative_to(DIST_DIR.parent))
    size_mb = zip_path.stat().st_size / 1_048_576
    print(f'  Created {zip_path.name}  ({size_mb:.1f} MB)')


if __name__ == '__main__':
    print(f'Building PulleyApp {VERSION} with Python {sys.version.split()[0]}')
    print(f'Root: {ROOT}')
    step1_clean()
    step2_pyarmor()
    step3_copy_launcher()
    step4_pyinstaller()
    step5_zip()
    print(f'\n[OK] Release ready: releases/PulleyApp_{VERSION}.zip')
    print(f'  Launcher exe:  dist/PulleyApp/PulleyApp.exe')
