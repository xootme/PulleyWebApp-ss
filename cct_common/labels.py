"""
labels.py — what the page calls each setting, and the messages that name
one (cct_common 0.31.0; Planetary Gear Designer's geometry/labels.py, its
ADR-023, which Gear, Worm and Bevel copied).

The owner, 2026-10-21: "correct all messages to make sense to the user, show
the labeled parameter". A refusal says "Disc Thickness must be 0.5 to 100 mm.",
never "carrier_t must be 0.5 to 100.0"; and its Auto-fix is a value the page
can type in (the nearest allowed one, or the default), never a range's text,
which every app's page typed in as NaN, so Auto-fix never cleared it.

    from cct_common.labels import Labels, SettingError

    LB = Labels({"carrier_t": ("Disc Thickness", "mm"), ...},     # the app's own settings
                cards={"sun_": "Sun", "carrier_": "Carrier"})      # its parts' cards
    t = LB.number(args.get("carrier_t"), "carrier_t", 0.5, 100, default=4)
    shape = LB.choice(args.get("sun_bore_shape"), "sun_bore_shape", SHAPES)
    except SettingError as e: {"error": str(e), "field": e.field, "fix": e.fix}

Each app keeps its labels as data and makes its own Labels: one object per
app, no module-level table, because the MCP gateway runs every app in one
process. A setting is looked up as:

  1. `variants[key][variant]`, when the caller names a variant (Bevel's
     "size" reads "Diametral Pitch" in the DP family);
  2. the app's `labels[key]`;
  3. a card setting: `key` is a `cards` prefix and a setting, and the label
     is the part's name and the setting's ("Carrier Hub OD"; a part named ""
     adds nothing, Gear's g2_). The setting's words come from the app's
     `labels` first (Gear's and Worm's cards are there), then from CARD_LABELS,
     the Bore, Hub, Set Screw, Hub Pins and Spline cards' common words, with
     the app's `card_labels` over them: where its page words a card
     differently (Bevel's "Hub Length", "Pins").

`stop` ends each message ("." by default; Bevel's messages have none).
cct_common.testing.labels.check_labels checks an app's table against its
page, and that every refusal's Auto-fix clears it. The page turns a
message's "… mm" into inches with CCTUnits.inchMessage (cct_units.js).
"""
from __future__ import annotations

import math

# A card's settings, as most apps' pages word them; the part's name goes in
# front ("Sun Bore Shape"). Planetary's table (0.31.0); an app's own wording
# overrides it.
CARD_LABELS = {
    "bore_shape": ("Bore Shape", ""), "bore_flat": ("Flat Depth", "mm"), "key_w": ("Key Width", "mm"),
    "key_h": ("Key Depth", "mm"),
    "spline_type": ("Spline", ""), "spline_preset": ("Spline Size", ""), "spline_n": ("Splines N", ""),
    "spline_minor": ("Minor d", "mm"), "spline_major": ("Major D", "mm"), "spline_width": ("Width B", "mm"),
    "spline_m": ("Module m", "mm"), "spline_z": ("Teeth z", ""), "spline_pa": ("Pressure Angle", "°"),
    "spline_root": ("Root", ""), "spline_hex_preset": ("Stock", ""), "spline_af": ("Across Flats", "mm"),
    "spline_ac": ("Across Corners", "mm"), "spline_series": ("Rings", ""), "spline_rounded": ("Rounded corners", ""),
    "spline_ring_top": ("Retaining ring, outer face", ""), "spline_ring_bottom": ("Retaining ring, inner face", ""),
    "spline_cb_top": ("Make counterbore", ""), "spline_cb_bottom": ("Make counterbore, inner face", ""),
    "spline_washer": ("Splined washer under the ring", ""), "spline_show_shaft": ("Show sample shaft", ""),
    "hub": ("Hub", ""), "hub_d": ("Hub OD", "mm"), "hub_len": ("Hub Height", "mm"),
    "hub_retention": ("Retention Method", ""), "hub_screw_size": ("Set Screw Size", ""),
    "hub_screw_hold": ("Retention Method", ""), "hub_screw_count": ("Number of Screws", ""),
    "hub_screw_hole_dia": ("Hole ⌀", "mm"), "hub_pins": ("Hub Pins", ""), "hub_pin_d": ("Hub Pin Dia.", "mm"),
    "hub_pin_fit": ("Hub Pin Hole vs Pin", "mm"),
}
TIGHT_UNITS = ("°", "%")            # written against the number: "45°", "92%"


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
    `field` is its key and `fix` a value the Auto-fix can type in."""

    def __init__(self, message: str, field: str, fix: str | None):
        super().__init__(message)
        self.field, self.fix = field, fix

    def to_json(self) -> dict:
        return {"error": str(self), "field": self.field, "fix": self.fix}


class Labels:
    """One app's labels and the messages that use them (see the module's doc)."""

    def __init__(self, labels: dict, cards: dict | None = None, card_labels: dict | None = None,
                 variants: dict | None = None, stop: str = "."):
        self.labels = dict(labels)
        self.cards = dict(cards or {})
        self.card_labels = {**CARD_LABELS, **(card_labels or {})}
        self.variants = {k: dict(v) for k, v in (variants or {}).items()}
        self.stop = stop

    # -- names -----------------------------------------------------------------
    def find(self, key: str, variant=None):
        """(label, unit) for `key`, or None when it has none."""
        if variant is not None and variant in self.variants.get(key, {}):
            return self.variants[key][variant]
        if key in self.labels:
            return self.labels[key]
        for prefix, part in self.cards.items():
            rest = key[len(prefix):] if key.startswith(prefix) else None
            found = self.labels.get(rest) or self.card_labels.get(rest) if rest else None
            if found:
                return f"{part} {found[0]}".strip(), found[1]
        return None

    def label(self, key: str, variant=None) -> str:
        """What the page calls `key` ("Disc Thickness", "Carrier Hub OD"); the key itself when it has no label."""
        found = self.find(key, variant)
        return found[0] if found else key

    def unit(self, key: str, variant=None) -> str:
        found = self.find(key, variant)
        return found[1] if found else ""

    def with_unit(self, key: str, text: str, variant=None) -> str:
        u = self.unit(key, variant)
        return text if not u else f"{text}{'' if u in TIGHT_UNITS else ' '}{u}"

    def missing(self, keys) -> list:
        """The keys with no label: each shows as its key in a message."""
        return sorted(k for k in keys if self.find(k) is None)

    # -- messages --------------------------------------------------------------
    def not_a_number(self, key: str, whole: bool = False, variant=None) -> str:
        return f"{self.label(key, variant)} must be {'a whole number' if whole else 'a number'}{self.stop}"

    def out_of_range(self, key: str, lo: float, hi: float, whole: bool = False, variant=None) -> str:
        what = "a whole number from " if whole else ""
        return f"{self.label(key, variant)} must be {what}{self.with_unit(key, f'{lo:g} to {hi:g}', variant)}{self.stop}"

    def not_a_choice(self, key: str, raw, variant=None) -> str:
        return f"{self.label(key, variant)}: “{raw}” isn't one of its choices{self.stop}"

    # -- reading a setting -----------------------------------------------------
    def number(self, raw, key: str, lo: float = -math.inf, hi: float = math.inf, whole: bool = False,
               default=None, variant=None):
        """`raw` as a number within [lo, hi], else SettingError with the
        page's label and the Auto-fix: the nearest allowed value; for one that
        isn't a number (text, blank, inf, nan), `default` when it is one
        (blank when it's blank: automatic), else the nearest limit."""
        text = "" if raw is None else str(raw).strip()
        try:
            v = float(text)
            if not math.isfinite(v):
                raise ValueError(text)
        except ValueError:
            if default is not None and str(default).strip() == "":
                fix = ""
            else:
                try:
                    fix = nearest(float(default), lo, hi, whole)
                except (TypeError, ValueError):
                    fix = nearest(lo if math.isfinite(lo) else 0.0, lo, hi, whole)
            raise SettingError(self.not_a_number(key, whole, variant), key, fix) from None
        if whole and not v.is_integer():
            raise SettingError(self.not_a_number(key, whole, variant), key, nearest(v, lo, hi, whole))
        if not lo <= v <= hi:
            raise SettingError(self.out_of_range(key, lo, hi, whole, variant), key, nearest(v, lo, hi, whole))
        return int(v) if whole else v

    def choice(self, raw, key: str, options, default=None, variant=None):
        """`raw` if it is one of `options`, else SettingError, the fix
        `default` (or the first option)."""
        options = [str(o) for o in options]
        text = "" if raw is None else str(raw).strip()
        if text not in options:
            fix = str(default) if default is not None else (options[0] if options else None)
            raise SettingError(self.not_a_choice(key, text, variant), key, fix)
        return text
