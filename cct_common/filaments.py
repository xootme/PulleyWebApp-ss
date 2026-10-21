"""3D-printing filaments: what a printed part is rated with, shared by every
CCT app that rates one — the hub pin calculator (Gear, Worm, Bevel Designer:
`pin_strength.py`, bearing = the tensile strength, shear 0.6 × it) and the
Strength checks (Bevel's ISO 10300 root, Worm's Lewis wheel). One table, so
the apps' menus and numbers can't drift apart (the owner, 2026-10-20).

    from cct_common import filaments

    filaments.FILAMENTS["pla"].tensile              # 50.0 MPa, printed XY
    filaments.root_allowable("petg")                # 4.5 MPa: 10 % of it

Each filament's **tensile** strength is printed XY (the layers along the
load), ISO 527 where the maker says so; its **modulus** the tensile modulus
printed XY. `basis` says where each came from: MFR a maker's datasheet
value, EST a typical figure no datasheet was read for (the first seven
tensile strengths are Gear Designer's typical values). Nylon's are dry and
annealed: a nylon part that has taken up water is weaker and far less
stiff (CoPA wet: 724 N/mm²).

**Printed root allowable** (`ROOT_FRACTION`): 10 % of the tensile strength.
O.H. Ezeh and L. Susmel, "On the fatigue strength of 3D-printed polylactide
(PLA)", Procedia Structural Integrity 9 (2018) 29–36: "when appropriate
experimental results cannot be generated", a reference curve with its
endurance limit at 2×10⁶ cycles equal to 10 % of the ultimate tensile
strength, for over 95 % survival. Measured on PLA only; the apps apply it to
every filament as a rough estimate, under a warning.

Left out: TPU (flexible; Polymaker gives no tensile strength, only
elongation), POM (hardly printable), PA6-CF (Polymaker 109.3 MPa annealed;
the table already has a carbon-fibre nylon at Gear's 70). Sources read
2026-10-20 (Bevel Designer's docs/specs.md §16, §17).
"""
from __future__ import annotations

from dataclasses import dataclass

MFR, EST = "MFR", "EST"
ROOT_FRACTION = 0.10
ROOT_SOURCE = ("Ezeh & Susmel 2018 (Procedia Structural Integrity 9, 29–36): 10 % of the printed "
               "tensile strength, printed PLA's endurance limit at 2×10⁶ cycles")
TYPICAL = "typical datasheet tensile, printed XY"


@dataclass(frozen=True)
class Filament:
    key: str
    label: str
    tensile: float              # MPa, printed XY
    tensile_source: str
    modulus: float              # MPa (N/mm²), tensile, printed XY
    modulus_source: str
    tensile_basis: str = MFR
    modulus_basis: str = MFR


FILAMENTS = {f.key: f for f in (
    # Gear Designer's typical values (EST); the moduli Polymaker's datasheets
    Filament("pla", "PLA (printed)", 50.0, TYPICAL, 3427.0, "Polymaker PolyLite PLA", EST),
    Filament("petg", "PETG (printed)", 45.0, TYPICAL, 2117.0, "Polymaker PolyLite PETG", EST),
    Filament("abs", "ABS (printed)", 38.0, TYPICAL, 2247.0, "Polymaker PolyLite ABS", EST),
    Filament("asa", "ASA (printed)", 40.0, TYPICAL, 2379.0, "Polymaker ASA", EST),
    Filament("pa", "Nylon PA12 (printed)", 45.0, TYPICAL, 1500.0, "typical unfilled PA12 (no datasheet read)",
             EST, EST),
    Filament("pa_cf", "Nylon, carbon fibre filled (printed)", 70.0, TYPICAL, 5000.0,
             "typical carbon fibre nylon (no datasheet read)", EST, EST),
    Filament("pc", "Polycarbonate (printed)", 60.0, TYPICAL, 2497.0, "Polymaker PolyLite PC", EST),
    # the owner's "common 3d printing materials" (2026-10-20): a maker's printed XY figures
    Filament("pla_pro", "PLA+ / PLA Pro (printed)", 49.8, "Polymaker PolyLite PLA Pro datasheet, printed XY",
             2932.0, "Polymaker PolyLite PLA Pro"),
    Filament("pla_cf", "PLA, carbon fibre filled (printed)", 31.2, "Polymaker PLA-CF datasheet, printed XY",
             3281.0, "Polymaker PLA-CF"),
    Filament("petg_cf", "PETG, carbon fibre filled (printed)", 59.8,
             "Polymaker Fiberon PETG-rCF08 datasheet, printed XY", 3710.0, "Polymaker Fiberon PETG-rCF08"),
    Filament("pctg", "PCTG (printed)", 45.0, "Fiberlogy PCTG datasheet, ISO 527 at break",
             1600.0, "Fiberlogy PCTG, flexural modulus (ISO 178): no tensile modulus given", MFR, EST),
    Filament("pet_cf", "PET, carbon fibre filled (printed, annealed)", 65.9,
             "Polymaker Fiberon PET-CF17 datasheet, printed XY, annealed", 5481.0,
             "Polymaker Fiberon PET-CF17, annealed 120 °C 10 h"),
    Filament("pc_abs", "PC-ABS (printed)", 39.9, "Polymaker PC-ABS datasheet, printed XY", 1835.0,
             "Polymaker PC-ABS"),
    Filament("pa6", "Nylon PA6 / CoPA (printed, annealed, dry)", 78.0,
             "Polymaker PolyMide CoPA datasheet, printed XY, annealed", 2703.0,
             "Polymaker PolyMide CoPA, dry, annealed"),
    Filament("pa6_gf", "Nylon PA6, glass fibre filled (printed, annealed, dry)", 80.1,
             "Polymaker Fiberon PA6-GF25 datasheet, printed XY, annealed", 5357.0,
             "Polymaker Fiberon PA6-GF25, dry, annealed"),
    Filament("pps_cf", "PPS, carbon fibre filled (printed, annealed)", 59.4,
             "Polymaker Fiberon PPS-CF10 datasheet, printed XY, annealed", 5447.0,
             "Polymaker Fiberon PPS-CF10, annealed 125 °C 16 h"),
)}
DEFAULT = "pla"


def filament(key: str) -> Filament:
    try:
        return FILAMENTS[key]
    except KeyError:
        raise ValueError(f"unknown filament {key!r}: choose one of {', '.join(FILAMENTS)}") from None


def root_allowable(key: str) -> float:
    """MPa: the printed root stress a rating holds a part to (ROOT_FRACTION)."""
    return ROOT_FRACTION * filament(key).tensile


def options() -> list:
    """The filaments, for the apps' menus."""
    return [{"key": f.key, "label": f.label, "tensile": f.tensile, "modulus": f.modulus,
             "tensile_basis": f.tensile_basis, "modulus_basis": f.modulus_basis} for f in FILAMENTS.values()]
