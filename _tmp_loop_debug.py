import sys, re
sys.path.insert(0, ".")

dxf = open(r"logs\debug_spoke_fail.dxf").read()

# Find all SPOKES layer segments (arcs + lines)
entities = re.split(r"\n  0\n", dxf)
spokes = [e for e in entities if "\n  8\nSPOKES" in e or "\n  8\n SPOKES" in e]

print(f"SPOKES entities: {len(spokes)}")
for e in spokes:
    kind = e.strip().split("\n")[0].strip()
    if kind == "ARC":
        cx = float(re.search(r"\n 10\n\s*([\-0-9.eE+]+)", e).group(1))
        cy = float(re.search(r"\n 20\n\s*([\-0-9.eE+]+)", e).group(1))
        r  = float(re.search(r"\n 40\n\s*([\-0-9.eE+]+)", e).group(1))
        a0 = float(re.search(r"\n 50\n\s*([\-0-9.eE+]+)", e).group(1))
        a1 = float(re.search(r"\n 51\n\s*([\-0-9.eE+]+)", e).group(1))
        print(f"  ARC  cx={cx:.3f} cy={cy:.3f} r={r:.3f}  {a0:.1f}->{a1:.1f}")
    elif kind == "LINE":
        x1 = float(re.search(r"\n 10\n\s*([\-0-9.eE+]+)", e).group(1))
        y1 = float(re.search(r"\n 20\n\s*([\-0-9.eE+]+)", e).group(1))
        x2 = float(re.search(r"\n 11\n\s*([\-0-9.eE+]+)", e).group(1))
        y2 = float(re.search(r"\n 21\n\s*([\-0-9.eE+]+)", e).group(1))
        print(f"  LINE ({x1:.3f},{y1:.3f}) -> ({x2:.3f},{y2:.3f})")
    elif kind == "CIRCLE":
        cx = float(re.search(r"\n 10\n\s*([\-0-9.eE+]+)", e).group(1))
        cy = float(re.search(r"\n 20\n\s*([\-0-9.eE+]+)", e).group(1))
        r  = float(re.search(r"\n 40\n\s*([\-0-9.eE+]+)", e).group(1))
        print(f"  CIRCLE cx={cx:.3f} cy={cy:.3f} r={r:.3f}")
    else:
        print(f"  {kind}")

# Also show other layers present
layers = set(re.findall(r"\n  8\n(.+)", dxf))
print(f"\nLayers in DXF: {sorted(layers)}")
