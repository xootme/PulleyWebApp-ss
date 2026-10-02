"""The session queue is compiled out (app.SESSION_QUEUE = False; the owner,
2026-10-01: the Google Cloud site no longer uses it). With it off nothing
queues even without QUEUE_DISABLED: the page opens straight away, the export
routes need no session, and the queue's own routes are not in the app."""
import pytest

import app as app_module

QUEUE_RULES = ["/queue", "/api/session/create", "/api/session/status", "/api/session/heartbeat",
               "/api/session/release", "/api/session/register-machine", "/api/queue/status",
               "/api/test/reset"]

pytestmark = pytest.mark.skipif(app_module.SESSION_QUEUE, reason="the session queue is compiled in")


@pytest.fixture
def client(monkeypatch):
    # As the live site would be with no switch set at all.
    monkeypatch.delenv("QUEUE_DISABLED", raising=False)
    monkeypatch.delenv("PULLEY_TESTING", raising=False)
    app_module.app.config["TESTING"] = False
    try:
        yield app_module.app.test_client()
    finally:
        app_module.app.config["TESTING"] = True


def test_the_switch_is_off():
    assert app_module.SESSION_QUEUE is False
    assert app_module.queue_off()


def test_the_queues_routes_are_not_in_the_app():
    rules = {r.rule for r in app_module.app.url_map.iter_rules()}
    assert not rules & set(QUEUE_RULES), rules & set(QUEUE_RULES)


def test_the_page_opens_without_a_session(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Timing Pulley Generator" in r.data
    assert "Location" not in r.headers


@pytest.mark.parametrize("rule", QUEUE_RULES)
def test_the_queue_routes_answer_404(client, rule):
    assert client.get(rule).status_code in (404, 405)
    assert client.post(rule, json={}).status_code == 404


def test_an_export_needs_no_session(client):
    r = client.post("/api/download/bundle", json={"name": "x", "files": [
        {"path": "/download/dxf", "params": {"family": "HTD", "pitch": "5M", "teeth": "20", "bore": "8"}}]})
    assert r.status_code == 200 and "job_id" in r.get_json()
