"""
bug_report.py — shared bug/feature-report endpoint for CCT Flask apps.

Always logs to a local file. In live mode it also sends a SendGrid email
— gated by an explicit mode, not by credential presence, so a stray key
left in a dev shell can never cause a local test run to send anything.

WHAT REACHES GITHUB, AND WHAT DOES NOT. An issue carries the tracking
half of a report: when, which tool, which version, what the person said
was wrong. It carries NO design and NO contact address.

That split exists because the two halves have different owners. The
description is written knowingly, by someone who can see it as they
type. The `state` blob is not: it is the board they imported, every mark
they drew, the standoff selection — their work, swept up automatically.
Publishing it in a repository they never chose is not something an issue
tracker should do, and closing an issue does not unpublish it: GitHub
has already emailed the body to every watcher and kept it in the events
API.

The design goes to the LOG AND NOWHERE ELSE. Not the issue, not the
notification email -- because an emailed design cannot be deleted, and
the log can (bug_report_admin exposes DELETE). One store, one delete,
and a promise that is true rather than meant. The reporter's address
travels to the private inbox only, since they typed it to be contacted.
See _issue_body() and _send_report_email().

    from cct_common.bug_report import register_bug_report_route

    register_bug_report_route(app, tool_name="ebox", app_version="0.1.0")

Mode selection (see cct_common.deploy_mode) — local-only "dev" vs
GitHub+email "live":
    - pass `mode="live"` explicitly, or
    - set CCT_BUG_REPORT_MODE=live, or
    - set the umbrella CCT_MODE=live (used when CCT_BUG_REPORT_MODE isn't set)
Anything else (unset, "dev", or any other value) stays local-only.

Live mode needs its own credentials per channel (each silently no-ops
without them — a delivery failure must never break the log write or the
user-facing response):
    - GitHub issue:  FEEDBACK_GITHUB_PAT, FEEDBACK_GITHUB_REPO
    - Email:         SENDGRID_API_KEY

Flask (and, in live mode, the `sendgrid` package) are only imported
inside register_bug_report_route()/the helpers that need them, so
importing cct_common never requires either unless a caller wires this in.
"""
from __future__ import annotations

import json
import os
from datetime import datetime


def _report_id(timestamp, seeing, should_see):
    """A short, stable handle for one report.

    The issue says it; the log and the email carry it. That is what lets
    a public tracking entry point at a private record without repeating
    any of it -- and what makes "we deleted our copy" a thing that can
    actually be done to a named row.
    """
    import hashlib

    raw = "|".join((timestamp, seeing or "", should_see or ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _issue_body(report_id, timestamp, label_seeing, label_should,
                seeing, should_see, tool_name, app_version):
    """The public half of a report, and only that.

    Everything here was typed by the reporter or is about the software.
    No state, no board, no email address. Assembled in its own function
    so that what is published is one readable list rather than something
    to be reasoned about at a call site.
    """
    lines = [
        "**Report:** `%s`" % report_id,
        "**Tool:** %s %s" % (tool_name, app_version),
        "**When:** %s" % timestamp,
        "",
        "**%s:**" % label_seeing,
        seeing or "_(not provided)_",
        "",
        "**%s:**" % label_should,
        should_see or "_(not provided)_",
        "",
        "---",
        "_The reporter's design and contact details are not "
        "included here. They are held privately against this "
        "report id._",
    ]
    return "\n".join(lines) + "\n"


def _create_github_issue(report_id, report_label, timestamp, label_seeing,
                         label_should, seeing, should_see, report_type,
                         tool_name, app_version):
    """File the tracking half of a report. Skips without credentials.

    Note what this does NOT take: no `state`, no `email`. They are not
    filtered out here -- they are not passed in, so a later edit cannot
    reintroduce them by reaching for a variable that happens to be in
    scope.
    """
    pat = os.environ.get("FEEDBACK_GITHUB_PAT", "").strip()
    repo = os.environ.get("FEEDBACK_GITHUB_REPO", "").strip()
    if not pat or not repo:
        return None
    try:
        from cct_common.github_api import request as gh_request

        title = f"[{tool_name}] {report_label}: " + (
            (seeing or should_see or "no description")[:70])
        resp, status = gh_request(
            "POST", f"/repos/{repo}/issues", pat,
            body={"title": title,
                  "body": _issue_body(report_id, timestamp, label_seeing,
                                      label_should, seeing, should_see,
                                      tool_name, app_version),
                  "labels": ["feature" if report_type == "feature" else "bug"]})
        if status >= 300:
            return None
        return (resp or {}).get("html_url")
    except Exception:
        return None  # a tracker failure must never break the log write


def _send_report_email(report_id, report_label, timestamp, label_seeing,
                       label_should, seeing, should_see, email,
                       tool_name, app_version):
    """Fire-and-forget SendGrid notification. Skips without a key.

    NO DESIGN. Like the issue, this does not TAKE `state` -- so it
    cannot leak one, and a later edit cannot reintroduce it by
    reaching for a variable in scope.

    The reason is a promise rather than a rule of thumb: a design that
    has been emailed cannot be deleted. It is in an inbox, a backup, a
    phone and a search index, and no amount of intent gets it back. So
    the design lives in exactly ONE place -- the log, which has a
    DELETE endpoint (see bug_report_admin) -- and "we delete our copy"
    becomes a thing that is true rather than meant.

    The reporter's own address DOES travel: they typed it in order to
    be contacted, and it goes to a private inbox, never to the issue.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not api_key:
        return
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        lines = [
            "%s - %s" % (report_label, timestamp),
            "Report id: %s" % report_id,
            "Tool: %s   App Version: %s" % (tool_name, app_version),
            "",
            "%s:" % label_seeing,
            "  %s" % (seeing or "(not provided)"),
            "",
            "%s:" % label_should,
            "  %s" % (should_see or "(not provided)"),
            "",
            "Contact email:",
            "  %s" % (email or "(not provided)"),
            "",
            "The reporter's design is NOT attached. It is held only in",
            "the report log, against the id above, and is deleted when",
            "the bug is fixed.",
        ]
        message = Mail(
            from_email="noreply@cheapcadtools.com",
            to_emails="info@cheapcadtools.com",
            subject="[%s] %s %s" % (tool_name, report_label, report_id),
            plain_text_content="\n".join(lines) + "\n",
        )
        SendGridAPIClient(api_key).send(message)
    except Exception:
        pass  # a notification failure must never break the log write


def _is_live_mode(mode):
    from cct_common.deploy_mode import is_live
    return is_live("CCT_BUG_REPORT_MODE", mode)


def register_bug_report_route(app, tool_name: str, app_version: str,
                              log_dir=None, path: str = "/api/report-bug",
                              mode: str | None = None):
    """Register a POST route on `app` that saves a bug/feature report.

    Always appends to `<log_dir>/bug_reports.log` (log_dir defaults to a
    `logs/` folder next to the calling app). In live mode (see module
    docstring) it also files a GitHub issue and emails a notification,
    best-effort. The ISSUE carries no design and no contact address --
    see the module docstring.
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
            report_id = _report_id(timestamp, seeing, should_see)
            entry = ("Report id:\n  %s\n\n" % report_id) + entry
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry)

            issue_url = None
            if _is_live_mode(mode):
                issue_url = _create_github_issue(
                    report_id, report_label, timestamp, label_seeing,
                    label_should, seeing, should_see, report_type,
                    tool_name, app_version)
                _send_report_email(
                    report_id, report_label, timestamp, label_seeing,
                    label_should, seeing, should_see, email,
                    tool_name, app_version)

            return jsonify({"ok": True, "issue_url": issue_url,
                            "report_id": report_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    _report_bug.__name__ = "_cct_common_report_bug"
    app.add_url_rule(path, endpoint="_cct_common_report_bug",
                     view_func=_report_bug, methods=["POST"])
    return _report_bug
