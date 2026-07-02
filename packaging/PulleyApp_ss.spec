# PulleyApp_ss.spec — PyInstaller build spec for PulleyWebApp-ss desktop build.
#
# Key differences from PulleyApp.spec (original PulleyWebApp):
#   - small_step.exe is bundled as a binary (replaces cadquery/OCP for STEP generation).
#   - trimesh, shapely, scipy, numpy, manifold3d ARE included (needed for STL/3D preview).
#   - cadquery, OCP, casadi are excluded (not used in the ss variant).
#   - Entry point is build/obfuscated_ss/launcher_ss.py.
#
# Run via packaging/build_test.py (from the repo root).

import os, glob
from PyInstaller.utils.hooks import collect_submodules

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = os.path.abspath('.')
OBFUSCATED  = os.path.join(ROOT, 'build', 'obfuscated_ss')
ENTRY_POINT = os.path.join(OBFUSCATED, 'launcher_ss.py')

_SS_CANDIDATES = [
    # Sibling directory (dev layout: PulleyWebApp-ss/ and small_step/ side by side)
    os.path.join(os.path.dirname(ROOT), 'small_step', 'target',
                 'x86_64-pc-windows-gnu', 'release', 'small_step.exe'),
    os.path.join(os.path.dirname(ROOT), 'small_step', 'target',
                 'release', 'small_step.exe'),
    # Subdirectory layout
    os.path.join(ROOT, 'small_step', 'target',
                 'x86_64-pc-windows-gnu', 'release', 'small_step.exe'),
    os.path.join(ROOT, 'small_step', 'target', 'release', 'small_step.exe'),
]
SS_BIN = next((p for p in _SS_CANDIDATES if os.path.isfile(p)), None)

# ── Data files ────────────────────────────────────────────────────────────────
datas = [
    (os.path.join(ROOT, 'templates'), 'templates'),
    (os.path.join(ROOT, 'static'),    'static'),
]

# PyArmor runtime folder
for rt in glob.glob(os.path.join(OBFUSCATED, 'pyarmor_runtime_*')):
    datas.append((rt, os.path.basename(rt)))

# Obfuscated .py source files (not the entry point — it's in scripts)
for py_file in glob.glob(os.path.join(OBFUSCATED, '**', '*.py'), recursive=True):
    if 'pyarmor_runtime' not in py_file and os.path.basename(py_file) != 'launcher_ss.py':
        dest = os.path.dirname(os.path.relpath(py_file, OBFUSCATED))
        datas.append((py_file, dest if dest else '.'))

# ── Binaries ──────────────────────────────────────────────────────────────────
binaries = []
if SS_BIN:
    binaries.append((SS_BIN, '.'))   # lands at root of _MEIPASS as small_step.exe
    print(f'Bundling small_step.exe from: {SS_BIN}')
else:
    import warnings
    warnings.warn('small_step.exe not found in any candidate path — STEP generation will fail')

# ── Hidden imports ─────────────────────────────────────────────────────────────
hidden_imports = [
    'flask', 'flask_compress', 'jinja2', 'werkzeug',
    'cairosvg', 'ezdxf', 'PIL',
    'sendgrid',
    'trimesh', 'trimesh.exchange', 'trimesh.primitives',
    'shapely', 'shapely.geometry',
    'manifold3d',
    'fileinput', 'tokenize', 'token',
    'tkinter', 'tkinter.ttk', 'tkinter.messagebox',
    'logging.handlers',
]
hidden_imports += collect_submodules('werkzeug')
hidden_imports += collect_submodules('flask')
hidden_imports += collect_submodules('jinja2')
hidden_imports += collect_submodules('PIL')
hidden_imports += collect_submodules('trimesh')
hidden_imports += collect_submodules('shapely')

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [ENTRY_POINT],
    pathex=[OBFUSCATED, ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'IPython', 'notebook',
        'cadquery', 'OCP', 'casadi',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

_icon = os.path.join(ROOT, 'static', 'favicon.ico')
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PulleyApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=_icon if os.path.exists(_icon) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PulleyApp',
)
