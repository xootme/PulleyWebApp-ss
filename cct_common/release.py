"""release.py — which cct_common this is, and whether it is a released copy
(0.26.0).

Every app vendors cct_common: its sync_cct_common.py copies the package
directory in. A copy says what it is by its version and a hash of its files,
so a copy edited in place, or synced from a commit between releases, shows:

    from cct_common.release import health_fields
    @app.route("/api/health")
    def health():
        return jsonify(status="ok", version=APP_VERSION, **health_fields())

    -> {"cct_common": "0.26.0", "cct_common_hash": "3f9a…",
        "cct_common_released": true}

The hash is sha256 over every file in the package (paths sorted, line endings
read as LF, so a Windows checkout and the Cloud Run build agree), leaving out
__pycache__, *.pyc, the sync script's _VENDORED_FROM.txt and RELEASE.json.

RELEASE.json, next to this file, is the release's own record: the version and
the hash it was released with. "Released" means the copy's version is
RELEASE.json's and its hash is too. A release stamps it before its commit:

    python -m cct_common.release --stamp      # in the cct_common repo

and tags that commit v<version>. cct_common's own tests fail while the package
differs from RELEASE.json, so a change can't be committed unstamped.
cct_common.testing.vendored checks an app's copy against the tag as well.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
MANIFEST = "RELEASE.json"
# Written by the apps' sync scripts, not part of a release.
_SKIP_FILES = {MANIFEST, "_VENDORED_FROM.txt"}


def _counted(rel: str) -> bool:
    parts = rel.split("/")
    return not (parts[-1] in _SKIP_FILES or parts[-1].endswith(".pyc")
                or any(p == "__pycache__" or p.startswith(".") for p in parts))


def hash_files(files) -> str:
    """sha256 over (relative path, contents) pairs; contents read with LF
    line endings. `files` yields (posix path relative to the package, bytes)."""
    h = hashlib.sha256()
    for rel, data in sorted((r, d) for r, d in files if _counted(r)):
        h.update(rel.encode("utf-8") + b"\0" + data.replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def content_hash(package_dir=None) -> str:
    """The hash of a cct_common package directory (this one by default)."""
    root = Path(package_dir or PACKAGE_DIR)
    return hash_files((p.relative_to(root).as_posix(), p.read_bytes())
                      for p in root.rglob("*") if p.is_file())


def archive_hash(tar_bytes: bytes, prefix: str = "cct_common/") -> str:
    """The hash of the package inside a `git archive --format=tar <ref>
    cct_common` — what a released tag holds."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        return hash_files((m.name[len(prefix):], tf.extractfile(m).read())
                          for m in tf.getmembers() if m.isfile() and m.name.startswith(prefix))


def read_manifest(package_dir=None) -> dict:
    """RELEASE.json's {version, hash}, or {} when the copy has none (any copy
    from before 0.26.0)."""
    try:
        return json.loads((Path(package_dir or PACKAGE_DIR) / MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def package_version(package_dir=None) -> str:
    """__version__ as the package's __init__.py states it, read without
    importing (the copy checked may not be the one imported)."""
    text = (Path(package_dir or PACKAGE_DIR) / "__init__.py").read_text("utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else ""


def describe(package_dir=None) -> dict:
    """{version, hash, released} for a package directory (this one by default)."""
    version, digest = package_version(package_dir), content_hash(package_dir)
    m = read_manifest(package_dir)
    return {"version": version, "hash": digest,
            "released": bool(m) and m.get("version") == version and m.get("hash") == digest}


@lru_cache(maxsize=1)
def version_info() -> dict:
    """This copy's {version, hash, released}, worked out once per process."""
    return describe()


def health_fields() -> dict:
    """The three fields an app's /api/health adds (see the module docstring)."""
    v = version_info()
    return {"cct_common": v["version"], "cct_common_hash": v["hash"],
            "cct_common_released": v["released"]}


def stamp(package_dir=None) -> dict:
    """Write RELEASE.json for the package as it stands; returns what it wrote."""
    root = Path(package_dir or PACKAGE_DIR)
    record = {"version": package_version(root), "hash": content_hash(root)}
    (root / MANIFEST).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="cct_common's version and content hash.")
    ap.add_argument("--stamp", action="store_true", help="write RELEASE.json for this package")
    ap.add_argument("package_dir", nargs="?", help="a cct_common package directory (default: this one)")
    args = ap.parse_args(argv)
    if args.stamp:
        print(json.dumps(stamp(args.package_dir)))
    else:
        print(json.dumps(describe(args.package_dir)))


if __name__ == "__main__":
    main()
