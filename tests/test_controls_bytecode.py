"""test_controls_bytecode.py — the fleet note of 2026-10-30 (CCTCommon): a
mutate-and-restore control can be fooled by a stale .pyc when the break is the
same length as the line and is undone within the second. This app's controls
(tools/neg_controls.py) run on cct_common.testing.controls, which copies each
lane without __pycache__ (git's files only), compiles it with checked-hash
bytecode (the source's hash, not its size and time) and runs every lane with
PYTHONDONTWRITEBYTECODE=1. These pin that the vendored runner still does."""
import inspect

from cct_common.testing import controls


def test_lanes_are_copied_without_bytecode_and_checked_by_hash():
    src = inspect.getsource(controls.copy_tree)
    assert '"ls-files", "-co", "--exclude-standard"' in src         # __pycache__ is ignored: never copied
    assert '"--invalidation-mode", "checked-hash"' in src


def test_the_runs_write_no_bytecode():
    assert '"PYTHONDONTWRITEBYTECODE": "1"' in inspect.getsource(controls)


def test_this_apps_controls_use_that_runner():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / 'tools' / 'neg_controls.py').read_text(encoding='utf-8')
    assert 'from cct_common.testing import controls' in text and 'controls.main(BREAKS' in text
