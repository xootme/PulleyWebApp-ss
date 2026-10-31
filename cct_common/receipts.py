"""
receipts.py — receipts and credit notes (0.40.0; account plan step 2,
CCT_Marketing accounts/Account_Page_Plan.md §1). All of it is behind the
`receipts` business setting, off until the owner turns it on: while off
nothing is numbered, stored or shown.

- A receipt for each completed purchase, a credit note for each refund or
  chargeback, at /account/receipts/<number> on the shared account page. The
  account sees its own, an admin any. The page is laid out to print, and
  "Print or save as PDF" is the browser's own print.
- Numbers run without gaps, by year: "CCT-2026-000001" for receipts,
  "CCT-CN-2026-000001" for credit notes. A number is given once, under the
  database's write lock, and never reused (UNIQUE on what it's for and on
  the number). issue_pending() numbers every paid order that has no receipt
  yet, in the order they were paid, so orders paid before the setting was
  on are numbered once, the first time it's used.
- Each receipt keeps a copy of everything printed on it (seller, buyer's
  email, pack, amounts) as it was then, so a later change of the seller's
  name or of a price never changes an issued receipt. The company name and
  tax ID are the account's (receipt_billing) and show as they are now.
- Deleting an account blanks its email on its receipts ("deleted account");
  the rest stays, as financial records (accounts.delete_account).
"""
from __future__ import annotations

import html
import json
import time
from typing import Callable, Optional

from .sqlite_db import INTEGRITY_ERRORS, SqliteDB

SELLER = {"name": "Myerchin Enterprises Inc., operating as CheapCADTools",
          "address": "12311 SE 23rd St., Bellevue, WA 98005, United States",
          "email": "info@cheapcadtools.com"}
BILLING_MAX = 100
DELETED = "deleted account"
CHARGEBACK_MARKS = ("dispute-", "reversal-")      # Stripe's disputes, PayPal's reversals

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    number      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    year        INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    provider    TEXT NOT NULL,
    order_id    TEXT NOT NULL,
    refund_ref  TEXT NOT NULL DEFAULT '',
    account_id  TEXT NOT NULL,
    issued_at   REAL NOT NULL,
    data        TEXT NOT NULL,
    UNIQUE (kind, provider, order_id, refund_ref),
    UNIQUE (kind, year, seq)
);
CREATE INDEX IF NOT EXISTS receipts_account ON receipts (account_id, issued_at);
CREATE TABLE IF NOT EXISTS receipt_billing (
    account_id  TEXT PRIMARY KEY,
    company     TEXT,
    tax_id      TEXT,
    updated_at  REAL NOT NULL
);
"""


def receipts_on(path) -> bool:
    from .settings import setting
    return setting(path, "receipts") is True


def number(kind: str, year: int, seq: int) -> str:
    return f"CCT-{'CN-' if kind == 'credit' else ''}{year}-{seq:06d}"


def _year(ts: float) -> int:
    return time.gmtime(ts).tm_year


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _date(ts: float) -> str:
    t = time.gmtime(ts)
    return f"{time.strftime('%b', t)} {t.tm_mday}, {t.tm_year}"


def clean_billing(value) -> Optional[str]:
    v = " ".join(str(value or "").split())[:BILLING_MAX]
    return v or None


class Receipts(SqliteDB):
    """The receipts and receipt_billing tables, in the accounts database
    (beside the payments table, which every receipt is for)."""

    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        super().__init__(path, SCHEMA)
        self._clock = clock

    @staticmethod
    def _next(db, kind: str, year: int) -> int:
        row = db.execute("SELECT MAX(seq) FROM receipts WHERE kind = ? AND year = ?", (kind, year)).fetchone()
        return int(row[0] or 0) + 1

    def _insert(self, db, kind, provider, order_id, refund_ref, account_id, when, data) -> Optional[str]:
        year = _year(when)
        seq = self._next(db, kind, year)
        num = number(kind, year, seq)
        try:
            db.execute("INSERT INTO receipts (number, kind, year, seq, provider, order_id, refund_ref, account_id, "
                       "issued_at, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (num, kind, year, seq, provider, order_id, refund_ref, account_id, self._clock(),
                        json.dumps(data, sort_keys=True)))
        except INTEGRITY_ERRORS:
            return None
        return num

    def issue_pending(self) -> list[str]:
        """A receipt for every paid order that has none yet, in the order they
        were paid; returns the numbers given."""
        out = []
        with self._write() as db:
            rows = db.execute(
                "SELECT p.*, a.email FROM payments p LEFT JOIN accounts a ON a.id = p.account_id "
                "WHERE p.completed_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM receipts r WHERE "
                "r.kind = 'receipt' AND r.provider = p.provider AND r.order_id = p.order_id) "
                "ORDER BY p.completed_at, p.order_id").fetchall()
            for p in rows:
                email = p["email"] if p["email"] and not p["email"].startswith("deleted:") else DELETED
                data = {"seller": SELLER, "email": email, "provider": p["provider"], "order_id": p["order_id"],
                        "payment_ref": p["payment_ref"], "tokens": int(p["tokens"]), "cents": int(p["cents"]),
                        "currency": "USD", "bonus_tokens": int(p["bonus_tokens"] or 0),
                        "paid_at": p["completed_at"]}
                num = self._insert(db, "receipt", p["provider"], p["order_id"], "", p["account_id"],
                                   p["completed_at"], data)
                if num:
                    out.append(num)
        return out

    def credit_note(self, order: dict, refund_ref: str, *, cents: int, tokens: int, bonus: int = 0) -> Optional[str]:
        """The credit note for one refund or chargeback of this order (its
        receipt first, if it has none yet); None if it already has one."""
        self.issue_pending()
        receipt = self.receipt_of(order["provider"], order["order_id"])
        chargeback = any(m in refund_ref for m in CHARGEBACK_MARKS)
        now = self._clock()
        data = {"seller": SELLER, "receipt": receipt, "provider": order["provider"], "order_id": order["order_id"],
                "cents": int(cents), "currency": "USD", "tokens": int(tokens), "bonus_tokens": int(bonus),
                "reason": "chargeback" if chargeback else "refund", "at": now}
        with self._write() as db:
            return self._insert(db, "credit", order["provider"], order["order_id"], refund_ref,
                                order["account_id"], now, data)

    def receipt_of(self, provider: str, order_id: str) -> Optional[str]:
        with self._read() as db:
            row = db.execute("SELECT number FROM receipts WHERE kind = 'receipt' AND provider = ? AND order_id = ?",
                             (provider, order_id)).fetchone()
        return row["number"] if row else None

    def get(self, num: str) -> Optional[dict]:
        with self._read() as db:
            row = db.execute("SELECT * FROM receipts WHERE number = ?", (num,)).fetchone()
        if not row:
            return None
        return dict(dict(row), data=json.loads(row["data"]))

    def for_order(self, provider: str, order_id: str) -> list[dict]:
        """The order's receipt and credit notes, oldest first."""
        with self._read() as db:
            rows = db.execute("SELECT * FROM receipts WHERE provider = ? AND order_id = ? ORDER BY issued_at, number",
                              (provider, order_id)).fetchall()
        return [dict(dict(r), data=json.loads(r["data"])) for r in rows]

    def numbers_for(self, account_id: str) -> dict:
        """{"<provider>:<order id>": receipt number, "<refund ref>": credit note number}."""
        with self._read() as db:
            rows = db.execute("SELECT number, kind, provider, order_id, refund_ref FROM receipts WHERE account_id = ?",
                              (account_id,)).fetchall()
        return {(f"{r['provider']}:{r['order_id']}" if r["kind"] == "receipt" else r["refund_ref"]): r["number"]
                for r in rows}

    def billing(self, account_id: str) -> dict:
        with self._read() as db:
            row = db.execute("SELECT company, tax_id FROM receipt_billing WHERE account_id = ?", (account_id,)).fetchone()
        return {"company": row["company"], "tax_id": row["tax_id"]} if row else {"company": None, "tax_id": None}

    def set_billing(self, account_id: str, *, company, tax_id) -> dict:
        company, tax_id = clean_billing(company), clean_billing(tax_id)
        with self._write() as db:
            db.execute("INSERT INTO receipt_billing (account_id, company, tax_id, updated_at) VALUES (?, ?, ?, ?) "
                       "ON CONFLICT (account_id) DO UPDATE SET company = excluded.company, tax_id = excluded.tax_id, "
                       "updated_at = excluded.updated_at", (account_id, company, tax_id, self._clock()))
        return {"company": company, "tax_id": tax_id}


def forget_email(db, account_id: str) -> None:
    """Deleting an account: its receipts keep everything but the email.
    Runs inside delete_account's write (AccountStore makes the tables)."""
    rows = db.execute("SELECT number, data FROM receipts WHERE account_id = ?", (account_id,)).fetchall()
    for r in rows:
        data = json.loads(r["data"])
        if "email" in data:
            data["email"] = DELETED
            db.execute("UPDATE receipts SET data = ? WHERE number = ?", (json.dumps(data, sort_keys=True), r["number"]))
    db.execute("DELETE FROM receipt_billing WHERE account_id = ?", (account_id,))


# ── the page ──────────────────────────────────────────────────────────────

_CSS = (
    "body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.5;"
    "color:#1f1f1f;background:#fff}h1{font-size:1.5rem;margin:0 0 1rem}.sub{color:#666;font-size:.9rem}"
    "table{width:100%;border-collapse:collapse;margin:1rem 0}td,th{text-align:left;padding:.4rem 0;"
    "border-bottom:1px solid #e3e3e3}td.amt,th.amt{text-align:right}.total td{font-weight:600;border:0}"
    ".seller{margin-bottom:1.2rem}.meta p{margin:.15rem 0}"
    "button{font:inherit;padding:.45rem 1.1rem;border-radius:6px;cursor:pointer;border:1px solid #0078d4;"
    "background:#0078d4;color:#fff}input{font:inherit;padding:.35rem .5rem;border:1px solid #c8c8c8;"
    "border-radius:6px;width:100%;max-width:20rem;box-sizing:border-box}.tools{margin-top:2rem;padding-top:1rem;"
    "border-top:1px solid #e3e3e3}.msg{min-height:1.2em;color:#555;font-size:.9rem}"
    "@media print{.tools{display:none}body{margin:0}}"
)

_SCRIPT = """<script>
document.getElementById('print').addEventListener('click', () => window.print());
const form = document.getElementById('billing');
if (form) form.addEventListener('submit', async e => {
  e.preventDefault();
  const msg = document.getElementById('billing-msg');
  try {
    const r = await fetch('/account/receipts/billing', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company: form.elements.company.value, tax_id: form.elements.tax_id.value }) });
    if (!r.ok) throw new Error();
    location.reload();
  } catch (err) { msg.textContent = "Couldn't save that. Please try again."; }
});
</script>"""


def _seller(s: dict) -> str:
    return (f"<div class='seller'><strong>{html.escape(s['name'])}</strong><br>{html.escape(s['address'])}<br>"
            f"{html.escape(s['email'])}</div>")


def render(rec: dict, billing: dict, credits: list, *, own: bool) -> str:
    """The receipt's or credit note's page body."""
    d, num = rec["data"], rec["number"]
    billed = "".join(f"<p>{label}: {html.escape(v)}</p>" for label, v in
                     (("Company", billing.get("company")), ("Tax ID", billing.get("tax_id"))) if v)
    if rec["kind"] == "receipt":
        method = ("Paid with PayPal" if d["provider"] == "paypal" else "Paid by card through Stripe")
        ref = d.get("payment_ref") or d["order_id"]
        rows = f"<tr><td>{d['tokens']:,} CheapCADTools tokens</td><td class='amt'>{_dollars(d['cents'])}</td></tr>"
        if d.get("bonus_tokens"):
            rows += f"<tr><td>{d['bonus_tokens']:,} bonus tokens (free)</td><td class='amt'>$0.00</td></tr>"
        refunds = "".join(f"<p>Credit note <a href='{html.escape(c['number'])}'>{html.escape(c['number'])}</a>: "
                          f"{_dollars(c['data']['cents'])} {'reversed' if c['data']['reason'] == 'chargeback' else 'refunded'}"
                          f" on {_date(c['data']['at'])}</p>" for c in credits if c["kind"] == "credit")
        body = (f"<h1>Receipt</h1>{_seller(d['seller'])}<div class='meta'>"
                f"<p>Receipt number: {html.escape(num)}</p><p>Date: {_date(d['paid_at'])}</p>"
                f"<p>Sold to: {html.escape(d.get('email') or DELETED)}</p>{billed}</div>"
                f"<table><tr><th>Item</th><th class='amt'>Amount</th></tr>{rows}"
                f"<tr class='total'><td>Total paid</td><td class='amt'>{_dollars(d['cents'])} USD</td></tr></table>"
                f"<p>{method} ({html.escape(ref)})</p>{refunds}")
    else:
        why = ("Payment reversed by your bank or card issuer" if d["reason"] == "chargeback" else "Refund")
        body = (f"<h1>Credit note</h1>{_seller(d['seller'])}<div class='meta'>"
                f"<p>Credit note number: {html.escape(num)}</p>"
                + (f"<p>For receipt <a href='{html.escape(d['receipt'])}'>{html.escape(d['receipt'])}</a></p>"
                   if d.get("receipt") else "")
                + f"<p>Date: {_date(d['at'])}</p>{billed}</div>"
                f"<table><tr><th>{why}</th><th class='amt'>Amount</th></tr>"
                f"<tr><td>{d['tokens']:,} tokens taken back</td><td class='amt'>{_dollars(d['cents'])}</td></tr>"
                + (f"<tr><td>{d['bonus_tokens']:,} bonus tokens taken back</td><td class='amt'>$0.00</td></tr>"
                   if d.get("bonus_tokens") else "")
                + f"<tr class='total'><td>Total refunded</td><td class='amt'>{_dollars(d['cents'])} USD</td></tr>"
                "</table>")
    tools = "<div class='tools'><p><button type='button' id='print'>Print or save as PDF</button></p>"
    if own and rec["kind"] == "receipt":
        tools += ("<form id='billing'><p><strong>Add a company name or tax ID</strong><br>"
                  "<span class='sub'>Shown on all your receipts.</span></p>"
                  f"<p><label>Company name<br><input name='company' maxlength='{BILLING_MAX}' "
                  f"value='{html.escape(billing.get('company') or '', quote=True)}'></label></p>"
                  f"<p><label>Tax ID (VAT)<br><input name='tax_id' maxlength='{BILLING_MAX}' "
                  f"value='{html.escape(billing.get('tax_id') or '', quote=True)}'></label></p>"
                  "<button type='submit'>Save</button><p class='msg' id='billing-msg' role='status'></p></form>")
    tools += "<p><a href='/account/'>Your account</a></p></div>"
    return body + tools


def register_receipts(app, store_payments) -> Receipts:
    """GET /account/receipts/<number> and POST /account/receipts/billing,
    for an app with payment routes. Each answers 404 while `receipts` is off."""
    from flask import jsonify, redirect, request

    from .account_page import _doc
    from .account_routes import current_session
    from .admin import admin_emails

    tokens = store_payments.tokens
    store = Receipts(tokens.path, clock=tokens._clock)

    def page(num):
        if not receipts_on(tokens.path):
            return _doc("Not found", "<p>That page isn't here.</p>", 404)
        s = current_session()
        if not s:
            return redirect(f"/account/sign-in?next=/account/receipts/{num}", code=302)
        store.issue_pending()
        rec = store.get(num)
        email = (tokens.account_email(s["account_id"]) or "").lower()
        own = bool(rec) and rec["account_id"] == s["account_id"]
        admin = s.get("kind") == "web" and email in admin_emails()
        if not rec or not (own or admin):
            return _doc("Not found", "<p>That page isn't here.</p>", 404)
        credits = store.for_order(rec["provider"], rec["order_id"])
        title = ("Receipt " if rec["kind"] == "receipt" else "Credit note ") + rec["number"]
        return _doc(title, render(rec, store.billing(rec["account_id"]), credits, own=own) + _SCRIPT,
                    extra=f"<style>{_CSS}</style>")

    def billing():
        if not receipts_on(tokens.path):
            return jsonify({"error": "not found"}), 404
        s = current_session()
        if not s:
            return jsonify({"error": "sign in required", "code": "SIGN_IN_REQUIRED"}), 401
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "JSON body required"}), 400
        return jsonify(store.set_billing(s["account_id"], company=data.get("company"), tax_id=data.get("tax_id")))

    app.add_url_rule("/account/receipts/<num>", endpoint="cct_receipt", view_func=page, methods=["GET"])
    app.add_url_rule("/account/receipts/billing", endpoint="cct_receipt_billing", view_func=billing,
                     methods=["POST"])
    return store
