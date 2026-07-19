"""
resend_email.py — send a transactional email via the Resend HTTP API
(https://resend.com), stdlib-only.

    from cct_common.resend_email import send

    ok, err = send("user@example.com", "Subject", "body text")
    if not ok:
        app.logger.error("email failed: %s", err)

Reads the API key from the RESEND_API_KEY environment variable — never
hardcode it in a caller. `send()` never raises; failures come back as
(False, error_message) so a notification-email bug can never break the
request that triggered it.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "CheapCAD Tools <info@cheapcadtools.com>"


def send(to: str, subject: str, body: str, from_addr: str = DEFAULT_FROM,
        api_key: str | None = None, user_agent: str = "CCT-App/1.0",
        logger=None):
    """Returns (True, '') on success or (False, error_message) on failure."""
    api_key = api_key or os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        return False, "RESEND_API_KEY not configured"
    payload = json.dumps({
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "text": body,
    }).encode()
    try:
        req = urllib.request.Request(
            RESEND_URL, data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": user_agent,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read())
        if result.get("id"):
            if logger:
                logger.info("Resend email sent to %s: %s", to, subject)
            return True, ""
        err = result.get("message", "resend failed")
        if logger:
            logger.error("Resend email failed to %s: %s", to, err)
        return False, err
    except urllib.error.HTTPError as exc:
        body_text = exc.read(512).decode("utf-8", errors="replace")
        if logger:
            logger.error("Resend email HTTP error to %s: %s body=%s", to, exc, body_text)
        return False, f"{exc} body={body_text}"
    except Exception as exc:
        if logger:
            logger.error("Resend email error to %s: %s", to, exc)
        return False, str(exc)
