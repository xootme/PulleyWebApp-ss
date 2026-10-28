"""The drift check (cct_common 0.26.0): an app's vendored cct_common must be
the released copy of its version, file for file.

An app's test suite calls it, so its nightly fails on a drifted copy:

    from cct_common.testing.vendored import check_vendored

    def test_vendored_cct_common_is_released():
        check_vendored()

It checks the cct_common that the app imports (its vendored copy) twice:
- against the copy's own RELEASE.json (cct_common.release), which is always
  there in a released copy, and
- against the cct_common repo's tag v<version>, when the repo is on this
  machine: CCT_COMMON_REPO, else ~/Documents/cct_common. Without it the
  check says so and relies on RELEASE.json.

A failure says what differs and how to fix it: re-run the app's
sync_cct_common.py from a released commit, and never edit the copy in place.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..release import archive_hash, content_hash, package_version, read_manifest

FIX = "re-run sync_cct_common.py from a released cct_common (a v<version> tag); don't edit the copy"


class VendoredDrift(AssertionError):
    """The vendored cct_common is not the released copy of its version."""


def default_repo():
    """The cct_common repo to check tags in, or None when there's none here."""
    env = os.environ.get("CCT_COMMON_REPO")
    repo = Path(env) if env else Path.home() / "Documents" / "cct_common"
    return repo if (repo / ".git").exists() else None


def tag_hash(repo, version: str):
    """The package hash at tag v<version> in `repo`, or None when there's no such tag."""
    r = subprocess.run(["git", "archive", "--format=tar", f"v{version}", "cct_common"],
                       cwd=repo, capture_output=True)
    return archive_hash(r.stdout) if r.returncode == 0 else None


def vendored_problems(package_dir=None, repo="default") -> tuple:
    """(problems, notes) for a cct_common package directory (by default the
    one this process imports). Problems mean the copy isn't released; notes
    say what couldn't be checked."""
    if package_dir is None:
        import cct_common
        package_dir = Path(cct_common.__file__).resolve().parent
    package_dir = Path(package_dir)
    version, digest = package_version(package_dir), content_hash(package_dir)
    problems, notes = [], []
    m = read_manifest(package_dir)
    if not m:
        problems.append(f"{package_dir} ({version}) has no RELEASE.json: "
                        "a copy from before 0.26.0, or not a release")
    elif m.get("version") != version:
        problems.append(f"RELEASE.json says {m.get('version')} but __init__.py says {version}")
    elif m.get("hash") != digest:
        problems.append(f"{package_dir} differs from the {version} release "
                        f"(hash {digest[:12]}, released {str(m.get('hash'))[:12]})")
    repo = default_repo() if repo == "default" else repo
    if repo is None:
        notes.append("no cct_common repo here (CCT_COMMON_REPO): checked against RELEASE.json only")
    else:
        released = tag_hash(repo, version)
        if released is None:
            problems.append(f"{repo} has no tag v{version}: {version} was never released")
        elif released != digest:
            problems.append(f"{package_dir} differs from tag v{version} in {repo} "
                            f"(hash {digest[:12]}, tag {released[:12]})")
    return problems, notes


def check_vendored(package_dir=None, repo="default") -> list:
    """Raise VendoredDrift when the copy isn't the released copy of its
    version; else return the notes (what couldn't be checked)."""
    problems, notes = vendored_problems(package_dir, repo)
    if problems:
        raise VendoredDrift("vendored cct_common is not a released copy:\n  - "
                            + "\n  - ".join(problems) + f"\nFix: {FIX}")
    return notes
