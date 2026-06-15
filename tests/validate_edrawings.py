"""
validate_edrawings.py
Batch-validate STEP files using the eDrawings 2026 COM control.

Uses EModelView.EModelViewControl.26 (in-proc COM, loads EModelView.dll).
Requires pywin32 in the environment:
    pip install pywin32      (already in .venv312)

Usage (run from repo root, using .venv312):
    .venv312\\Scripts\\python.exe tests\\validate_edrawings.py <file.step> [...]
    .venv312\\Scripts\\python.exe tests\\validate_edrawings.py tests\\step_samples\\

Exit code 0 = all passed, 1 = any failed.
"""
import sys
import os
import time
import glob
import argparse
import pythoncom
import win32com.client

# ProgID confirmed on eDrawings 2026 install
EDRAWINGS_PROGID = "EModelView.EModelViewControl.26"
LOAD_TIMEOUT_S   = 15     # seconds to wait for OnFinished/OnFailed event


def validate_step(step_path):
    """
    Open step_path in eDrawings and return (ok: bool, message: str).

    eDrawings loads asynchronously; we pump the Windows message queue
    until OnFinishedLoadingDocument or OnFailedLoadingDocument fires.
    """
    step_path = os.path.abspath(step_path)
    result = {}

    class _Events:
        def OnFinishedLoadingDocument(self, fileName):
            result['ok']  = True
            result['msg'] = f"loaded"

        def OnFailedLoadingDocument(self, fileName, errorCode, errorString):  # noqa: N802
            _ = fileName
            result['ok']  = False
            result['msg'] = f"[err {errorCode}] {errorString}"

    pythoncom.CoInitialize()
    ctrl = None
    try:
        ctrl = win32com.client.DispatchWithEvents(EDRAWINGS_PROGID, _Events)
        ctrl.OpenDoc(step_path, False, False, False, "")

        deadline = time.time() + LOAD_TIMEOUT_S
        while time.time() < deadline:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.05)
            if 'ok' in result:
                break

        if 'ok' not in result:
            return False, f"timeout after {LOAD_TIMEOUT_S} s"

        return result['ok'], result['msg']

    except Exception as e:
        return False, f"COM error: {e}"
    finally:
        if ctrl is not None:
            try:
                ctrl.CloseActiveDoc("")
            except Exception:
                pass
        pythoncom.CoUninitialize()


def collect_files(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += glob.glob(os.path.join(p, "*.step"))
            files += glob.glob(os.path.join(p, "*.stp"))
        elif os.path.isfile(p):
            files.append(p)
        else:
            files += glob.glob(p)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(
        description="Validate STEP files using eDrawings 2026")
    parser.add_argument("paths", nargs="+",
                        help="STEP files or directories to test")
    args = parser.parse_args()

    files = collect_files(args.paths)
    if not files:
        print("No STEP files found.")
        sys.exit(1)

    passed = failed = 0
    for f in files:
        rel = os.path.relpath(f)
        ok, msg = validate_step(f)
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {rel}: {msg}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{passed} passed, {failed} failed  ({len(files)} total)")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
