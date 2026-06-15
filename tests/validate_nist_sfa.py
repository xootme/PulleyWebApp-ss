"""
validate_nist_sfa.py
Batch-validate STEP files using the NIST STEP File Analyzer (SFA) 5.45.

Runs sfa-cl.exe in syntax-check mode and parses stdout for errors/warnings.
Exit code is always 0 from sfa-cl regardless of errors, so we parse output.

Prerequisites:
  - tools/sfa/sfa-cl.exe  (already installed in this repo)
  - IFCsvr toolkit installed (run sfa-cl.exe once to auto-install)

Usage:
    python tests/validate_nist_sfa.py <file.step> [file2.step ...]
    python tests/validate_nist_sfa.py tests/step_samples/

Exit code 0 = all passed, 1 = any warnings or errors found.
"""
import sys
import os
import subprocess
import glob
import argparse

# Path to sfa-cl.exe relative to this file's repo root
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
SFA_EXE = os.path.join(_REPO_ROOT, "tools", "sfa", "sfa-cl.exe")


def validate_step(step_path):
    """
    Run NIST SFA syntax check on step_path.
    Returns (ok: bool, lines: list[str]) where lines are the relevant output.
    """
    if not os.path.isfile(SFA_EXE):
        return False, [f"sfa-cl.exe not found at: {SFA_EXE}"]

    step_path = os.path.abspath(step_path)
    try:
        result = subprocess.run(
            [SFA_EXE, step_path, "syntax", "noopen", "nolog"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, ["timeout after 60 s"]
    except OSError as e:
        return False, [f"could not launch sfa-cl.exe: {e}"]

    output = result.stdout + result.stderr
    lines = [l.rstrip() for l in output.splitlines()]

    # Classify: pass = "No syntax errors or warnings"; fail = any "**" line
    problem_lines = [l for l in lines if "**" in l]
    ok_line = any("No syntax errors or warnings" in l for l in lines)

    if ok_line and not problem_lines:
        return True, ["No syntax errors or warnings"]

    if not problem_lines:
        # No "** " but also no clean pass line — inconclusive
        summary = [l for l in lines if l.strip() and "---" not in l
                   and "NIST STEP" not in l and "Updated" not in l]
        return False, summary or ["unexpected output — no clean result"]

    return False, problem_lines


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
        description="Validate STEP files using NIST SFA 5.45 syntax checker")
    parser.add_argument("paths", nargs="+",
                        help="STEP files or directories to validate")
    args = parser.parse_args()

    files = collect_files(args.paths)
    if not files:
        print("No STEP files found.")
        sys.exit(1)

    passed = failed = 0
    for f in files:
        rel = os.path.relpath(f)
        ok, detail = validate_step(f)
        tag = "PASS" if ok else "FAIL"
        msg = "; ".join(detail)
        print(f"  [{tag}] {rel}: {msg}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{passed} passed, {failed} failed  ({len(files)} total)")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
