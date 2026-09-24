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

It copies the package as of cct_common's last COMMIT (git archive HEAD),
never its working tree: uncommitted edits there — anyone's work in
progress — must not ride into this repo and on to Render unreviewed, and
the commit recorded in _VENDORED_FROM.txt must describe exactly what was
copied. Uncommitted package files are listed as skipped.

Run this whenever cct_common changes upstream, then commit the result:

    py -3.14 sync_cct_common.py
    git add cct_common/
    git commit -m "Sync cct_common vendored copy"

See web_provisioning.md Step 2 for the deploy-checklist entry.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

SOURCE_REPO = Path(r"C:\Users\cmyer\Documents\cct_common")
SOURCE_PKG = SOURCE_REPO / "cct_common"
DEST_PKG = Path(__file__).resolve().parent / "cct_common"


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=SOURCE_REPO,
                          capture_output=True, check=True).stdout


def _uncommitted_package_files() -> list[str]:
    out = _git("status", "--porcelain", "--", "cct_common").decode()
    return [line[3:] for line in out.splitlines() if line.strip()]


def main() -> None:
    if not SOURCE_PKG.is_dir():
        raise SystemExit(f"Source package not found: {SOURCE_PKG}")

    commit = _git("rev-parse", "HEAD").decode().strip()
    archive = _git("archive", "--format=tar", "HEAD", "cct_common")

    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter="data")
        committed_pkg = Path(tmp) / "cct_common"
        init = (committed_pkg / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.M)
        version = m.group(1) if m else "unknown"

        if DEST_PKG.exists():
            shutil.rmtree(DEST_PKG)
        shutil.copytree(committed_pkg, DEST_PKG,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    (DEST_PKG / "_VENDORED_FROM.txt").write_text(
        f"Vendored from {SOURCE_REPO}\n"
        f"cct_common version: {version}\n"
        f"source commit: {commit}\n"
        f"Run sync_cct_common.py (repo root) to refresh.\n",
        encoding="utf-8",
    )
    print(f"Vendored cct_common {version} (commit {commit[:8]}) into {DEST_PKG}")
    skipped = _uncommitted_package_files()
    if skipped:
        print("Skipped uncommitted changes in cct_common (only the commit was copied):")
        for f in skipped:
            print(f"  {f}")


if __name__ == "__main__":
    main()
