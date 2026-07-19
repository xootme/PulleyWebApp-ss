"""
sync_cct_common.py — vendor the shared cct_common package into this repo.

cct_common (C:\\Users\\cmyer\\Documents\\cct_common) is a private GitHub repo, so it
can't be `pip install`'d from a git URL on Render, and Render's build container
never has a sibling checkout for an editable path install either — the same
constraint documented in web_provisioning.md for the small_step binary, solved
there by committing a build artifact instead of relying on a clone. This script
applies the same fix for cct_common: copy its package directory straight into
this repo so both local dev and Render import the one vendored copy, with no
install step (a package directory next to app.py is on sys.path by default).

Run this whenever cct_common changes upstream, then commit the result:

    py -3.14 sync_cct_common.py
    git add cct_common/
    git commit -m "Sync cct_common vendored copy"

See web_provisioning.md Step 2 for the deploy-checklist entry.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SOURCE_REPO = Path(r"C:\Users\cmyer\Documents\cct_common")
SOURCE_PKG = SOURCE_REPO / "cct_common"
DEST_PKG = Path(__file__).resolve().parent / "cct_common"


def _source_commit_hash() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SOURCE_REPO,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _source_version() -> str:
    sys.path.insert(0, str(SOURCE_REPO))
    try:
        import cct_common as _cc
        return _cc.__version__
    finally:
        sys.path.pop(0)


def main() -> None:
    if not SOURCE_PKG.is_dir():
        raise SystemExit(f"Source package not found: {SOURCE_PKG}")

    if DEST_PKG.exists():
        shutil.rmtree(DEST_PKG)
    shutil.copytree(SOURCE_PKG, DEST_PKG,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    commit = _source_commit_hash()
    version = _source_version()
    (DEST_PKG / "_VENDORED_FROM.txt").write_text(
        f"Vendored from {SOURCE_REPO}\n"
        f"cct_common version: {version}\n"
        f"source commit: {commit}\n"
        f"Run sync_cct_common.py (repo root) to refresh.\n",
        encoding="utf-8",
    )
    print(f"Vendored cct_common {version} (commit {commit[:8]}) into {DEST_PKG}")


if __name__ == "__main__":
    main()
