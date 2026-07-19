"""
cct_common — shared CheapCAD Tools platform code.

Embed/extract design parameters in CAD export files (see
CCT_Architecture.md "Embedded metadata") so a file exported by any CCT tool
can be dragged back into that tool's web UI, or into a CAD-platform addin,
to restore the exact design that produced it. The metadata API below is
stdlib-only; no dependency on any specific tool's geometry or web
framework.

    from cct_common import embed_step, extract_step

    step_bytes = embed_step(step_bytes, model.to_dict(), tool="ebox", version="0.1.0")
    meta = extract_step(step_bytes)  # {"cct": {...}, "tool": "ebox", "v": "0.1.0"}

Also provides optional Flask app helpers (imported separately, so plain
metadata use never requires Flask):

    from cct_common.flask_shutdown import register_shutdown_route
    register_shutdown_route(app)  # adds POST /api/shutdown

    from cct_common.live_reload import register_live_reload
    register_live_reload(app)  # adds /api/_boot_id + /_cct_live_reload.js
    # then in the page: <script src="/_cct_live_reload.js"></script>
    # tabs left open across a /api/shutdown + relaunch reload themselves

    from cct_common.bug_report import register_bug_report_route
    register_bug_report_route(app, tool_name="ebox", app_version="0.1.0")
    # adds POST /api/report-bug — always logs locally; also files a GitHub
    # issue + emails a notification when mode="live" or
    # CCT_BUG_REPORT_MODE=live (explicit opt-in, not just credential
    # presence — see cct_common/bug_report.py and cct_common/deploy_mode.py)

    from cct_common.bug_report_admin import register_bug_report_admin_routes
    register_bug_report_admin_routes(app, admin_secret="...")
    # adds the read/manage side of the log bug_report.py writes: list,
    # look up by short hash, delete, comment, and (given GitHub
    # credentials) close/sync the linked GitHub issue

Small dependency-free utilities, also imported separately:

    from cct_common.text_utils import safe_float, safe_dl_name
    from cct_common.jsonl_log import append_jsonl, trim_jsonl
    from cct_common.github_api import request as github_request
    from cct_common.resend_email import send as send_email
    from cct_common.addin_mirror import mirror_to_addins
    from cct_common.flask_caching import (
        register_etag_caching, register_admin_cors, register_download_signal,
    )

Async job tracking + single-active-user session queue with weekly
trial/web-download rate limiting, backed by cross-process file-locked
state (so every worker process sees the same queue):

    from cct_common import job_queue
    job_queue.configure(log_dir="/path/to/logs")
    job_queue.start_background_threads()  # not automatic on import

Machine fingerprinting and desktop-app licensing (key activation,
WooCommerce/LMFWC import, Autodesk App Store entitlement/IPN):

    from cct_common.machine_id import get_machine_id
    from cct_common.licensing import register_licensing_routes
    register_licensing_routes(app, admin_secret=..., log_dir=...)
    # see cct_common/licensing.py's module docstring for the full
    # parameter list — it deliberately does NOT port the hardcoded dev
    # backdoor found in the original during this survey
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

__version__ = "0.3.2"
