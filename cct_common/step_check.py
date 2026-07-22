"""
step_check.py — reusable STEP-file validation helpers for fuzz/dev tools.

Every project that generates STEP files (EBoxDesigner-ss via small_step_ebox,
PulleyWebApp-ss via small_step) has repeatedly found bugs that DON'T raise an
exception — a bad winding, a flipped face, a wall-panel gap — only via STEP
validity/volume checks, not crashes. This module is the shared toolkit those
checks live in, so a project's own fuzz_<name>.py only has to write its own
random-model generator and STEP/STL export glue, then wire these checks in.

Four independent checks, at four different costs:
  * check_wire_order(step_bytes) — free, pure-stdlib text parsing, no CAD
    kernel. Catches an EDGE_LOOP whose ORIENTED_EDGEs aren't head-to-tail —
    OCCT silently re-sorts it (so it still validates), but Fusion 360
    tessellates in file order and renders "webbing".
  * PersistentStepWorker (occt_server.py) — OCCT solid validity + volume.
    Needs OCP, which has no wheel for Python 3.13+, so it talks to a
    persistent subprocess worker under an OCCT-capable venv (e.g. Python
    3.12) instead of importing OCP in-process.
  * PersistentStepWorker (edrawings_server.py) — does eDrawings (a free,
    widely-used STEP viewer) accept the file at all? Also a persistent
    worker, since it needs pywin32 + COM automation on Windows.
  * validate_nist_sfa(step_bytes, sfa_exe) — NIST's STEP File Analyzer, a
    genuinely independent parser opinion (FreeCAD/OCCT/eDrawings all sit on
    OCCT; SFA is built on IFCsvr instead). A standalone Windows exe — no
    persistent-worker wrapper needed, since its own per-invocation startup
    cost is much lower than Python+OCCT import or COM automation.

Both workers are meant to be spawned ONCE per fuzz run and reused for every
file — the worker's own startup cost (Python+OCCT import, or COM init)
would dominate a tight loop if paid per file:

    from cct_common.step_check import PersistentStepWorker, check_wire_order

    worker = PersistentStepWorker(python_exe, "occt_server.py")
    try:
        for step_bytes in every_generated_file:
            wire = check_wire_order(step_bytes)
            tmp.write_bytes(step_bytes)
            result = worker.check(str(tmp))  # {'solids', 'invalid', 'volume'}
    finally:
        worker.close()
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path


def check_wire_order(step_bytes: bytes) -> dict:
    """{'total': n, 'out_of_order': m, 'bad_loops': [ids...]}

    Every EDGE_LOOP's ORIENTED_EDGEs must chain head-to-tail. Ported from
    small_step/archive/check_wire_order.py (its importable form)."""
    txt = step_bytes.decode("utf-8", "ignore")
    ents = {}
    for m in re.finditer(r"#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*?)\)\s*;", txt, re.S):
        ents[int(m.group(1))] = (m.group(2), m.group(3))

    def refs(s):
        return [int(x) for x in re.findall(r"#(\d+)", s)]

    def edge_verts(eid):
        return refs(ents[eid][1])[:2]  # EDGE_CURVE('', #vs, #ve, #curve, flag)

    bad, tot = [], 0
    for eid, (t, b) in ents.items():
        if t != "EDGE_LOOP":
            continue
        tot += 1
        chain = []
        for oe in refs(b):  # ORIENTED_EDGE('',*,*,#edge,.T./.F.)
            ob = ents[oe][1]
            edge = refs(ob)[-1]
            orient = ob.rsplit(",", 1)[1].strip().rstrip(")")
            vs, ve = edge_verts(edge)
            if orient == ".F.":
                vs, ve = ve, vs
            chain.append((vs, ve))
        ok = all(
            chain[i][1] == chain[(i + 1) % len(chain)][0] for i in range(len(chain))
        )
        if not ok:
            bad.append(eid)
    return {"total": tot, "out_of_order": len(bad), "bad_loops": bad}


class PersistentStepWorker:
    """A generic persistent subprocess worker for per-STEP-file checks: one
    line in (a STEP file path), one JSON line out. `server_script` is the
    worker's own implementation (e.g. small_step/archive/occt_server.py or
    edrawings_server.py) — this class only owns the request/response
    protocol and process lifecycle, not what the worker actually checks.

    Reads back non-JSON lines defensively: some CAD tooling (OCCT's C++
    layer on a malformed STEP file, eDrawings' own console noise) writes
    diagnostics straight to the child's stdout OUTSIDE the worker script's
    own control, ahead of its one real JSON response line for that same
    request. A naive single readline() would return that diagnostic line,
    fail to parse it, AND permanently desync every future response by one
    (each later check() would silently return the PREVIOUS request's real
    answer) — seen in practice once with occt_server.py. Skipping non-JSON
    lines (bounded) until one parses self-heals within the SAME request
    instead of poisoning every request after it.
    """

    def __init__(self, python_exe: str | Path, server_script: str | Path):
        self.proc = subprocess.Popen(
            [str(python_exe), str(server_script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )

    def check(self, step_path: str) -> dict:
        """Sends `step_path` to the worker and returns its parsed JSON
        response (shape depends on the server script — e.g. {'solids',
        'invalid', 'volume'} for occt_server.py, {'ok', 'msg'} for
        edrawings_server.py) or {'error': str} if the worker didn't answer
        cleanly."""
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(str(step_path) + "\n")
        self.proc.stdin.flush()
        skipped = []
        for _ in range(20):
            line = self.proc.stdout.readline()
            if not line:
                err = self.proc.stderr.read() if self.proc.stderr else ""
                return {"error": f"worker died (no response): {err[-500:]}"}
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                skipped.append(line.rstrip("\n"))
        return {"error": f"worker gave no JSON after {len(skipped)} "
                         f"stray line(s): {skipped[:5]!r}"}

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def validate_nist_sfa(step_bytes: bytes, sfa_exe: str | Path,
                      timeout: float = 60) -> tuple[bool | None, str]:
    """NIST STEP File Analyzer (SFA) syntax check — a genuinely independent
    parser opinion from OCCT (FreeCAD/OCCT/eDrawings all sit on OCCT; SFA is
    built on IFCsvr instead). Returns (True, msg) / (False, msg), or
    (None, msg) if `sfa_exe` doesn't exist — treat as SKIPPED, not failed:
    Windows-only, needs a one-time interactive IFCsvr MSI install."""
    sfa_exe = Path(sfa_exe)
    if not sfa_exe.is_file():
        return None, f"sfa-cl.exe not found at {sfa_exe}"
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
        f.write(step_bytes)
        tmp = f.name
    try:
        result = subprocess.run(
            [str(sfa_exe), tmp, "syntax", "noopen", "nolog"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        output = result.stdout + result.stderr
        lines = output.splitlines()
        problems = [l.strip() for l in lines if "**" in l]
        ok_line = any("No syntax errors or warnings" in l for l in lines)
        if ok_line and not problems:
            return True, "No syntax errors or warnings"
        if problems:
            return False, "; ".join(problems[:3])
        return False, "no clean result line in output"
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    finally:
        try:
            Path(tmp).unlink()
        except OSError:
            pass  # transient AV lock on a short-lived temp file; not fatal
