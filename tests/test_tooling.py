"""The test tooling's own settings: pytest.ini loads the shared recorder and names
caches that exist, nightly.json runs what it should, and the release script's
version rules hold (tools/release.py; its deploys are checked by its --dry-run and
--smoke, never here). Kept out of test_affected.py, which names every file and so
is skipped when a non-Python file's tests are looked up (cct_common.testing.affected
naming_skip): a control on pytest.ini needs a test here to catch it."""
import configparser
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _ini():
    cp = configparser.ConfigParser()
    cp.read(ROOT / "pytest.ini", encoding="utf-8")
    return cp["pytest"]


def test_every_suite_run_can_record():
    assert "-p cct_common.testing.record" in _ini()["addopts"]


def test_the_listed_caches_are_dicts():
    specs = _ini()["cct_test_caches"].split()
    assert specs
    for spec in specs:
        module, _, attr = spec.partition(":")
        assert isinstance(getattr(importlib.import_module(module), attr), dict), spec


def test_nightly_runs_the_suite_and_the_release():
    cfg = json.loads((ROOT / "nightly.json").read_text(encoding="utf-8"))
    assert cfg["test"][1:] == ["{worktree}/tools/nightly_test.py", "--keep-logs", "{repo}"]
    assert cfg["release"][1:] == ["{worktree}/tools/release.py"]


def test_the_night_runs_every_browser_harness():
    """The owner, 2026-10-25: every harness, every night (dims_ui's regression sat live
    10-08 to 10-23 with none run); a new harness file joins tools/harnesses.py's list."""
    import harnesses
    files = {p.stem for p in (ROOT / "tests" / "browser").glob("*.js")}
    assert files == set(harnesses.HARNESSES) - {"tokens_off"}
    assert "tokens_off" in harnesses.HARNESSES                  # tokens_ui.js with tokens off too
    assert "'tools' / 'harnesses.py'" in (ROOT / "tools" / "nightly_test.py").read_text(encoding="utf-8")


def test_the_release_version(monkeypatch):
    import release
    monkeypatch.delenv("CCT_RELEASE_VERSION", raising=False)
    assert release.next_version("2.0.28") == "2.0.29"
    monkeypatch.setenv("CCT_RELEASE_VERSION", "2.1.0")
    assert release.next_version("2.0.28") == "2.1.0"
    for bad in ("2.0.28", "2.0.3", "2.1"):
        monkeypatch.setenv("CCT_RELEASE_VERSION", bad)
        with pytest.raises(release.Fail):
            release.next_version("2.0.28")


def test_the_release_bumps_both_places(tmp_path, monkeypatch):
    import release
    for path, _ in release.VERSION_FILES:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes((ROOT / path).read_bytes())
    monkeypatch.setattr(release, "ROOT", tmp_path)
    release.bump("9.9.9")
    assert release.current_version() == "9.9.9"
    for path, _ in release.VERSION_FILES:
        assert b"9.9.9" in (tmp_path / path).read_bytes(), path
