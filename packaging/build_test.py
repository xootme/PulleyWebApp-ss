"""
build_test.py — Test build for PulleyWebApp-ss desktop standalone.

Produces dist/PulleyApp/ ready for addin testing (Fusion 360 + FreeCAD).
Installs the result to %APPDATA%\\CheapCADTools\\PulleyApp\\ automatically.

PyArmor: uses the .venv312 pyarmor (registered on this machine) to obfuscate.
PyInstaller: uses the .venv314 Python (same version as the app) to bundle.

Run from the PulleyWebApp-ss repo root:
    .venv314\\Scripts\\python.exe packaging\\build_test.py

Flags:
    --no-pyarmor   Skip obfuscation (plain source, faster for quick tests)
    --no-install   Skip copying to APPDATA after build
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT       = Path(__file__).resolve().parent.parent
BUILD_DIR  = ROOT / 'build' / 'obfuscated_ss'
DIST_DIR   = ROOT / 'dist' / 'PulleyApp'
SPEC_FILE  = ROOT / 'packaging' / 'PulleyApp_ss.spec'

# PyArmor runs in the same venv as the app (generates Python-3.14-compatible runtime)
PYARMOR_PYTHON = Path(r'C:\Users\cmyer\Documents\PulleyWebApp-ss\.venv314\Scripts\python.exe')
PYARMOR_EXE    = Path(r'C:\Users\cmyer\Documents\PulleyWebApp-ss\.venv314\Scripts\pyarmor.exe')

# PyInstaller must run with the same Python the app uses (3.14)
THIS_PYTHON = sys.executable   # expected to be .venv314\Scripts\python.exe

INSTALL_DIR = Path(os.environ.get('APPDATA', Path.home())) / 'CheapCADTools' / 'PulleyApp'

# Source files/dirs to obfuscate
OBFUSCATE_TARGETS = [
    ROOT / 'app.py',
    ROOT / 'geometry',
    ROOT / 'exporters',
]

# Launcher is NOT obfuscated (PyInstaller uses it as the entry point script)
LAUNCHER_SRC = ROOT / 'packaging' / 'launcher_ss.py'


def _run(cmd, cwd=None, env=None):
    print(f'\n$ {" ".join(str(c) for c in cmd)}')
    subprocess.run([str(c) for c in cmd], check=True, cwd=str(cwd or ROOT), env=env)


def build(use_pyarmor: bool, do_install: bool):
    # 1. Clean previous build
    print('── Clean ────────────────────────────────────────────────────────')
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)

    if use_pyarmor:
        # 2a. Obfuscate source with PyArmor
        print('\n── PyArmor obfuscate ─────────────────────────────────────────────')
        obf_cmd = [
            PYARMOR_EXE, 'gen',
            '--output', str(BUILD_DIR),
            '--recursive',
        ]
        for t in OBFUSCATE_TARGETS:
            obf_cmd.append(str(t))
        _run(obf_cmd)
    else:
        # 2b. Plain copy (no obfuscation)
        print('\n── Copy source (no PyArmor) ──────────────────────────────────────')
        shutil.copy(str(ROOT / 'app.py'), str(BUILD_DIR / 'app.py'))
        for d in ('geometry', 'exporters'):
            shutil.copytree(str(ROOT / d), str(BUILD_DIR / d),
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

    # 3. Copy the launcher into the obfuscated dir (not obfuscated — PyInstaller entry point)
    shutil.copy(str(LAUNCHER_SRC), str(BUILD_DIR / 'launcher_ss.py'))

    # 4. PyInstaller bundle
    print('\n── PyInstaller ───────────────────────────────────────────────────')
    _run([
        THIS_PYTHON, '-m', 'PyInstaller',
        '--clean',
        '--noconfirm',
        str(SPEC_FILE),
    ])

    print(f'\nBuild complete: {DIST_DIR}')

    if do_install:
        # 5. Install to %APPDATA%\CheapCADTools\PulleyApp\
        print('\n── Install ───────────────────────────────────────────────────────')
        if INSTALL_DIR.exists():
            shutil.rmtree(INSTALL_DIR)
        shutil.copytree(str(DIST_DIR), str(INSTALL_DIR))
        print(f'Installed to: {INSTALL_DIR}')
        print(f'  EXE: {INSTALL_DIR / "PulleyApp.exe"}')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Build PulleyWebApp-ss test release')
    p.add_argument('--no-pyarmor', action='store_true', help='Skip PyArmor obfuscation')
    p.add_argument('--no-install', action='store_true', help='Skip install to APPDATA')
    args = p.parse_args()

    if not PYARMOR_EXE.is_file() and not args.no_pyarmor:
        print(f'ERROR: PyArmor not found at {PYARMOR_EXE}')
        print('Either pass --no-pyarmor or ensure PulleyWebApp/.venv312 has pyarmor installed.')
        sys.exit(1)

    build(use_pyarmor=not args.no_pyarmor, do_install=not args.no_install)
