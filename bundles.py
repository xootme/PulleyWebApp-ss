"""
bundles.py — the download window's zip (ADR-008): every file the customer
ticked, for every part shown, in one zip, charged once for the whole
design at the highest tier ticked.

    POST /api/download/bundle
        {"design_id": "...", "name": "HTD-5M-20T",
         "files": [{"path": "/download/step", "params": {...}}, ...],
         "_fpt": "..."}                       (tokens off: the weekly trial token)
        -> {"job_id", "status_url"}; the job's output_file is the zip.
    The zip is served from its results.py link (/download/result/...).

The route is cct_common's (cct_common.download_bundle, moved there with the
Download window; the 2026-10-01 UI audit, row 37); this app runs it as a job
on its queue, charged through charging.charges, the zip stored through
results.py (a random 192-bit link, deleted after an hour).
"""
from __future__ import annotations

from cct_common.download_bundle import MAX_FILES, register_bundle_routes as _register  # noqa: F401
from cct_common.download_bundle import _filename, safe_name as _safe_name  # noqa: F401

from charging import charges
from results import save_result


def register_bundle_routes(app, *, log_dir: str, record_trial, create_job, start_job,
                           update_progress, finish_job, run_inline, guard=lambda f: f):
    """record_trial(body) consumes the weekly-trial token when tokens are off;
    run_inline() says whether to run the job in the request (tests, desktop);
    guard wraps the start route (the app's queue-session check)."""
    _register(app, charges=charges, default_name="pulley",
              jobs=(create_job, start_job, update_progress, finish_job),
              save_result=lambda data, filename: save_result(log_dir, data, filename),
              record_trial=record_trial, run_inline=run_inline, guard=guard)
