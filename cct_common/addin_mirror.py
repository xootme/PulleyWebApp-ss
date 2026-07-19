"""
addin_mirror.py — mirror a download into connected CAD add-ins' watch
folders, so a desktop add-in (Fusion, SolidWorks, FreeCAD, ...) auto-
imports the file the instant it's downloaded, instead of the user
manually opening it from their Downloads folder.

Add-ins write their connection flag and watch directory to a shared
config file (default `%APPDATA%/CheapCADTools/config.json`) when they
start.

    from cct_common.addin_mirror import mirror_to_addins

    mirrored = mirror_to_addins(step_bytes, "part.step")
    if mirrored:
        ...suppress the redundant browser download...

No Flask/app-framework dependency — pure filesystem + JSON config.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "CheapCADTools", "config.json")

# (connected-flag key, watch-directory key) per add-in, in the shared config file.
DEFAULT_TARGETS = (
    ("fusion_connected", "fusion_watch_dir"),
    ("solidworks_connected", "solidworks_watch_dir"),
    ("freecad_connected", "freecad_watch_dir"),
)


def mirror_to_addins(content: bytes, filename: str, *, config_path=None,
                     targets=DEFAULT_TARGETS, skip: bool = False) -> bool:
    """Copy `content` into every connected add-in's watch folder.

    Returns True if the file was written to at least one watch folder, so
    the caller can suppress the browser download (avoids a duplicate
    landing in the user's Downloads folder alongside the mirrored copy).

    `skip` lets the caller hold mirroring back under test — a CAD add-in
    left connected on a dev machine would otherwise turn every download
    route into a no-browser-download response and fail download tests.
    """
    if skip:
        return False
    config_path = config_path or DEFAULT_CONFIG_PATH
    try:
        if not os.path.exists(config_path):
            return False
        with open(config_path) as f:
            cfg = json.load(f)
    except Exception as e:
        logger.error("mirror: could not read config: %s", e)
        return False

    mirrored = False
    for connected_key, dir_key in targets:
        watch_dir = cfg.get(dir_key)
        if not (cfg.get(connected_key) and watch_dir):
            continue
        try:
            os.makedirs(watch_dir, exist_ok=True)
            with open(os.path.join(watch_dir, filename), "wb") as f:
                f.write(content)
            mirrored = True
        except Exception as e:
            logger.error("mirror to %s failed for %s: %s", dir_key, filename, e)
    return mirrored
