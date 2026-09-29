"""
spline_parts.py — the parts that go with a splined bore (ADR-017): a sample
splined shaft that fits it, and the splined washer that sits under the
retaining ring. Geometry from cct_common (splines at the default fit,
retaining_rings for the ring's groove); this module only builds the files.

    shaft_stl(spline, length, groove_at)   the shaft standing on z = 0, its ring
                                           groove `groove_at` mm below the top
    washer_stl(spline)                     the washer, OD × thickness from the ring
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


def shaft_stl(spline, length: float, groove_at: float) -> bytes:
    """The sample shaft: the mating external spline, `length` long, with the
    ring's groove (DIN 471: width m, diameter d2 — cut across the tooth tops)
    whose outer wall is `groove_at` mm below the top end."""
    sp = _as_spline(spline)
    c = _spline_print(spline)
    rt = spline.get('retainer') if isinstance(spline, dict) else None
    pts = spl.printed(spl.sample_closed(spl.shaft_path(sp), 0.02), c, hole=False)
    mesh = trimesh.creation.extrude_polygon(_polygon(pts), length)
    if rt:
        m = rt['m'] + 2 * c                                   # a pocket grows...
        r_groove = rt['d2'] / 2.0 - c                         # ...its floor comes in
        outer = trimesh.creation.cylinder(radius=spl.shaft(sp).outer, height=m, sections=_SECTIONS)
        inner = trimesh.creation.cylinder(radius=r_groove, height=m + 1.0, sections=_SECTIONS)
        groove = trimesh.boolean.difference([outer, inner], engine='manifold')
        groove.apply_translation([0.0, 0.0, length - groove_at - m / 2.0])
        mesh = trimesh.boolean.difference([mesh, groove], engine='manifold')
    return mesh.export(file_type='stl')


def washer_stl(spline) -> bytes:
    """The splined washer: the ring's washer OD and thickness, the bore's
    spline for its hole (so it keys to the shaft and can't turn)."""
    sp = _as_spline(spline)
    c = _spline_print(spline)
    rt = spline['retainer']
    hole = spl.printed(spl.sample_closed(spl.path(sp), 0.02), c, hole=True)
    disc = ShapelyPoint(0, 0).buffer(rt['washer_od'] / 2.0 - c, resolution=_SECTIONS // 4)
    ring = shapely_orient(disc.difference(ShapelyPolygon(hole)), sign=1.0)
    return trimesh.creation.extrude_polygon(ring, rt['washer_t']).export(file_type='stl')


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
