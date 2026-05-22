# PyInstaller spec for the CCT SolidWorks Listener tray app
# Build: pyinstaller SolidWorksListener.spec
# Output: dist/SolidWorksListener/SolidWorksListener.exe

a = Analysis(
    ['listener.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pystray._win32',
        'win32com.client',
        'pythoncom',
        'win32api',
        'watchdog.observers.winapi',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SolidWorksListener',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no console window — tray only
    windowed=True,
    icon=None,              # replace with .ico path if available
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SolidWorksListener',
)
