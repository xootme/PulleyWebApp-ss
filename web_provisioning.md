# Web Provisioning & Deployment

**Host:** GreenGeeks — LiteSpeed + CGI  
**Server:** `chi203.greengeeks.net`  
**User:** `xootpro`  
**Remote path:** `~/public_html/cheapcadtools/tst_pulleys/`  
**Live URL:** `https://cheapcadtools.com/tst_pulleys/`  
**GitHub:** `https://github.com/xootme/PulleyWebApp` (private)

---

## Important Architectural Findings (GreenGeeks LiteSpeed)

During deployment, several strict limitations of GreenGeeks' LiteSpeed server were discovered and resolved:

1. **Strict `stderr` Execution Limits:** LiteSpeed immediately kills any CGI script and returns a `500 Internal Server Error` if the script outputs *anything* to `stderr` before the HTTP headers are fully sent. This includes harmless Python `DeprecationWarnings`.
   - *Fix applied:* `sys.stderr = sys.stdout` and `warnings.filterwarnings("ignore")` are enforced in `index.cgi` to prevent sudden termination.
2. **`ProxyFix` Initialization Crash:** Initializing Werkzeug's `ProxyFix` middleware dynamically based on the CGI `SCRIPT_NAME` environment variable caused fatal application crashes.
   - *Fix applied:* `ProxyFix` was removed from `app.py`. The `wsgiref.handlers.CGIHandler` naturally respects the `SCRIPT_NAME` passed from `index.cgi`, making `ProxyFix` redundant for this environment.
3. **Virtual Environment Permissions:** Running aggressive `chmod 644` across the deployment directory breaks the virtual environment by stripping the executable (`+x`) permissions from `venv/bin/python3` and `pip`. 
   - *Fix applied:* File permissions are left default during sync. Only `index.cgi` and `provision_remote.sh` require explicit `chmod 755`.

---

## How It Works

LiteSpeed doesn't support WSGI natively. The request flow is:

```
Browser → LiteSpeed (.htaccess) → index.cgi (CGI) → wsgiref → Flask (app.py)
```

`index.cgi` activates the venv, suppresses warnings to appease LiteSpeed, sets `SCRIPT_NAME=/tst_pulleys`, and hands off to Flask via `wsgiref.handlers.CGIHandler`.

---

## Files Created for Deployment

| File | Purpose |
|------|---------|
| `index.cgi` | CGI entry point — must be `chmod 755` on server |
| `provision_remote.sh` | Runs on server: creates venv, installs deps, sets permissions |
| `deploy.sh` | Runs locally: rsync files → SSH provision |

---

## Source Control (GitHub)

Repo: `https://github.com/xootme/PulleyWebApp` (private)

Always commit to GitHub before deploying to the server:

```bash
git add .
git commit -m "describe what changed"
git push
```

**Note:** Use a Personal Access Token when prompted for a password — GitHub no longer accepts account passwords over HTTPS.  
GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate (repo scope).

---

## Deploy (after code changes)

From **Git Bash** or **WSL** on Windows, from the project root:

```bash
# Recommended sequence: commit first, then deploy
git add . && git commit -m "your message" && git push
bash deploy.sh

# Sync files AND run provisioner (full deploy)
bash deploy.sh

# Sync files only (skip provisioner — use when only templates/static changed)
bash deploy.sh --sync
```

`deploy.sh` excludes: `venv/`, `__pycache__/`, `*.pyc`, `tests/`, `*.md`, `deploy.sh`, `testing.html`.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| 500 Internal Server Error | `index.cgi` not executable | `chmod 755 index.cgi` on server |
| 500 Internal Server Error | Uncaught Python error | Check raw output by running `./index.cgi` via SSH |
| Pip "Permission Denied" | `venv` binaries lost `+x` flag | Delete remote `venv` folder and run `bash deploy.sh` |
| CSS/JS not loading | Wrong `SCRIPT_NAME` | Confirm `SCRIPT_NAME=/tst_pulleys` is set in `index.cgi` |

---

## Python on GreenGeeks

GreenGeeks CloudLinux provides multiple Python versions via `/opt/alt/`:

```bash
/opt/alt/python311/bin/python3   # Python 3.11 — use this
```

The venv is created at `~/public_html/cheapcadtools/tst_pulleys/venv/`.
