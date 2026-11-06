"""
autofix.py — up to three Auto-fix options for a problem (0.58.0; the owner,
2026-11-05: "when possible provide up to three options to auto fix a problem
(it gives options without overwhelming)", and CCTCommon's proposal, every
choice as recommended: Controller campaigns/2026-11-05-autofix-up-to-three-options.md).

An option is the shape every app's `fix` already has:

    {"set": {field: value}, "changes": ["Hub OD: 20 → 26 (…)"], "params": {name: value},
     "clears": [...], "remaining": [...]}          # clears / remaining: optional

- fix_options(candidates, recheck): the app's candidates, in its order (the
  one it recommends first, then the smallest change), less the empty and the
  repeated ones (the same set) and those recheck(set) says don't clear what
  they claim, at most MAX_FIXES. The same rule caps small_step's STEP
  alternatives (small_step_check.to_autofix returns them all, for the app
  to verify and pass through here).
- agent_fixes(answer): /api/v1/check's answer with `fixes` beside `fix`,
  fix = fixes[0] (agent_api does it for every app, so `fix` is what it was
  and an app that gives one fix answers fixes = [fix]). Pulley's flat
  fix_b / fix_c fold into `fixes`.
- The page's buttons: static/cct/cct_autofix.js (cctAutofix.show).
"""
from __future__ import annotations

import json
from typing import Callable, Iterable, Optional

MAX_FIXES = 3
EXTRA_KEYS = ("fix_b", "fix_c")           # Pulley's page answer (2026-11-04/05), folded into fixes


def _key(set_: dict) -> str:
    return json.dumps(set_, sort_keys=True, default=str)


def option(set_: dict, changes, *, params: Optional[dict] = None, clears=(), remaining=()) -> dict:
    """One option in the shared shape; params default to the set (an app
    whose page fields are its API's names)."""
    out = {"set": dict(set_), "changes": [changes] if isinstance(changes, str) else list(changes),
           "params": dict(set_ if params is None else params)}
    if clears:
        out["clears"] = list(clears)
    if remaining:
        out["remaining"] = list(remaining)
    return out


def fix_options(candidates: Iterable[Optional[dict]], recheck: Optional[Callable[[dict], object]] = None,
                limit: int = MAX_FIXES) -> list:
    """The options to offer, at most `limit` (module docstring). recheck(set)
    answers falsy for an option that doesn't clear its problem, True to keep
    it, or a dict merged into it ({"clears": [...], "remaining": [...]}). A
    recheck that raises drops that option."""
    out, seen = [], set()
    for c in candidates:
        if not c or not isinstance(c.get("set"), dict) or not c["set"]:
            continue
        k = _key(c["set"])
        if k in seen:
            continue
        seen.add(k)
        if recheck is not None:
            try:
                verdict = recheck(dict(c["set"]))
            except Exception:
                continue
            if not verdict:
                continue
            if isinstance(verdict, dict):
                c = {**c, **verdict}
        out.append(c)
        if len(out) >= limit:
            break
    return out


def agent_fixes(answer: dict, *, fix_params: Optional[Callable[[dict], dict]] = None) -> dict:
    """The check answer with `fix` and `fixes` (module docstring). An option
    without `params` gets fix_params(set), or the set itself when the app's
    params are its page's fields (its fix's params equal its set); else it
    is left out rather than sent with names the API doesn't know."""
    out = dict(answer)
    fix = out.get("fix")
    extras = [out.pop(k, None) for k in EXTRA_KEYS]
    listed = out.get("fixes")
    given = listed if isinstance(listed, list) else [fix, *extras]
    identity = not (isinstance(fix, dict) and "params" in fix and fix.get("params") != fix.get("set"))

    def with_params(o):
        if "params" in o:
            return o
        if fix_params is not None:
            return {**o, "params": dict(fix_params(dict(o["set"])))}
        return {**o, "params": dict(o["set"])} if identity else None
    fixes = fix_options(with_params(o) for o in given if isinstance(o, dict) and o.get("set"))
    out["fixes"] = fixes
    if fixes:
        out["fix"] = fixes[0]
    elif not isinstance(fix, dict) or fix.get("set"):
        out["fix"] = {"set": {}, "changes": [], "params": {}}
    return out
