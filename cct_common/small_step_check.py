"""
small_step_check.py — ask small_step whether it can build a design, before
building it, so an app can show why not (and how to fix it) while the user
edits, instead of only at Download.

    from cct_common import small_step_check as ssc

    cmd = command(design, binary, dxf)            # the app's own build argv
    result = ssc.check_cmd(cmd)                   # = ssc.check(cmd[0], cmd[1:])
    if result.refused:
        for p in result.problems:
            show(p.code, p.message)               # key UI on code, never on message text
        choices = ssc.to_autofix(result, {("--hub", 0): "hub_d",
                                          ("--screw-hole", 1): "screw_hole_d"})
    elif not result.available:
        show("STEP check unavailable: " + result.reason)   # fall back to the app's own rules

small_step 0.9.0 added `small_step check <exactly a build's argv>`
(small_step's PLAN_0.9.0_CHECK.md). It parses the argv as the build does and
prints one JSON object: {"small_step", "ok", "problems": [...]}. Each problem
has a `code`, its `[gap NN]`, the build's refusal `message` word for word, a
`measure`, and `fixes`: every alternative small_step knows, each a list of
flag changes that together clear the problem. Exit 0 means the check ran
(the verdict is in the JSON). Exit 2 is a usage error (the app's argv is
wrong), and exit 1 is an input it couldn't read (a missing DXF).

`ok` means the build would succeed, not that the STEP is valid: validity
stays each app's fuzzer's job.

What this module adds over calling it directly:
  * The floor. A binary older than 0.9.0 has no `check`. Its result is
    `unavailable`, never an error, so the app falls back to its own rules.
    The version is read once per binary path and modification time.
  * A cache per argv. A file argument (an app's temporary DXF, new on every
    call) is keyed by its contents' SHA-256, not its path. So the same design
    hits, and a reused temp name holding a new design misses. The file must
    exist when check() runs, so call it before deleting the temp file.
  * UTF-8. The messages carry an em dash. Python on Windows decodes `text=True`
    output as cp1252, which garbles it, and then a message no longer equals
    the build's stderr. Everything here is decoded as UTF-8.
  * to_autofix(): turns each fix alternative into the app's own field
    changes. Every alternative is returned, because the owner's rule is that
    the user picks and no app chooses one silently. An alternative with a flag
    the app has no field for is returned marked `complete=False`, never mapped
    by guesswork.

Any subcommand works (combined, flange-3d, flange-metal…): the argv is passed
through as given.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple, Union

CHECK_FLOOR = (0, 9, 0)
TIMEOUT_S = 60.0
CACHE_SIZE = 256

_VERSION = re.compile(r"small_step\s+(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class Change:
    """One flag change in a fix: `op` is "above", "below" or "set" with a
    `value`, or "remove" (the whole flag goes; `arg` and `value` are None).
    `arg` is a 0-based index into the flag's own arguments."""
    flag: str
    arg: Optional[int]
    op: str
    value: Optional[float] = None


@dataclass(frozen=True)
class Problem:
    tier: str                        # "rule" (closed-form) or "build" (the real build refused)
    code: str
    gap: Optional[int]
    message: str                     # the build's refusal text, word for word
    measure: dict
    fixes: Tuple[Tuple[Change, ...], ...]   # alternatives; each clears the problem alone


@dataclass(frozen=True)
class CheckResult:
    status: str                      # "ok" | "refused" | "unavailable" | "error"
    problems: Tuple[Problem, ...] = ()
    version: Optional[Tuple[int, int, int]] = None
    reason: str = ""                 # why "unavailable" or "error"

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def refused(self) -> bool:
        return self.status == "refused"

    @property
    def available(self) -> bool:
        """The check ran: its verdict can stand in for the app's own rules."""
        return self.status in ("ok", "refused")


@dataclass(frozen=True)
class FieldChange:
    """A fix in the app's own terms: set `field` above, below or to `value`,
    or remove it ("remove": the feature goes, `value` is None)."""
    field: str
    op: str
    value: Optional[float] = None

    def nudged(self, step: float) -> Optional[float]:
        """The value on the app's grid of `step`: the smallest multiple above
        an "above" bound, the largest below a "below" bound, "set" as given."""
        if self.value is None or self.op == "set":
            return self.value
        q = self.value / step
        k = math.floor(q + 1e-9) + 1 if self.op == "above" else math.ceil(q - 1e-9) - 1
        return round(k * step, 9)


@dataclass(frozen=True)
class Choice:
    """One way to clear one problem. `unmapped` holds the changes the app has
    no field for; such a choice is not `complete` and the app can't apply it."""
    code: str
    gap: Optional[int]
    message: str
    changes: Tuple[FieldChange, ...]
    unmapped: Tuple[Change, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unmapped


# --------------------------------------------------------------------------- version

_versions: dict = {}
_lock = threading.Lock()


def _stat_key(binary: Path) -> Optional[tuple]:
    try:
        st = binary.stat()
    except OSError:
        return None
    return (str(binary.resolve()), st.st_mtime_ns, st.st_size)


def binary_version(binary: Union[str, os.PathLike]) -> Optional[Tuple[int, int, int]]:
    """small_step's version from `--version`, read once per path and
    modification time; None when the binary is missing or won't say."""
    key = _stat_key(Path(binary))
    if key is None:
        return None
    with _lock:
        if key in _versions:
            return _versions[key]
    try:
        run = subprocess.run([str(binary), "--version"], capture_output=True, timeout=10)
        m = _VERSION.search(run.stdout.decode("utf-8", "replace"))
        version = tuple(int(g) for g in m.groups()) if m else None
    except (OSError, subprocess.SubprocessError):
        version = None
    with _lock:
        _versions[key] = version
    return version


# --------------------------------------------------------------------------- check

_cache: "OrderedDict[tuple, CheckResult]" = OrderedDict()


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _versions.clear()


def _arg_key(arg: str) -> str:
    """A file argument by its contents, anything else as itself."""
    try:
        if os.path.isfile(arg):
            return "sha256:" + hashlib.sha256(Path(arg).read_bytes()).hexdigest()
    except (OSError, ValueError):
        pass
    return arg


def _change(raw: dict) -> Change:
    if raw.get("remove"):
        return Change(raw["flag"], None, "remove")
    for op in ("above", "below", "set"):
        if op in raw:
            return Change(raw["flag"], raw.get("arg"), op, float(raw[op]))
    raise ValueError(f"a fix change with no above/below/set/remove: {raw}")


def _problem(raw: dict) -> Problem:
    return Problem(tier=raw.get("tier", ""), code=raw["code"], gap=raw.get("gap"),
                   message=raw.get("message", ""), measure=dict(raw.get("measure") or {}),
                   fixes=tuple(tuple(_change(c) for c in alt) for alt in raw.get("fixes") or ()))


def check(binary: Union[str, os.PathLike, None], args: Sequence[str],
          timeout: float = TIMEOUT_S) -> CheckResult:
    """Run `binary check *args`, where `args` is a build's argv without the
    binary (subcommand first). Never raises for anything small_step does:
    a binary that is missing or older than 0.9.0 is "unavailable", and a
    usage error, an unreadable input or a timeout is an "error" (not cached)."""
    if binary is None or not Path(binary).is_file():
        return CheckResult("unavailable", reason="the small_step binary isn't available")
    version = binary_version(binary)
    if version is None:
        return CheckResult("unavailable", reason="small_step didn't report its version")
    if version < CHECK_FLOOR:
        have, need = ".".join(map(str, version)), ".".join(map(str, CHECK_FLOOR))
        return CheckResult("unavailable", version=version,
                           reason=f"small_step {have} is older than {need}, which added check")
    args = [str(a) for a in args]
    key = (_stat_key(Path(binary)), tuple(_arg_key(a) for a in args))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    try:
        run = subprocess.run([str(binary), "check", *args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CheckResult("error", version=version, reason=f"small_step check took longer than {timeout:g} s")
    except OSError as e:
        return CheckResult("error", version=version, reason=f"small_step check couldn't run: {e}")
    out = run.stdout.decode("utf-8", "replace")
    err = run.stderr.decode("utf-8", "replace").strip()
    if run.returncode != 0:
        what = "didn't understand the call" if run.returncode == 2 else "couldn't check it"
        first = err.splitlines()[0] if err else f"exit {run.returncode}"
        return CheckResult("error", version=version, reason=f"small_step {what}: {first}")
    try:
        data = json.loads(out)
        problems = tuple(_problem(p) for p in data.get("problems") or ())
        status = "ok" if data["ok"] else "refused"
    except (ValueError, KeyError, TypeError) as e:
        return CheckResult("error", version=version, reason=f"small_step check's answer wasn't readable: {e}")
    if status == "refused" and not problems:
        return CheckResult("error", version=version, reason="small_step check refused with no problem listed")
    result = CheckResult(status, problems, version)
    with _lock:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result


def check_cmd(cmd: Sequence[str], timeout: float = TIMEOUT_S) -> CheckResult:
    """check() for a whole build command: [binary, subcommand, …]."""
    if not cmd:
        return CheckResult("unavailable", reason="the small_step binary isn't available")
    return check(cmd[0], cmd[1:], timeout=timeout)


# --------------------------------------------------------------------------- Auto-fix

FlagMap = Mapping[Tuple[str, Optional[int]], Optional[str]]


def to_autofix(result: CheckResult, flag_map: FlagMap) -> list:
    """Every fix alternative of every problem, as the app's field changes.
    `flag_map` maps (flag, arg) to the app's field, with (flag, None) for a
    "remove" change. A missing or None entry means the app has no such field:
    that alternative is still returned, with `complete=False`."""
    choices = []
    for p in result.problems:
        for alt in p.fixes:
            mapped, unmapped = [], []
            for c in alt:
                field = flag_map.get((c.flag, c.arg))
                if field:
                    mapped.append(FieldChange(field, c.op, c.value))
                else:
                    unmapped.append(c)
            choices.append(Choice(p.code, p.gap, p.message, tuple(mapped), tuple(unmapped)))
    return choices
