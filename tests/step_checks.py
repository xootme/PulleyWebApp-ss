"""
step_checks.py — what every STEP test checks of a file, read as a CAD program
reads it (OCP / OpenCASCADE).

The owner, 2026-10-12: CCT_WebSite found a GT2 30T pulley's top flange inside
out (-208 mm3) while BRepCheck called every solid valid, so "valid" alone
passes a broken file. Each solid must also enclose a positive volume, and the
whole must be the STL's (the app's own model of the same part).

The owner, 2026-10-13: eDrawings drew a hub's outer wall inside out, its end
circles wound as a bore's. OpenCASCADE re-winds faces as it reads them, so
nothing above could see it. The file is read raw for that: in a closed solid
every edge is walked once each way by the loops of its two faces.

    from step_checks import solids, problems, wrong_way_edges

    solids(step_bytes)      [(volume mm3, valid), ...] one per solid
    wrong_way_edges(step_bytes)
                            {surface types: count} of edges two faces walk the same way
    problems(step_bytes, stl_volume=v, rel=0.01)
                            [] for a good file, else what's wrong, in words
    reference_volume(client, query, n)
                            pulley n's STL volume, plus its separate top flange's
"""
from __future__ import annotations

import collections
import os
import re
import tempfile

# How far a STEP's volume may sit from its STL's. Measured 2026-10-12 on the
# site's 40 sample pulleys: every one that's right lies within -0.31..+0.42 %
# (the STL's chords against the STEP's true arcs); the faults found were 1 to
# 3 % off. An overlap between two solids counted twice can hide inside this.
STEP_REL = 0.01


def solids(data: bytes) -> list[tuple[float, bool]]:
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(data)
        path = f.name
    try:
        rd = STEPControl_Reader()
        rd.ReadFile(path)
        rd.TransferRoots()
        ex = TopExp_Explorer(rd.OneShape(), TopAbs_SOLID)
        out = []
        while ex.More():
            s = ex.Current()
            g = GProp_GProps()
            BRepGProp.VolumeProperties_s(s, g)
            out.append((g.Mass(), BRepCheck_Analyzer(s).IsValid()))
            ex.Next()
        return out
    finally:
        os.unlink(path)


def stl_volume(data: bytes) -> float:
    """A binary STL's enclosed volume (the divergence theorem over its triangles)."""
    import numpy as np
    n = int(np.frombuffer(data[80:84], '<u4')[0])
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    v = np.frombuffer(data[84:84 + 50 * n], rec)['v'].astype(float)
    return float(np.einsum('ij,ij->i', v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6.0)


_ENTITY = re.compile(rb'#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*?)\)\s*;', re.S)
_REF = re.compile(r'#(\d+)')


def _last_flag(args: str) -> bool:
    return args.rsplit(',', 1)[1].strip().rstrip(')') == '.T.'


def wrong_way_edges(data: bytes) -> dict:
    """Edges that the loops of their two faces walk the SAME way, by the
    surface types of the two faces ({} for a well-wound file). Read from the
    raw entities as SmallStep's edge_senses.py does: an ORIENTED_EDGE's own
    flag, flipped by its bound's flag; the face's same_sense doesn't enter.
    An edge with other than two uses is skipped, as there: small_step's files
    have such edges (9 in a plain captured-nut hub) and they aren't a winding."""
    ents = {int(m.group(1)): (m.group(2).decode(), m.group(3).decode('utf-8', 'replace'))
            for m in _ENTITY.finditer(data)}
    refs = lambda s: [int(x) for x in _REF.findall(s)]                          # noqa: E731
    loop_of, bound_of, face_of = {}, {}, {}
    for i, (t, a) in ents.items():
        if t == 'EDGE_LOOP':
            for o in refs(a):
                loop_of[o] = i
        elif t in ('FACE_OUTER_BOUND', 'FACE_BOUND'):
            bound_of[refs(a)[0]] = i
        elif t == 'ADVANCED_FACE':
            r = refs(a)
            for b in r[:-1]:
                face_of[b] = ents[r[-1]][0]
    uses = collections.defaultdict(list)
    for i, (t, a) in ents.items():
        if t == 'ORIENTED_EDGE' and i in loop_of and loop_of[i] in bound_of:
            bound = bound_of[loop_of[i]]
            walk = _last_flag(a) ^ (not _last_flag(ents[bound][1]))
            uses[refs(a)[-1]].append((walk, face_of.get(bound, '?')))
    out = collections.Counter()
    for u in uses.values():
        if len(u) == 2 and u[0][0] == u[1][0]:
            out['/'.join(sorted(x[1].replace('_SURFACE', '') for x in u))] += 1
    return dict(out)


def problems(data: bytes, stl_volume: float | None = None, rel: float = STEP_REL) -> list[str]:
    """Why this STEP isn't the part ([] if it is): no solids, an invalid solid,
    a solid of zero or negative volume (inside out), a face wound against its
    neighbours (wrong_way_edges), or a total off the STL's by more than `rel`."""
    got = solids(data)
    if not got:
        return ['no solids']
    out = [f'solid {i}: OpenCASCADE says invalid' for i, (_v, ok) in enumerate(got) if not ok]
    wrong = wrong_way_edges(data)
    if wrong:
        out.append(f'faces wound against their neighbours (edges walked the same way, by surface): {wrong}')
    out += [f'solid {i}: volume {v:.1f} mm3 (inside out or empty)' for i, (v, _ok) in enumerate(got)
            if v <= 0.0]
    if stl_volume is not None:
        total = sum(v for v, _ok in got)
        if abs(total - stl_volume) > rel * abs(stl_volume):
            out.append(f'total {total:.1f} mm3 against the STL\'s {stl_volume:.1f} '
                       f'({(total / stl_volume - 1) * 100:+.2f} %, allowed ±{rel * 100:g} %)')
    return out


def _pulley_query(query: dict, n: int) -> dict:
    """Pulley n's settings under the plain names, as the page sends a single
    part's route (index.html _flangeParams): pulley 2's p2_ names win."""
    if n == 1:
        return dict(query)
    out = {k: v for k, v in query.items() if not k.startswith('p2_')}
    out.update({k[3:]: v for k, v in query.items() if k.startswith('p2_')})
    return out


def reference_volume(client, query: dict, n: int) -> float:
    """What pulley n's STEP should hold, from the app's STLs: the pulley's own
    STL, plus its top flange's when that's printed as a separate part (its own
    file, /download/flange-stl, while the STEP carries it as a product of its
    own). Missing that flange made four sample pulleys look 7-22 % heavy."""
    pfx = 'p2_' if n == 2 else ''
    r = client.get('/download/stl', query_string=dict(query, pulley=str(n)))
    assert r.status_code == 200, r.data[:200]
    vol = stl_volume(r.data)
    if all(query.get(pfx + k) == '1' for k in ('flange_enabled', 'flange_3dprint', 'flange_top_separate')):
        f = client.get('/download/flange-stl', query_string=dict(_pulley_query(query, n), flange_which='top'))
        assert f.status_code == 200, f.data[:200]
        vol += stl_volume(f.data)
    return vol
