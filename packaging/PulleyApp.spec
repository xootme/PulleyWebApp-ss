# PulleyApp.spec — PyInstaller build spec for the Pulley App desktop build.
#
# Heavy scientific deps (cadquery, OCP, trimesh, shapely, scipy, numpy,
# manifold3d, casadi) are NOT bundled here — they live in the shared
# CheapCADTools runtime installed separately by the Fusion addin.
#
# Output: dist/PulleyApp/PulleyApp.exe  (+ supporting files in same folder)

import os, glob
from PyInstaller.utils.hooks import collect_submodules

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = os.path.abspath('.')
OBFUSCATED  = os.path.join(ROOT, 'build', 'obfuscated')
ENTRY_POINT = os.path.join(OBFUSCATED, 'launcher.py')

# ── Data files ────────────────────────────────────────────────────────────────
datas = [
    (os.path.join(ROOT, 'templates'), 'templates'),
    (os.path.join(ROOT, 'static'),    'static'),
]

# PyArmor runtime folder
for rt in glob.glob(os.path.join(OBFUSCATED, 'pyarmor_runtime_*')):
    datas.append((rt, os.path.basename(rt)))

# Obfuscated .py source files as data (PyInstaller can't analyse PyArmor bytecode)
for py_file in glob.glob(os.path.join(OBFUSCATED, '**', '*.py'), recursive=True):
    if 'pyarmor_runtime' not in py_file and os.path.basename(py_file) != 'launcher.py':
        dest = os.path.dirname(os.path.relpath(py_file, OBFUSCATED))
        datas.append((py_file, dest if dest else '.'))

# ── Hidden imports (lightweight only — heavy deps come from runtime) ───────────
hidden_imports = [
    'flask', 'flask_compress', 'jinja2', 'werkzeug',
    'cairosvg', 'ezdxf', 'PIL',
    'sendgrid',
    # stdlib modules pulled in indirectly by scipy/trimesh from the shared runtime
    'fileinput', 'tokenize', 'token',
    # tkinter — used by the licence activation dialog in launcher.py
    'tkinter', 'tkinter.ttk', 'tkinter.messagebox',
]
hidden_imports += collect_submodules('werkzeug')
hidden_imports += collect_submodules('flask')
hidden_imports += collect_submodules('jinja2')
hidden_imports += collect_submodules('PIL')

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [ENTRY_POINT],
    pathex=[OBFUSCATED, ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'IPython', 'notebook',
        'cadquery', 'OCP', 'trimesh', 'shapely', 'scipy', 'numpy',
        'manifold3d', 'casadi',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

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
    icon=os.path.join(ROOT, 'static', 'favicon.ico') if os.path.exists(
        os.path.join(ROOT, 'static', 'favicon.ico')) else None,
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
