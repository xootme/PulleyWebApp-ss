"""
agent.py — Timing Pulley Generator's side of the CCT agent interface
(cct_common.agent_api, docs/AGENT_API.md in cct_common; ADR-019).

The parameters are the app's own query keys — the ones the page sends and the
download routes read — so an agent's design and a browser link are the same
thing. `check` is the Dimensions panel as data; `files` is the download
window's file list (templates/index.html `_dlParts` / `_dlFiles`) rebuilt on
the server. `register(app)` wires the four /api/v1 routes.
"""
from __future__ import annotations

from cct_common.agent_api import Description, Param, Part, AgentError, register_agent_api

APP_ID = "pulleys"

_BOOL_TF = ("true", "false")        # `dual` is sent as true / false, the rest as 1 / 0


def _params(families: dict, screw_sizes: list) -> list:
    P = Param
    pitch_by = {"family": {f: list(ps) for f, ps in families.items()}}
    size = "Size"
    return [
        # ── the belt and pulley 1 ──
        P("family", "enum", "Belt profile family", choices=tuple(families), default="HTD", group="Belt"),
        P("pitch", "enum", "Belt pitch within the family", choices_by=pitch_by, default="5M", group="Belt"),
        P("belt_height", "number", "Belt width", unit="mm", min=1, max=200, default=10, group="Belt"),
        P("clearance_height", "number", "Extra face width beyond the belt (the page defaults it to the "
          "belt standard's minimum face width)", unit="mm", min=0, max=50, default=0, group="Belt"),
        P("teeth", "integer", "Number of teeth", min=8, max=500, default=20, group=size, per_part=True),
        P("bore", "number", "Round bore diameter (a spline or hex bar sets the bore itself)", unit="mm",
          min=1, max=300, default=8, group=size, per_part=True),
        P("print_extra", "number", "3D-print compensation: tooth profile offset; for a splined or hex "
          "bore the STL's bore grows by it (STEP, SVG, DXF stay nominal)", unit="mm", min=0, max=1,
          default=0, group=size, per_part=True),
        P("clearance_preset", "enum", "Tooth clearance", choices=("TIGHT", "STANDARD", "LOOSE", "CUSTOM"),
          default="STANDARD", group=size, per_part=True),
        P("clearance_custom", "number", "Tooth clearance when CUSTOM", unit="mm", min=0, max=2,
          when={"clearance_preset": "CUSTOM"}, group=size, per_part=True),
        P("backlash_preset", "enum", "Backlash", choices=("NONE", "TIGHT", "STANDARD", "LOOSE", "CUSTOM"),
          default="STANDARD", group=size, per_part=True),
        P("backlash_custom", "number", "Backlash when CUSTOM", unit="mm", min=0, max=2,
          when={"backlash_preset": "CUSTOM"}, group=size, per_part=True),
        # ── a two-pulley drive ──
        P("dual", "boolean", "Two-pulley drive: pulley 2 takes the p2_ parameters", default=False,
          group="Drive", wire=_BOOL_TF),
        P("center_distance", "number", "Centre distance (snapped to a whole-tooth belt; check reports "
          "the corrected value)", unit="mm", min=1, max=10000, when={"dual": True}, group="Drive"),
        # ── bore shape ──
        P("hub_flat_depth", "number", "D-flat: how far the flat sits in from the bore wall", unit="mm",
          min=0.1, max=50, group="Bore shape", per_part=True),
        P("hub_keyway_w", "number", "Keyway width", unit="mm", min=0.5, max=50, group="Bore shape",
          per_part=True),
        P("hub_keyway_h", "number", "Keyway depth into the hub (ISO 773 t2 + corner clearance)",
          unit="mm", min=0.1, max=50, when={"hub_keyway_w": "set"}, group="Bore shape", per_part=True),
        P("bore_shape", "enum", "round (with hub_flat_depth or hub_keyway_* for a D-flat or keyway), or "
          "spline (a splined or hex bar bore: see spline_type)", choices=("round", "spline"),
          default="round", group="Bore shape", per_part=True),
        P("spline_type", "enum", "straight (ISO 14), involute (ISO 4156) or hex (hex bar)",
          choices=("straight", "involute", "hex"), when={"bore_shape": "spline"}, group="Bore shape",
          per_part=True),
        P("spline_n", "integer", "ISO 14: number of splines N", min=3, max=20,
          when={"spline_type": "straight"}, group="Bore shape", per_part=True),
        P("spline_minor", "number", "ISO 14: minor diameter d", unit="mm", min=1, max=300,
          when={"spline_type": "straight"}, group="Bore shape", per_part=True),
        P("spline_major", "number", "ISO 14: major diameter D", unit="mm", min=1, max=300,
          when={"spline_type": "straight"}, group="Bore shape", per_part=True),
        P("spline_width", "number", "ISO 14: spline width B", unit="mm", min=0.5, max=50,
          when={"spline_type": "straight"}, group="Bore shape", per_part=True),
        P("spline_m", "number", "ISO 4156: module m", unit="mm", min=0.25, max=10,
          when={"spline_type": "involute"}, group="Bore shape", per_part=True),
        P("spline_z", "integer", "ISO 4156: number of teeth z", min=6, max=100,
          when={"spline_type": "involute"}, group="Bore shape", per_part=True),
        P("spline_pa", "enum", "ISO 4156: pressure angle", choices=("30", "37.5", "45"),
          when={"spline_type": "involute"}, group="Bore shape", per_part=True),
        P("spline_root", "enum", "ISO 4156: root (flat only at 30 degrees)", choices=("flat", "fillet"),
          when={"spline_type": "involute"}, group="Bore shape", per_part=True),
        P("spline_af", "number", "Hex bar: across flats (1/2 inch = 12.7)", unit="mm", min=1, max=80,
          when={"spline_type": "hex"}, group="Bore shape", per_part=True),
        P("spline_series", "enum", "Hex bar: which retaining rings — metric (DIN 471) or inch (SH / 5100)",
          choices=("metric", "inch"), when={"spline_type": "hex"}, group="Bore shape", per_part=True),
        P("spline_rounded", "boolean", "Hex bar: rounded corners (rounded hex stock)",
          when={"spline_type": "hex"}, group="Bore shape", per_part=True),
        P("spline_ac", "number", "Hex bar: across-corners diameter of rounded stock (REV 1/2 inch "
          "rounded hex: 13.75)", unit="mm", min=1, max=100, when={"spline_rounded": True},
          group="Bore shape", per_part=True),
        P("spline_ring_top", "boolean", "Retaining ring on the top face",
          when={"bore_shape": "spline"}, group="Retaining rings", per_part=True),
        P("spline_cb_top", "boolean", "A counterbore in the top face for its ring (and washer), so they "
          "sit flush; false: they sit on the face", default=True, when={"spline_ring_top": True},
          group="Retaining rings", per_part=True),
        P("spline_ring_bottom", "boolean", "Retaining ring on the bottom face",
          when={"bore_shape": "spline"}, group="Retaining rings", per_part=True),
        P("spline_cb_bottom", "boolean", "A counterbore in the bottom face for its ring (and washer); "
          "false: they sit on the face", default=True, when={"spline_ring_bottom": True},
          group="Retaining rings", per_part=True),
        P("spline_washer", "boolean", "Splined washer under each ring", when={"bore_shape": "spline"},
          group="Retaining rings", per_part=True),
        # ── hub and set screws ──
        P("hub_od", "number", "Hub outside diameter (0: no hub)", unit="mm", min=0, max=300,
          group="Hub", per_part=True),
        P("hub_height", "number", "Hub height (0: no hub)", unit="mm", min=0, max=200, group="Hub",
          per_part=True),
        P("hub_screw_size", "enum", "Set screw size (none on a spline; one or two on a hex bar's flats)",
          choices=tuple(screw_sizes) + ("Custom",), when={"hub_od": "set"}, group="Hub", per_part=True),
        P("hub_screw_hold", "enum", "How the screw holds: thread (self-tapping), nut (captured nut), "
          "insert (heat-set insert)", choices=("thread", "nut", "insert"), when={"hub_screw_size": "set"},
          group="Hub", per_part=True),
        P("hub_screw_count", "integer", "Number of screws", min=1, max=2, when={"hub_screw_size": "set"},
          group="Hub", per_part=True),
        P("hub_screw_hole_dia", "number", "Hole diameter for a Custom size or a heat-set insert", unit="mm",
          min=0.5, max=30, group="Hub", per_part=True),
        P("screw_hole_shape", "enum", "Threaded hole: round or hex", choices=("round", "hex"), group="Hub"),
        P("thread_engagement", "integer", "Threaded round hole: % thread engagement", min=0, max=100,
          group="Hub"),
        P("hex_flat", "integer", "Threaded hex hole: across flats as % of the major diameter", min=50,
          max=100, group="Hub"),
        # ── spokes ──
        P("spokes_enabled", "boolean", "Spokes (a lightened web)", group="Spokes", per_part=True),
        P("spokes_count", "integer", "Number of spokes", min=2, max=12, when={"spokes_enabled": True},
          group="Spokes", per_part=True),
        P("spokes_width", "number", "Spoke width", unit="mm", min=0.5, max=100, when={"spokes_enabled": True},
          group="Spokes", per_part=True),
        P("spokes_hub_od", "number", "Spoke hub diameter (fitted round the bore)", unit="mm", min=0, max=300,
          when={"spokes_enabled": True}, group="Spokes", per_part=True),
        P("spokes_rim_depth", "number", "Rim depth under the teeth", unit="mm", min=0.5, max=50,
          when={"spokes_enabled": True}, group="Spokes", per_part=True),
        P("spokes_fillet_tip", "number", "Fillet at the rim", unit="mm", min=0, max=20,
          when={"spokes_enabled": True}, group="Spokes", per_part=True),
        P("spokes_fillet_base", "number", "Fillet at the hub", unit="mm", min=0, max=20,
          when={"spokes_enabled": True}, group="Spokes", per_part=True),
        P("spokes_height", "number", "Web height (0: full face width)", unit="mm", min=0, max=200,
          when={"spokes_enabled": True}, group="Spokes", per_part=True),
        # ── flanges ──
        P("flange_enabled", "boolean", "Flanges on both sides of the belt", group="Flanges", per_part=True),
        P("flange_3dprint", "boolean", "Printed flanges (else metal plates)", when={"flange_enabled": True},
          group="Flanges", per_part=True),
        P("flange_top_separate", "boolean", "Print the top flange as a separate part (v1: its own file "
          "isn't exported yet)", when={"flange_3dprint": True}, group="Flanges", per_part=True),
        P("flange_angle", "number", "Flange angle (ISO: 8-25)", unit="deg", min=0, max=45,
          when={"flange_enabled": True}, group="Flanges", per_part=True),
        P("flange_rim_radius", "number", "How far the flange reaches past the teeth", unit="mm", min=0.5,
          max=50, when={"flange_enabled": True}, group="Flanges", per_part=True),
        P("flange_height", "number", "Printed flange thickness", unit="mm", min=0.1, max=20,
          when={"flange_3dprint": True}, group="Flanges", per_part=True),
        P("flange_plate_height", "number", "Metal plate thickness", unit="mm", min=0.1, max=10,
          when={"flange_3dprint": False}, group="Flanges", per_part=True),
        P("flange_bend_radius", "number", "Metal plate bend radius (0: 1.5 x thickness)", unit="mm", min=0,
          max=20, when={"flange_3dprint": False}, group="Flanges", per_part=True),
        # A separate top flange: gluing nubs locate it on the pulley.
        P("flange_nubs_enabled", "boolean", "Gluing nubs on a separate top flange (pins into sockets in "
          "the pulley)", when={"flange_top_separate": True}, group="Flanges", per_part=True),
        P("flange_nub_count", "integer", "Number of nubs", min=1, max=100,
          when={"flange_nubs_enabled": True}, group="Flanges", per_part=True),
        P("flange_nub_dia", "number", "Nub diameter", unit="mm", min=1, max=15,
          when={"flange_nubs_enabled": True}, group="Flanges", per_part=True),
        P("flange_nub_height", "number", "Nub height", unit="mm", min=0.5, max=20,
          when={"flange_nubs_enabled": True}, group="Flanges", per_part=True),
        P("flange_nub_allowance", "number", "Socket fit allowance round each nub", unit="mm", min=0, max=2,
          when={"flange_nubs_enabled": True}, group="Flanges", per_part=True),
        # A top flange printed in place: breakaway supports under its overhang.
        P("flange_supports_enabled", "boolean", "Breakaway print supports under a top flange printed in "
          "place", when={"flange_top_separate": False}, group="Flanges", per_part=True),
        P("flange_support_nozzle_dia", "number", "Supports: printer nozzle diameter", unit="mm", min=0.1,
          max=2, when={"flange_supports_enabled": True}, group="Flanges", per_part=True),
        P("flange_support_max_spacing", "number", "Supports: largest gap between them", unit="mm", min=1,
          max=50, when={"flange_supports_enabled": True}, group="Flanges", per_part=True),
        P("flange_support_air_gap", "number", "Supports: air gap under the flange", unit="mm", min=0,
          max=1, when={"flange_supports_enabled": True}, group="Flanges", per_part=True),
    ]


PARTS = [
    Part("pulley1", "Pulley 1", ("step", "stl", "svg", "dxf")),
    Part("pulley2", "Pulley 2", ("step", "stl", "svg", "dxf"), when="dual"),
    Part("belt", "Belt", ("step", "stl", "svg", "dxf"), when="a belt family; STEP and STL need dual"),
    Part("drive", "Whole drive drawing", ("dxf",), when="dual, a belt family"),
    Part("shaft1", "Pulley 1 sample splined shaft", ("step", "stl", "svg", "dxf"),
         when="pulley 1 has a spline or hex bore"),
    Part("washer1", "Pulley 1 splined washer", ("step", "stl", "svg", "dxf"),
         when="pulley 1 has a ring and spline_washer"),
    Part("shaft2", "Pulley 2 sample splined shaft", ("step", "stl", "svg", "dxf"),
         when="pulley 2 has a spline or hex bore"),
    Part("washer2", "Pulley 2 splined washer", ("step", "stl", "svg", "dxf"),
         when="pulley 2 has a ring and spline_washer"),
]


def describe(version: str = "") -> Description:
    from app import FAMILIES
    from geometry.set_screw import INCH, METRIC
    return Description(
        app=APP_ID, title="Timing Pulley Generator",
        summary="Timing belt pulleys and two-pulley drives: HTD, GT, STD, T, AT, MXL-XXH, RPP profiles; "
                "round, D-flat, keyway, ISO 14 / ISO 4156 spline and hex bar bores; hubs with set screws, "
                "spokes, flanges, retaining rings. STEP, STL, SVG, DXF.",
        params=_params(FAMILIES, list(METRIC) + list(INCH)), parts=PARTS,
        prefixes={"p2_": "pulley 2 of a two-pulley drive (dual = true): p2_teeth, p2_bore, …"},
        notes=["Sizes are mm. Parameters left out take the app's defaults (describe lists them).",
               "check returns the dimensions, the spec warnings and an Auto-fix: fix.set holds the "
               "parameter changes (keyed by the page's field ids) and fix.changes says what they do.",
               "STL files carry the print compensation; STEP, SVG and DXF are nominal.",
               "STEP is one file for the whole design: an assembly of every part asked for, each a "
               "component placed as assembled (a shaft carries its retaining rings).",
               "With a splined or hex bore the STEP needs the cadquery backend; the small_step build "
               "refuses it for now."],
        version=version)


# ── check: the Dimensions panel, the spline and the spoke fit as data ───────

def _fix_params(fix_set: dict) -> dict:
    """The Auto-fix's page-field changes as agent parameters (the names
    describe lists): hub1_od -> hub_od, spline2_preset "N,d,D,B" -> p2_spline_n …"""
    import re
    out = {}
    for key, value in fix_set.items():
        m = re.match(r"^(hub|spline|flange|spokes)([12])_(.+)$", key)
        if not m:
            out[key] = value                                     # clearance_height, …
            continue
        kind, n, rest = m.groups()
        pfx = "p2_" if n == "2" else ""
        if kind == "spline" and rest == "preset":
            N, d, D, B = str(value).split(",")
            out.update({f"{pfx}spline_n": int(N), f"{pfx}spline_minor": float(d),
                        f"{pfx}spline_major": float(D), f"{pfx}spline_width": float(B)})
        elif kind == "spline" and rest == "hex_preset":
            series, af = str(value).split(":")[:2]
            out.update({f"{pfx}spline_series": series, f"{pfx}spline_af": float(af),
                        f"{pfx}spline_rounded": False})
        else:
            out[f"{pfx}{kind}_{rest}"] = value                   # hub_od, spline_z, flange_angle …
    return out


def check(query: dict) -> dict:
    from app import _dimensions, _spline_of, _spoke_fit
    from cct_common import splines
    out = _dimensions(dict(query, feature_build="1"))
    if out.get("fix"):
        out["fix"]["params"] = _fix_params(out["fix"]["set"])
    extra = {}
    for n, pfx in ((1, ""), (2, "p2_")) if query.get("dual") == "true" else ((1, ""),):
        sp = _spline_of(query, pfx)
        if sp:
            s = splines.Spline(**{k: sp[k] for k in ("kind", "n", "minor", "major", "width", "module",
                                                      "pressure", "root", "series")})
            extra[f"spline{n}"] = {"label": s.label(), "fit": splines.fit_source(s), "bore": sp["bore"],
                                   "hole_major": round(splines.hole(s).outer, 4),
                                   "shaft": [round(splines.shaft(s).inner, 4), round(splines.shaft(s).outer, 4)],
                                   "retainer": sp["retainer"]}
        if query.get(f"{pfx}spokes_enabled") == "1":
            fit = _spoke_fit(query, pfx)
            extra[f"spokes{n}"] = {"fits": bool(getattr(fit, "possible", True)),
                                   "changes": list(getattr(fit, "changes", []) or [])}
    return dict(out, **extra)


# ── files: the download window's list ──────────────────────────────────────

def files(query: dict, parts: list, formats: list) -> list:
    from app import PULLEY_SPECS, _resolve_key, _spline_of
    from geometry.pulley_geometry import BELT_FAMILIES, correct_center_distance
    dual = query.get("dual") == "true"
    has_belt = query.get("family", "HTD") in BELT_FAMILIES
    family, pitch = query.get("family", "HTD"), query.get("pitch", "5M")
    stem = f"{family}-{pitch}"
    out = []

    asm = []                     # STEP is one assembly file of every part asked for (the owner, 2026-10-06)
    ids = {"pulley1": "p1", "pulley2": "p2", "belt": "belt",
           "shaft1": "sh1", "washer1": "wa1", "shaft2": "sh2", "washer2": "wa2"}

    def add(part, fmt, path, params, name):
        if part in parts and fmt in formats:
            if fmt == "step":
                asm.append(part)
                if len(asm) == 1:
                    out.append(None)                         # the assembly's place in the list
                return
            out.append((part, fmt, path, params, name))

    for n, pfx in ((1, ""), (2, "p2_")) if dual else ((1, ""),):
        base = dict(query, pulley=str(n)) if n == 2 else dict(query)
        teeth = query.get(f"{pfx}teeth", "")
        name = f"{stem}-{teeth}T{'-P2' if n == 2 else ''}"
        add(f"pulley{n}", "step", None, None, None)
        # The server's STL names: -1flange when the top flange is a separate part, and
        # with print supports (a top printed in place) a -w-supports copy too.
        printed = query.get(f"{pfx}flange_enabled") == "1" and query.get(f"{pfx}flange_3dprint") == "1"
        separate = query.get(f"{pfx}flange_top_separate", "1") == "1"
        stl_name = f"{name}-1flange" if printed and separate else name
        add(f"pulley{n}", "stl", "/download/stl", base, f"{stl_name}.stl")
        if printed and not separate and query.get(f"{pfx}flange_supports_enabled") == "1":
            add(f"pulley{n}", "stl", "/download/stl", dict(base, with_supports="1"),
                f"{stl_name}-w-supports.stl")
        add(f"pulley{n}", "svg", "/download/svg", dict(base, include_data="1"), f"{name}.svg")
        add(f"pulley{n}", "dxf", "/download/dxf", base, f"{name}.dxf")
        sp = _spline_of(query, pfx)
        if sp:
            rt = sp["retainer"]
            add(f"shaft{n}", "step", None, None, None)
            if rt and rt["washer_t"] > 0:
                add(f"washer{n}", "step", None, None, None)
            for fmt in ("stl", "svg", "dxf"):
                add(f"shaft{n}", fmt, f"/download/spline-{fmt}", dict(base, pulley=str(n), part="shaft"),
                    f"{name}-spline-shaft.{fmt}")
                if rt and rt["washer_t"] > 0:
                    add(f"washer{n}", fmt, f"/download/spline-{fmt}", dict(base, pulley=str(n), part="washer"),
                        f"{name}-spline-washer.{fmt}")
    if has_belt:
        if dual:
            spec = PULLEY_SPECS[_resolve_key(family, pitch)]
            _L, n_belt, _c = correct_center_distance(
                spec["pitch"], int(query.get("teeth", spec["min_teeth"])),
                int(query.get("p2_teeth", spec["min_teeth"])), float(query.get("center_distance", 100)))
            belt = dict(query, n_belt=str(n_belt))
        else:
            belt = {"family": family, "pitch": pitch, "belt_height": query.get("belt_height", "10")}
        # a belt's STEP and STL are of the drive's loop: two pulleys only (the server
        # refuses them for one)
        for fmt in ("step", "stl", "svg", "dxf") if dual else ("svg", "dxf"):
            add("belt", fmt, f"/download/belt-{fmt}", belt, f"{stem}-belt.{fmt}")
        if dual:
            add("drive", "dxf", "/download/all-dxf", belt, f"{stem}-drive.dxf")
    if asm:
        design = f"{stem}-{query.get('teeth', '')}T" + (f"-{query.get('p2_teeth', '')}T" if dual else "")
        params = dict(query, parts=",".join(ids[p] for p in asm))
        if dual and has_belt:
            params["n_belt"] = belt["n_belt"]
        out[out.index(None)] = (",".join(asm), "step", "/download/assembly-step", params, f"{design}.step")
    # every file embeds the whole design for Import, whatever its own request names
    # (a single belt's is only its profile): app._design_of
    import json
    whole = json.dumps(query)
    return [(pt, fmt, path, dict(params, design=whole), name) for pt, fmt, path, params, name in out]


# ── charging: register the design, price it ─────────────────────────────────

def _account():
    from cct_common.account_routes import current_account_id
    return current_account_id()


def _design_union(listed) -> dict:
    """Every parameter any of the files sends: the design one payment covers."""
    union = {}
    for _part, _fmt, _path, params, _name in listed:
        union.update(params)
    return union


def make_design_id(charges):
    def design_id(query, listed):
        if not charges.enabled:
            return None
        acct = _account()
        if not acct:
            return None                 # the downloads will answer SIGN_IN_REQUIRED themselves
        return charges.designs.register(acct, _design_union(listed) or query)
    return design_id


def make_quote(charges):
    from cct_common.tokens import FORMAT_TIER, TIER_PRICE

    def quote(query, formats):
        if not charges.enabled:
            return None
        acct = _account()
        if not acct:
            raise AgentError("sign in to see a price (the gateway's sign_in)", code="SIGN_IN_REQUIRED")
        top = max(formats, key=lambda f: TIER_PRICE[FORMAT_TIER[f]])
        listed = files(query, [p.id for p in PARTS], formats)
        did = charges.designs.register(acct, _design_union(listed) or query)
        q = charges.state.tokens.quote(acct, did, top)
        return {"tokens": True, "fmt": q.fmt, "tier": q.tier, "cost": q.cost, "held_tier": q.held_tier,
                "unlocked_until": q.unlocked_until, "balance": charges.state.tokens.balance(acct),
                "buy_url": charges.buy_url, "design_id": did}
    return quote


def register(app, charges, version: str = "") -> None:
    register_agent_api(app, describe(version), check=check, files=files,
                       quote=make_quote(charges), design_id=make_design_id(charges))
