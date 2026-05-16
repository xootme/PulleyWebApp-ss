"""
launcher.py — PyInstaller entry point for the Pulley App desktop build.

Responsibilities:
  1. Fix template/static/log paths for the PyInstaller bundle (_MEIPASS).
  2. Load the shared CheapCADTools runtime (cadquery, OCP, trimesh, etc.)
     from %APPDATA%\CheapCADTools\runtime\site-packages into sys.path.
  3. Verify the desktop licence (activate on first run; verify every 7 days).
  4. Start Flask on a fixed local port in a background thread.
  5. Open the user's default browser to the app URL.
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

# ── Load shared runtime ───────────────────────────────────────────────────────
_runtime_sp = os.path.join(_appdata, 'CheapCADTools', 'runtime', 'site-packages')
if os.path.isdir(_runtime_sp):
    sys.path.insert(0, _runtime_sp)
    for _name in os.listdir(_runtime_sp):
        _sub = os.path.join(_runtime_sp, _name)
        if os.path.isdir(_sub) and (_name.endswith('.libs') or _name == 'casadi'):
            os.add_dll_directory(_sub)
else:
    print(f'WARNING: runtime not found at {_runtime_sp}', file=sys.stderr)

# ── Licence system ────────────────────────────────────────────────────────────
import hashlib as _hashlib
import json as _json
import platform as _platform
import urllib.request as _urllib_req
import urllib.error as _urllib_err
from datetime import datetime as _dt, timedelta as _td

_PROVISION_URL    = 'https://cheapcadtools.com'
_LICENCE_FILE     = os.path.join(_appdata, 'CheapCADTools', 'PulleyApp', 'licence.dat')
_VERIFY_DAYS      = 7    # call server at most every N days
_GRACE_DAYS       = 14   # allow offline this long before hard-blocking
_DEV_BACKDOOR     = 'xoot'  # TODO: remove before public launch


def _machine_id():
    """Stable 32-hex-char machine identifier (Windows registry MachineGuid)."""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'SOFTWARE\Microsoft\Cryptography')
        guid, _ = winreg.QueryValueEx(k, 'MachineGuid')
        return _hashlib.sha256(guid.encode()).hexdigest()[:32]
    except Exception:
        s = f"{_platform.node()}:{os.environ.get('USERNAME', '')}"
        return _hashlib.sha256(s.encode()).hexdigest()[:32]


def _load_licence():
    try:
        with open(_LICENCE_FILE) as f:
            return _json.load(f)
    except Exception:
        return None


def _save_licence(data):
    os.makedirs(os.path.dirname(_LICENCE_FILE), exist_ok=True)
    with open(_LICENCE_FILE, 'w') as f:
        _json.dump(data, f, indent=2)


def _api_post(path, payload):
    url  = f'{_PROVISION_URL}{path}'
    body = _json.dumps(payload).encode()
    req  = _urllib_req.Request(url, data=body,
                               headers={'Content-Type': 'application/json'},
                               method='POST')
    with _urllib_req.urlopen(req, timeout=12) as resp:
        return _json.loads(resp.read())


def _activate(key):
    """Send activation request. Returns (ok: bool, message: str)."""
    mid = _machine_id()
    if key.lower() == _DEV_BACKDOOR:
        _save_licence({
            'key':         key,
            'machine_id':  mid,
            'valid_until': (_dt.now() + _td(days=90)).isoformat(),
            'verified_at': _dt.now().isoformat(),
        })
        return True, 'Dev backdoor — valid for 90 days.'
    try:
        resp = _api_post('/api/desktop/activate', {
            'licence_key': key,
            'machine_id':  mid,
            'hostname':    _platform.node(),
        })
        _save_licence({
            'key':         key,
            'machine_id':  mid,
            'valid_until': resp['valid_until'],
            'verified_at': _dt.now().isoformat(),
        })
        return True, f"Activated — valid until {resp['valid_until'][:10]}."
    except _urllib_err.HTTPError as e:
        try:
            msg = _json.loads(e.read().decode()).get('error', 'Activation failed.')
        except Exception:
            msg = f'Server error ({e.code}).'
        return False, msg
    except Exception as exc:
        return False, f'Cannot reach activation server: {exc}'


def _verify_licence():
    """
    Check licence. Returns (ok: bool, message: str).
    'no_licence'  → no licence.dat found (first run)
    'ok'          → licence valid (online or within grace)
    'expired:...' → hard block
    'error:...'   → network/server problem past grace period
    """
    dat = _load_licence()
    if not dat:
        return False, 'no_licence'

    # Dev backdoor — only check local expiry, never hit server
    if (dat.get('key') or '').lower() == _DEV_BACKDOOR:
        try:
            if _dt.fromisoformat(dat['valid_until']) < _dt.now():
                return False, 'no_licence'
        except Exception:
            pass
        return True, 'ok (dev)'

    mid = _machine_id()
    if dat.get('machine_id') != mid:
        return False, 'Machine mismatch — licence was activated on a different computer.\nContact support@cheapcadtools.com.'

    # Cached expiry check (fast path)
    try:
        if _dt.fromisoformat(dat['valid_until']) < _dt.now():
            return False, f"expired:Licence expired on {dat['valid_until'][:10]}.\nRenew at cheapcadtools.com/shop"
    except Exception:
        pass

    # Within verify interval → skip network call
    try:
        verified_at = _dt.fromisoformat(dat.get('verified_at', '2000-01-01'))
        if _dt.now() - verified_at < _td(days=_VERIFY_DAYS):
            return True, 'ok'
    except Exception:
        pass

    # Online verify
    try:
        resp = _api_post('/api/desktop/verify', {
            'licence_key': dat['key'],
            'machine_id':  mid,
        })
        dat['verified_at'] = _dt.now().isoformat()
        dat['valid_until'] = resp['valid_until']
        _save_licence(dat)
        return True, 'ok'
    except _urllib_err.HTTPError as e:
        try:
            msg = _json.loads(e.read().decode()).get('error', '')
        except Exception:
            msg = ''
        if 'expired' in msg.lower():
            return False, f"expired:{msg}"
        # Other server errors — fall through to grace check
    except Exception:
        pass

    # Offline grace
    try:
        verified_at = _dt.fromisoformat(dat.get('verified_at', '2000-01-01'))
        if _dt.now() - verified_at < _td(days=_GRACE_DAYS):
            return True, 'ok (offline)'
    except Exception:
        pass

    return False, 'error:Cannot reach the licence server and grace period has elapsed.\nConnect to the internet and try again.'


# ── Activation dialog (tkinter) ───────────────────────────────────────────────

def _show_activation_dialog():
    """Show the licence key entry window. Returns True if activated successfully."""
    import tkinter as tk
    from tkinter import ttk

    result = [False]

    root = tk.Tk()
    root.title('CheapCADTools — Activate PulleyApp')
    root.resizable(False, False)
    root.geometry('460x280')
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'+{(sw - 460)//2}+{(sh - 280)//2}')

    frame = ttk.Frame(root, padding=28)
    frame.pack(fill='both', expand=True)

    ttk.Label(frame, text='PulleyApp', font=('Segoe UI', 16, 'bold')).pack()
    ttk.Label(frame,
              text='Enter your licence key to activate this computer.\nKeys are emailed after purchase at cheapcadtools.com/shop',
              justify='center', wraplength=400).pack(pady=(6, 18))

    key_var = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=key_var, width=38, font=('Courier New', 11))
    entry.pack()
    entry.focus()

    status_var = tk.StringVar()
    status_lbl = ttk.Label(frame, textvariable=status_var, foreground='red', wraplength=400)
    status_lbl.pack(pady=(8, 0))

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(pady=(14, 0))

    def do_activate():
        key = key_var.get().strip()
        if not key:
            status_var.set('Please enter your licence key.')
            return
        act_btn.config(state='disabled', text='Activating…')
        root.update()
        ok, msg = _activate(key)
        if ok:
            result[0] = True
            root.destroy()
        else:
            status_var.set(msg)
            act_btn.config(state='normal', text='Activate')

    def do_buy():
        webbrowser.open('https://cheapcadtools.com/shop')

    def do_cancel():
        root.destroy()

    act_btn = ttk.Button(btn_frame, text='Activate', command=do_activate, width=14)
    act_btn.grid(row=0, column=0, padx=6)
    ttk.Button(btn_frame, text='Buy a licence →', command=do_buy, width=16).grid(row=0, column=1, padx=6)
    ttk.Button(btn_frame, text='Cancel', command=do_cancel, width=10).grid(row=0, column=2, padx=6)

    entry.bind('<Return>', lambda _: do_activate())
    root.protocol('WM_DELETE_WINDOW', do_cancel)
    root.mainloop()
    return result[0]


def _show_error_dialog(message):
    """Show a blocking error/renewal dialog. Returns True if user wants to buy/renew."""
    import tkinter as tk
    from tkinter import ttk

    open_shop = [False]

    root = tk.Tk()
    root.title('CheapCADTools — Licence Issue')
    root.resizable(False, False)
    root.geometry('420x220')
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'+{(sw - 420)//2}+{(sh - 220)//2}')

    frame = ttk.Frame(root, padding=28)
    frame.pack(fill='both', expand=True)

    ttk.Label(frame, text='Licence Issue', font=('Segoe UI', 13, 'bold')).pack()
    ttk.Label(frame, text=message, wraplength=360, justify='center').pack(pady=(10, 20))

    btn_frame = ttk.Frame(frame)
    btn_frame.pack()

    def do_buy():
        open_shop[0] = True
        root.destroy()

    ttk.Button(btn_frame, text='Renew / Buy →', command=do_buy, width=16).grid(row=0, column=0, padx=6)
    ttk.Button(btn_frame, text='Exit', command=root.destroy, width=10).grid(row=0, column=1, padx=6)

    root.mainloop()
    if open_shop[0]:
        webbrowser.open('https://cheapcadtools.com/shop')


# ── First-run setup ───────────────────────────────────────────────────────────

_INSTALL_MARKER = os.path.join(_appdata, 'CheapCADTools', 'PulleyApp', 'installed.json')


def _create_start_menu_shortcut():
    """Create a PulleyApp.lnk in the user's Start Menu Programs folder."""
    shortcut_dir = os.path.join(
        os.environ.get('APPDATA', ''),
        'Microsoft', 'Windows', 'Start Menu', 'Programs',
    )
    shortcut_path = os.path.join(shortcut_dir, 'PulleyApp.lnk')
    exe_path = sys.executable  # PulleyApp.exe in frozen mode
    ps = (
        f'$s = (New-Object -COM WScript.Shell).CreateShortcut("{shortcut_path}");'
        f'$s.TargetPath = "{exe_path}";'
        f'$s.Description = "CheapCAD Tools — PulleyApp";'
        f'$s.Save()'
    )
    try:
        import subprocess
        subprocess.run(
            ['powershell', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', ps],
            capture_output=True, timeout=10,
        )
    except Exception as exc:
        print(f'Start Menu shortcut failed: {exc}', file=sys.stderr)


def _first_run_setup():
    """On first launch: open the welcome page and offer a Start Menu shortcut."""
    if os.path.exists(_INSTALL_MARKER):
        return  # already done

    # Open welcome HTML in the default browser
    welcome_html = os.path.join(_base, 'static', 'welcome.html')
    if os.path.exists(welcome_html):
        webbrowser.open(f'file:///{welcome_html.replace(os.sep, "/")}')

    # Ask about Start Menu shortcut
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title('CheapCAD Tools — Setup')
    root.resizable(False, False)
    root.geometry('420x170')
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'+{(sw - 420)//2}+{(sh - 170)//2}')
    root.attributes('-topmost', True)

    frame = ttk.Frame(root, padding=28)
    frame.pack(fill='both', expand=True)

    ttk.Label(frame, text='Add PulleyApp to the Windows Start Menu?',
              font=('Segoe UI', 12, 'bold'), wraplength=360).pack()
    ttk.Label(frame,
              text='This creates a shortcut so you can launch PulleyApp from the Start Menu at any time.',
              wraplength=360, justify='center').pack(pady=(8, 20))

    btn_frame = ttk.Frame(frame)
    btn_frame.pack()

    def yes():
        _create_start_menu_shortcut()
        root.destroy()

    def no():
        root.destroy()

    ttk.Button(btn_frame, text='Yes, add shortcut', command=yes, width=18).grid(row=0, column=0, padx=8)
    ttk.Button(btn_frame, text='No thanks',          command=no,  width=12).grid(row=0, column=1, padx=8)

    root.mainloop()

    # Write marker so this never runs again
    os.makedirs(os.path.dirname(_INSTALL_MARKER), exist_ok=True)
    with open(_INSTALL_MARKER, 'w') as _f:
        _json.dump({'installed_at': _dt.now().isoformat(), 'exe': sys.executable}, _f)


_first_run_setup()

# ── Licence gate ──────────────────────────────────────────────────────────────

def _check_licence():
    ok, msg = _verify_licence()
    if ok:
        return  # valid — proceed

    if msg == 'no_licence':
        activated = _show_activation_dialog()
        if not activated:
            sys.exit(0)
        return

    # Strip tag prefix for display
    display = msg.split(':', 1)[1] if ':' in msg else msg
    _show_error_dialog(display)
    sys.exit(0)


_check_licence()

# ── Start Flask ───────────────────────────────────────────────────────────────
PORT = 5154

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
