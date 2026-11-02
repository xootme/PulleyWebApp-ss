"""linux_binary_check.py — the Linux small_step that ships (bin/small_step_linux, what Cloud Run
runs) makes the same STEP as the Windows build every test runs, on the site's sample designs.

Production made its STEPs with 0.13.4 while the tests checked 0.18.x (found 2026-11-02): the
committed Linux binary is only proved by comparing it with the build the tests prove. Every
sample pulley (CCT_WebSite's samples.generated.json, as tests/test_sample_designs_step.py
reads it; --queries FILE for a JSON list of other designs' queries) goes through the app's own
/download/assembly-step, and each small_step run it makes is run a second time, with the same
arguments and input files, by the Linux binary in WSL. The two outputs must be byte-identical
(small_step writes no timestamp), but for a signed zero ("-0." against "0.") and a number's
last printed digit (at most 1e-12 mm); the runs each rule was needed for are counted and named
(compare() below). Also: both binaries report the same version, and the Linux one is static
(no NEEDED, no INTERP: the Dockerfile's gate runs it on a slim image).

From the repo root, with an interpreter that has the app's packages (a test turn; WSL's
Ubuntu must be installed, so it stays on this PC):

  .venv312/Scripts/python.exe tools/linux_binary_check.py [--linux bin/small_step_linux]
      [--windows <small_step.exe>] [--distro ubuntu-claude] [--queries FILE] [--keep DIR]

--keep DIR keeps both outputs of every run that isn't byte-identical.

Exit 0 when every run matches. Copy it for another app: only `designs()` and the STEP route
are this app's."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
SAMPLES = Path(os.environ.get('CCT_SAMPLES_JSON',
                              r'C:\Users\cmyer\Documents\CCT_WebSite\site\data\samples.generated.json'))
WINDOWS = r'C:\Users\cmyer\Documents\small_step\target\x86_64-pc-windows-gnu\release\small_step.exe'


def wsl_path(p: str) -> str:
    """C:\\a\\b -> /mnt/c/a/b; anything else as it is."""
    m = re.match(r'^([A-Za-z]):[\\/](.*)$', p)
    return f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}" if m else p


# The two differences allowed, both the platforms' maths libraries rounding the last bit, each
# counted and named in the report (Controller, 2026-11-02; SmallStep is to make the builds print
# the same, and then this goes back to exact):
# - a signed zero: a sine rounded to 0 from either side reads "-0." on one and "0." on the other
#   (SmallStep, 189 builds of 0.18.2: 7 captured-nut cases, one line each);
# - the last printed digit: a number that differs by at most one unit in its last printed place
#   (the longer of the two, as small_step drops trailing zeros), and at most LAST_DIGIT_MM,
#   everything else on the line the same (Pulley's samples on 0.18.2: 7 of 40 runs, 1 to 13
#   lines each, largest 1.0e-12).
NEG_ZERO = re.compile(rb'(?<=[(,])-0\.(?=[,)])')
NUMBER = re.compile(rb'-?\d+\.(\d*)(?:E[-+]?\d+)?')
LAST_DIGIT_MM = Decimal('1e-12')


def _last_digit(x: bytes, y: bytes) -> float | None:
    """The largest difference on two lines that differ only in numbers' last printed digits, or
    None if they differ any other way."""
    if NUMBER.sub(b'#', x) != NUMBER.sub(b'#', y):
        return None
    worst = 0.0
    for p, q in zip(NUMBER.finditer(x), NUMBER.finditer(y)):
        if p.group(0) == q.group(0):
            continue
        # small_step drops trailing zeros: 7.1432323412 is 7.143232341200, one unit from ...201
        places = max(len(p.group(1)), len(q.group(1)))
        if b'E' in p.group(0) or b'E' in q.group(0) or places == 0:
            return None
        d = abs(Decimal(p.group(0).decode()) - Decimal(q.group(0).decode()))   # exact, as printed
        if d > Decimal(1).scaleb(-places) or d > LAST_DIGIT_MM:
            return None
        worst = max(worst, float(d))
    return worst


def compare(a: bytes, b: bytes) -> tuple:
    """(rule, largest last-digit difference): rule 'exact', 'signed zero', 'last digit' or None
    (a difference no rule allows)."""
    if a == b:
        return 'exact', 0.0
    a, b = NEG_ZERO.sub(b'0.', a), NEG_ZERO.sub(b'0.', b)
    if a == b:
        return 'signed zero', 0.0
    la, lb = a.split(b'\n'), b.split(b'\n')
    if len(la) != len(lb):
        return None, 0.0
    worst = 0.0
    for x, y in zip(la, lb):
        if x != y:
            d = _last_digit(x, y)
            if d is None:
                return None, 0.0
            worst = max(worst, d)
    return 'last digit', worst


def wsl(distro, *argv, **kw):
    # --exec, not --: with -- WSL's shell reads the arguments, and "#4A9FD4" (--color) is a comment
    return subprocess.run(['wsl', '-d', distro, '--exec', *argv], capture_output=True, **kw)


def designs(queries_file=None):
    """[(label, query, part)]: each sample pulley alone, as the Download window makes its STEP."""
    if queries_file:
        items = [(f'query {i}', q) for i, q in enumerate(json.loads(Path(queries_file).read_text('utf-8')))]
    else:
        items = [(s['slug'], dict(parse_qsl(s['link_query'])))
                 for s in json.loads(SAMPLES.read_text(encoding='utf-8')).get('samples', [])
                 if s.get('app') == 'pulley']
    return [(f'{label}-p{n}', q, n) for label, q in items
            for n in ((1, 2) if q.get('dual') == 'true' else (1,))]


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--linux', default=str(ROOT / 'bin' / 'small_step_linux'))
    ap.add_argument('--windows', default=os.environ.get('SMALL_STEP_BIN') or WINDOWS)
    ap.add_argument('--distro', default='ubuntu-claude')
    ap.add_argument('--queries', help='a JSON list of design queries instead of the samples')
    ap.add_argument('--keep', help='a folder to keep both outputs of each run that differs in')
    a = ap.parse_args(argv)
    linux = wsl_path(str(Path(a.linux).resolve()))

    wv = subprocess.run([a.windows, '--version'], capture_output=True, text=True).stdout.strip()
    lv = wsl(a.distro, linux, '--version', text=True)
    lv = (lv.stdout or lv.stderr).strip()
    elf = wsl(a.distro, 'sh', '-c', f"readelf -d '{linux}' | grep -c NEEDED; readelf -l '{linux}' | grep -c INTERP",
              text=True).stdout.split()
    print(f'windows: {wv}  ({a.windows})\nlinux:   {lv}  ({a.linux})\nlinux static: NEEDED {elf[0] if elf else "?"}, '
          f'INTERP {elf[1] if len(elf) > 1 else "?"}', flush=True)
    bad = []
    if wv != lv:
        bad.append(f'versions differ: windows {wv!r}, linux {lv!r}')
    if elf != ['0', '0']:
        bad.append(f'the Linux binary is not static (NEEDED, INTERP = {elf})')

    os.environ['SMALL_STEP_BIN'] = a.windows
    os.environ.setdefault('QUEUE_DISABLED', '1')
    import app as A
    from exporters import step_worker_ss as W
    real = W._run_cmd
    runs, ruled = [], {}

    def both(cmd):
        out = real(cmd)                                  # the Windows build, as the app runs it
        args = [wsl_path(str(x)) if os.path.isfile(str(x)) else str(x) for x in cmd[1:]]
        r = wsl(a.distro, linux, *args)
        runs.append((cmd, out, r))
        return out
    W._run_cmd = both
    A._run_ss_worker = lambda kw, **_: W.run(kw, a.windows)    # in this process, so both() sees each run

    client = A.app.test_client()
    todo = designs(a.queries)
    for label, q, n in todo:
        start = len(runs)
        r = client.get('/download/assembly-step', query_string=dict(q, parts=f'p{n}'))
        mine = runs[start:]
        if r.status_code != 200 and not mine:
            print(f'skip   {label}: STEP {r.status_code} before small_step ran')
            continue
        for cmd, out, lr in mine:
            sub = cmd[1] if len(cmd) > 1 else '?'
            if lr.returncode != 0:
                bad.append(f'{label} ({sub}): the Linux binary failed (exit {lr.returncode}): '
                           f'{lr.stderr.decode(errors="replace")[:200]}')
                print(f'FAIL   {label} ({sub}): linux exit {lr.returncode}')
                continue
            rule, d = compare(out, lr.stdout)
            if rule is None:
                bad.append(f'{label} ({sub}): outputs differ ({len(out)} bytes on Windows, {len(lr.stdout)} on Linux)')
                print(f'DIFF   {label} ({sub}): windows {hashlib.md5(out).hexdigest()[:12]}, '
                      f'linux {hashlib.md5(lr.stdout).hexdigest()[:12]}')
            elif rule == 'exact':
                print(f'same   {label} ({sub}): {len(out)} bytes, md5 {hashlib.md5(out).hexdigest()[:12]}')
            else:
                ruled.setdefault(rule, []).append((label, d))
                print(f'same   {label} ({sub}): {len(out)} bytes, but for the {rule} rule'
                      + (f' (largest {d:.1e} mm)' if rule == 'last digit' else ''))
            if rule != 'exact' and a.keep:
                Path(a.keep).mkdir(parents=True, exist_ok=True)
                (Path(a.keep) / f'{label}.windows.step').write_bytes(out)
                (Path(a.keep) / f'{label}.linux.step').write_bytes(lr.stdout)
    print(f'\n{len(runs)} small_step runs on {len(todo)} pulleys; {len(bad)} problem(s)')
    for rule in ('signed zero', 'last digit'):
        got = ruled.get(rule, [])
        worst = f', largest {max(d for _, d in got):.1e} mm' if got and rule == 'last digit' else ''
        print(f'{len(got)} of {len(runs)} runs needed the {rule} rule{worst}'
              + (f': {", ".join(lab for lab, _ in got)}' if got else ''))
    for b in bad:
        print('  ' + b)
    return 1 if bad or not runs else 0


if __name__ == '__main__':
    sys.exit(main())
