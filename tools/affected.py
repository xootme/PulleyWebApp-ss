"""What the work in progress touches: the test files and browser harnesses to run
while working (tests/suite.py --affected). The whole set runs at night
(tools/nightly_test.py, CCT_Controller's nightly run). The selection is
cct_common.testing.affected (0.25.2, from Bevel Gear Designer's pilot); these are
the pulley app's rules for it:

- TEST_RULES: the page's template reaches every test that fetches the page;
- HARNESS_RULES: what each browser harness (tests/browser/*_ui.js) covers;
- pytest.ini and the requirements reach everything.

The pulley app has one kind of run: its @slow tests are in the default run (no
-m "not slow"), so only the "fast" record is kept (select(fast_only=True)).

The record (what each test runs, when each passed) lives outside the repo:
CCT_TEST_RECORD_DIR, else %LOCALAPPDATA%/cct/test-records/PulleyWebApp.

    .venv314\\Scripts\\python.exe tools/affected.py      (prints the selection, and why)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                            # the vendored cct_common, run as a script
from cct_common.testing import affected as _affected  # noqa: E402

ALL = _affected.ALL
EVERYTHING = ("pytest.ini", "requirements.txt", "requirements-dev.txt")
# a non-Python file the app reads without the tests naming it -> the tests whose source matches
TEST_RULES = [("templates/*", r"""\.get\(f?['"]/[?'"]""")]   # the page: the tests that fetch it

# changed file (glob, repo-relative, /) -> the harnesses it reaches
HARNESS_RULES = [
    ("app.py", ALL), ("templates/index.html", ALL), ("static/style.css", ALL), ("static/cct_*", ALL),
    ("cct_common/*", ALL), ("geometry/pulley_geometry.py", ALL), ("geometry/pulley_validation.py", ALL),
    ("tests/browser/cdp*.js", ALL),
    ("static/three.module.js", ["turn_ui", "download_design_ui"]), ("static/jsm/*", ["turn_ui"]),
    ("geometry/belt_motion.py", ["turn_ui"]), ("geometry/belt_specs.py", ["dims_ui", "turn_ui"]),
    ("geometry/set_screw.py", ["screw_ui", "roundtrip_ui"]), ("geometry/captured_nut_hub.py", ["screw_ui"]),
    ("geometry/nut_counterbore.py", ["screw_ui"]),
    ("geometry/spoke_fit.py", ["roundtrip_ui", "import_ui"]), ("geometry/flange_geometry.py", ["import_ui"]),
    ("exporters/spline_parts.py", ["bore_ui", "download_design_ui"]),
    ("exporters/*", ["download_design_ui"]), ("results.py", ["download_design_ui", "tokens_ui"]),
    ("shared_jobs.py", ["download_design_ui"]), ("bundles.py", ["download_design_ui", "tokens_ui"]),
    ("charging.py", ["tokens_ui", "download_design_ui"]), ("accounts_setup.py", ["tokens_ui", "admin_ui"]),
    ("static/tokens.js", ["tokens_ui", "download_design_ui"]), ("bug_store.py", ["admin_ui"]),
    ("static/*_help.html", ["help_ui"]), ("static/help/*", ["help_ui"]),
]

_affected.configure(ROOT, "PulleyWebApp", test_rules=TEST_RULES, harness_rules=HARNESS_RULES,
                    everything=EVERYTHING, naming_skip={"test_affected.py"})   # it names them all
from cct_common.testing.affected import *  # noqa: E402,F401,F403  (after configure: STATE is the pulley's)

if __name__ == "__main__":
    _affected.main(["--fast-only", *sys.argv[1:]])
