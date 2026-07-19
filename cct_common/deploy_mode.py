"""
deploy_mode.py — shared dev/live gate for cct_common's optional capabilities.

Every capability that behaves differently on a hosted/production deployment
than on a local dev machine (bug-report GitHub filing, the queue/session
system, licensing, ...) checks this instead of reinventing its own
credential-presence heuristic. Explicit opt-in only: a stray API key left
over in a dev shell must never silently enable production behavior.

    from cct_common.deploy_mode import is_live

    if is_live("CCT_QUEUE_MODE"):
        ...

Resolution order for is_live(env_var, mode):
    1. the explicit `mode` argument, if given ("live" -> True, else False)
    2. the named env_var, if set ("live" -> True, else False)
    3. the umbrella CCT_MODE env var, if set ("live" -> True, else False)
    4. default: False (dev)

A capability-specific env var always wins over the umbrella one, so a
single CCT_MODE=live flips everything into live mode for a normal
deployment, while an individual CCT_XXX_MODE=dev can still hold one
specific capability back during a staged rollout (and vice versa).
"""
from __future__ import annotations

import os


def is_live(env_var: str = "CCT_MODE", mode: str | None = None) -> bool:
    if mode is not None:
        return mode == "live"
    val = os.environ.get(env_var)
    if val is not None:
        return val == "live"
    if env_var != "CCT_MODE":
        umbrella = os.environ.get("CCT_MODE")
        if umbrella is not None:
            return umbrella == "live"
    return False
