"""
edge_proxy.py — which forwarding headers to believe.

The app builds links in emails from the request's host (sign-in links),
and keys abuse limits on the visitor's IP (sign-in emails, free signup
tokens). Both come from X-Forwarded-* headers when there's a proxy in
front — and a visitor can send those headers too:

- a forged X-Forwarded-Host would make a sign-in email for someone
  else's address link to the forger's site, which then receives the
  sign-in token when the victim clicks it;
- a forged X-Forwarded-For would dodge the per-IP limits.

So the headers are believed only from our own edge: the Cloudflare Worker
in front of cheapcadtools.com sends X-CCT-Edge with a shared secret
(EDGE_SECRET). For those requests the Worker's X-Forwarded-For (the
visitor's IP, from CF-Connecting-IP), -Host and -Proto are trusted, with
Cloud Run's front end appending one more X-Forwarded-For entry (the
Worker's): EDGE_HOPS=2. Every other request — someone calling the
*.run.app address directly — gets only what Cloud Run's front end itself
adds: the last X-Forwarded-For entry and the protocol, never a forwarded
host or prefix (DIRECT_HOPS=1).
"""
from __future__ import annotations

import hmac
import os

from werkzeug.middleware.proxy_fix import ProxyFix

EDGE_HEADER = "HTTP_X_CCT_EDGE"


class EdgeAwareProxyFix:
    def __init__(self, app, *, secret: str | None = None,
                 edge_hops: int | None = None, direct_hops: int | None = None):
        self.secret = (os.environ.get("EDGE_SECRET", "") if secret is None else secret).strip()
        edge_hops = int(os.environ.get("EDGE_HOPS", "2")) if edge_hops is None else edge_hops
        direct_hops = int(os.environ.get("PROXY_HOPS", "1")) if direct_hops is None else direct_hops
        self.edge = ProxyFix(app, x_for=edge_hops, x_proto=1, x_host=1, x_prefix=1)
        self.direct = ProxyFix(app, x_for=direct_hops, x_proto=1, x_host=0, x_prefix=0)

    def from_edge(self, environ) -> bool:
        got = environ.get(EDGE_HEADER, "")
        return bool(self.secret) and hmac.compare_digest(got.encode(), self.secret.encode())

    def __call__(self, environ, start_response):
        edge = self.from_edge(environ)
        environ.pop(EDGE_HEADER, None)          # the secret never reaches the app
        return (self.edge if edge else self.direct)(environ, start_response)
