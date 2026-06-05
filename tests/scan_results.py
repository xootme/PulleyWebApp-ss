#!/usr/bin/env python3
"""
Test result scanner — runs tests and summarizes pass/fail.
Parses pytest output to extract failures without verbose output.
Usage: python tests/scan_results.py
"""

import subprocess
import sys
import re
from pathlib import Path

def run_tests():
    """Run pytest and capture output."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Test timeout after 120s", 1

def parse_results(stdout):
    """Parse pytest output for pass/fail summary."""
    lines = stdout.split('\n')

    # Find summary line: "passed", "failed", "error"
    summary_pattern = r'(\d+)\s+(passed|failed|error)'

    passed = 0
    failed = 0
    errors = 0
    failures = []

    for line in lines:
        # Look for FAILED lines
        if 'FAILED' in line:
            # Extract test name
            match = re.search(r'FAILED\s+(.+?)\s+', line)
            if match:
                failures.append(match.group(1))

        # Count from summary line
        if 'passed' in line.lower() or 'failed' in line.lower():
            for match in re.finditer(summary_pattern, line):
                count = int(match.group(1))
                status = match.group(2)
                if status == 'passed':
                    passed = count
                elif status == 'failed':
                    failed = count
                elif status == 'error':
                    errors = count

    return {
        'passed': passed,
        'failed': failed,
        'errors': errors,
        'failures': failures,
        'total': passed + failed + errors
    }

def main():
    print("Running test suite...\n")
    stdout, stderr, code = run_tests()

    results = parse_results(stdout)

    # Print summary
    print("=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)
    print(f"Passed:  {results['passed']}")
    print(f"Failed:  {results['failed']}")
    print(f"Errors:  {results['errors']}")
    print(f"Total:   {results['total']}")
    print("=" * 60)

    # Show failures if any
    if results['failures']:
        print("\nFAILED TESTS:")
        for failure in results['failures']:
            print(f"  [FAIL] {failure}")
        print()

    # Exit code
    if code == 0 and results['failed'] == 0 and results['errors'] == 0:
        print("[PASS] All tests passed!")
        return 0
    else:
        print("[FAIL] Some tests failed")
        if stderr:
            print(f"\nErrors:\n{stderr[:500]}")
        return 1

if __name__ == '__main__':
    sys.exit(main())
