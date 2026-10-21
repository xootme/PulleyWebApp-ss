"""The pulley app's rules for cct_common.testing.affected (tools/affected.py); the
shared selection's own tests are cct_common's."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import affected  # noqa: E402

PAGE_TESTS = ["tests/test_brand.py", "tests/test_cct_theme.py", "tests/test_session_queue.py",
              "tests/test_set_screw_holes.py"]


def test_the_page_template_reaches_the_tests_that_fetch_it():
    hit = affected.tests_naming(["templates/index.html"])
    assert set(PAGE_TESTS) <= set(hit)


def test_harness_rules():
    every = affected.all_harnesses()
    assert affected.harnesses_for(["app.py"]) == every
    assert affected.harnesses_for(["geometry/set_screw.py"]) == ["roundtrip_ui", "screw_ui"]
    assert affected.harnesses_for(["static/Hub_help.html"]) == ["help_ui"]
    assert affected.harnesses_for(["README.md"]) == []
    assert set(affected.harnesses_for(["tests/browser/turn_ui.js"])) == {"turn_ui"}


def test_deps_are_one_key_that_reaches_everything():
    snap = affected.snapshot()
    assert affected.FOLDED in snap and affected.FOLDED in affected.EVERYTHING
    assert not [k for k in snap if k.startswith(affected.FOLDED) and k != affected.FOLDED]
    assert "app.py" in snap and any(k.startswith("app.py::") for k in snap)


def test_the_shared_code_uses_this_snapshot():
    from cct_common.testing import affected as shared
    assert shared.snapshot is affected.snapshot and shared.APP == "PulleyWebApp"
