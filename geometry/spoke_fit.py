"""Spoke settings that fit the pulley they are on.

Spoke settings are absolute millimetres, so a layout that suits a big pulley
can be impossible on a smaller one (a smaller pitch or fewer teeth): fillets
collide, spokes crowd the rim, the hub fills the web. fit_spokes() decides
what can actually be built and, when the requested settings cannot, the
smallest change that makes them buildable.

It judges a layout with the real spoke builder, _spoke_void_polygons() — the
same code the preview, STL and STEP use, with the same fillet-order check the
DXF/STEP segment builder shares — so "valid" here means "the exporters can
make it", not a separate formula that can drift from them.

Changes are made in the order the settings depend on each other, least
intrusive first: make room between hub and rim (Hub OD, then Rim Depth), then
Fillet Base, Fillet Tip, Spoke Width, Spoke Count; finally the fillets are
grown back as far as the new width/count allows.
"""
import math
from dataclasses import dataclass, field
from functools import lru_cache

WALL = 0.5         # mm: thinnest hub wall around the bore that the builder accepts
HUB_WALL = 1.0     # mm: hub wall kept when Auto-fit has to shrink the hub
MIN_RIM = 1.0      # mm: rim kept when Auto-fit has to shrink it (never more than asked for)
MIN_WEB = 0.5      # mm: the builder needs R_rim_inner > R_hub + 0.5
MIN_OPENING = 1.5  # mm: radial room we aim to leave for an opening when making room
MIN_WIDTH = 0.5    # mm
MIN_AREA = 0.05    # mm²: an opening smaller than this is degenerate

FIELDS = ('hub_od', 'rim_depth', 'width', 'fillet_tip', 'fillet_base', 'count')
LABELS = {'hub_od': 'Hub OD', 'rim_depth': 'Rim Depth', 'width': 'Spoke Width',
          'fillet_tip': 'Fillet Tip', 'fillet_base': 'Fillet Base', 'count': 'Spoke Count'}


@dataclass
class SpokeFit:
    ok: bool                         # the requested settings are buildable as they are
    possible: bool                   # some spoke layout fits this pulley at all
    fitted: dict                     # settings to build with (== requested when ok)
    changes: list = field(default_factory=list)   # [(field, requested, fitted)]


def _valid(r_root, bore, s):
    """Can the spoke builder make this layout cleanly?"""
    from shapely.geometry import Polygon
    from exporters.png_exporter import _spoke_void_polygons

    r_hub = s['hub_od'] / 2.0
    r_rim = r_root - s['rim_depth']
    n, w = int(s['count']), s['width']
    if n < 2 or w < MIN_WIDTH - 1e-9:
        return False
    if s['hub_od'] < bore + 2 * WALL - 1e-9:
        return False
    if r_rim < r_hub + MIN_WEB + 1e-9:
        return False
    if w / 2.0 > r_rim * 0.45:           # the builder would silently narrow the spoke
        return False
    report = {}
    try:
        voids = _spoke_void_polygons(r_hub, r_rim, n, w, s['fillet_tip'], s['fillet_base'],
                                     report=report)
    except ValueError:                    # fillets cross on a flank
        return False
    if len(voids) != n or report:         # or a requested fillet was silently left out
        return False
    for pts in voids:
        poly = Polygon(pts)
        if poly.area < MIN_AREA:
            return False
        if not poly.is_valid:
            # The builder can repeat a point (segments ~1e-16 mm), which shapely
            # reports as a self-intersection. Repaired, such a polygon is one
            # piece with the same area; a real crossing (a bowtie) is not.
            fixed = poly.buffer(0)
            if fixed.geom_type != 'Polygon' or abs(fixed.area - poly.area) > 0.01 * poly.area:
                return False
    return True


def _largest(lo, hi, ok, steps=24):
    """Largest x in [lo, hi] with ok(x), given ok(lo) (bisection)."""
    if ok(hi):
        return hi
    for _ in range(steps):
        mid = (lo + hi) / 2.0
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _floor(x, step=0.05):
    return math.floor(x / step + 1e-9) * step


@lru_cache(maxsize=512)
def _fit_cached(r_root, bore, hub_od, rim_depth, width, fillet_tip, fillet_base, count):
    req = dict(hub_od=hub_od, rim_depth=rim_depth, width=width,
               fillet_tip=fillet_tip, fillet_base=fillet_base, count=count)
    if _valid(r_root, bore, req):
        return SpokeFit(True, True, dict(req))

    s = dict(req)
    # 1. The hub must clear the bore.
    s['hub_od'] = max(s['hub_od'], bore + 2 * WALL)
    # 2. Room for the openings between hub and rim. If there is too little,
    #    Rim Depth and Hub OD give up their excess over a sensible minimum in
    #    proportion — neither is driven to nothing while the other stays big.
    #    If even the minimums don't fit, the pulley is too small for spokes.
    need = MIN_WEB + MIN_OPENING
    rim_min = min(s['rim_depth'], MIN_RIM)
    hub_min = min(s['hub_od'], bore + 2 * HUB_WALL)
    if r_root - s['rim_depth'] - s['hub_od'] / 2.0 < need:
        spare = r_root - need - rim_min - hub_min / 2.0
        if spare < 0:
            return SpokeFit(False, False, {})
        rim_x, hub_x = s['rim_depth'] - rim_min, (s['hub_od'] - hub_min) / 2.0
        k = spare / (rim_x + hub_x) if rim_x + hub_x > 0 else 0.0
        s['rim_depth'] = rim_min + rim_x * k
        s['hub_od'] = hub_min + 2.0 * hub_x * k

    def trial(**kw):
        return _valid(r_root, bore, dict(s, **kw))

    # 3. Best case: one setting is the problem — shrink just that one.
    if not trial():
        for k, lo in (('fillet_base', 0.0), ('fillet_tip', 0.0), ('width', MIN_WIDTH)):
            if s[k] > lo and trial(**{k: lo}):
                s[k] = _largest(lo, s[k], lambda v, k=k: trial(**{k: v}))
                break
    # 4. Otherwise fillets, then width, then count — each only as far as needed.
    if not trial():
        s['fillet_base'] = _largest(0.0, s['fillet_base'], lambda v: trial(fillet_base=v)) \
            if trial(fillet_base=0.0) else 0.0
    if not trial():
        s['fillet_tip'] = _largest(0.0, s['fillet_tip'], lambda v: trial(fillet_tip=v)) \
            if trial(fillet_tip=0.0) else 0.0
    if not trial():
        if trial(width=MIN_WIDTH):
            s['width'] = _largest(MIN_WIDTH, s['width'], lambda v: trial(width=v))
        else:
            s['width'] = MIN_WIDTH
    while not trial() and s['count'] > 2:
        s['count'] -= 1
    if not trial():
        return SpokeFit(False, False, {})

    # 5. Grow the fillets back as far as the new layout allows.
    s['fillet_tip'] = _largest(s['fillet_tip'], req['fillet_tip'], lambda v: trial(fillet_tip=v))
    s['fillet_base'] = _largest(s['fillet_base'], req['fillet_base'],
                                lambda v: trial(fillet_base=v))

    # Round to tidy values that stay on the valid side.
    for k in ('rim_depth', 'width', 'fillet_tip', 'fillet_base'):
        if s[k] < req[k]:
            s[k] = _floor(s[k])
    for k in ('fillet_tip', 'fillet_base'):              # a sliver of a fillet is no fillet:
        if s[k] < req[k] and s[k] < 0.25:                # say 0 rather than 0.05
            s[k] = 0.0
    if s['hub_od'] > req['hub_od']:
        s['hub_od'] = math.ceil(s['hub_od'] / 0.05 - 1e-9) * 0.05
    elif s['hub_od'] < req['hub_od']:
        s['hub_od'] = _floor(s['hub_od'])
    s = {k: (round(v, 3) if k != 'count' else int(v)) for k, v in s.items()}
    if not _valid(r_root, bore, s):                      # rounding tipped it over: back off
        for k in ('fillet_base', 'fillet_tip'):
            s[k] = _floor(s[k] * 0.9)
        if not _valid(r_root, bore, s):
            return SpokeFit(False, False, {})

    changes = [(k, req[k], s[k]) for k in FIELDS if abs(s[k] - req[k]) > 1e-9]
    return SpokeFit(False, True, s, changes)


def fit_spokes(r_root, bore, hub_od, rim_depth, width, fillet_tip, fillet_base, count):
    """Check requested spoke settings against a pulley whose groove bottom is at
    radius r_root (mm), with a bore of `bore` mm; see SpokeFit."""
    r = lambda v: round(float(v), 4)
    return _fit_cached(r(r_root), r(bore), r(hub_od), r(rim_depth), r(width),
                       r(fillet_tip), r(fillet_base), int(count))


def describe(fit):
    """One line per change, for the warning in the page."""
    if not fit.possible:
        return ['This pulley is too small for spokes with this bore — turn Spokes off, '
                'use a smaller bore or a bigger pulley.']
    out = []
    for k, a, b in fit.changes:
        if k == 'count':
            out.append(f'{LABELS[k]}: {int(a)} → {int(b)}')
        else:
            out.append(f'{LABELS[k]}: {a:g} → {b:g} mm')
    return out
