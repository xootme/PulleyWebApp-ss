"""What the page calls each setting, and the refusals that name one (the owner,
2026-10-21: messages that make sense to the user, with the labelled parameter,
and an Auto-fix that is a value). One table, plain data, to move to the shared
labels in cct_common 0.26.0 (Planetary's geometry/labels.py is the model; the
helpers below are its, the table is the pulley's).

SETTINGS: query key (pulley 1's; pulley 2's is p2_<key>) -> (the page's label,
the page's field id with {n} for the pulley's number). The range, the type and
the default are agent.py's Param list (the agent API's describe), so a link, the
page and an agent are held to the same limits. check(args) is what the page's
routes (/api/dimensions, the previews, every download) run before reading a
setting: the first bad one raises SettingError(message, field, fix), `field` the
page's id and `fix` a value the page's Auto-fix types in: the nearest allowed
value, a whole number for a count, the default for text."""
import math
import re

SETTINGS = {
    # the belt and pulley 1
    "family": ("Family", "family"), "pitch": ("Pitch", "pitch"),
    "belt_height": ("Belt Height", "belt_height"), "clearance_height": ("Clearance Height", "clearance_height"),
    "teeth": ("Num. Teeth", "teeth"), "bore": ("Bore Diameter", "bore"),
    "print_extra": ("3D Print Compensation", "print_extra"),
    "clearance_preset": ("Tooth Clearance", "clearance_preset"),
    "clearance_custom": ("Tooth Clearance Custom Offset", "clearance_custom"),
    "backlash_preset": ("Backlash", "backlash_preset"),
    "backlash_custom": ("Backlash Custom Offset", "backlash_custom"),
    "center_distance": ("Centre Distance", "center_distance"),
    # the bore card
    "bore_shape": ("Bore Shape", "bore{n}_shape"), "hub_flat_depth": ("Flat Depth", "hub{n}_flat_depth"),
    "hub_keyway_w": ("Key Width", "hub{n}_keyway_w"), "hub_keyway_h": ("Key Depth", "hub{n}_keyway_h"),
    "spline_type": ("Spline", "spline{n}_type"), "spline_n": ("Splines N", "spline{n}_n"),
    "spline_minor": ("Spline Minor d", "spline{n}_minor"), "spline_major": ("Spline Major D", "spline{n}_major"),
    "spline_width": ("Spline Width B", "spline{n}_width"), "spline_m": ("Spline Module m", "spline{n}_m"),
    "spline_z": ("Spline Teeth z", "spline{n}_z"), "spline_pa": ("Spline Pressure Angle", "spline{n}_pa"),
    "spline_root": ("Spline Root", "spline{n}_root"), "spline_af": ("Hex Across Flats", "spline{n}_af"),
    "spline_series": ("Retaining Rings", "spline{n}_series"), "spline_ac": ("Hex Across Corners", "spline{n}_ac"),
    # the hub and its set screws
    "hub_od": ("Hub OD", "hub{n}_od"), "hub_height": ("Hub Height", "hub{n}_height"),
    "hub_screw_size": ("Set Screw Size", "hub{n}_screw_size"), "hub_screw_hold": ("Retention Method", "hub{n}_retention"),
    "hub_screw_count": ("Number of Screws", "hub{n}_screw_count"),
    "hub_screw_hole_dia": ("Screw Hole ⌀", "hub{n}_screw_hole_dia"),
    "screw_hole_shape": ("Threaded Hole Shape", "screw_hole_shape"),
    "thread_engagement": ("Thread Engagement", "thread_engagement"), "hex_flat": ("Hex Flats", "hex_flat"),
    # spokes
    "spokes_count": ("Spoke Count", "spokes{n}_count"), "spokes_width": ("Spoke Width", "spokes{n}_width"),
    "spokes_hub_od": ("Spokes Hub OD", "spokes{n}_hub_od"), "spokes_rim_depth": ("Rim Depth", "spokes{n}_rim_depth"),
    "spokes_fillet_tip": ("Fillet Tip", "spokes{n}_fillet_tip"),
    "spokes_fillet_base": ("Fillet Base", "spokes{n}_fillet_base"),
    "spokes_height": ("Spoke Height", "spokes{n}_height"),
    # flanges
    "flange_angle": ("Flange Angle", "flange{n}_angle"), "flange_rim_radius": ("Flange Rim Radius", "flange{n}_rim_radius"),
    "flange_height": ("Flange Height", "flange{n}_height"),
    "flange_plate_height": ("Plate Thickness", "flange{n}_plate_height"),
    "flange_bend_radius": ("Bend Radius", "flange{n}_bend_radius"),
    "flange_nub_count": ("Number of Nubs", "flange{n}_nub_count"), "flange_nub_dia": ("Nub Diameter", "flange{n}_nub_dia"),
    "flange_nub_height": ("Nub Height", "flange{n}_nub_height"),
    "flange_nub_allowance": ("Socket Fit Allow.", "flange{n}_nub_allowance"),
    "flange_support_nozzle_dia": ("Nozzle Dia.", "flange{n}_support_nozzle_dia"),
    "flange_support_max_spacing": ("Max Spacing", "flange{n}_support_max_spacing"),
    "flange_support_air_gap": ("Air Gap", "flange{n}_support_air_gap"),
}
# pulley 2's own fields keep the query key's p2_ (teeth, bore, …); the cards number theirs
P2_SAME_ID = {"teeth", "bore", "print_extra", "clearance_preset", "clearance_custom", "backlash_preset",
              "backlash_custom"}
UNITS = {"deg": "°"}


def split(key: str):
    """(pulley 1's key, the pulley's number) for a query key."""
    return (key[3:], 2) if key.startswith("p2_") else (key, 1)


def label(key: str) -> str:
    """What the page calls `key` ("Hub OD", "Pulley 2 Num. Teeth")."""
    base, n = split(key)
    name = SETTINGS.get(base, (base,))[0]
    return f"Pulley 2 {name}" if n == 2 else name


def field(key: str) -> str:
    """The page's id for `key`'s field ("hub2_od" for p2_hub_od)."""
    base, n = split(key)
    if base not in SETTINGS:
        return key
    if n == 2 and base in P2_SAME_ID:
        return key
    return SETTINGS[base][1].format(n=n)


# -- the helpers (Planetary's geometry/labels.py, for cct_common 0.26.0) ---------
def _with_unit(text: str, unit: str) -> str:
    u = UNITS.get(unit, unit)
    return text if not u else f"{text}{'' if u in ('°', '%') else ' '}{u}"


def nearest(v: float, lo: float, hi: float, whole: bool = False) -> str:
    """The allowed value nearest `v`, as the Auto-fix types it."""
    v = min(max(v, lo), hi)
    if whole:                               # a limit may be unbounded (±inf): round inside it
        v = round(v)
        if math.isfinite(lo):
            v = max(v, math.ceil(lo))
        if math.isfinite(hi):
            v = min(v, math.floor(hi))
    return f"{v:g}"


class SettingError(ValueError):
    """A setting the server can't use: the message names it as the page does,
    `field` is the page's id and `fix` a value the Auto-fix can type in."""

    def __init__(self, message: str, field: str, fix):
        super().__init__(message)
        self.field, self.fix = field, fix


def number(raw, key: str, lo=-math.inf, hi=math.inf, whole=False, default=None, unit=""):
    """`raw` as a number within [lo, hi], else SettingError: the fix is the nearest
    allowed value; for text, `default` (blank when it's blank), else the nearest limit."""
    text = str(raw).strip()
    try:
        v = float(text)
        if math.isnan(v):
            raise ValueError(text)
    except ValueError:
        if default is not None and str(default).strip() == "":
            fix = ""
        else:
            try:
                fix = nearest(float(default), lo, hi, whole)
            except (TypeError, ValueError):
                fix = nearest(lo if math.isfinite(lo) else 0.0, lo, hi, whole)
        raise SettingError(f"{label(key)} must be {'a whole number' if whole else 'a number'}.",
                           field(key), fix) from None
    if whole and math.isfinite(v) and not v.is_integer():
        raise SettingError(f"{label(key)} must be a whole number.", field(key), nearest(v, lo, hi, whole))
    if not lo <= v <= hi:
        what = "a whole number from " if whole else ""
        raise SettingError(f"{label(key)} must be {what}{_with_unit(f'{lo:g} to {hi:g}', unit)}.",
                           field(key), nearest(v, lo, hi, whole))
    return int(v) if whole else v


def choice(raw, key: str, options, default=None):
    """`raw` if it is one of `options`, else SettingError, the fix `default` (or the first)."""
    text = str(raw).strip()
    if text not in options:
        raise SettingError(f"{label(key)}: “{text}” isn't one of its choices.", field(key),
                           default if default is not None and str(default) in options else options[0])
    return text


# -- the limits: agent.py's Param list ---------------------------------------------
_LIMITS: dict = {}


def configure(params) -> None:
    """The app's Param list (agent._params), handed over when app.py starts: this
    module never imports app (run as `python app.py`, that would load it twice)."""
    _LIMITS.clear()
    _LIMITS.update({p.name: p for p in params})


def limits() -> dict:
    """pulley 1's key -> agent.py's Param (its type, range, choices, default, when)."""
    if not _LIMITS:
        raise RuntimeError("geometry.labels.configure() wasn't called (app.py does it)")
    return _LIMITS


def _on(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def _applies(p, args, prefix: str) -> bool:
    """Whether a setting matters with these args (its Param's `when`, pulley 2's needing dual)."""
    if prefix and not _on(args.get("dual")):
        return False
    for k, want in (p.when or {}).items():
        lim = limits().get(k)
        got = args.get((prefix if lim is not None and lim.per_part else "") + k)
        if want is True and not _on(got):
            return False
        if want is False and _on(got):
            return False
        if want == "set" and str(got or "").strip() in ("", "0"):
            return False
        if want not in (True, False, "set") and str(got) != str(want):
            return False
    return True


def check(args) -> None:
    """Raise SettingError for the first setting in `args` the page couldn't have sent;
    a blank or missing one takes the app's default, as before."""
    for prefix in ("", "p2_"):
        for name in SETTINGS:
            p = limits().get(name)
            key = prefix + name
            if p is None or (prefix and not p.per_part):
                continue
            raw = args.get(key)
            if raw is None or str(raw).strip() == "" or not _applies(p, args, prefix):
                continue
            if p.type in ("number", "integer"):
                number(raw, key, -math.inf if p.min is None else p.min, math.inf if p.max is None else p.max,
                       whole=p.type == "integer", default=p.default, unit=p.unit)
            elif p.type == "enum":
                options = [str(c) for c in p.choices]
                if p.choices_by:
                    (other, table), = p.choices_by.items()
                    options = [str(c) for c in table.get(str(args.get(other, limits()[other].default)), [])]
                if options:
                    choice(raw, key, options, p.default)


def blank_number(key: str, raw) -> bool:
    """Whether `raw` is a number setting left empty: app.py drops it before the routes
    read it, so each takes its default as for a missing one, as check() does (a blank
    belt height made the downloads fail, 2026-10-28)."""
    base, n = split(key)
    p = limits().get(base)
    return (p is not None and p.type in ("number", "integer") and (n == 1 or p.per_part)
            and str(raw).strip() == "")


def agent_message(text: str) -> str:
    """The agent API's refusal ("p2_teeth must be at most 500") with the page's label
    in front of the key ("Pulley 2 Num. Teeth (p2_teeth) must be at most 500")."""
    def named(m):
        base, _ = split(m.group(1))
        return f"{label(m.group(1))} ({m.group(1)}) " if base in SETTINGS else m.group(0)
    return re.sub(r"\b((?:p2_)?[a-z][a-z0-9_]*) (?=must )", named, text)
