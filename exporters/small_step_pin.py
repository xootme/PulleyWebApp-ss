"""The small_step Windows build this repo's tests, fuzzers, night run and dev server use: SmallStep's
versioned exe (target/windows/<version>/, one folder per version, never rebuilt over), pinned by
path and sha256 (campaign 2026-11-17-small-step-windows-pin; tests/test_small_step_windows_pin.py).
Before it, they ran the one shared exe SmallStep replaces at each build, and a rebuild changed
another app's results mid-gate (2026-11-17). Production runs bin/small_step_linux (Dockerfile).

To bump: put the new version and the sha256 from its folder's small_step.exe.sha256 in PIN."""
from pathlib import Path

PIN = ('0.25.0', '6d493f1e1052290723316760a5489994ab16cc2a307738489b5ad724c6380c19')   # (version, sha256)
VERSION, SHA256 = PIN
WINDOWS_DIR = Path(r'C:\Users\cmyer\Documents\small_step\target\windows')
EXE = str(WINDOWS_DIR / VERSION / 'small_step.exe')
