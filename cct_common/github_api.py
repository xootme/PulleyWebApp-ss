"""
github_api.py — a tiny generic GitHub REST API caller.

Shared by cct_common.bug_report (issue creation) and any app's own admin
routes that need to close/comment/sync GitHub issues. Stdlib-only
(urllib), no dependency on requests/PyGithub/etc.

    from cct_common.github_api import request

    data, status = request("POST", "/repos/xootme/cct-feedback/issues", pat,
                           body={"title": "...", "body": "...", "labels": ["bug"]})
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def request(method: str, path: str, pat: str, body: dict | None = None):
    """Call `https://api.github.com<path>`. Returns (response_dict, status_code).

    `path` must start with `/` (e.g. `/repos/<owner>/<repo>/issues`).
    """
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}"), e.code
