"""Forwarding headers are believed only from our Cloudflare Worker
(edge_proxy.py). The attack this stops: request a sign-in link for a
victim's address with a forged X-Forwarded-Host, so the emailed link points
at the attacker's site and hands them the victim's sign-in token."""
import re

import flask
import pytest

from accounts_setup import init_accounts
from edge_proxy import EdgeAwareProxyFix

SECRET = "edge-secret-for-tests"


@pytest.fixture
def site(tmp_path):
    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    sent = []
    init_accounts(app, log_dir=str(tmp_path), enabled=True, live=False,
                  email_sender=lambda *a: sent.append(a) or (True, ""))

    @app.route("/whoami")
    def whoami():
        return {"ip": flask.request.remote_addr, "root": flask.request.url_root}

    app.wsgi_app = EdgeAwareProxyFix(app.wsgi_app, secret=SECRET, edge_hops=2, direct_hops=1)
    app.sent = sent
    return app


def _link(app, headers):
    c = app.test_client()
    c.post("/api/account/login-link", json={"email": "victim@example.com"},
           headers=headers, environ_overrides={"REMOTE_ADDR": "10.0.0.9"})
    return re.search(r"(https?://\S+/account/login\?token=)", app.sent[-1][2]).group(1)


def test_forged_forwarded_host_cannot_redirect_a_sign_in_link(site):
    link = _link(site, {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
    assert "evil.example" not in link and link.startswith("https://localhost/")


def test_forged_forwarded_for_is_ignored_without_the_edge_secret(site):
    r = site.test_client().get("/whoami", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.5"},
                               environ_overrides={"REMOTE_ADDR": "10.0.0.9"})
    assert r.get_json()["ip"] == "203.0.113.5"       # only the entry Cloud Run appended


def test_the_edge_is_trusted_for_host_and_visitor_ip(site):
    # The Worker sets X-Forwarded-For to the visitor's IP; Cloud Run appends
    # the Worker's own address after it.
    headers = {"X-CCT-Edge": SECRET, "X-Forwarded-Host": "cheapcadtools.com",
               "X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.7, 172.70.1.1"}
    r = site.test_client().get("/whoami", headers=headers, environ_overrides={"REMOTE_ADDR": "10.0.0.9"})
    assert r.get_json() == {"ip": "198.51.100.7", "root": "https://cheapcadtools.com/"}
    assert _link(site, headers).startswith("https://cheapcadtools.com/account/login?token=")


def test_a_wrong_edge_secret_counts_as_direct(site):
    headers = {"X-CCT-Edge": "guess", "X-Forwarded-Host": "evil.example",
               "X-Forwarded-For": "6.6.6.6, 203.0.113.5"}
    r = site.test_client().get("/whoami", headers=headers, environ_overrides={"REMOTE_ADDR": "10.0.0.9"})
    assert r.get_json()["ip"] == "203.0.113.5" and "evil" not in r.get_json()["root"]


def test_no_secret_configured_trusts_no_edge(tmp_path):
    app = flask.Flask(__name__)

    @app.route("/whoami")
    def whoami():
        return {"root": flask.request.url_root}

    app.wsgi_app = EdgeAwareProxyFix(app.wsgi_app, secret="", edge_hops=2, direct_hops=1)
    r = app.test_client().get("/whoami", headers={"X-CCT-Edge": "", "X-Forwarded-Host": "evil.example"})
    assert "evil" not in r.get_json()["root"]


def test_the_secret_header_never_reaches_the_app(site):
    @site.route("/headers")
    def headers():
        return {"edge": flask.request.headers.get("X-CCT-Edge")}

    r = site.test_client().get("/headers", headers={"X-CCT-Edge": SECRET})
    assert r.get_json() == {"edge": None}


def test_real_app_uses_the_edge_aware_proxy():
    import app as app_module
    assert isinstance(app_module.app.wsgi_app, EdgeAwareProxyFix)
