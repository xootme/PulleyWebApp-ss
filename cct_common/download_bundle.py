"""
download_bundle.py — the Download window's zip (Timing Pulley Generator's
bundles.py, ADR-008, moved into cct_common with the window in
static/cct_download.js; CCT_App_Baseline §4): every file ticked, for every
part shown, in one zip.

    POST /api/download/bundle
        {"name": "HTD-5M-20T", "files": [{"path": "/download/step", "params": {...}}, ...],
         "design_id": "...", "_fpt": "..."}

The files are made by the app's own download routes, called in-process, so
every file is exactly what that route would give on its own.

Two ways to answer, one zip:

  * In the request (register_bundle_routes(app)): the response is the zip.
    For an app with no job queue (Sprocket and Gear Designer until launch).
  * As a job (jobs=…, save_result=…): {"job_id", "status_url"}, the zip
    stored through save_result and named in the job's output_file — the
    pulley app's queue, where STEP takes a while.

With charging (charges=cct_common.charging.Charges, attached and enabled)
the zip is one purchase: charged once, around the whole build, at the
highest tier among the files, for the registered design. Every file's
parameters must belong to that design (charges.matches) — the zip can't
carry a different design than the one paid for. If any file fails, the
build fails and the charge is refunded. The in-process calls carry
charges.internal_secret, so each route skips its own per-file charge.
"""
from __future__ import annotations

import io
import os
import re
import threading
import zipfile

from .tokens import FORMAT_TIER, TIER_PRICE, BudgetReached, InsufficientTokens

MAX_FILES = 40
_SAFE = re.compile(r"[^A-Za-z0-9._+-]+")


def safe_name(name: str, default: str = "download") -> str:
    name = _SAFE.sub("-", (name or "").strip()).strip("-.")
    return name[:80] or default


def _filename(resp, fallback: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename="?([^";]+)"?', cd)
    return os.path.basename(m.group(1)) if m else fallback


def build_zip(app, planned, *, headers=None, progress=None) -> bytes:
    """The zip of every (path, params): each file from its route, called
    in-process; a name that repeats gets -2, -3 … Raises RuntimeError naming
    the first route that fails."""
    buf, seen = io.BytesIO(), set()
    with app.test_client() as c, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (path, params) in enumerate(planned):
            r = c.get(path, query_string=params, headers=headers or {})
            if r.status_code != 200:
                err = (r.get_json(silent=True) or {}).get("error") if r.is_json else None
                raise RuntimeError(f"{path} failed ({r.status_code}): "
                                   f"{err or r.get_data(as_text=True)[:200]}")
            fname = _filename(r, os.path.basename(path))
            base, ext = os.path.splitext(fname)
            n = 2
            while fname in seen:
                fname = f"{base}-{n}{ext}"
                n += 1
            seen.add(fname)
            z.writestr(fname, r.get_data())
            if progress:
                progress(int(100 * (i + 1) / (len(planned) + 1)))
    return buf.getvalue()


def register_bundle_routes(app, *, charges=None, default_name: str = "design",
                           jobs=None, save_result=None, record_trial=lambda body: None,
                           run_inline=lambda: True, guard=lambda f: f):
    """jobs: (create_job, start_job, update_progress, finish_job) for the job
    mode, with save_result(data, filename) -> url; without them the zip is
    the response. record_trial(body) consumes a free-trial token when
    charging is off; guard wraps the route (the pulley app's queue check)."""
    from flask import Response, jsonify, request

    def plan(files):
        """(planned, top format) or (None, error response)."""
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            return None, None, (jsonify({"error": f"choose 1 to {MAX_FILES} files"}), 400)
        adapter = app.url_map.bind("localhost")
        planned, top = [], None
        for f in files:
            path = str((f or {}).get("path", ""))
            params = (f or {}).get("params")
            if params is None:
                params = {}
            if not path.startswith("/download/") or not isinstance(params, dict):
                return None, None, (jsonify({"error": f"not a download: {path}"}), 400)
            try:
                endpoint, _ = adapter.match(path, method="GET")
            except Exception:
                return None, None, (jsonify({"error": f"not a download: {path}"}), 400)
            fmt = charges.formats.get(endpoint) if charges is not None else None
            if charges is not None and charges.enabled and not fmt:
                return None, None, (jsonify({"error": f"not a download: {path}"}), 400)
            if fmt in FORMAT_TIER and (top is None or TIER_PRICE[FORMAT_TIER[fmt]] > TIER_PRICE[FORMAT_TIER[top]]):
                top = fmt
            planned.append((path, {str(k): str(v) for k, v in params.items()}))
        return planned, top, None

    def start_bundle():
        body = request.get_json(silent=True) or {}
        planned, top, refusal = plan(body.get("files"))
        if refusal:
            return refusal

        context = None
        if charges is not None and charges.enabled:
            acct, refusal = charges._refusal()
            if refusal:
                return refusal
            did = str(body.get("design_id") or "")
            design = charges.designs.get(did) if charges.designs else None
            if design is None:
                return jsonify({"error": "unknown design — register it first"}), 400
            for path, params in planned:
                if not charges.matches(params, design):
                    return jsonify({"error": f"{path} isn't part of this design"}), 400
            context, refusal = charges.begin_design(did, top)
            if refusal:
                return refusal
        else:
            record_trial(body)

        name = safe_name(body.get("name"), default_name)
        headers = {"X-CCT-Internal": charges.internal_secret} if charges is not None else {}
        charge = (lambda: charges.charge_in_job(context, "/api/download/bundle")) if charges is not None \
            else None

        if jobs is None:                                   # the zip is the answer
            quote = None
            try:
                if charge:
                    with charge() as quote:
                        data = build_zip(app, planned, headers=headers)
                else:
                    data = build_zip(app, planned, headers=headers)
            except InsufficientTokens as e:
                return jsonify({"error": f"Not enough tokens: needs {e.needed}, you have {e.balance}."}), 402
            except BudgetReached as e:
                return jsonify({"error": f"This add-in reached its daily limit of {e.budget} tokens."}), 429
            except RuntimeError as e:
                return jsonify({"error": str(e)}), 400
            resp = Response(data, mimetype="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
            if quote is not None and quote.spend_id is not None:
                # Paid only if the zip is sent in full (cct_common.charging).
                from .charging import on_delivery
                spend = quote.spend_id
                return on_delivery(resp, lambda: None,
                                   lambda: charges.state.tokens.refund(spend, detail="download not completed"))
            return resp

        create_job, start_job, update_progress, finish_job = jobs
        job = create_job("bundle", {"files": len(planned), "name": name})

        def build():
            ctx = app.app_context()
            ctx.push()
            start_job(job.id)
            try:
                with (charge() if charge else _null()) as quote:
                    data = build_zip(app, planned, headers=headers,
                                     progress=lambda pct: update_progress(job.id, pct))
                    url = save_result(data, name + ".zip")
                    if charges is not None:
                        # The charge stands once the zip is fetched (the
                        # result route's serve_delivery); else it's refunded.
                        charges.await_delivery(quote, url)
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


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
