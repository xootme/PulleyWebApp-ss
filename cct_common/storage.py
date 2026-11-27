"""
storage.py — an app's private files in a bucket (0.64.0; the PCB Importer
cloud move, the owner's "all as recommended", CCT_Business D-36).

    from cct_common.storage import storage_for
    st = storage_for("gs://cheapcadtools-pcb")      # or a folder: storage_for(r"C:\\pcb-data")
    st.put("accounts/a1/boards/b1/board.zip", data, "application/zip")
    data = st.get("accounts/a1/boards/b1/board.zip")
    with st.open("accounts/a1/boards/b1/board.zip") as f:    # streamed, for a download route
        chunk = f.read(1 << 20)
    st.list("accounts/a1/")                         # the names under it, sorted
    st.delete_prefix("accounts/a1/")                # how many went

- Two backends, one contract. GCSStorage: Cloud Storage's JSON API with
  this service's own identity (the metadata server's token, kept until a
  minute before it expires), stdlib HTTP only, the code admin_export used
  for its upload. FolderStorage: a folder, for the local install,
  development and tests.
- No signed URLs (the owner's Q-C5): a person downloads through the app's
  own signed-in route, which streams the object (open()), so a link can't
  be passed on and the service account needs no signing role.
- Names are checked the same way for both: segments of letters, digits,
  "_", "-" and ".", not starting with ".", joined by "/", at most 1,024
  bytes. The app makes its names (account id, board id); a name a person or
  an upload chose never goes in raw. A prefix ends with "/" and is never
  empty, so nothing deletes a whole bucket.
- Over RESUMABLE_AT (8 MB) an object goes up as a resumable upload in CHUNK
  pieces (8 MiB, a multiple of the 256 KiB Cloud Storage requires), from
  bytes or a binary file, never all in memory at once from a file.
- get() or open() of a missing object raises NotFound (a KeyError).
  delete() of one isn't an error: a second delete, or a purge retried after
  a crash, is the normal case.
- Cloud Storage's 429 and 5xx answers are retried, twice, 1 s then 2 s
  later, for every request but a resumable upload's pieces (that upload
  fails as a whole, and the caller's job runs again). Anything else (a 403:
  the service account lacks a role) is raised as it came.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO, Callable, Union

METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
GCS_API = "https://storage.googleapis.com/storage/v1/b/{bucket}/o"
GCS_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
RESUMABLE_AT = 8 * 1000 * 1000
CHUNK = 8 * 1024 * 1024
RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRY_WAITS = (1.0, 2.0)
MAX_NAME = 1024

_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{0,221}")    # Google checks the rest of its rules

Data = Union[bytes, BinaryIO]


class StorageError(Exception):
    """A name or prefix the store refuses."""


class NotFound(KeyError):
    """No object by that name."""


def check_name(name: str) -> str:
    """The name, if it's one an object may have; else StorageError."""
    if not isinstance(name, str) or not name or len(name.encode()) > MAX_NAME:
        raise StorageError(f"not an object name: {name!r}")
    if not all(_SEGMENT.fullmatch(s) for s in name.split("/")):
        raise StorageError(f"not an object name: {name!r}")
    return name


def check_prefix(prefix: str) -> str:
    """A prefix: a name's leading segments, ending with "/"; never empty."""
    if not isinstance(prefix, str) or not prefix.endswith("/"):
        raise StorageError(f"a prefix ends with '/': {prefix!r}")
    check_name(prefix[:-1])
    return prefix


def _read(f: BinaryIO, n: int) -> bytes:
    """Up to n bytes, fewer only at the end."""
    parts, got = [], 0
    while got < n:
        b = f.read(n - got)
        if not b:
            break
        parts.append(b)
        got += len(b)
    return b"".join(parts)


def _urlopen(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


class GCSStorage:
    """A Cloud Storage bucket, as this service's own service account."""

    def __init__(self, bucket: str, *, opener: Callable = _urlopen, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        if not _BUCKET.fullmatch(bucket or ""):
            raise StorageError(f"not a bucket name: {bucket!r}")
        self.bucket = bucket
        self._opener, self._clock, self._sleep = opener, clock, sleep
        self._token, self._token_until = None, 0.0
        self._b = urllib.parse.quote(bucket, safe="")

    def __repr__(self):
        return f"GCSStorage(gs://{self.bucket})"

    # ── HTTP ──
    def _auth(self) -> dict:
        if self._token is None or self._clock() >= self._token_until:
            req = urllib.request.Request(METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
            with self._opener(req, 10) as r:
                d = json.loads(r.read())
            self._token = d["access_token"]
            self._token_until = self._clock() + max(0, int(d.get("expires_in") or 0) - 60)
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method, url, data=None, headers=None, *, timeout=60, retry=True, keep_open=False):
        """The response (read and closed, or open with keep_open); HTTPError as raised."""
        waits = list(RETRY_WAITS) if retry else []
        while True:
            req = urllib.request.Request(url, data=data, method=method, headers={**self._auth(), **(headers or {})})
            try:
                r = self._opener(req, timeout)
            except urllib.error.HTTPError as e:
                if e.code in RETRY_STATUSES and waits:
                    self._sleep(waits.pop(0))
                    continue
                raise
            if keep_open:
                return r
            with r:
                return getattr(r, "status", 200), dict(getattr(r, "headers", None) or {}), r.read()

    def _object_url(self, name: str) -> str:
        return GCS_API.format(bucket=self._b) + "/" + urllib.parse.quote(name, safe="")

    # ── the contract ──
    def put(self, name: str, data: Data, content_type: str = "application/octet-stream") -> None:
        check_name(name)
        if isinstance(data, (bytes, bytearray)):
            data = bytes(data)
            if len(data) <= RESUMABLE_AT:
                return self._simple(name, data, content_type)
            import io
            return self._resumable(name, io.BytesIO(data), content_type)
        head = _read(data, RESUMABLE_AT + 1)
        if len(head) <= RESUMABLE_AT:
            return self._simple(name, head, content_type)
        return self._resumable(name, _Joined(head, data), content_type)

    def _simple(self, name, data, content_type):
        url = GCS_UPLOAD.format(bucket=self._b) + "?" + urllib.parse.urlencode({"uploadType": "media", "name": name})
        self._request("POST", url, data, {"Content-Type": content_type})

    def _resumable(self, name, f, content_type):
        url = GCS_UPLOAD.format(bucket=self._b) + "?" + urllib.parse.urlencode(
            {"uploadType": "resumable", "name": name})
        _, headers, _ = self._request("POST", url, b"", {"X-Upload-Content-Type": content_type,
                                                          "Content-Length": "0"})
        session = next((v for k, v in headers.items() if k.lower() == "location"), None)
        if not session:
            raise StorageError("Cloud Storage gave no upload session")
        offset, chunk = 0, _read(f, CHUNK)
        while True:
            nxt = _read(f, CHUNK) if len(chunk) == CHUNK else b""
            end = offset + len(chunk)
            last = not nxt
            rng = (f"bytes {offset}-{end - 1}/{end if last else '*'}" if chunk else f"bytes */{end}")
            try:
                self._request("PUT", session, chunk, {"Content-Range": rng}, timeout=300, retry=False)
            except urllib.error.HTTPError as e:
                if e.code != 308 or last:          # 308: "resume incomplete", the piece was taken
                    raise
            if last:
                return
            offset, chunk = end, nxt

    def open(self, name: str) -> BinaryIO:
        check_name(name)
        try:
            return self._request("GET", self._object_url(name) + "?alt=media", keep_open=True, timeout=300)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(name) from None
            raise

    def get(self, name: str) -> bytes:
        with self.open(name) as f:
            return f.read()

    def delete(self, name: str) -> bool:
        """True if it was there."""
        check_name(name)
        try:
            self._request("DELETE", self._object_url(name))
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise

    def list(self, prefix: str) -> list[str]:
        check_prefix(prefix)
        names, token = [], None
        while True:
            q = {"prefix": prefix, "fields": "items(name),nextPageToken"}
            if token:
                q["pageToken"] = token
            _, _, body = self._request("GET", GCS_API.format(bucket=self._b) + "?" + urllib.parse.urlencode(q))
            page = json.loads(body or b"{}")
            names += [i["name"] for i in page.get("items", [])]
            token = page.get("nextPageToken")
            if not token:
                return sorted(names)

    def delete_prefix(self, prefix: str) -> int:
        return sum(self.delete(n) for n in self.list(prefix))


class _Joined:
    """head, then the rest of f: a file read once, never all in memory."""

    def __init__(self, head: bytes, f: BinaryIO):
        self._head, self._f = head, f

    def read(self, n: int = -1) -> bytes:
        if not self._head:
            return self._f.read(n)
        if n < 0:
            out, self._head = self._head + self._f.read(), b""
        else:
            out, self._head = self._head[:n], self._head[n:]
        return out


class FolderStorage:
    """A folder: the same names, as files under root."""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)

    def __repr__(self):
        return f"FolderStorage({str(self.root)!r})"

    def _path(self, name: str) -> Path:
        return self.root.joinpath(*check_name(name).split("/"))

    def put(self, name: str, data: Data, content_type: str = "application/octet-stream") -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".put-", dir=path.parent)    # "." names are never objects
        try:
            with os.fdopen(fd, "wb") as out:
                if isinstance(data, (bytes, bytearray)):
                    out.write(data)
                else:
                    shutil.copyfileobj(data, out, CHUNK)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def open(self, name: str) -> BinaryIO:
        path = self._path(name)
        if not path.is_file():
            raise NotFound(name)
        return open(path, "rb")

    def get(self, name: str) -> bytes:
        with self.open(name) as f:
            return f.read()

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.is_file():
            return False
        path.unlink()
        self._prune(path.parent)
        return True

    def list(self, prefix: str) -> list[str]:
        base = self.root.joinpath(*check_prefix(prefix)[:-1].split("/"))
        if not base.is_dir():
            return []
        names = []
        for p in base.rglob("*"):
            if p.is_file():
                name = p.relative_to(self.root).as_posix()
                try:
                    names.append(check_name(name))
                except StorageError:
                    pass                                       # a put's temp file
        return sorted(names)

    def delete_prefix(self, prefix: str) -> int:
        return sum(self.delete(n) for n in self.list(prefix))

    def _prune(self, d: Path) -> None:
        """Empty folders go with their last object, up to the root."""
        while d != self.root and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent


def storage_for(spec: str):
    """ "gs://<bucket>" -> GCSStorage; a folder path -> FolderStorage."""
    if not spec:
        raise StorageError("no storage given")
    if spec.startswith("gs://"):
        return GCSStorage(spec[len("gs://"):].rstrip("/"))
    if "://" in spec:
        raise StorageError(f"storage is gs://<bucket> or a folder, not {spec!r}")
    return FolderStorage(spec)
