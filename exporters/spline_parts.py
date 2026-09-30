"""
spline_parts.py — the parts that go with a splined bore (ADR-017): a sample
splined shaft that fits it, and the splined washer that sits under the
retaining ring. Geometry from cct_common (splines at the default fit,
retaining_rings for the ring's groove); this module only builds the files.

    shaft_stl(spline, span)                the shaft for a part `span` long, standing
                                           on z = 0: a ring's groove at each ringed face
    washer_stl(spline)                     the washer, OD × thickness from the ring
    preview_parts(spline, span)            shaft, rings, washers where they sit, for
                                           the 3D view (the part's faces at 0 and span)
    shaft_svg / shaft_dxf / washer_svg / washer_dxf   the exact outlines (2D)

STL parts carry the print compensation (the shaft and washer OD shrink, the
washer's hole grows); drawings are the nominal fit, true lines and arcs.
"""
from __future__ import annotations

import io
import math

import trimesh
from shapely.geometry import Point as ShapelyPoint, Polygon as ShapelyPolygon
from shapely.geometry.polygon import orient as shapely_orient

from cct_common import splines as spl

from exporters.step_exporter import _as_spline, _spline_print

_SECTIONS = 128
TAIL = 5.0          # mm of shaft past the part's other face


def _polygon(points):
    return shapely_orient(ShapelyPolygon(points), sign=1.0)


def _faces(spline):
    rt = spline.get('retainer') if isinstance(spline, dict) else None
    return (rt, rt['faces']) if rt else (None, [])


def _span_z(face, span, lo_hi):
    """A (inner, outer) span measured outward from a face (the retainer's
    `stack`, cct_common.retaining_rings) as (z low, z high) for a part whose
    faces are at z = 0 (bottom) and z = span (top)."""
    lo, hi = lo_hi
    return (span + lo, span + hi) if face == 'top' else (-hi, -lo)


def ends(spline):
    """(below, above): how far the shaft runs past the part's bottom and top
    faces — DIN 471's edge margin n past a ring's groove (which stands out
    with its washer and ring when the face has no counterbore), else TAIL."""
    rt, faces = _faces(spline)
    return tuple(rt['stack'][f]['shaft_end'] if f in faces else TAIL for f in ('bottom', 'top'))


def shaft_mesh(spline, span: float):
    """The sample shaft for a part whose faces are at z = 0 and z = span: the
    mating external spline (printed), running ends() past them, with a ring's
    groove at each ringed face (DIN 471: width m, floor d2 — across the tooth
    tops) where the retainer's `stack` puts it: its outer wall on the face in
    a counterbore, further out without one."""
    sp = _as_spline(spline)
    c = _spline_print(spline)
    rt, faces = _faces(spline)
    below, above = ends(spline)
    pts = spl.printed(spl.sample_closed(spl.shaft_path(sp), 0.02), c, hole=False)
    mesh = trimesh.creation.extrude_polygon(_polygon(pts), span + below + above)
    mesh.apply_translation([0.0, 0.0, -below])
    for face in faces:
        w = rt['m'] + 2 * c                                   # a pocket grows...
        r_groove = rt['d2'] / 2.0 - c                         # ...its floor comes in
        outer = trimesh.creation.cylinder(radius=spl.shaft(sp).outer, height=w, sections=_SECTIONS)
        inner = trimesh.creation.cylinder(radius=r_groove, height=w + 1.0, sections=_SECTIONS)
        groove = trimesh.boolean.difference([outer, inner], engine='manifold')
        z_lo, z_hi = _span_z(face, span, rt['stack'][face]['groove'])
        groove.apply_translation([0.0, 0.0, (z_lo + z_hi) / 2.0])
        mesh = trimesh.boolean.difference([mesh, groove], engine='manifold')
    return mesh


def shaft_stl(spline, span: float) -> bytes:
    """The sample shaft's STL, standing on z = 0."""
    mesh = shaft_mesh(spline, span)
    mesh.apply_translation([0.0, 0.0, -float(mesh.bounds[0][2])])
    return mesh.export(file_type='stl')


def _ring_mesh(rt, gap_deg: float = 40.0):
    """A retaining ring as it sits in its groove: an open ring from d2 out
    to its lugs (a past the groove), s thick, standing on z = 0."""
    from shapely.geometry import Polygon as _P
    r_in, r_out = rt['d2'] / 2.0, rt['d2'] / 2.0 + rt['a']
    ring = ShapelyPoint(0, 0).buffer(r_out, resolution=32).difference(
        ShapelyPoint(0, 0).buffer(r_in, resolution=32))
    half = math.radians(gap_deg / 2.0)
    wedge = _P([(0, 0), (2 * r_out * math.cos(half), 2 * r_out * math.sin(half)),
                (2 * r_out * math.cos(half), -2 * r_out * math.sin(half))])
    return trimesh.creation.extrude_polygon(shapely_orient(ring.difference(wedge), sign=1.0), rt['s'])


def _washer_mesh(spline):
    sp = _as_spline(spline)
    c = _spline_print(spline)
    rt = spline['retainer']
    hole = spl.printed(spl.sample_closed(spl.path(sp), 0.02), c, hole=True)
    disc = ShapelyPoint(0, 0).buffer(rt['washer_od'] / 2.0 - c, resolution=_SECTIONS // 4)
    ring = shapely_orient(disc.difference(ShapelyPolygon(hole)), sign=1.0)
    return trimesh.creation.extrude_polygon(ring, rt['washer_t'])


def preview_parts(spline, span: float) -> dict:
    """What the 3D view shows through a splined part whose faces are at z = 0
    and z = span: the sample shaft, a ring on each ringed face (on its washer,
    at the groove's inner wall) and the washers — {'shaft', 'rings',
    'washers'}, each a mesh or None."""
    rt, faces = _faces(spline)
    out = {'shaft': shaft_mesh(spline, span), 'rings': None, 'washers': None}
    rings, washers = [], []
    for face in faces:
        st = rt['stack'][face]                                   # in the counterbore or on the face
        r = _ring_mesh(rt)
        r.apply_translation([0.0, 0.0, _span_z(face, span, st['ring'])[0]])
        rings.append(r)
        if st['washer']:
            w = _washer_mesh(spline)
            w.apply_translation([0.0, 0.0, _span_z(face, span, st['washer'])[0]])
            washers.append(w)
    if rings:
        out['rings'] = trimesh.util.concatenate(rings)
    if washers:
        out['washers'] = trimesh.util.concatenate(washers)
    return out


def washer_stl(spline) -> bytes:
    """The splined washer: the ring's washer OD and thickness, the bore's
    spline for its hole (so it keys to the shaft and can't turn)."""
    return _washer_mesh(spline).export(file_type='stl')


# ── 2D: exact outlines ──────────────────────────────────────────────────────

def _svg_d(segs, cx: float = 0.0) -> str:
    """Lines and arcs as an SVG path, y flipped (a CCW arc is sweep-flag 0)."""
    x0, y0 = segs[0].start
    parts = [f"M {cx + x0:.4f} {-y0:.4f}"]
    for seg in segs:
        x, y = seg.end
        if isinstance(seg, spl.Line):
            parts.append(f"L {cx + x:.4f} {-y:.4f}")
        else:
            large = 1 if abs(seg.sweep) > math.pi else 0
            parts.append(f"A {seg.radius:.4f} {seg.radius:.4f} 0 {large} {0 if seg.sweep > 0 else 1} "
                         f"{cx + x:.4f} {-y:.4f}")
    return " ".join(parts) + " Z"


def _svg(paths: list[str], half: float, title: str) -> str:
    sw = max(0.1, half * 0.006)
    body = "\n".join(f'<path d="{d}" fill="none" stroke="#1a1a1a" stroke-width="{sw:.3f}"/>'
                     for d in paths)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-half} {-half} {2 * half} {2 * half}" '
            f'width="{2 * half}mm" height="{2 * half}mm">\n<title>{title}</title>\n{body}\n</svg>\n')


def _circle_d(r: float) -> str:
    return f"M {r:.4f} 0 A {r:.4f} {r:.4f} 0 1 0 {-r:.4f} 0 A {r:.4f} {r:.4f} 0 1 0 {r:.4f} 0 Z"


def shaft_svg(spline) -> str:
    sp = _as_spline(spline)
    return _svg([_svg_d(spl.shaft_path(sp))], spl.shaft(sp).outer / 2 + 2,
                f"Splined shaft {sp.label()} — {spl.fit_source(sp)}")


def washer_svg(spline) -> str:
    sp = _as_spline(spline)
    rt = spline['retainer']
    return _svg([_circle_d(rt['washer_od'] / 2.0), _svg_d(spl.path(sp))], rt['washer_od'] / 2 + 2,
                f"Splined washer {sp.label()}, {rt['washer_od']:g} OD x {rt['washer_t']:g}")


def _dxf(segments_by_layer: dict) -> bytes:
    import ezdxf
    doc = ezdxf.new('R2010')
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    for layer, items in segments_by_layer.items():
        for item in items:
            if isinstance(item, tuple):                          # ('circle', r)
                msp.add_circle((0, 0), item[1], dxfattribs={'layer': layer})
            elif isinstance(item, spl.Line):
                msp.add_line(item.start, item.end, dxfattribs={'layer': layer})
            else:
                a0, a1 = ((item.start_angle, item.end_angle) if item.sweep > 0
                          else (item.end_angle, item.start_angle))
                msp.add_arc(item.center, item.radius, math.degrees(a0), math.degrees(a1),
                            dxfattribs={'layer': layer})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode('utf-8')


def shaft_dxf(spline) -> bytes:
    return _dxf({'SHAFT': spl.shaft_path(_as_spline(spline))})


def washer_dxf(spline) -> bytes:
    rt = spline['retainer']
    return _dxf({'WASHER': [('circle', rt['washer_od'] / 2.0)] + spl.path(_as_spline(spline))})
