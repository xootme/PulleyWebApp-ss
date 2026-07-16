"""
cct_metadata — embed/extract design parameters in CAD export files.

Shared across CheapCAD Tools apps (see CCT_Architecture.md "Embedded
metadata") so a file exported by any CCT tool can be dragged back into that
tool's web UI, or into a CAD-platform addin, to restore the exact design
that produced it. Stdlib-only; no dependency on any specific tool's
geometry or web framework.

    from cct_metadata import embed_step, extract_step

    step_bytes = embed_step(step_bytes, model.to_dict(), tool="ebox", version="0.1.0")
    meta = extract_step(step_bytes)  # {"cct": {...}, "tool": "ebox", "v": "0.1.0"}
"""
from .core import build_meta, dump_blob, flatten_params, parse_meta
from .dxf import embed_dxf, extract_dxf
from .step import embed_step, extract_step, rename_step_product
from .stl import embed_stl, extract_stl
from .svg import embed_svg, extract_svg

__all__ = [
    "build_meta", "dump_blob", "parse_meta", "flatten_params",
    "embed_step", "extract_step", "rename_step_product",
    "embed_stl", "extract_stl",
    "embed_dxf", "extract_dxf",
    "embed_svg", "extract_svg",
]

__version__ = "0.1.0"
