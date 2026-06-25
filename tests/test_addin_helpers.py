"""
test_addin_helpers.py — unit tests for exporters/addin_helpers.AddinDownloader.

Covers the REST client used by CAD addins: success paths for STEP/DXF/STL,
the trial-limit (HTTP 429) path, generic HTTP/network errors, and that the
machine_id is included in every request payload.

All network I/O is mocked (urllib.request.urlopen); no server required.
"""
import io
import json
import urllib.error
from unittest.mock import patch, MagicMock

import pytest

from exporters.addin_helpers import (
    AddinDownloader,
    DownloadError,
    DownloadLimitExceeded,
)

BASE_URL = "https://cheapcadtools.com"
MACHINE_ID = "abc123machine"


def _downloader():
    return AddinDownloader(BASE_URL, MACHINE_ID, timeout=5)


def _ok_response(data: bytes, content_type: str = "application/octet-stream"):
    """A urlopen() context-manager mock returning binary `data`."""
    resp = MagicMock()
    resp.read.return_value = data
    resp.headers.get.return_value = content_type
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def _http_error(code: int, body: bytes = b"", reason: str = "err"):
    return urllib.error.HTTPError(
        url="http://x", code=code, msg=reason, hdrs=None, fp=io.BytesIO(body)
    )


# ── Success paths ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,fmt", [
    ("download_step", "step"),
    ("download_dxf",  "dxf"),
    ("download_stl",  "stl"),
])
def test_download_success_returns_bytes(method, fmt):
    payload = b"BINARY-FILE-DATA"
    with patch("urllib.request.urlopen", return_value=_ok_response(payload)) as m:
        out = getattr(_downloader(), method)({"teeth": 20})
    assert out == payload
    # endpoint URL carries the format
    req = m.call_args.args[0]
    assert req.full_url == f"{BASE_URL}/api/download/{fmt}"


def test_machine_id_in_every_request_payload():
    captured = {}

    def _capture(req, timeout=None):
        captured["data"] = req.data
        return _ok_response(b"x")

    with patch("urllib.request.urlopen", side_effect=_capture):
        _downloader().download_step({"teeth": 30})
    body = json.loads(captured["data"].decode())
    assert body["machine_id"] == MACHINE_ID
    assert body["params"] == {"teeth": 30}


# ── Trial-limit (HTTP 429) ──────────────────────────────────────────────────────
def test_http_429_raises_limit_exceeded_with_counts():
    body = json.dumps({"count": 2, "limit": 2}).encode()
    with patch("urllib.request.urlopen", side_effect=_http_error(429, body)):
        with pytest.raises(DownloadLimitExceeded) as ei:
            _downloader().download_step({})
    assert ei.value.count == 2
    assert ei.value.limit == 2


def test_http_429_without_json_falls_back_to_defaults():
    with patch("urllib.request.urlopen", side_effect=_http_error(429, b"not-json")):
        with pytest.raises(DownloadLimitExceeded) as ei:
            _downloader().download_stl({})
    # falls back to (0, 2)
    assert ei.value.limit == 2


# ── Other errors ────────────────────────────────────────────────────────────────
def test_http_500_raises_download_error():
    with patch("urllib.request.urlopen", side_effect=_http_error(500, b"", "Server Error")):
        with pytest.raises(DownloadError) as ei:
            _downloader().download_dxf({})
    assert "500" in str(ei.value)
    assert not isinstance(ei.value, DownloadLimitExceeded)


def test_network_failure_raises_download_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        with pytest.raises(DownloadError):
            _downloader().download_step({})


def test_limit_exceeded_is_a_download_error_subclass():
    # Callers may catch DownloadError broadly; limit must still be distinguishable.
    assert issubclass(DownloadLimitExceeded, DownloadError)
