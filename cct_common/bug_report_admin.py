"""
bug_report_admin.py — admin dashboard companion to cct_common.bug_report.

Reads and manages what bug_report.py writes: list/search reports, attach
a reviewer comment, delete a report, and (given GitHub credentials)
close/reopen or sync state with the linked GitHub issue.

    from cct_common.bug_report_admin import register_bug_report_admin_routes

    register_bug_report_admin_routes(app, admin_secret=SECRET)

Routes (all require `Authorization: Bearer <admin_secret>`):
    GET    /api/admin/bug-reports
    GET    /api/admin/bug-reports/hash/<hash_id>
    DELETE /api/admin/bug-reports/<ts_id>
    POST   /api/admin/bug-reports/<ts_id>/comment
    POST   /api/admin/bug-reports/<ts_id>/github-close
    POST   /api/admin/github-sync

GitHub close/sync need FEEDBACK_GITHUB_PAT/FEEDBACK_GITHUB_REPO
regardless of cct_common.deploy_mode — this is an explicit admin action
a human triggered, not automatic reporting, so it isn't gated by
CCT_BUG_REPORT_MODE the way bug_report.py's own GitHub filing is.

`log_dir` must match the `log_dir` passed to register_bug_report_route()
so this reads the same bug_reports.log.
"""
from __future__ import annotations

import ctypes
import json
import os
import re

from cct_common.github_api import request as gh_request


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data, log_dir):
    os.makedirs(log_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _normalize_issue_record(val):
    """Upgrade a bare URL string to the structured {url, number, state} format."""
    if isinstance(val, str):
        m = re.search(r"/issues/(\d+)$", val)
        return {"url": val, "number": int(m.group(1)) if m else None, "state": "unknown"}
    return val


def _bug_hash(ts_id: str) -> str:
    """FNV-1a 32-bit hash — stable short id for a report, e.g. for URLs."""
    h = 0x811C9DC5
    for c in ts_id:
        h ^= ord(c)
        h = ctypes.c_uint32(h * 0x01000193).value
    return format(h, "08X")


def _parse_bug_reports(log_file, comments_file, issue_urls_file):
    """Parse bug_reports.log (written by bug_report.py) into structured dicts."""
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return []

    sep = "=" * 60
    parts = raw.split(sep)
    reports = []
    i = 0
    while i < len(parts) - 1:
        hdr = parts[i].strip()
        if not (hdr.startswith("Bug Report") or hdr.startswith("Feature Request")):
            i += 1
            continue
        body = parts[i + 1] if i + 1 < len(parts) else ""
        lines = hdr.split("\n")
        title = lines[0]
        ver_line = lines[1] if len(lines) > 1 else ""

        m = re.match(r"(Bug Report|Feature Request) — (.+)", title)
        rtype = m.group(1) if m else "Bug Report"
        timestamp = m.group(2).strip() if m else ""
        vm = re.match(r"(?:Tool:\s*(\S+)\s+)?App Version:\s*(\S+)(?:\s+Build:\s*(.+))?", ver_line)
        tool = vm.group(1).strip() if vm and vm.group(1) else ""
        version = vm.group(2).strip() if vm else ""
        build = vm.group(3).strip() if vm and vm.group(3) else ""

        is_feature = rtype == "Feature Request"
        lbl_seeing = "Would like to do:" if is_feature else "Currently seeing:"
        lbl_should = "Why it's useful:" if is_feature else "Should be seeing:"

        def extract(text, lbl, *stop_lbls):
            pos = text.find(lbl)
            if pos == -1:
                return ""
            start = pos + len(lbl)
            end = len(text)
            for sl in stop_lbls:
                p = text.find(sl, start)
                if p != -1:
                    end = min(end, p)
            chunk = text[start:end].strip()
            lines_ = [l[2:] if l.startswith("  ") else l for l in chunk.split("\n")]
            return "\n".join(lines_).strip()

        seeing = extract(body, lbl_seeing, lbl_should, "Contact email:", "App state:")
        should_see = extract(body, lbl_should, "Contact email:", "App state:")
        email = extract(body, "Contact email:", "App state:")

        state = {}
        sm = re.search(r"App state:\n(\{.*)\Z", body, re.DOTALL)
        if sm:
            try:
                state = json.loads(sm.group(1))
            except Exception:
                pass

        reports.append({
            "id": timestamp,
            "type": rtype,
            "timestamp": timestamp,
            "tool": tool,
            "version": version,
            "build": build,
            "seeing": seeing,
            "should_see": should_see,
            "email": email,
            "state": state,
            "comment": "",
        })
        i += 2

    comments = _load_json(comments_file, {})
    issue_urls = _load_json(issue_urls_file, {})
    for r in reports:
        r["comment"] = comments.get(r["timestamp"], "")
        raw_issue = issue_urls.get(r["timestamp"])
        if raw_issue:
            rec = _normalize_issue_record(raw_issue)
            r["issue_url"] = rec.get("url", "")
            r["issue_number"] = rec.get("number")
            r["issue_state"] = rec.get("state", "unknown")
        else:
            r["issue_url"] = ""
            r["issue_number"] = None
            r["issue_state"] = None
    return reports


def _write_bug_log(log_file, log_dir, reports):
    """Rewrite bug_reports.log from a list of parsed report dicts."""
    sep = "=" * 60
    content = ""
    for r in reports:
        is_feature = r["type"] == "Feature Request"
        lbl_seeing = "Would like to do" if is_feature else "Currently seeing"
        lbl_should = "Why it's useful" if is_feature else "Should be seeing"
        tool_line = f'Tool: {r["tool"]}   ' if r.get("tool") else ""
        content += (
            f'\n{sep}\n'
            f'{r["type"]} — {r["timestamp"]}\n'
            f'{tool_line}App Version: {r["version"]}'
            + (f'   Build: {r["build"]}' if r.get("build") else "") + "\n"
            f'{sep}\n'
            f'{lbl_seeing}:\n  {r["seeing"] or "(not provided)"}\n\n'
            f'{lbl_should}:\n  {r["should_see"] or "(not provided)"}\n\n'
            f'Contact email:\n  {r["email"] or "(not provided)"}\n\n'
            f'App state:\n{json.dumps(r["state"], indent=2)}\n'
        )
    os.makedirs(log_dir, exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(content)


def register_bug_report_admin_routes(app, admin_secret: str, log_dir=None,
                                     path_prefix: str = "/api/admin/bug-reports"):
    from flask import jsonify, request

    log_dir = log_dir or os.path.join(os.getcwd(), "logs")
    log_file = os.path.join(log_dir, "bug_reports.log")
    comments_file = os.path.join(log_dir, "bug_comments.json")
    issue_urls_file = os.path.join(log_dir, "bug_issue_urls.json")

    def _auth():
        auth = request.headers.get("Authorization", "")
        if not admin_secret or auth != f"Bearer {admin_secret}":
            return jsonify({"error": "unauthorized"}), 401
        return None

    def _github_creds():
        pat = os.environ.get("FEEDBACK_GITHUB_PAT", "").strip()
        repo = os.environ.get("FEEDBACK_GITHUB_REPO", "").strip()
        return pat, repo

    def list_reports():
        err = _auth()
        if err:
            return err
        reports = _parse_bug_reports(log_file, comments_file, issue_urls_file)
        return jsonify({"count": len(reports), "reports": reports})

    def get_by_hash(hash_id):
        err = _auth()
        if err:
            return err
        hash_id = hash_id.upper()
        for r in _parse_bug_reports(log_file, comments_file, issue_urls_file):
            if _bug_hash(r["id"]) == hash_id:
                return jsonify(r)
        return jsonify({"error": f"No report found with hash {hash_id}"}), 404

    def delete_report(ts_id):
        err = _auth()
        if err:
            return err
        reports = _parse_bug_reports(log_file, comments_file, issue_urls_file)
        kept = [r for r in reports if r["id"] != ts_id]
        if len(kept) == len(reports):
            return jsonify({"error": "not found"}), 404
        _write_bug_log(log_file, log_dir, kept)
        comments = _load_json(comments_file, {})
        comments.pop(ts_id, None)
        _save_json(comments_file, comments, log_dir)
        issue_urls = _load_json(issue_urls_file, {})
        if ts_id in issue_urls:
            issue_urls.pop(ts_id)
            _save_json(issue_urls_file, issue_urls, log_dir)
        return jsonify({"ok": True, "remaining": len(kept)})

    def comment_report(ts_id):
        err = _auth()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        comment = str(data.get("comment", "")).strip()
        comments = _load_json(comments_file, {})
        if comment:
            comments[ts_id] = comment
        else:
            comments.pop(ts_id, None)
        _save_json(comments_file, comments, log_dir)
        return jsonify({"ok": True})

    def github_close(ts_id):
        err = _auth()
        if err:
            return err
        pat, repo = _github_creds()
        if not pat or not repo:
            return jsonify({"error": "GitHub not configured"}), 503
        issue_urls = _load_json(issue_urls_file, {})
        raw = issue_urls.get(ts_id)
        if not raw:
            return jsonify({"error": "No GitHub issue linked to this report"}), 404
        rec = _normalize_issue_record(raw)
        number = rec.get("number")
        if not number:
            return jsonify({"error": "Cannot determine issue number from URL"}), 400
        data = request.get_json(silent=True) or {}
        target = "closed" if data.get("state", "closed") == "closed" else "open"
        resp, status = gh_request("PATCH", f"/repos/{repo}/issues/{number}", pat,
                                  body={"state": target})
        if status not in (200, 201):
            return jsonify({"error": resp.get("message", "GitHub error"), "status": status}), 502
        rec["state"] = resp.get("state", target)
        issue_urls[ts_id] = rec
        _save_json(issue_urls_file, issue_urls, log_dir)
        return jsonify({"ok": True, "state": rec["state"]})

    def github_sync():
        err = _auth()
        if err:
            return err
        pat, repo = _github_creds()
        if not pat or not repo:
            return jsonify({"ok": False, "error": "GitHub not configured", "updated": {}})
        issue_urls = _load_json(issue_urls_file, {})
        updated = {}
        changed = False
        for ts_id, raw in issue_urls.items():
            rec = _normalize_issue_record(raw)
            number = rec.get("number")
            if not number:
                continue
            resp, status = gh_request("GET", f"/repos/{repo}/issues/{number}", pat)
            if status == 200:
                new_state = resp.get("state", "unknown")
                if rec.get("state") != new_state:
                    rec["state"] = new_state
                    issue_urls[ts_id] = rec
                    changed = True
                updated[ts_id] = new_state
        if changed:
            _save_json(issue_urls_file, issue_urls, log_dir)
        return jsonify({"ok": True, "updated": updated})

    app.add_url_rule(path_prefix, endpoint="_cct_common_bug_admin_list",
                     view_func=list_reports, methods=["GET"])
    app.add_url_rule(f"{path_prefix}/hash/<hash_id>", endpoint="_cct_common_bug_admin_by_hash",
                     view_func=get_by_hash, methods=["GET"])
    app.add_url_rule(f"{path_prefix}/<ts_id>", endpoint="_cct_common_bug_admin_delete",
                     view_func=delete_report, methods=["DELETE"])
    app.add_url_rule(f"{path_prefix}/<ts_id>/comment", endpoint="_cct_common_bug_admin_comment",
                     view_func=comment_report, methods=["POST"])
    app.add_url_rule(f"{path_prefix}/<ts_id>/github-close", endpoint="_cct_common_bug_admin_github_close",
                     view_func=github_close, methods=["POST"])
    app.add_url_rule("/api/admin/github-sync", endpoint="_cct_common_bug_admin_github_sync",
                     view_func=github_sync, methods=["POST"])
