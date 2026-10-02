"""
charging.py — charges tokens for exports (ADR-008). Off unless accounts are
on (TOKENS_ENABLED=1); with them off every download behaves as before.

The charging itself is cct_common's (cct_common.charging — moved there with
the Download window so every CCT app charges the same way; the 2026-10-01 UI
audit, row 37). This module is what is Timing Pulley Generator's own: how
pulley 2 names its parameters, the flange routes' flat names, and the
background STEP routes — and the one `charges` the routes are decorated with.

    from charging import charges

    @app.route('/download/stl')
    @charges.charged('stl')
    def download_stl(): ...

    charges.attach(app, accounts_state)   # once, after init_accounts()
"""
from __future__ import annotations

from cct_common import charging as _c
from cct_common.charging import (  # noqa: F401  (the names this app's code and tests use)
    DESIGN_TTL_S, _EMPTY, _ExportFailed, _norm,
)

# Request keys that select or deliver a part rather than describe the design.
ROUTE_ONLY_KEYS = _c.ROUTE_ONLY_KEYS | frozenset({"flange_which", "part"})   # part: a spline part (shaft / washer)

# The flange routes send some hub values under flat names.
ALIASES = {
    "flat_depth": "hub_flat_depth",
    "keyway_w": "hub_keyway_w",
    "keyway_h": "hub_keyway_h",
}

# The background STEP routes' formats (begin_async users).
FORMATS = {"api_download_step_async": "step", "api_download_all_step_async": "step"}


def canonical_design(design: dict) -> dict:
    return _c.canonical_design(design, ROUTE_ONLY_KEYS)


def design_matches(route_params: dict, design: dict) -> bool:
    """True if every design parameter this download uses is part of the
    registered design, under its own name, its pulley-2 name, or the flange
    routes' flat alias."""
    return _c.design_matches(route_params, design, part2_prefix="p2_",
                             aliases=ALIASES, route_only=ROUTE_ONLY_KEYS)


class DesignStore(_c.DesignStore):
    """Registered designs, kept 30 days, in the accounts database file."""

    def __init__(self, path: str):
        super().__init__(path, ROUTE_ONLY_KEYS)


class Charges(_c.Charges):
    """cct_common's Charges with this app's names."""

    def __init__(self):
        super().__init__(part2_prefix="p2_", aliases=ALIASES,
                         route_only_keys=ROUTE_ONLY_KEYS, formats=FORMATS)


charges = Charges()
