"""
accounts_setup.py — wires the token model's accounts into a Flask app
(ADR-008): the SQLite ledger + accounts store, email-link sign-in routes,
and the startup integrity check.

Kept out of app.py so tests can run it against a fresh Flask app and a
temporary database. app.py calls init_accounts() once at import time.

Off unless TOKENS_ENABLED=1: until accounts, charging and the UI are all
done, the live site must behave exactly as before — and with the flag off
the sign-in routes don't exist, so nobody can use them to send email.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Callable

ACCOUNT_PATH_PREFIXES = ("/api/account", "/account/")


@dataclass
class AccountsState:
    enabled: bool
    healthy: bool = False
    tokens: object = None      # cct_common.tokens.TokenStore
    accounts: object = None    # cct_common.accounts.AccountStore
    problems: tuple = ()


def make_email_sender(send: Callable, *, live: bool, has_key: bool, logger):
    """Sign-in emails go through `send` (Resend) when a key is configured.
    In local dev without one, the email (link included) is logged as a
    warning instead — in this app that lands in logs/server_errors.log —
    so sign-in still works on a developer machine. Live without a key
    fails loudly rather than pretending to send, and never logs a link."""
    def sender(to: str, subject: str, body: str):
        if has_key:
            return send(to, subject, body)
        if not live:
            logger.warning("No RESEND_API_KEY — sign-in email for %s not sent. Body:\n%s", to, body)
            return True, ""
        return False, "RESEND_API_KEY is not configured"
    return sender


def init_accounts(app, *, log_dir: str, enabled: bool, live: bool,
                  email_sender: Callable, signup_grant: int = 10,
                  app_name: str = "CheapCAD Tools") -> AccountsState:
    state = AccountsState(enabled=enabled)
    app.extensions["pulley_accounts"] = state
    if not enabled:
        return state

    from flask import jsonify, request
    from cct_common.account_routes import register_account_routes
    from cct_common.accounts import AccountStore
    from cct_common.tokens import TokenStore

    db_path = os.path.join(log_dir, "accounts.sqlite3")
    try:
        tokens = TokenStore(db_path)
        problems = tuple(tokens.integrity_check())
    except sqlite3.DatabaseError as e:  # unreadable file: schema setup itself fails
        tokens, problems = None, (str(e),)

    if problems:
        state.problems = problems
        app.logger.error("Accounts database failed its integrity check (%s): %s",
                         db_path, "; ".join(problems))

        @app.before_request
        def _accounts_unavailable():
            # Refuse rather than act on a damaged ledger; restore from backup.
            if request.path.startswith(ACCOUNT_PATH_PREFIXES):
                return jsonify({"error": "Accounts are temporarily unavailable.",
                                "code": "ACCOUNTS_UNAVAILABLE"}), 503
        return state

    accounts = AccountStore(tokens, signup_grant=signup_grant)
    register_account_routes(app, accounts, email_sender=email_sender,
                            app_name=app_name, secure_cookies=live)
    state.tokens, state.accounts, state.healthy = tokens, accounts, True
    return state
