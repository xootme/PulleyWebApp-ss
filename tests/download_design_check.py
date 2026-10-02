"""download_design_check.py — does every file the Download window asks for
belong to the design the page registers?

The zip builder (bundles.py) refuses a file unless each of its parameters is a
setting of the registered design (charging.design_matches): a value the
design doesn't have — like the spline routes' `part` before 2026-10-02 (bug
30ae49ecd8) — fails every zip of that kind of design. This checks recorded
cases with the app's own rule, so there is no second copy of it to drift.

A case is what the page sends: {"name", "design": cctFullDesign(),
"files": [{"path", "params"}, ...]} — written by tests/browser/download_design_ui.js
(tests/data/download_window_files.json) and read by tests/test_download_design.py.

    python tests/download_design_check.py < cases.json     (exit 1 on a problem)
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def problems(cases: list) -> list[str]:
    """One line per file that the zip builder would refuse, naming the
    parameters the registered design doesn't have (or has otherwise)."""
    from charging import canonical_design, design_matches
    out = []
    for case in cases:
        design = canonical_design(case["design"])
        for f in case["files"]:
            params = {str(k): str(v) for k, v in f["params"].items()}
            if design_matches(params, design):
                continue
            bad = sorted(k for k, v in params.items() if not design_matches({k: v}, design))
            shown = ", ".join(f"{k}={params[k]!r} (design: {design.get(k, design.get('p2_' + k, '—'))!r})"
                              for k in bad)
            out.append(f"{case['name']}: {f['path']} — {shown}")
    return out


if __name__ == "__main__":
    found = problems(json.load(sys.stdin))
    for line in found:
        print(line)
    print(f"{len(found)} file(s) the zip builder would refuse")
    sys.exit(1 if found else 0)
