"""
bug_report.py — shared bug/feature-report endpoint for CCT Flask apps.

Always logs to a local file. Optionally also files a GitHub issue and
sends a SendGrid email notification — gated by an explicit mode, not by
credential presence, so a stray FEEDBACK_GITHUB_PAT left in a dev shell
can never cause a local test run to silently file real GitHub issues.

    from cct_common.bug_report import register_bug_report_route

    register_bug_report_route(app, tool_name="ebox", app_version="0.1.0")

Mode selection (see cct_common.deploy_mode) — local-only "dev" vs
GitHub+email "live":
    - pass `mode="live"` explicitly, or
    - set CCT_BUG_REPORT_MODE=live, or
    - set the umbrella CCT_MODE=live (used when CCT_BUG_REPORT_MODE isn't set)
Anything else (unset, "dev", or any other value) stays local-only.

Live mode needs its own credentials per channel, exactly as before
(each channel silently no-ops if its own credentials are absent — a
failure in GitHub/email delivery must never break the log write or the
user-facing response):
    - GitHub issue:  FEEDBACK_GITHUB_PAT, FEEDBACK_GITHUB_REPO (e.g. "xootme/cct-feedback")
    - Email:         SENDGRID_API_KEY

Flask (and, in live mode, the `sendgrid` package) are only imported
inside register_bug_report_route()/the helpers that need them, so
importing cct_common never requires either unless a caller wires this in.
"""
from __future__ import annotations

import json
import os
from datetime import datetime


def _create_github_issue(report_label, timestamp, label_seeing, label_should,
                         seeing, should_see, email, state, report_type,
                         tool_name, app_version):
    """POST a GitHub issue in the feedback repo. Silently skips if PAT/repo
    aren't configured; silently swallows any delivery failure."""
    pat = os.environ.get("FEEDBACK_GITHUB_PAT", "").strip()
    repo = os.environ.get("FEEDBACK_GITHUB_REPO", "").strip()
    if not pat or not repo:
        return None
    try:
        from cct_common.github_api import request as gh_request

        state_json = json.dumps(state, indent=2)
        body = (
            f"**Tool:** {tool_name}\n"
            f"**Type:** {report_label}\n"
            f"**Submitted:** {timestamp}\n"
            f"**App Version:** {app_version}\n\n"
            f"---\n\n"
            f"**{label_seeing}:**\n{seeing or '_(not provided)_'}\n\n"
            f"**{label_should}:**\n{should_see or '_(not provided)_'}\n\n"
            f"**Contact email:** {email or '_(not provided)_'}\n\n"
            f"---\n\n"
            f"<details><summary>Full app state</summary>\n\n"
            f"```json\n{state_json}\n```\n\n</details>\n"
        )
        title = f"[{tool_name}] [{report_label}] {(seeing or should_see or 'No description')[:80]}"
        label = "feature-request" if report_type == "feature" else "bug"
        resp, status = gh_request("POST", f"/repos/{repo}/issues", pat,
                                  body={"title": title, "body": body, "labels": [label]})
        if status >= 300:
            return None
        return resp.get("html_url")
    except Exception:
        return None  # GitHub failure must never break the log write


def _send_report_email(report_label, timestamp, label_seeing, label_should,
                       seeing, should_see, email, state, tool_name, app_version):
    """Fire-and-forget SendGrid notification. Silently skips if key isn't
    configured; silently swallows any delivery failure."""
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not api_key:
        return
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        state_json = json.dumps(state, indent=2)
        body = (
            f"{report_label} — {timestamp}\n"
            f"Tool: {tool_name}   App Version: {app_version}\n\n"
            f'{label_seeing}:\n  {seeing or "(not provided)"}\n\n'
            f'{label_should}:\n  {should_see or "(not provided)"}\n\n'
            f'Contact email:\n  {email or "(not provided)"}\n\n'
            f"App state:\n{state_json}\n"
        )
        message = Mail(
            from_email="noreply@cheapcadtools.com",
            to_emails="info@cheapcadtools.com",
            subject=f"[{tool_name}] {report_label}",
            plain_text_content=body,
        )
        SendGridAPIClient(api_key).send(message)
    except Exception:
        pass  # email failure must never break the log write


def _is_live_mode(mode):
    from cct_common.deploy_mode import is_live
    return is_live("CCT_BUG_REPORT_MODE", mode)


def register_bug_report_route(app, tool_name: str, app_version: str,
                              log_dir=None, path: str = "/api/report-bug",
                              mode: str | None = None):
    """Register a POST route on `app` that saves a bug/feature report.

    Always appends to `<log_dir>/bug_reports.log` (log_dir defaults to a
    `logs/` folder next to the calling app). In live mode (see module
    docstring), also best-effort files a GitHub issue and emails a
    notification.
    """
    from flask import jsonify, request

    log_dir = log_dir or os.path.join(os.getcwd(), "logs")
    log_file = os.path.join(log_dir, "bug_reports.log")

    def _report_bug():
        try:
            data = request.get_json(force=True) or {}
            seeing = str(data.get("seeing", "")).strip()
            should_see = str(data.get("should_see", "")).strip()
            email = str(data.get("email", "")).strip()
            state = data.get("state", {})
            report_type = str(data.get("report_type", "bug")).strip()

            if not seeing and not should_see:
                return jsonify({"error": "At least one description field is required."}), 400

            is_feature = report_type == "feature"
            report_label = "Feature Request" if is_feature else "Bug Report"
            label_seeing = "Would like to do" if is_feature else "Currently seeing"
            label_should = "Why it's useful" if is_feature else "Should be seeing"

            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = (
                f'\n{"=" * 60}\n'
                f"{report_label} — {timestamp}\n"
                f"Tool: {tool_name}   App Version: {app_version}\n"
                f'{"=" * 60}\n'
                f'{label_seeing}:\n  {seeing or "(not provided)"}\n\n'
                f'{label_should}:\n  {should_see or "(not provided)"}\n\n'
                f'Contact email:\n  {email or "(not provided)"}\n\n'
                f"App state:\n{json.dumps(state, indent=2)}\n"
            )
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry)

            issue_url = None
            if _is_live_mode(mode):
                issue_url = _create_github_issue(
                    report_label, timestamp, label_seeing, label_should,
                    seeing, should_see, email, state, report_type,
                    tool_name, app_version)
                _send_report_email(
                    report_label, timestamp, label_seeing, label_should,
                    seeing, should_see, email, state, tool_name, app_version)

            return jsonify({"ok": True, "issue_url": issue_url})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    _report_bug.__name__ = "_cct_common_report_bug"
    app.add_url_rule(path, endpoint="_cct_common_report_bug",
                     view_func=_report_bug, methods=["POST"])
    return _report_bug
