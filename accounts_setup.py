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
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

ACCOUNT_PATH_PREFIXES = ("/api/account", "/account/")


@dataclass
class AccountsState:
    enabled: bool
    healthy: bool = False
    tokens: object = None      # cct_common.tokens.TokenStore
    accounts: object = None    # cct_common.accounts.AccountStore
    problems: tuple = ()
    backup_stop: object = None  # threading.Event; set() stops the backup thread
    housekeeping_stop: object = None  # threading.Event for the daily inactivity run


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


def make_backup_alert(email_sender: Callable, *, alert_to: str, logger):
    """A backup problem always goes to the error log; it's also emailed
    when an alert address is configured (BACKUP_ALERT_EMAIL)."""
    def alert(message: str):
        logger.error(message)
        if alert_to:
            email_sender(alert_to, "CheapCAD Tools: database backup problem", message)
    return alert


def make_inactivity_notify(email_sender: Callable, *, app_name: str, site_url: str):
    """The reminder emails for cct_common.accounts housekeeping: free tokens
    about to expire, or an account with nothing bought about to be closed.
    Returns True only when the email was sent — nothing expires otherwise."""
    def notify(email: str, kind: str, deadline: float, tokens: int) -> bool:
        when = datetime.fromtimestamp(deadline).strftime("%d %B %Y")
        sign_in = f"Signing in to {app_name} before then ({site_url}) keeps them."
        if kind == "free_tokens":
            subject = f"Your {tokens} free {app_name} tokens expire on {when}"
            body = (f"Hi,\n\nYour account hasn't been signed in to for almost 2 years, so its "
                    f"{tokens} free token{'s' if tokens != 1 else ''} will expire on {when}. "
                    f"{sign_in}\n\nTokens you bought never expire.\n")
        else:
            subject = f"Your {app_name} account will be closed on {when}"
            body = (f"Hi,\n\nYour account hasn't been signed in to for almost 5 years and holds "
                    f"no purchased tokens, so it will be closed on {when} and your email "
                    f"address removed. Signing in before then ({site_url}) keeps it open.\n")
        ok, _ = email_sender(email, subject, body)
        return bool(ok)
    return notify


def start_housekeeping(accounts, notify, logger, *, interval_s: float = 24 * 3600,
                       first_delay_s: float = 120) -> threading.Event:
    """Run accounts.housekeeping(notify) daily in a daemon thread. A failure
    is logged and never reaches the app. Returns an Event; set() stops it."""
    stop = threading.Event()

    def loop():
        if stop.wait(first_delay_s):
            return
        while True:
            try:
                done = accounts.housekeeping(notify)
                if any(done.values()):
                    logger.info("Account housekeeping: %s", done)
            except Exception as e:
                logger.error("Account housekeeping failed: %s", e)
            if stop.wait(interval_s):
                return

    threading.Thread(target=loop, name="cct-account-housekeeping", daemon=True).start()
    return stop


def init_accounts(app, *, log_dir: str, enabled: bool, live: bool,
                  email_sender: Callable, signup_grant: int = 10,
                  app_name: str = "CheapCAD Tools",
                  backup_dir: str | None = None,
                  backup_alert: Callable[[str], None] | None = None,
                  backup_upload: Callable[[str], None] | None = None,
                  backup_interval_s: float = 3600,
                  backup_first_delay_s: float = 30,
                  inactivity_notify: Callable | None = None) -> AccountsState:
    """backup_dir=None means no scheduled backups (tests). backup_upload
    is the off-server copy — the Azure Blob Storage upload once hosting
    moves (ADR-008). inactivity_notify starts the daily inactivity run
    (free tokens after 2 idle years, empty dead accounts after 5); None
    (tests) leaves it off."""
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
        message = (f"Accounts database failed its integrity check ({db_path}): "
                   + "; ".join(problems) + ". Account routes answer 503 until it is restored.")
        (backup_alert or app.logger.error)(message)

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

    if backup_dir:
        from cct_common.db_backup import start_backup_thread
        state.backup_stop = start_backup_thread(
            tokens, backup_dir, name="accounts", interval_s=backup_interval_s,
            first_delay_s=backup_first_delay_s,
            on_failure=backup_alert or app.logger.error, upload=backup_upload)
    if inactivity_notify is not None:
        state.housekeeping_stop = start_housekeeping(accounts, inactivity_notify, app.logger)
    return state
