"""
launcher_ss.py — PyInstaller entry point for the PulleyWebApp-ss desktop build.

Differences from launcher.py (original PulleyWebApp):
  - No shared CheapCADTools runtime loading — trimesh/shapely/manifold3d are
    bundled directly in the PyInstaller archive.
  - Sets SMALL_STEP_BIN to the small_step.exe bundled alongside the exe.
  - QUEUE_DISABLED=1 is always set (single-user desktop, no queueing needed).
"""

import os
import sys
import threading
import time
import webbrowser

# ── Path fix for PyInstaller bundle ──────────────────────────────────────────
if hasattr(sys, '_MEIPASS'):
    _base = sys._MEIPASS
else:
    _base = os.path.dirname(os.path.abspath(__file__))

os.environ['PULLEY_BASE_DIR'] = _base

_appdata  = os.environ.get('APPDATA') or os.path.expanduser('~')
_log_dir  = os.path.join(_appdata, 'CheapCADTools', 'PulleyApp', 'logs')
os.environ['PULLEY_LOG_DIR'] = _log_dir
os.makedirs(_log_dir, exist_ok=True)

# Redirect stdout/stderr to a log file (no console window in release build)
_err_log = open(os.path.join(_log_dir, 'stderr.log'), 'w', encoding='utf-8', buffering=1)
sys.stderr = _err_log
sys.stdout = _err_log

# ── small_step binary ─────────────────────────────────────────────────────────
# Bundled alongside the exe by PyInstaller (binaries=[(..., '.')]).
_ss_bin = os.path.join(_base, 'small_step.exe')
if os.path.isfile(_ss_bin):
    os.environ['SMALL_STEP_BIN'] = _ss_bin
else:
    print(f'WARNING: small_step.exe not found at {_ss_bin}', file=sys.stderr)

# ── Licence system ────────────────────────────────────────────────────────────
import hashlib as _hashlib
import json as _json
import platform as _platform
import urllib.request as _urllib_req
import urllib.error as _urllib_err
from datetime import datetime as _dt, timedelta as _td

_PROVISION_URL    = 'https://cheapcadtools.com'
_LICENCE_FILE     = os.path.join(_appdata, 'CheapCADTools', 'licence.dat')
_VERIFY_DAYS      = 7    # call server at most every N days
_GRACE_DAYS       = 14   # allow offline this long before hard-blocking
_DEV_BACKDOOR     = 'xoot'  # TODO: remove before public launch
_INSTALL_MARKER   = os.path.join(_appdata, 'CheapCADTools', 'PulleyApp', '.installed')


def _machine_id() -> str:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'SOFTWARE\Microsoft\Cryptography')
        guid, _ = winreg.QueryValueEx(k, 'MachineGuid')
        return _hashlib.sha256(guid.encode()).hexdigest()
    except Exception:
        pass
    try:
        import subprocess as _sp
        out = _sp.check_output('wmic csproduct get uuid', shell=True,
                               text=True, timeout=5).strip().split()[-1]
        return _hashlib.sha256(out.encode()).hexdigest()
    except Exception:
        pass
    fallback = f'{_platform.node()}:{os.environ.get("USERNAME", "")}'
    return _hashlib.sha256(fallback.encode()).hexdigest()


def _load_licence():
    try:
        with open(_LICENCE_FILE, 'r', encoding='utf-8') as f:
            return _json.load(f)
    except Exception:
        return {}


def _save_licence(data: dict):
    os.makedirs(os.path.dirname(_LICENCE_FILE), exist_ok=True)
    with open(_LICENCE_FILE, 'w', encoding='utf-8') as f:
        _json.dump(data, f, indent=2)


def _call_provision(path: str, payload: dict) -> dict:
    import urllib.parse as _up
    url  = f'{_PROVISION_URL}{path}'
    body = _json.dumps(payload).encode()
    req  = _urllib_req.Request(url, data=body,
                               headers={'Content-Type': 'application/json'})
    resp = _urllib_req.urlopen(req, timeout=10)
    return _json.loads(resp.read())


def _verify_licence() -> tuple[bool, str]:
    lic = _load_licence()
    mid = _machine_id()

    # Dev backdoor
    if lic.get('backdoor') == _DEV_BACKDOOR:
        return True, 'backdoor'

    expiry_str = lic.get('expiry', '')
    if not expiry_str:
        return False, 'no_licence'

    try:
        expiry = _dt.fromisoformat(expiry_str)
    except Exception:
        return False, 'no_licence'

    if _dt.now() > expiry:
        return False, f'error:Your licence expired on {expiry.strftime("%Y-%m-%d")}.\nRenew at cheapcadtools.com.'

    # Check if we need to re-verify with server
    last_check = _dt.fromisoformat(lic.get('last_verified', '2000-01-01'))
    if (_dt.now() - last_check).days < _VERIFY_DAYS:
        return True, 'cached'

    try:
        resp = _call_provision('/api/provision', {
            'machine_id': mid,
            'action': 'verify',
            'backdoor': _DEV_BACKDOOR,
        })
        if resp.get('valid'):
            lic['last_verified'] = _dt.now().isoformat()
            if resp.get('expiry'):
                lic['expiry'] = resp['expiry']
            _save_licence(lic)
            return True, 'verified'
        return False, f'error:{resp.get("message", "Licence not valid.")}'
    except Exception:
        # Offline grace period
        days_since = (_dt.now() - last_check).days
        if days_since < _GRACE_DAYS:
            return True, 'offline_grace'
        return False, f'error:Cannot reach licence server.\nOffline grace period ({_GRACE_DAYS} days) exceeded.\nConnect to the internet and try again.'


def _show_activation_dialog() -> bool:
    import tkinter as tk
    from tkinter import ttk

    result = {'key': None}

    root = tk.Tk()
    root.title('Activate PulleyApp')
    root.resizable(False, False)
    root.attributes('-topmost', True)

    ttk.Label(root, text='Enter your licence key:', padding=10).pack()
    key_var = tk.StringVar()
    ttk.Entry(root, textvariable=key_var, width=40).pack(padx=10)

    def _activate():
        key = key_var.get().strip()
        if not key:
            return
        mid = _machine_id()
        try:
            resp = _call_provision('/api/provision', {
                'machine_id': mid,
                'licence_key': key,
                'action': 'activate',
                'backdoor': _DEV_BACKDOOR,
            })
            if resp.get('valid'):
                lic = {'expiry': resp.get('expiry', ''), 'machine_id': mid,
                       'last_verified': _dt.now().isoformat()}
                _save_licence(lic)
                result['key'] = key
                root.destroy()
            else:
                ttk.Label(root, text=resp.get('message', 'Activation failed.'),
                          foreground='red', padding=5).pack()
        except Exception as e:
            ttk.Label(root, text=f'Error: {e}', foreground='red', padding=5).pack()

    ttk.Button(root, text='Activate', command=_activate).pack(pady=5)
    ttk.Button(root, text='Cancel', command=root.destroy).pack(pady=5)
    root.mainloop()
    return result['key'] is not None


def _show_error_dialog(msg: str):
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.withdraw()
    messagebox.showerror('PulleyApp — Licence Error', msg, parent=root)
    root.destroy()


def _first_run_setup():
    if os.path.isfile(_INSTALL_MARKER):
        return
    os.makedirs(os.path.dirname(_INSTALL_MARKER), exist_ok=True)
    with open(_INSTALL_MARKER, 'w') as _f:
        _json.dump({'installed_at': _dt.now().isoformat(), 'exe': sys.executable}, _f)


_first_run_setup()

# ── Licence gate ──────────────────────────────────────────────────────────────

def _check_licence():
    ok, msg = _verify_licence()
    if ok:
        return
    if msg == 'no_licence':
        activated = _show_activation_dialog()
        if not activated:
            sys.exit(0)
        return
    display = msg.split(':', 1)[1] if ':' in msg else msg
    _show_error_dialog(display)
    sys.exit(0)


_check_licence()

# ── Start Flask ───────────────────────────────────────────────────────────────
PORT = 5154

# Disable the queue system — single-user desktop, no contention.
os.environ['QUEUE_DISABLED'] = '1'

from app import app  # noqa: E402


def _open_browser():
    time.sleep(1.5)
    webbrowser.open(f'http://127.0.0.1:{PORT}')


threading.Thread(target=_open_browser, daemon=True).start()

app.run(
    host='127.0.0.1',
    port=PORT,
    threaded=True,
    use_reloader=False,
    debug=False,
)
