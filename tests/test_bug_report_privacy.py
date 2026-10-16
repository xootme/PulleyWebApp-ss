"""What a bug report sends where (privacy policy, cct_common.bug_report):
the GitHub issue gets the description only — no design, no address; the
notification email gets the description and the address, not the design;
the log and the database keep everything, the database outliving the
server (Cloud Run)."""
import json

import pytest

EMAIL = "reporter@example.com"
DESIGN = {"family": "HTD", "pitch": "5M", "teeth": "31", "bore": "8.25", "secret_marker": "DESIGN-XYZ"}


def _report(client, **extra):
    body = {"seeing": "flange looks wrong", "should_see": "a round flange",
            "email": EMAIL, "state": DESIGN, "error_message": "E1", "user_comment": "thanks"}
    body.update(extra)
    return client.post("/api/report-bug", data=json.dumps(body), content_type="application/json")


@pytest.fixture
def issues(monkeypatch):
    sent = []
    import cct_common.github_api as gh

    def fake_request(method, path, token, body=None, **kw):
        sent.append({"method": method, "path": path, "body": body})
        return {"html_url": "https://github.com/xootme/cct-feedback/issues/7"}, 201

    monkeypatch.setattr(gh, "request", fake_request)
    monkeypatch.setenv("FEEDBACK_GITHUB_PAT", "test-pat")
    monkeypatch.setenv("FEEDBACK_GITHUB_REPO", "xootme/cct-feedback")
    monkeypatch.setenv("CCT_BUG_REPORT_MODE", "live")
    return sent


@pytest.fixture
def emails(monkeypatch):
    import app as app_module
    sent = []
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setattr(app_module, "_smtp_send",
                        lambda to, subject, body, reply_to="": sent.append((to, subject, body, reply_to)))
    return sent


def test_issue_has_the_description_but_no_design_or_address(client, issues):
    r = _report(client)
    assert r.status_code == 200 and r.get_json()["issue_url"].endswith("/issues/7")
    (call,) = issues
    issue = json.dumps(call["body"])
    assert "flange looks wrong" in issue and r.get_json()["report_id"] in issue
    assert EMAIL not in issue and "DESIGN-XYZ" not in issue and "8.25" not in issue


def test_email_has_the_address_but_not_the_design(client, emails):
    r = _report(client)
    (to, subject, body, reply_to) = emails[-1]
    assert to == "support@cheapcadtools.com" and r.get_json()["report_id"] in subject
    assert EMAIL in body and "flange looks wrong" in body and reply_to == EMAIL
    assert "DESIGN-XYZ" not in body and "8.25" not in body


@pytest.fixture
def resend(monkeypatch):
    """_smtp_send's two paths, recorded: with a Reply-To (local) and plain (cct_common)."""
    import app as app_module
    calls = {"reply_to": [], "plain": [], "reply_to_ok": True}

    def with_reply_to(to, subject, body, from_addr, reply_to):
        calls["reply_to"].append(reply_to)
        return (True, "") if calls["reply_to_ok"] else (False, "422 invalid reply_to")

    monkeypatch.setattr(app_module, "_resend_with_reply_to", with_reply_to)
    monkeypatch.setattr(app_module, "_cc_send_email", lambda to, *a, **kw: calls["plain"].append(to) or (True, ""))
    return calls


def test_reply_to_is_the_reporters_address(resend):
    from app import _smtp_send
    assert _smtp_send("support@cheapcadtools.com", "s", "b", reply_to=EMAIL) == (True, "")
    assert resend["reply_to"] == [EMAIL] and resend["plain"] == []


@pytest.mark.parametrize("bad", ["", "not an address", "a@b.com, evil@x.com", "a@b.com\nBcc: evil@x.com",
                                 "Name <a@b.com>", "a@b"])
def test_no_reply_to_unless_it_is_one_address(resend, bad):
    from app import _smtp_send
    _smtp_send("support@cheapcadtools.com", "s", "b", reply_to=bad)
    assert resend["reply_to"] == [] and resend["plain"] == ["support@cheapcadtools.com"]


def test_a_refused_reply_to_still_sends_the_report(resend):
    from app import _smtp_send
    resend["reply_to_ok"] = False
    assert _smtp_send("support@cheapcadtools.com", "s", "b", reply_to=EMAIL) == (True, "")
    assert resend["reply_to"] == [EMAIL] and resend["plain"] == ["support@cheapcadtools.com"]


def test_log_keeps_everything_under_the_report_id(client):
    from app import _LOG_FILE
    rid = _report(client).get_json()["report_id"]
    log = open(_LOG_FILE, encoding="utf-8").read()
    tail = log[log.rindex(rid):]
    assert EMAIL in tail and "DESIGN-XYZ" in tail and "E1" in tail


def test_database_keeps_everything_and_can_delete_it(client, monkeypatch, tmp_path):
    import app as app_module
    from bug_store import BugReports
    store = BugReports(str(tmp_path / "bugs.sqlite3"))
    monkeypatch.setattr(app_module, "_bug_store", store)
    rid = _report(client).get_json()["report_id"]
    row = store.get(rid)
    assert row["email"] == EMAIL and row["state"]["secret_marker"] == "DESIGN-XYZ"
    assert row["error_msg"] == "E1" and row["user_comment"] == "thanks"
    assert store.delete(rid) and store.get(rid) is None       # a deletion request


def test_database_records_the_issue_link(client, issues, monkeypatch, tmp_path):
    import app as app_module
    from bug_store import BugReports
    store = BugReports(str(tmp_path / "bugs.sqlite3"))
    monkeypatch.setattr(app_module, "_bug_store", store)
    rid = _report(client).get_json()["report_id"]
    assert store.get(rid)["issue_url"].endswith("/issues/7")


def test_no_forwarding_to_the_old_desktop_path(client, monkeypatch):
    # There's no local version any more: a report is never relayed elsewhere.
    import urllib.request
    monkeypatch.setenv("PULLEY_BASE_DIR", "C:/somewhere")
    monkeypatch.delenv("FEEDBACK_GITHUB_PAT", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("report forwarded over the network"))
    assert _report(client).status_code == 200


def test_the_reply_to_request_carries_the_address(monkeypatch):
    import io
    import urllib.request
    import app as app_module
    posted = []

    def fake_urlopen(req, timeout):
        posted.append(json.loads(req.data))
        return io.BytesIO(b'{"id": "e1"}')

    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert app_module._resend_with_reply_to("support@cheapcadtools.com", "s", "b",
                                            "CheapCADTools <info@cheapcadtools.com>", EMAIL) == (True, "")
    (p,) = posted
    assert p["reply_to"] == EMAIL and p["to"] == ["support@cheapcadtools.com"]
    assert p["from"] == "CheapCADTools <info@cheapcadtools.com>"
