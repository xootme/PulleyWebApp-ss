"""The checks every app's label table needs (cct_common 0.31.0; Planetary
Gear Designer's tests/test_labels.py, made shared):

    from cct_common.testing.labels import check_labels, check_auto_fixes

    def test_labels(page):
        assert check_labels(LB, page, keys_the_server_reads) == []

    def test_every_refusal_has_an_auto_fix_that_clears_it(client, page):
        def ask(params):
            r = client.get("/api/set", query_string=params)
            return r.status_code, r.get_json()
        assert check_auto_fixes(ask, page_ids(page)) == []

Each returns a list of problems, one line each, so a failure names them all.
"""
from __future__ import annotations

import html
import re
import unicodedata

INPUT_ID = re.compile(r'<(?:input|select)[^>]*\bid="([A-Za-z0-9_]+)"')


def page_ids(page: str) -> list:
    """The ids of the page's inputs and selects."""
    return sorted(set(INPUT_ID.findall(page)))


def page_label(page: str, key: str):
    """The page's label for `key` (<label for="key">), its unit and notes in
    brackets left out; None when it has none."""
    m = re.search(r'<label[^>]*\bfor="%s"[^>]*>(.*?)</label>' % re.escape(key), page, re.S)
    if not m:
        return None
    inner = re.split(r"<(?:input|select|button)\b", m.group(1))[0]
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner)))
    return re.sub(r"\([^)]*\)|≈", "", text).strip() or None


def _norm(text: str) -> str:
    """Compared as read: subscripts as letters (zₛ, zs), spaces as one."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def check_labels(labels, page: str, read_keys, min_checked: int = 1) -> list:
    """Every setting the server reads (`read_keys`) has a label in `labels`
    (a cct_common.labels.Labels), and each label is the page's: the same
    words, its unit apart; a card's setting ("Carrier Hub OD") ends with the
    card's own ("Hub OD"). Fewer than `min_checked` labels found on the page
    is a problem too: the page's markup changed under the check."""
    problems = [f"{k}: no label (a message would name it {k!r})" for k in labels.missing(read_keys)]
    checked = 0
    for key in page_ids(page):
        shown = page_label(page, key)
        found = labels.find(key)
        if shown is None or found is None:
            continue
        ours, theirs = _norm(found[0]), _norm(shown)
        exact = key in labels.labels
        if (ours != theirs) if exact else not ours.endswith(theirs):
            problems.append(f"{key}: labelled {found[0]!r}, the page says {shown!r}")
        checked += 1
    if checked < min_checked:
        problems.append(f"only {checked} of the page's labels checked (want {min_checked} or more)")
    return problems


def check_auto_fixes(ask, keys, bad=("abc", "99999"), min_refused: int = 1) -> list:
    """For each setting in `keys`, sent each `bad` value: a refusal that names
    it (`ask(params)` -> (status, body), body {"error", "field", "fix"}) has a
    fix, and sending the fix ({key: fix}, or the fix itself when it's a dict
    of settings) clears it: it may show the next thing to fix, on another
    setting, never a refusal of the same one. (Planetary's check let a fix
    through that drew another refusal of the same setting: a range's text,
    "0.5 … 100", refused as not a number, the NaN bug itself.) Fewer than
    `min_refused` refusals is a problem too."""
    problems, refused = [], 0
    for key in keys:
        for value in bad:
            status, body = ask({key: value})
            if status < 400 or not isinstance(body, dict) or body.get("field") != key:
                continue
            refused += 1
            fix = body.get("fix")
            if fix is None:
                problems.append(f"{key}={value!r}: refused ({body.get('error')!r}) with no Auto-fix")
                continue
            status2, body2 = ask(fix if isinstance(fix, dict) else {key: fix})
            if status2 >= 400 and isinstance(body2, dict) and body2.get("field") == key:
                problems.append(f"{key}={value!r}: its Auto-fix {fix!r} is refused too: {body2.get('error')!r}")
    if refused < min_refused:
        problems.append(f"only {refused} refusals seen (want {min_refused} or more)")
    return problems
