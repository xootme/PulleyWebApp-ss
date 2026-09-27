"""Seed a fresh database for tests/browser/admin_ui.js and print the session
cookies it needs, as JSON. Run with the app's interpreter from the repo root:

    python tests/browser/admin_seed.py <tmp>/cct.sqlite3

Accounts (signup grant 20 each):
  admin@example.com   the admin (ADMIN_EMAILS=admin@example.com)
  plain@example.com   signed in, not an admin
  alice@example.com   3 downloads
  bob@example.com     6 downloads (only the last 5 show), a $5.00 / 50-token PayPal pack
  carol@example.com   granted 100 tokens (the highest lifetime total)
plus two bug reports.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())

from bug_store import BugReports
from cct_common.accounts import AccountStore
from cct_common.payments import Pack, Payments
from cct_common.tokens import TokenStore

path = sys.argv[1]
t = [time.time() - 2 * 86400]     # two days ago; each event an hour later (sessions stay live)


def clock():
    t[0] += 3600
    return t[0]


tokens = TokenStore(path, app="pulleys", clock=clock)
accounts = AccountStore(tokens, signup_grant=20)


def acct(email):
    return accounts.sign_in("email", email, email, email_verified=True)


admin, plain, alice, bob, carol = (acct(f"{n}@example.com")
                                   for n in ("admin", "plain", "alice", "bob", "carol"))
for i, fmt in enumerate(("svg", "stl", "step")):
    with tokens.charge(alice, f"alice-{i}", fmt):
        pass
for i in range(6):
    with tokens.charge(bob, f"bob-{i}", "svg"):
        pass
pay = Payments(tokens, clock=clock)
pay.record("paypal", "ORDER-BOB", bob, Pack("paypal", 500, 50))
pay.complete("paypal", "ORDER-BOB", paid_cents=500, currency="USD", payment_ref="CAPTURE-BOB")
tokens.credit(carol, 100, kind="promo", detail="seed")

bugs = BugReports(path, clock=clock)
for rid, seeing in (("rep-1", "Belt teeth look wrong"), ("rep-2", "Flange too thin")):
    bugs.add(rid, report_type="bug", seeing=seeing, should_see="", error_msg="", user_comment="",
             email="alice@example.com", state={"teeth": 20}, app_version="2.0.0")

print(json.dumps({"admin": accounts.create_session(admin, kind="web"),
                  "plain": accounts.create_session(plain, kind="web")}))
