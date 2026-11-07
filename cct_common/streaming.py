"""
streaming.py — large responses go out in pieces, with no Content-Length
(0.59.0; Worm's app._stream_large, 1.0.8, moved here for every app).

Cloud Run answers 500 in place of any response over 32 MiB sent with a
Content-Length ("Response size was too large"). Worm lost a user's 47.7 MiB
zip three times on 2026-11-06, and a big wheel's STEP alone can be 86.7 MiB;
the owner chose streaming (2026-11-07). The cap doesn't apply to a chunked
response, so one call at the end of app.py:

    from cct_common.streaming import stream_large_responses
    stream_large_responses(app)            # over=STREAM_OVER (16 MiB), piece=PIECE (1 MiB)

Every 200 response whose Content-Length is over `over` then goes out as the
same bytes in pieces of at most `piece`, with no Content-Length: result
links (results.py), the direct /download/* routes, previews, the in-request
zip. Smaller responses, other statuses, HEAD and bodies already streamed
(no Content-Length) are left exactly as they were.

A charged result keeps its delivery rule. The body is iterated through and
closed as it is, so a charging.serve_delivery / on_delivery wrapper sees
the end of the body only when the server asks for more after the last
piece (the claim fires then), and close() before that (the client went
away) is still an abort: the charge stays pending, or is refunded.

The hook runs after every other after_request hook, the app's own included,
so none of them reads a body that is already being streamed.
tests/test_streaming.py.
"""
from __future__ import annotations

STREAM_OVER = 16 * 2**20      # half Cloud Run's 32 MiB cap
PIECE = 2**20


class Chunked:
    """A response body handed out in pieces of at most `piece` bytes;
    close() closes the body it reads (a file, or the delivery wrapper)."""

    def __init__(self, body, piece: int = PIECE):
        self._body = body
        self._piece = piece

    def __iter__(self):
        n = self._piece
        for block in self._body:
            for i in range(0, len(block), n):
                yield block[i:i + n]

    def close(self) -> None:
        close = getattr(self._body, "close", None)
        if close:
            close()


def stream_large_responses(app, *, over: int = STREAM_OVER, piece: int = PIECE):
    """Send every 200 response over `over` bytes in pieces of `piece`, with
    no Content-Length. Returns the hook; its .over and .piece are read on
    each response, so a test can lower them."""
    from flask import request

    def stream_large(resp):
        n = resp.content_length
        if n is None or n <= stream_large.over or resp.status_code != 200 or request.method == "HEAD":
            return resp
        resp.response = Chunked(resp.response, stream_large.piece)
        del resp.headers["Content-Length"]
        return resp

    stream_large.over, stream_large.piece = over, piece
    # Flask runs a scope's after_request hooks last-registered first: at the
    # front of the list, this one runs after all the others.
    app.after_request_funcs.setdefault(None, []).insert(0, stream_large)
    return stream_large
