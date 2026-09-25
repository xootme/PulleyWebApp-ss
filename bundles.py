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

The files are made by the existing download routes, called in-process with
charges.internal_secret, so every file is exactly what that route would
give on its own. They are part of one purchase: the charge is taken once,
around the whole job, at the highest tier among the files, for the
registered design. Every file's parameters must belong to that design
(charging.design_matches) — the zip can't carry a different design than
the one paid for. If any file fails, the job fails and the charge is
refunded.

The zip is stored through results.py: a random 192-bit link, deleted
after an hour.
"""
from __future__ import annotations

import io
import os
import re
import threading
import zipfile

from cct_common.tokens import FORMAT_TIER, TIER_PRICE, BudgetReached, InsufficientTokens

from charging import charges, design_matches
from results import save_result

MAX_FILES = 40
_SAFE = re.compile(r"[^A-Za-z0-9._+-]+")


def _safe_name(name: str, default: str = "download") -> str:
    name = _SAFE.sub("-", (name or "").strip()).strip("-.")
    return name[:80] or default


def _filename(resp, fallback: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename="?([^";]+)"?', cd)
    return os.path.basename(m.group(1)) if m else fallback


def register_bundle_routes(app, *, log_dir: str, record_trial, create_job, start_job,
                           update_progress, finish_job, run_inline, guard=lambda f: f):
    """record_trial(body) consumes the weekly-trial token when tokens are off;
    run_inline() says whether to run the job in the request (tests, desktop);
    guard wraps the start route (the app's queue-session check)."""
    from flask import jsonify, request

    def start_bundle():
        body = request.get_json(silent=True) or {}
        files = body.get("files")
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            return jsonify({"error": "choose at least one file"}), 400

        adapter = app.url_map.bind("localhost")
        planned, top = [], None
        for f in files:
            path = str((f or {}).get("path", ""))
            params = (f or {}).get("params")
            if not path.startswith("/download/") or not isinstance(params, dict):
                return jsonify({"error": f"not a download: {path}"}), 400
            try:
                endpoint, _ = adapter.match(path, method="GET")
            except Exception:
                endpoint = None
            fmt = charges.formats.get(endpoint)
            if not fmt:
                return jsonify({"error": f"not a download: {path}"}), 400
            tier = FORMAT_TIER[fmt]
            if top is None or TIER_PRICE[tier] > TIER_PRICE[FORMAT_TIER[top]]:
                top = fmt
            planned.append((path, {str(k): str(v) for k, v in params.items()}))

        context = None
        if charges.enabled:
            acct, refusal = charges._refusal()
            if refusal:
                return refusal
            did = str(body.get("design_id") or "")
            design = charges.designs.get(did) if charges.designs else None
            if design is None:
                return jsonify({"error": "unknown design — register it first"}), 400
            for path, params in planned:
                if not design_matches(params, design):
                    return jsonify({"error": f"{path} isn't part of this design"}), 400
            context, refusal = charges.begin_design(did, top)
            if refusal:
                return refusal
        else:
            record_trial(body)

        name = _safe_name(body.get("name"), "pulley")
        job = create_job("bundle", {"files": len(planned), "name": name})
        headers = {"X-CCT-Internal": charges.internal_secret}

        def build():
            ctx = app.app_context()
            ctx.push()
            start_job(job.id)
            try:
                with charges.charge_in_job(context, "/api/download/bundle"):
                    buf, seen = io.BytesIO(), set()
                    with app.test_client() as c, \
                            zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                        for i, (path, params) in enumerate(planned):
                            r = c.get(path, query_string=params, headers=headers)
                            if r.status_code != 200:
                                raise RuntimeError(
                                    f"{path} failed ({r.status_code}): "
                                    f"{r.get_data(as_text=True)[:200]}")
                            fname = _filename(r, os.path.basename(path))
                            base, ext = os.path.splitext(fname)
                            n = 2
                            while fname in seen:
                                fname = f"{base}-{n}{ext}"
                                n += 1
                            seen.add(fname)
                            z.writestr(fname, r.get_data())
                            update_progress(job.id, int(100 * (i + 1) / (len(planned) + 1)))
                    url = save_result(log_dir, buf.getvalue(), name + ".zip")
                finish_job(job.id, output_file=url)
            except InsufficientTokens as e:
                finish_job(job.id, error=f"Not enough tokens: needs {e.needed}, you have {e.balance}.")
            except BudgetReached as e:
                finish_job(job.id, error=f"This add-in reached its daily limit of {e.budget} tokens.")
            except Exception as e:
                finish_job(job.id, error=str(e))
            finally:
                ctx.pop()

        if run_inline():
            build()
        else:
            threading.Thread(target=build, daemon=True).start()
        return jsonify({"job_id": job.id, "status_url": f"/api/download-status/{job.id}"})

    app.add_url_rule("/api/download/bundle", endpoint="api_download_bundle",
                     view_func=guard(start_bundle), methods=["POST"])
