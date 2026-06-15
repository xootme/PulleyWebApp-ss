"""
FreeCAD headless STEP import script.
Run via: freecadcmd.exe tests/_freecad_import.py <step_file>
Stdout: OK: N solids, volume=V mm3   or   ERROR: ...
Exit code: 0=pass, 1=fail, 2=warn(no solids)
"""
import sys
import os

# freecadcmd argv: [freecadcmd.exe, this_script.py, step_file]
step_file = sys.argv[2] if len(sys.argv) > 2 else None
if not step_file or not os.path.isfile(step_file):
    sys.stdout.write(f"ERROR: file not found: {step_file}\n")
    sys.stdout.flush()
    sys.exit(1)

try:
    import Part
    shape = Part.read(step_file)
    if not shape.Solids:
        sys.stdout.write("WARN: no solids found in STEP\n")
        sys.stdout.flush()
        sys.exit(2)
    vol = shape.Volume
    if vol <= 0:
        sys.stdout.write(f"ERROR: total volume = {vol:.3f}\n")
        sys.stdout.flush()
        sys.exit(1)
    sys.stdout.write(f"OK: {len(shape.Solids)} solids, volume={vol:.1f} mm3\n")
    sys.stdout.flush()
    sys.exit(0)
except OSError as e:
    sys.stdout.write(f"ERROR: {e}\n")
    sys.stdout.flush()
    sys.exit(1)
except RuntimeError as e:
    sys.stdout.write(f"ERROR: {e}\n")
    sys.stdout.flush()
    sys.exit(1)
