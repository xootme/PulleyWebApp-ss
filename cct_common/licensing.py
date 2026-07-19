"""
licensing.py — desktop-app licence key activation, WooCommerce/LMFWC
licence import, and Autodesk App Store entitlement/IPN handling. Ported
from PulleyWebApp-ss/app.py's licensing routes, parameterized.

    from cct_common.licensing import register_licensing_routes

    register_licensing_routes(
        app, admin_secret=os.environ["PROVISION_SECRET"], log_dir=LOG_DIR,
        app_download_url=os.environ.get("APP_URL", ""),
        app_version=..., app_changelog=..., runtime_url=..., runtime_version=...,
        licence_b64=os.environ.get("LICENCE_B64", ""),
        licence_expiry=os.environ.get("LICENCE_EXPIRY", ""),
        licence_email_subject_prefix="Your CheapCAD Tools licence key",
        licence_email_body=my_licence_email_template,     # callable(key, order_id, valid_until) -> str
        purchase_welcome_email_body=my_welcome_template,   # callable(txn_id) -> str
        wc_webhook_secret=os.environ.get("WC_WEBHOOK_SECRET", ""),
        lmfwc_site_url=..., lmfwc_consumer_key=..., lmfwc_consumer_secret=...,
        autodesk_app_id=os.environ.get("AUTODESK_APP_ID", ""),
    )

Routes added (mirroring PulleyWebApp-ss's originals byte-for-byte in
path/method/response shape):
    POST /api/desktop/licence-import       (admin/webhook: register a licence key)
    POST /api/desktop/licence-import-wc    (WooCommerce webhook -> LMFWC import)
    POST /api/desktop/activate             (desktop app: bind key to a machine)
    POST /api/desktop/verify               (desktop app: periodic re-check)
    GET  /api/admin/licences               (admin: list all licence records)
    POST /api/admin/licences/<key>/resend-email
    POST /api/admin/licences/<key>/reset
    POST /api/provision                    (Fusion add-in: verify + install assets)
    POST /api/autodesk-ipn                 (Autodesk App Store payment notification)
    POST /api/woo-webhook                  (WooCommerce order webhook)
    POST /api/subscribers/add / /remove    (manual fallback subscriber list)

Deliberately NOT ported: the original had a hardcoded dev backdoor
(`data.get("backdoor_key") == "xoot"` bypassing entitlement checks
entirely) with its own "remove before public launch" comment. A shared
library should never carry a hardcoded bypass credential — if a
dev/test bypass is needed, gate it on cct_common.deploy_mode instead
(e.g. `not is_live(...)`), not a fixed string.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta

ENTITLEMENT_URL = "https://apps.autodesk.com/webservices/checkentitlement"


def _default_licence_email_body(key, order_id, valid_until):
    return (
        f"Hi,\n\nThank you for your purchase!\n\n"
        f"Your licence key is:\n\n    {key}\n\n"
        f"Valid until: {valid_until}\n\n"
        f"Order: #{order_id}\n\n"
        f"If you have any questions, reply to this email.\n"
    )


def _default_welcome_email_body(txn_id):
    return (
        f"Hi,\n\nThank you for subscribing!\n\n"
        f"Your subscription is now active.\n\n"
        f"Transaction ID: {txn_id}\n"
    )


def register_licensing_routes(
    app, *, admin_secret: str, log_dir=None,
    app_download_url: str = "", app_version: str = "", app_changelog: str = "",
    runtime_url: str = "", runtime_version: str = "",
    licence_b64: str = "", licence_expiry: str = "",
    licence_default_max_activations: int = 2,
    licence_default_valid_years: int = 1,
    email_sender=None,  # callable(to, subject, body) -> (ok, err); defaults to resend_email.send
    licence_email_subject: str = "Your licence key",
    licence_email_body=_default_licence_email_body,
    purchase_welcome_email_subject: str = "Welcome — your subscription is active",
    purchase_welcome_email_body=_default_welcome_email_body,
    wc_webhook_secret: str = "", lmfwc_site_url: str = "",
    lmfwc_consumer_key: str = "", lmfwc_consumer_secret: str = "",
    autodesk_app_id: str = "",
):
    from flask import jsonify, request

    if email_sender is None:
        from cct_common.resend_email import send as email_sender

    log_dir = log_dir or os.path.join(os.getcwd(), "logs")
    licences_file = os.path.join(log_dir, "desktop_licences.json")
    subscribers_file = os.path.join(log_dir, "subscribers.json")
    purchases_file = os.path.join(log_dir, "purchases.json")

    licences_lock = threading.Lock()
    subscribers_lock = threading.Lock()
    purchases_lock = threading.Lock()

    # ── shared storage helpers ──────────────────────────────────────────

    def _load_json(path, default):
        try:
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _save_json(path, data):
        os.makedirs(log_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def _load_licences():
        return _load_json(licences_file, {})

    def _save_licences(data):
        _save_json(licences_file, data)

    def _load_subscribers():
        return _load_json(subscribers_file, {})

    def _save_subscribers(data):
        _save_json(subscribers_file, data)

    def _append_purchase(record):
        with purchases_lock:
            purchases = _load_json(purchases_file, [])
            purchases.append(record)
            _save_json(purchases_file, purchases)
            return purchases

    def _auth():
        auth = request.headers.get("Authorization", "")
        if not admin_secret or auth != f"Bearer {admin_secret}":
            return jsonify({"error": "unauthorized"}), 401
        return None

    def _send_licence_email(email, key, order_id, valid_until):
        return email_sender(email, licence_email_subject,
                            licence_email_body(key, order_id, valid_until))

    def _send_welcome_email(email, txn_id):
        return email_sender(email, purchase_welcome_email_subject,
                            purchase_welcome_email_body(txn_id))

    def _verify_autodesk_entitlement(user_id):
        if not autodesk_app_id or not user_id:
            return False
        try:
            import urllib.request
            url = f"{ENTITLEMENT_URL}?userid={user_id}&appid={autodesk_app_id}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                return bool(data.get("IsValid"))
        except Exception:
            return False

    # ── desktop licence activation ──────────────────────────────────────

    def licence_import():
        err = _auth()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        key = data.get("licence_key", "").strip().upper()
        email = data.get("email", "").lower().strip()
        order_id = str(data.get("order_id", ""))
        valid_years = max(1, int(data.get("valid_years", licence_default_valid_years)))
        if not key:
            return jsonify({"error": "licence_key required"}), 400
        valid_until = (datetime.now() + timedelta(days=365 * valid_years)).isoformat()
        with licences_lock:
            licences = _load_licences()
            licences[key] = {
                "email": email, "order_id": order_id,
                "created_at": datetime.now().isoformat(), "valid_until": valid_until,
                "activations": [], "max_activations": licence_default_max_activations,
            }
            _save_licences(licences)
        return jsonify({"ok": True, "valid_until": valid_until})

    def licence_import_wc():
        sig = request.headers.get("X-WC-Webhook-Signature", "")
        raw = request.get_data()
        if wc_webhook_secret:
            expected = base64.b64encode(
                hmac.new(wc_webhook_secret.encode(), raw, hashlib.sha256).digest()).decode()
            if not hmac.compare_digest(expected, sig):
                return jsonify({"error": "invalid signature"}), 401

        order = request.get_json(silent=True) or {}
        if order.get("status") != "completed":
            return jsonify({"ok": True, "skipped": "not completed"}), 200

        order_id = order.get("id")
        email = (order.get("billing", {}).get("email", "") or "").lower().strip()
        if not order_id:
            return jsonify({"error": "missing order id"}), 400

        creds = base64.b64encode(f"{lmfwc_consumer_key}:{lmfwc_consumer_secret}".encode()).decode()
        lmfwc_url = f"{lmfwc_site_url}/wp-json/lmfwc/v2/licenses?order_id={order_id}"
        try:
            import urllib.request
            req = urllib.request.Request(lmfwc_url, headers={"Authorization": f"Basic {creds}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                lmfwc_data = json.loads(resp.read())
        except Exception as exc:
            return jsonify({"error": f"LMFWC API error: {exc}"}), 502

        licenses = lmfwc_data.get("data", [])
        if not licenses:
            return jsonify({"error": "no licences found for this order"}), 404

        imported = []
        with licences_lock:
            db = _load_licences()
            for lic in licenses:
                key = (lic.get("licenseKey") or "").strip().upper()
                if not key:
                    continue
                expires = lic.get("expiresAt")
                valid_until = expires if expires else (datetime.now() + timedelta(days=365)).isoformat()
                db[key] = {
                    "email": email, "order_id": str(order_id),
                    "created_at": datetime.now().isoformat(), "valid_until": valid_until,
                    "activations": [], "max_activations": licence_default_max_activations,
                }
                imported.append(key)
            _save_licences(db)
        return jsonify({"ok": True, "imported": len(imported)})

    def activate():
        data = request.get_json(silent=True) or {}
        key = data.get("licence_key", "").strip().upper()
        machine_id = data.get("machine_id", "").strip()
        hostname = (data.get("hostname", "") or "")[:64]
        if not key or not machine_id:
            return jsonify({"error": "licence_key and machine_id required"}), 400
        with licences_lock:
            licences = _load_licences()
            rec = licences.get(key)
            if not rec:
                return jsonify({"error": "Invalid licence key — check your order email."}), 404
            if datetime.fromisoformat(rec["valid_until"]) < datetime.now():
                return jsonify({"error": "Licence expired — renew to continue."}), 403
            for act in rec["activations"]:
                if act["machine_id"] == machine_id:
                    return jsonify({"ok": True, "valid_until": rec["valid_until"]})
            if len(rec["activations"]) >= rec.get("max_activations", licence_default_max_activations):
                return jsonify({"error": "Activation limit reached — contact support to reset."}), 403
            rec["activations"].append({
                "machine_id": machine_id, "hostname": hostname,
                "activated_at": datetime.now().isoformat(),
            })
            _save_licences(licences)
        return jsonify({"ok": True, "valid_until": rec["valid_until"],
                        "app_url": app_download_url or ""})

    def verify():
        data = request.get_json(silent=True) or {}
        key = data.get("licence_key", "").strip().upper()
        machine_id = data.get("machine_id", "").strip()
        if not key or not machine_id:
            return jsonify({"error": "licence_key and machine_id required"}), 400
        with licences_lock:
            licences = _load_licences()
            rec = licences.get(key)
        if not rec:
            return jsonify({"error": "Invalid licence key"}), 404
        if datetime.fromisoformat(rec["valid_until"]) < datetime.now():
            return jsonify({"error": "Licence expired — renew to continue."}), 403
        if not any(act["machine_id"] == machine_id for act in rec["activations"]):
            return jsonify({"error": "Machine not activated for this licence"}), 403
        return jsonify({"ok": True, "valid_until": rec["valid_until"]})

    def admin_licences():
        err = _auth()
        if err:
            return err
        with licences_lock:
            licences = _load_licences()
        result = [{
            "key": key, "email": rec.get("email", ""), "order_id": rec.get("order_id", ""),
            "valid_until": rec.get("valid_until", ""), "created_at": rec.get("created_at", ""),
            "max_activations": rec.get("max_activations", 1),
            "activations": rec.get("activations", []),
        } for key, rec in licences.items()]
        result.sort(key=lambda r: r["created_at"], reverse=True)
        return jsonify(result)

    def admin_licence_resend_email(key):
        err = _auth()
        if err:
            return err
        key = key.strip().upper()
        with licences_lock:
            rec = _load_licences().get(key)
        if not rec:
            return jsonify({"error": "key not found"}), 404
        email = rec.get("email", "")
        order_id = rec.get("order_id", "")
        valid_until = rec.get("valid_until", "")[:10]
        if not email:
            return jsonify({"error": "no email on record for this key"}), 400
        ok, err_msg = _send_licence_email(email, key, order_id, valid_until)
        if not ok:
            return jsonify({"error": f"Email failed: {err_msg}"}), 502
        return jsonify({"ok": True, "email": email})

    def admin_licence_reset(key):
        err = _auth()
        if err:
            return err
        key = key.strip().upper()
        with licences_lock:
            licences = _load_licences()
            if key not in licences:
                return jsonify({"error": "key not found"}), 404
            prev = len(licences[key].get("activations", []))
            licences[key]["activations"] = []
            _save_licences(licences)
        return jsonify({"ok": True, "key": key, "activations_cleared": prev})

    # ── Autodesk / subscriber provisioning ──────────────────────────────

    def provision():
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id", "").strip()
        email = data.get("email", "").lower().strip()
        if not user_id and not email:
            return jsonify({"error": "user_id or email required"}), 400

        entitled = _verify_autodesk_entitlement(user_id)
        if not entitled:
            with subscribers_lock:
                subs = _load_subscribers()
            record = subs.get(user_id) or subs.get(email)
            entitled = bool(record and record.get("active"))

        if not entitled:
            return jsonify({"error": "No active subscription found for this account."}), 403
        if not licence_b64 or not app_download_url:
            return jsonify({"error": "Release not yet published — contact support."}), 503

        return jsonify({
            "app_url": app_download_url, "app_version": app_version,
            "app_changelog": app_changelog, "runtime_url": runtime_url,
            "runtime_version": runtime_version, "licence_b64": licence_b64,
            "licence_expiry": licence_expiry,
        })

    def autodesk_ipn():
        payload = request.form.to_dict()
        status = payload.get("payment_status", "").strip()
        txn_id = payload.get("txn_id", "").strip()
        email = payload.get("buyer_adsk_account", "").lower().strip()
        app_id = payload.get("appId", "").strip()
        txn_type = payload.get("txn_type", "").strip()
        amount = payload.get("mc_gross", "0.00").strip()

        if autodesk_app_id and app_id and app_id != autodesk_app_id:
            return "", 200  # always 200 to Autodesk

        record = {"ts": int(time.time()), "txn_id": txn_id, "txn_type": txn_type,
                  "status": status, "email": email, "app_id": app_id,
                  "amount": amount, "raw": payload}

        # Check for a prior Completed purchase *before* appending this one —
        # the original compared integer-second timestamps to exclude the
        # just-appended record, which misfires (double-welcomes) whenever
        # two purchases for the same email land in the same second.
        if status == "Completed" and email:
            with purchases_lock:
                existing = _load_json(purchases_file, [])
            already_welcomed = any(
                p.get("email") == email and p.get("status") == "Completed"
                for p in existing)
            if not already_welcomed:
                _send_welcome_email(email, txn_id)

        _append_purchase(record)
        return "", 200

    def woo_webhook():
        raw_body = request.get_data()
        if wc_webhook_secret:
            sig_header = request.headers.get("X-WC-Webhook-Signature", "")
            expected = base64.b64encode(
                hmac.new(wc_webhook_secret.encode(), raw_body, hashlib.sha256).digest()).decode()
            if not hmac.compare_digest(sig_header, expected):
                return jsonify({"error": "invalid signature"}), 401
        try:
            payload = json.loads(raw_body)
        except Exception:
            return jsonify({"error": "invalid JSON"}), 400

        status_map = {"completed": "Completed", "refunded": "Refunded",
                      "cancelled": "Cancelled", "processing": "Processing",
                      "pending": "Pending", "on-hold": "On-Hold", "failed": "Failed"}
        woo_status = payload.get("status", "").lower()
        status = status_map.get(woo_status, woo_status.capitalize())
        record = {
            "ts": int(time.time()), "txn_id": str(payload.get("id", "")),
            "txn_type": "WooCommerce", "status": status,
            "email": (payload.get("billing", {}).get("email") or "").lower().strip(),
            "app_id": ", ".join(li.get("name", "") for li in payload.get("line_items", []) if li.get("name")),
            "amount": str(payload.get("total", "0.00")), "raw": payload,
        }
        _append_purchase(record)
        return "", 200

    def subscribers_add():
        err = _auth()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id", "").strip()
        email = data.get("email", "").lower().strip()
        if not user_id and not email:
            return jsonify({"error": "user_id or email required"}), 400
        with subscribers_lock:
            subs = _load_subscribers()
            key = user_id or email
            subs[key] = {"user_id": user_id, "email": email, "active": True,
                        "added": datetime.now().isoformat()}
            _save_subscribers(subs)
        return jsonify({"ok": True, "key": key})

    def subscribers_remove():
        err = _auth()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        key = (data.get("user_id") or data.get("email") or "").strip()
        if not key:
            return jsonify({"error": "user_id or email required"}), 400
        with subscribers_lock:
            subs = _load_subscribers()
            if key in subs:
                subs[key]["active"] = False
                _save_subscribers(subs)
        return jsonify({"ok": True, "key": key})

    app.add_url_rule("/api/desktop/licence-import", view_func=licence_import, methods=["POST"])
    app.add_url_rule("/api/desktop/licence-import-wc", view_func=licence_import_wc, methods=["POST"])
    app.add_url_rule("/api/desktop/activate", view_func=activate, methods=["POST"])
    app.add_url_rule("/api/desktop/verify", view_func=verify, methods=["POST"])
    app.add_url_rule("/api/admin/licences", view_func=admin_licences, methods=["GET"])
    app.add_url_rule("/api/admin/licences/<key>/resend-email",
                     view_func=admin_licence_resend_email, methods=["POST"])
    app.add_url_rule("/api/admin/licences/<key>/reset",
                     view_func=admin_licence_reset, methods=["POST"])
    app.add_url_rule("/api/provision", view_func=provision, methods=["POST"])
    app.add_url_rule("/api/autodesk-ipn", view_func=autodesk_ipn, methods=["POST"])
    app.add_url_rule("/api/woo-webhook", view_func=woo_webhook, methods=["POST"])
    app.add_url_rule("/api/subscribers/add", view_func=subscribers_add, methods=["POST"])
    app.add_url_rule("/api/subscribers/remove", view_func=subscribers_remove, methods=["POST"])
