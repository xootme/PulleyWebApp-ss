"""
data_export.py — "Download all my data" (0.45.0; account plan step 6).
Behind the data_download business setting, off until the owner turns it on.

The account page's button POSTs to /account/data (its own form: the session
cookie isn't sent on another site's POST) and the zip comes straight back,
made in the request: an account holds at most 500 designs with pictures of
40 KB at most, so even the largest is about 20 MB. Nothing is kept on the
server afterwards. A browser session only, never an add-in's or AI app's
token. LIMIT_PER_HOUR per account, counted in the database (data_exports).

In the zip: everything the account keeps, whichever features are on (see
README_TEXT). Out of it: CAD files (they cost tokens; the settings rebuild
them), anything an admin wrote (a grant's reason, the GitHub link),
internal values (the payer key, session and link hashes), and anyone
else's data. Projects (step 7) and referrals get their files when they're
built.
"""
from __future__ import annotations

import csv
import io
import json
import time
import zipfile
from typing import Optional

LIMIT_PER_HOUR = 5
LIMITED = "You've downloaded your data 5 times in the last hour. Please try again later."

SCHEMA = """
CREATE TABLE IF NOT EXISTS data_exports (
    account_id  TEXT NOT NULL,
    at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS data_exports_account ON data_exports (account_id, at);
"""

README_TEXT = """Your CheapCADTools data, made on {date} for {email}.

account.json            your account: email, sign-in methods, balance, when unused tokens expire
designs/                each saved design: <id>.json (tool, name, settings, bookmark, note) and its picture
share_links.json        the links you made to share a design, and how often each was opened
referrals.json          the accounts made from your links: when, and the tokens each earned you
printer_settings.json   your units and 3D Printer Compensation
token_history.csv/json  every change to your tokens, in plain words
orders.json             each purchase of tokens
receipts/               each receipt and credit note as issued; billing.json, your company name and tax ID
reports.json            the bug and feature reports you sent, their status, and the design you sent with each
newsletter.json         every newsletter choice you made, with the words you were shown
sessions.json           the browsers, add-ins and AI apps signed in to your account
projects/               your projects: each one's name, note and designs (by their designs/ id)

CAD files aren't included: open a design in its tool to download those.
"""


def _iso(ts) -> Optional[str]:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


def _rows(db, sql: str, args: tuple) -> list:
    return [dict(r) for r in db.execute(sql, args).fetchall()]


class DataExport:
    """One account's export; build() returns the zip's bytes."""

    def __init__(self, accounts, *, host_url: str = "https://cheapcadtools.com/"):
        self.accounts, self.tokens = accounts, accounts.tokens
        self.host_url = host_url

    # ── the rate limit ────────────────────────────────────────────────────

    def allowed(self, account_id: str) -> bool:
        """Count this one if it's within LIMIT_PER_HOUR; False if it isn't."""
        now = self.accounts._clock()
        with self.accounts._write() as db:
            db.execute("DELETE FROM data_exports WHERE at < ?", (now - 3600,))
            n = db.execute("SELECT COUNT(*) FROM data_exports WHERE account_id = ? AND at >= ?",
                           (account_id, now - 3600)).fetchone()[0]
            if n >= LIMIT_PER_HOUR:
                return False
            db.execute("INSERT INTO data_exports (account_id, at) VALUES (?, ?)", (account_id, now))
        return True

    # ── what goes in ──────────────────────────────────────────────────────

    def _table(self, sql: str, args: tuple) -> list:
        """Rows of a table that may not exist in this database (payments)."""
        try:
            with self.tokens._read() as db:
                return _rows(db, sql, args)
        except Exception:
            return []

    def build(self, account_id: str) -> bytes:
        from .account_page import history_line
        from .printer_settings import PrinterSettings
        from .receipts import Receipts
        from .saved_designs import SavedDesigns, parse_thumb

        acct, tokens, accounts = account_id, self.tokens, self.accounts
        email = tokens.account_email(acct) or ""
        out = io.BytesIO()
        z = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED)
        put = lambda name, data: z.writestr(name, json.dumps(data, indent=2, ensure_ascii=False))  # noqa: E731
        now = accounts._clock()
        t = time.gmtime(now)
        z.writestr("README.txt", README_TEXT.format(date=f"{time.strftime('%b', t)} {t.tm_mday}, {t.tm_year}",
                                                    email=email))

        from .accounts import DEAD_ACCOUNT_IDLE_S
        last = accounts.last_sign_in(acct)
        balance, free = tokens.balance(acct), tokens.free_remaining(acct)
        put("account.json", {
            "email": email, "created": _iso(tokens.account_created_at(acct)), "last_sign_in": _iso(last),
            "unused_tokens_expire": _iso(last + DEAD_ACCOUNT_IDLE_S) if last else None,
            "balance": balance, "bought": balance - free, "free": free,
            "sign_in_methods": [{"provider": i["provider"], "email": i["email"], "added": _iso(i["created_at"]),
                                 "last_used": _iso(i["last_used_at"])} for i in accounts.identities(acct)]})

        designs = SavedDesigns(tokens.path)
        with tokens._read() as db:
            saved = _rows(db, "SELECT * FROM saved_designs WHERE account_id = ? ORDER BY created_at", (acct,))
            shares = _rows(db, "SELECT code, saved_id, app, created_at, revoked_at, opens, note FROM shared_designs "
                               "WHERE account_id = ? ORDER BY created_at", (acct,))
            consents = _rows(db, "SELECT at, subscribed, app, source, label FROM newsletter_consents "
                                 "WHERE account_id = ? ORDER BY id", (acct,))
            reports = _rows(db, "SELECT created_at, app, report_type, seeing, should_see, error_msg, user_comment, "
                                "app_version, status, status_note, fixed_in, state FROM bug_reports "
                                "WHERE account_id = ? ORDER BY created_at", (acct,))
        for d in saved:
            put(f"designs/{d['id']}.json", {
                "tool": d["app"], "name": d["name"], "settings": json.loads(d["params"]),
                "created": _iso(d["created_at"]), "updated": _iso(d["updated_at"]), "came_from": d["source"],
                "last_downloaded": _iso(d["last_download_at"]), "bookmarked": bool(d.get("bookmarked")),
                "note": d.get("note"), "note_edited": _iso(d.get("note_edited_at"))})
            pic = parse_thumb(d["thumb"])
            if pic:
                ext = {"image/webp": "webp", "image/jpeg": "jpg", "image/png": "png"}[pic[0]]
                z.writestr(f"designs/{d['id']}.{ext}", pic[1])
        apps = designs.apps()
        put("share_links.json", [{
            "link": f"{self.host_url.rstrip('/')}{(apps.get(s['app']) or {}).get('page') or '/tools/' + s['app'] + '/'}"
                    f"?shared={s['code']}",
            "design": s["saved_id"], "tool": s["app"], "made": _iso(s["created_at"]), "opens": s["opens"],
            "live": s["revoked_at"] is None, "stopped": _iso(s["revoked_at"]), "note_included": bool(s["note"])}
            for s in shares])

        with tokens._read() as db:            # referrals (0.49.0): never who the other account is
            mine = _rows(db, "SELECT app, created_at, outcome, decided_at FROM referrals WHERE sharer_id = ? "
                             "ORDER BY created_at", (acct,))
            came = _rows(db, "SELECT app, created_at FROM referrals WHERE referred_id = ?", (acct,))
        from .referrals import REWARD, referrals_on
        paid = ("rewarded", "over_cap")       # the sharer sees only what their link earned, nothing about the other person
        if mine or came or referrals_on(tokens.path):
            put("referrals.json", {
                "accounts_from_your_links": [{
                    "tool": r["app"], "made": _iso(r["created_at"]),
                    "first_purchase": _iso(r["decided_at"]) if r["outcome"] in paid else None,
                    "tokens": REWARD if r["outcome"] == "rewarded" else 0} for r in mine],
                "this_account_came_from_a_share_link":
                    {"tool": came[0]["app"], "made": _iso(came[0]["created_at"])} if came else None})

        put("printer_settings.json", PrinterSettings(tokens.path).get(acct))

        history = list(reversed(tokens.history(acct, limit=1_000_000)))
        from .account_page import _orders
        orders = _orders(tokens, history)
        lines = [{"date": _iso(r["ts"]), "what": history_line(r, orders), "tokens": int(r["amount"]),
                  "tool": r.get("app"), "format": r.get("fmt")} for r in history]
        put("token_history.json", lines)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["date", "what", "tokens", "tool", "format"])
        for x in lines:
            w.writerow([x["date"], x["what"], x["tokens"], x["tool"] or "", x["format"] or ""])
        z.writestr("token_history.csv", buf.getvalue())

        put("orders.json", [{
            "provider": o["provider"], "order": o["order_id"], "pack": o["pack_id"], "tokens": o["tokens"],
            "price_cents": o["cents"], "currency": "USD", "status": o["status"],
            "refunded_cents": o["refunded_cents"], "bonus_tokens": o.get("bonus_tokens") or 0,
            "ordered": _iso(o["created_at"]), "paid": _iso(o["completed_at"])}
            for o in self._table("SELECT * FROM payments WHERE account_id = ? ORDER BY created_at", (acct,))])

        rec = Receipts(tokens.path)
        for r in self._table("SELECT number, kind, issued_at, data FROM receipts WHERE account_id = ? "
                             "ORDER BY issued_at", (acct,)):
            put(f"receipts/{r['number']}.json", {"number": r["number"], "kind": r["kind"],
                                                 "issued": _iso(r["issued_at"]), **json.loads(r["data"])})
        put("receipts/billing.json", rec.billing(acct))

        from .account_page import tool_title
        from .reports import status_line
        put("reports.json", [{
            "date": _iso(r["created_at"]), "tool": tool_title(r["app"]), "type": r["report_type"],
            "you_saw": r["seeing"], "you_expected": r["should_see"], "error": r["error_msg"] or None,
            "comment": r["user_comment"] or None, "version": r["app_version"],
            "status": status_line(r, tool_title(r["app"])), "design_you_sent": json.loads(r["state"] or "null")}
            for r in reports])

        put("newsletter.json", [{"date": _iso(c["at"]), "subscribed": bool(c["subscribed"]), "tool": c["app"],
                                 "where": c["source"], "words_shown": c["label"]} for c in consents])

        put("sessions.json", [{"kind": "browser" if s["kind"] == "web" else "add-in or AI app",
                               "name": s["label"], "added": _iso(s["created_at"]), "last_used": _iso(s["last_used_at"]),
                               "daily_limit": s["daily_budget"] if s["kind"] != "web" else None}
                              for s in accounts.list_sessions(acct)])
        from .projects import Projects
        groups = Projects(tokens.path)
        for p in groups.list(acct):                     # 0.46.0
            full = groups.get(acct, p["id"])
            put(f"projects/{p['id']}.json", {"name": full["name"], "note": full["note"],
                                             "created": _iso(full["created_at"]), "updated": _iso(full["updated_at"]),
                                             "designs": [d["id"] for d in full["designs"]],
                                             "shared": bool(full["shared"])})
        z.close()
        return out.getvalue()
