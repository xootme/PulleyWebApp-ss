"""
printer_settings.py — printer settings, units and formats kept with the
account (0.41.0; account plan step 3, CCT_Marketing
accounts/Account_Page_Plan.md §3). Behind the printer_settings business
setting, off until the owner turns it on: while off nothing is read, saved
or shown.

- One row per account (account_prefs), each item optional; empty means the
  tool's own default:
    units                  "mm" or "in": tools open in these units;
    print_compensation_mm  3D Printer Compensation, 0 to 1.00 mm (the owner,
                           2026-10-30, in Pulley's help's words): every
                           printed surface moves this far into the material,
                           so an FDM print comes out at its nominal size. It
                           changes STL files only; SVG, DXF and STEP stay
                           nominal. Tools with such a setting start with it,
                           the others ignore it. (Kept in the clearance_mm
                           column.)
  The owner dropped a download-formats preference (2026-10-30): the formats
  column stays, unused.
- The page side (tokens.js) gives them to the app as window.cctPrinterSettings
  and calls the app's window.cctApplyPrinterSettings(settings) only on a fresh
  start (no query in the address), so a saved or shared design keeps its own
  values. Each app maps them onto its own parameters (its adoption task).
- Deleting the account deletes the row (accounts.delete_account).
- One range for every app (0.54.0; the owner, 2026-10-31, "1 a"): 0 to
  1.00 mm in 0.01 mm steps (COMPENSATION_MIN_MM, COMPENSATION_MAX_MM,
  COMPENSATION_STEP_MM). tokens.js has the same numbers
  (cctTokens.printCompensation; a test keeps them equal), so pages don't
  write their own. An app whose small gears need less (a tooth-size cap,
  such as 0.15 × module) clamps the account's value with
  clamp_compensation(value, cap), or cctTokens.clampCompensation on the
  page, rather than refusing it, and says so where the cap applies.
"""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

from .sqlite_db import SqliteDB

UNITS = ("mm", "in")
COMPENSATION_MIN_MM = 0.0
COMPENSATION_MAX_MM = 1.0
COMPENSATION_STEP_MM = 0.01


def clamp_compensation(value, cap: Optional[float] = None) -> Optional[float]:
    """The compensation an app can use: `value` (mm) inside [MIN, MAX], and
    under the app's own `cap` when it has one (a tooth-size limit), on the
    0.01 mm grid, rounded down so the cap holds. None stays None (the
    tool's default)."""
    if value is None:
        return None
    hi = COMPENSATION_MAX_MM if cap is None else max(COMPENSATION_MIN_MM, min(COMPENSATION_MAX_MM, float(cap)))
    v = min(max(float(value), COMPENSATION_MIN_MM), hi)
    return round(math.floor(v / COMPENSATION_STEP_MM + 1e-9) * COMPENSATION_STEP_MM, 2)

SCHEMA = """
CREATE TABLE IF NOT EXISTS account_prefs (
    account_id    TEXT PRIMARY KEY,
    units         TEXT,
    formats       TEXT,
    clearance_mm  REAL,
    updated_at    REAL NOT NULL
);
"""


class PrinterSettingsError(ValueError):
    """A value the account page can't save, in a sentence that says why."""


def clean(data) -> dict:
    """{units, print_compensation_mm} as kept, or PrinterSettingsError."""
    if not isinstance(data, dict):
        raise PrinterSettingsError("Send the settings as an object.")
    units = data.get("units") or None
    if units is not None and units not in UNITS:
        raise PrinterSettingsError("Units are mm or in.")
    comp = data.get("print_compensation_mm")
    if comp in (None, ""):
        comp = None
    else:
        if isinstance(comp, bool):
            raise PrinterSettingsError("The 3D Printer Compensation is a number of millimetres.")
        try:
            comp = float(comp)
        except (TypeError, ValueError):
            raise PrinterSettingsError("The 3D Printer Compensation is a number of millimetres.") from None
        steps = comp / COMPENSATION_STEP_MM
        if not COMPENSATION_MIN_MM <= comp <= COMPENSATION_MAX_MM or abs(steps - round(steps)) > 1e-6:
            raise PrinterSettingsError(f"The 3D Printer Compensation is {COMPENSATION_MIN_MM:g} to "
                                       f"{COMPENSATION_MAX_MM:.2f} mm, in steps of {COMPENSATION_STEP_MM:g} mm.")
        comp = round(comp, 2)
    return {"units": units, "print_compensation_mm": comp}


class PrinterSettings(SqliteDB):
    """The account_prefs table, in the accounts database."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        super().__init__(path, SCHEMA)
        self._clock = clock

    def get(self, account_id: str) -> dict:
        with self._read() as db:
            row = db.execute("SELECT units, clearance_mm FROM account_prefs WHERE account_id = ?",
                             (account_id,)).fetchone()
        if not row:
            return {"units": None, "print_compensation_mm": None}
        return {"units": row["units"], "print_compensation_mm": row["clearance_mm"]}

    def set(self, account_id: str, data) -> dict:
        v = clean(data)
        with self._write() as db:
            db.execute("INSERT INTO account_prefs (account_id, units, formats, clearance_mm, updated_at) "
                       "VALUES (?, ?, ?, ?, ?) ON CONFLICT (account_id) DO UPDATE SET units = excluded.units, "
                       "formats = excluded.formats, clearance_mm = excluded.clearance_mm, "
                       "updated_at = excluded.updated_at",
                       (account_id, v["units"], None, v["print_compensation_mm"], self._clock()))
        return v
