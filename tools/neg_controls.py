"""Negative controls: break one formula or check at a time; the tests meant to
catch it must fail; then restore. From the repo root, with the test interpreter
(the controls run `python -m pytest` with it, and the STEP tests need OCP):

  C:/Users/cmyer/Documents/PulleyWebApp/.venv312/Scripts/python.exe tools/neg_controls.py
      [name filter ...] [--all] [--whole-suite]

The controls are this file's BREAKS: name -> (file, the text, its broken form).
The runner is cct_common.testing.controls (0.25.0, from Bevel Gear Designer's
pilot): by default only the controls whose check changed since each was last
caught; each on the *tests* that ran what it breaks (tests/suite.py records
them), cheapest first, with -x, the first that fails ending it, the whole suite
as the fallback (SCOPE or MISSED); CCT_TEST_CPUS lanes, each breaking its own
copy of the tree. A filter runs every control whose name holds it; --all runs
them all (tools/nightly_test.py does, every night).

A starter set (2026-10-21): the breaks that matter most, not yet every check."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import affected  # noqa: E402,F401  (configures cct_common.testing.affected with the pulley's rules)
import nightly_test  # noqa: E402
from cct_common.testing import controls  # noqa: E402

_copy_tree = controls.copy_tree


def copy_tree(dst: Path):
    """controls.copy_tree, also in a git worktree (the nightly run's), whose .git is a
    file naming its gitdir, which 0.25.0's copytree refuses: copied as it is, so the
    lane reads that gitdir (its tests only read git). Told to Controller for 0.25.1."""
    root = affected.ROOT
    if (root / ".git").is_dir():
        return _copy_tree(dst)
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root, capture_output=True,
                         check=True)
    for f in sorted(set(out.stdout.decode("utf-8").split("\0")) - {""}):
        if (root / f).is_file():
            (dst / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / f, dst / f)
    shutil.copy2(root / ".git", dst / ".git")
    subprocess.run([sys.executable, "-m", "compileall", "-q", "--invalidation-mode", "checked-hash",
                    "-x", r"[\\/]\.git[\\/]", str(dst)], check=True, capture_output=True)


controls.copy_tree = copy_tree

PG, SE, SF, SS = ("geometry/pulley_geometry.py", "exporters/step_exporter.py", "geometry/spoke_fit.py",
                  "geometry/set_screw.py")
AP, EP = "app.py", "edge_proxy.py"
BREAKS = {  # name: (file, old, new); old must occur exactly once
    # the pulley itself
    "pitch diameter": (PG, "return (numTeeth * pitch_mm) / math.pi", "return (numTeeth * pitch_mm) / (2 * math.pi)"),
    "outside diameter: the pitch line offset": (PG, "return getPitchDiameter(numTeeth, pitch_mm) - 2.0 * offset_mm",
                                                "return getPitchDiameter(numTeeth, pitch_mm) - offset_mm"),
    "teeth from the outside diameter": (PG, "return round((outerDia_mm + 2.0 * offset_mm) * math.pi / pitch_mm)",
                                        "return round(outerDia_mm * math.pi / pitch_mm)"),
    "RPP pitch diameter": (PG, "    PD    = number_of_teeth * pitch / math.pi", "    PD    = number_of_teeth * pitch / 3.0"),
    # hub screws (ADR-013, ADR-018; the hex hole corner-up, owner 2026-10-18)
    "hex screw hole corner-up": (SE, "[(-along, across) for across, along in", "[(across, along) for across, along in"),
    "screw angles on a hex bar": (SE, "hex_bar=sp is not None and sp.kind == 'hex')", "hex_bar=False)"),
    "set screw: a captured nut's clearance hole": (SS, '"diameter": screws.clearance_diameter(size)}, screws.nut(size)',
                                                   '"diameter": screws.get(size).major_diameter}, screws.nut(size)'),
    "set screw: the old captured-nut box": (SS, '        "nut" if args.get(f"{prefix}hub_captured_nut") == "1" else "thread")',
                                            '        "thread")'),
    # spokes (ADR-010)
    "spokes: hub wall round the bore": (SF, "    if s['hub_od'] < bore + 2 * WALL - 1e-9:", "    if False:"),
    # (not "the web between hub and rim": _valid's MIN_WEB test is an early exit, as the void
    # builder refuses a web under 0.5 mm itself; with it gone nothing changes, so no test can fail)
    # the site: charging, the edge, bug reports
    "the STL is charged": (AP, "@app.route('/download/stl')\n@charges.charged('stl')\n", "@app.route('/download/stl')\n"),
    "edge: the secret must match": (EP, "return bool(self.secret) and hmac.compare_digest(got.encode(), self.secret.encode())",
                                    "return bool(got)"),
    "bug reports go to support@": (AP, "_smtp_send('support@cheapcadtools.com'", "_smtp_send('info@cheapcadtools.com'"),
    "bug reports: Reply-To only a single address": (AP, "    if reply_to and _ONE_ADDRESS_RE.match(reply_to):",
                                                    "    if reply_to:"),
    # refused settings: labelled, on the page's field, with a value (geometry/labels.py, 2026-10-21)
    "labels: the fix is a value, not the range's text": ("geometry/labels.py", '    return f"{v:g}"',
                                                         '    return f"{lo:g} to {hi:g}"'),
    "labels: the refusal names the page's field": ("geometry/labels.py", "    return SETTINGS[base][1].format(n=n)",
                                                   "    return key"),
    "labels: pulley 2's settings only in a drive": ("geometry/labels.py",
                                                    '    if prefix and not _on(args.get("dual")):\n        return False',
                                                    "    if False:\n        return False"),
    "labels: the agent API names the label": ("geometry/labels.py",
                                              'return f"{label(m.group(1))} ({m.group(1)}) " if base in SETTINGS else m.group(0)',
                                              "return m.group(0)"),
    "labels: downloads are checked": (AP, "(path.startswith('/download/') and not path.startswith('/download/result/'))",
                                      "False"),
    "labels: the page keeps the refusal's fix": ("templates/index.html", "fix: data.field && data.fix != null",
                                                 "fix: false && data.fix != null"),
    # the test tooling (cct_common.testing)
    "tests: every suite run can record": ("pytest.ini", " -p cct_common.testing.record", ""),
    "tests: the caches cleared exist": ("pytest.ini", "exporters.step_worker_ss:_versions", "exporters.step_worker_ss:_nothing"),
    "tests: the page's template reaches its tests": ("tools/affected.py", 'TEST_RULES = [("templates/*",',
                                                     'TEST_RULES = [("nothing/*",'),
    "tests: .deps folded into one key": ("tools/affected.py", "        if f.startswith(FOLDED):", "        if False:"),
    "release: +0.0.1": ("tools/release.py", "    return f'{a}.{b}.{c + 1}'", "    return f'{a}.{b + 1}.{c}'"),
}


if __name__ == "__main__":
    os.environ.setdefault("QUEUE_DISABLED", "1")                  # what the day runs and the nightly set
    os.environ.setdefault("SMALL_STEP_BIN", nightly_test.SMALL_STEP)
    sys.exit(controls.main(BREAKS, sys.argv[1:]))
