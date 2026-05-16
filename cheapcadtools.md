# CheapCADTools.com - Knowledge Base

## Environment & Hosting
*   **Domain:** https://cheapcadtools.com/
*   **Platform:** WordPress + Python (Flask via CGI)
*   **Hosting:** GreenGeeks (Shared hosting account with xoot.pro)
*   **Python:** `/opt/alt/python311/bin/python3` (Python 3.11)
*   **SSH Host:** `chi203.greengeeks.net`
*   **SSH User:** `xootpro`
*   **SSH Key:** ED25519, local path `~/.ssh/id_ed25519_greengeeks` (Windows: `C:\Users\cmyer\.ssh\id_ed25519_greengeeks`)
*   **Auth:** Public key only — GreenGeeks rejects password auth. Key added via cPanel → SSH Access → Manage Keys.
*   **SSH Client:** Use **paramiko** (installed in `.venv312`). `sshpass` is unavailable on Windows; Posh-SSH failed to install non-interactively.
*   **WordPress Root:** `/home/xootpro/public_html/cheapcadtools`
*   **WP-CLI:** Available on server; run as `wp <command>` from the WordPress root. Always `cd` to WP root first.
*   **Configuration File:** Managed locally via `wp_settings.json`.

## SSH / WordPress Edit Pattern

All WordPress edits are done over SSH via paramiko. Standard pattern:

```python
import paramiko, os, tempfile

key_path = os.path.expanduser("~/.ssh/id_ed25519_greengeeks")
wp_root  = "/home/xootpro/public_html/cheapcadtools"

k = paramiko.Ed25519Key.from_private_key_file(key_path)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("chi203.greengeeks.net", username="xootpro", pkey=k)

# Read post content
_, out, _ = c.exec_command(f"cd {wp_root} && wp post get <ID> --field=post_content 2>/dev/null")
content = out.read().decode()

# ... modify content in Python ...

# Write modified content via temp file (avoids shell quoting issues)
tmp_local  = os.path.join(tempfile.gettempdir(), "wp_post.txt")
tmp_remote = "/tmp/wp_post.txt"
with open(tmp_local, "w", encoding="utf-8") as f:
    f.write(content)
sftp = c.open_sftp()
sftp.put(tmp_local, tmp_remote)
sftp.close()
c.exec_command(f'cd {wp_root} && wp post update <ID> --post_content="$(cat {tmp_remote})" 2>&1')

# Purge LiteSpeed cache after every content change
c.exec_command(f"cd {wp_root} && wp litespeed-purge all 2>&1")
c.close()
```

**Key gotchas:**
*   **String encoding:** Never embed Unicode em-dashes or en-dashes directly — they corrupt on upload. Use `&mdash;` and `&ndash;` HTML entities instead.
*   **`wp post update` via heredoc:** Always write content to a remote temp file and use `$(cat /tmp/file)` — shell heredoc injection mangles multiline content.
*   **WP-CLI metadata (e.g. CF7 `_mail`):** Use `wp eval` with native PHP `serialize()` — bash string injection produces corrupt PHP serialized arrays.
*   **Navigation (FSE):** The main nav is stored as a `wp_navigation` post (ID 11) with Gutenberg block markup, not a classic menu. Edit its `post_content` like any other post.
*   **Featured image on Home page:** Controlled via `_thumbnail_id` post meta on post 16. Was deleted to allow the animated SVG banner approach.
*   **`mesg: ttyname failed`** warning on exec_command output is harmless — ignore it.

## Pulley Web App (Flask/CGI)
*   **Path:** `/home/xootpro/public_html/cheapcadtools/tst_pulleys/`
*   **URL:** `https://cheapcadtools.com/tst_pulleys/`
*   **Architecture:** LiteSpeed handles requests → `.htaccess` rewrites to `index.cgi` → `wsgiref` bridge → Flask `app.py`.
*   **Technical Findings (LiteSpeed Constraints):**
    *   **Strict `stderr` Handling:** LiteSpeed kills scripts if they output to `stderr` before headers. Enforced `sys.stderr = sys.stdout` and `warnings.filterwarnings("ignore")` in the CGI entry point.
    *   **CGI vs ProxyFix:** `ProxyFix` middleware (Werkzeug) is incompatible/redundant in this CGI environment and was removed to prevent crashes.
    *   **Permissions:** Strict SuExec requires `755` for directories and `644` for files. Stripping `+x` from `venv/bin` binaries breaks deployment.
*   **Deployment Workflow:** Managed via `deploy.sh` (local) and `provision_remote.sh` (remote).

## Theme & Architecture
*   **Theme:** Blockbase (Full Site Editing / Block Theme).
*   **Typography:** Google Fonts - Roboto (Weights: 400, 700, 900).
*   **Active Plugins:** 
    *   Contact Form 7
    *   LiteSpeed Cache
    *   WooCommerce
*   **Cleanup:** Default WooCommerce and WordPress pages (Cart, Checkout, Shop, Sample Page, etc.) were deleted to keep the site lean. Akismet was removed.

## Brand Identity & Colors
*   **Primary Accent (Deep Red):** `#761516`
*   **Background / Complementary (Silver/Gray):** `#eaebed` (Complements the metallic/slate logo lettering).
*   **Text:** `#000000` (Black), `#ffffff` (White).

## Pages & Structure
*   **Home (ID: 16):** Set as Front Page.
    *   Features a custom, 100% full-width native Gutenberg Cover Block banner.
    *   Displays `CCT_Banner.gif` via the Featured Image, uncropped.
    *   Left-aligned black text: "Professional Grade CAD Tools / at an Affordable Price" (Roboto Black 900).
    *   Red badge: "More to Come....".
    *   *Technical Hack:* Custom CSS applied via an HTML block to hide the default "Home" page title that Blockbase forces onto the template.
*   **About (ID: 15):** 
    *   Fully native Gutenberg layout.
    *   Silver/gray background (`#eaebed`) spanning full width.
    *   White letterboxed content card containing the text.
*   **Contact (ID: 19):** 
    *   Contains Contact Form 7 shortcode.
    *   Custom CSS injected to style the submit button (large, centered, brand red `#761516`, with hover transitions) and input fields.
*   **Navigation Menu (ID: 11):** Contains static, manual links to Home, About, and Contact. The animated logo (ID 62) is set as the official Site Logo and displays on the far left.

## Contact Form Configuration
*   **Form ID:** 18
*   **Recipient:** `info@cheapcadtools.com`
*   **Sender:** `wordpress@cheapcadtools.com` (Used to bypass GreenGeeks strict anti-spoofing/SMTP filters).
*   **Fields:** Name, Email, Subject, Message. The message field is mandatory (`[textarea* your-message]`), and the "(optional)" text has been removed.
*   **Technical Note:** When updating CF7 settings via WP-CLI, the `_mail` metadata requires native PHP array serialization via `wp eval` to update safely. Standard bash string injection causes corrupt headers and `mail_failed` errors.

## Render.com (PulleyWebApp Service)
*   **Service ID:** `srv-d7bve2a8qa3s738n68ig`
*   **Service URL:** `https://pulleywebapp.onrender.com`
*   **SSH:** `ssh -i ~/.ssh/id_ed25519_claude_cct srv-d7bve2a8qa3s738n68ig@ssh.oregon.render.com`
*   **SSH User:** `render` | **Working dir:** `/opt/render/project/src`
*   **API Key name:** "Cheap Cad Tools Dashboard" — stored in Windows Credential Manager; key value is in Render env var `RENDER_API_KEY`.
*   **Disk:** 1 GB mounted at `/var/data`; `PULLEY_LOG_DIR=/var/data/logs` routes all logs there.
*   **Required Render env vars:**
    *   `PROVISION_SECRET` — guards all `/api/admin/*` and `/api/subscribers/*` endpoints
    *   `RENDER_API_KEY` — Render API key for the dashboard service proxy endpoints
    *   `RENDER_SERVICE_ID` — defaults to `srv-d7bve2a8qa3s738n68ig` if not set
    *   `PULLEY_LOG_DIR` — set to `/var/data/logs`
    *   `SENDGRID_API_KEY` — milestone/bug email notifications
    *   `PULLEY_LICENCE_B64`, `PULLEY_LICENCE_EXPIRY`, `PULLEY_APP_URL` — set after annual build
    *   `AUTODESK_APP_ID` — set after App Store registration

## Admin Dashboard
*   **File:** `admin_dashboard.html` — open locally in any browser; no server required.
*   **Login fields:** Base URL (`https://cheapcadtools.com`), Provision Secret, Render API Key.
*   **Credentials persist in `sessionStorage`** (cleared on tab close).
*   **Tabs:** Health | Metrics | Bug Reports | Downloads | Subscribers.
*   **Metrics logged** every 60 s to `/var/data/logs/metrics.jsonl`: `req_per_min`, `cpu`, `mem_mb`, `mem_pct`, `disk_pct`.
*   **Constraint events** logged to `/var/data/logs/constraint_events.jsonl` when CPU >80%, memory >85%, or req rate >120/min.
*   **Retention:** 30 days; trimmed automatically on each metrics sample.

## Autodesk IPN (Instant Payment Notification)

Autodesk App Store sends a form-encoded POST to this endpoint after every transaction.

*   **Route:** `POST /api/autodesk-ipn`
*   **Content-Type:** `application/x-www-form-urlencoded`
*   **Response:** Always `'', 200` — Autodesk requires a 200 regardless of outcome; any other status causes retries.

### Key fields

| Field | Description |
|---|---|
| `buyer_adsk_account` | Buyer's email address |
| `appId` | App Store app ID (must match `AUTODESK_APP_ID` env var) |
| `txn_id` | Unique transaction ID |
| `payment_status` | `Completed`, `Refunded`, or `Reversed` |
| `mc_gross` | Amount charged (e.g. `"19.99"`) |
| `txn_type` | Transaction type string from Autodesk |

### Processing logic

1. If `AUTODESK_APP_ID` is set and `appId` doesn't match → log warning and return 200 (ignore).
2. On `Completed`: append record to `logs/autodesk_purchases.json` (guarded by `_purchases_lock` threading lock).
3. On first `Completed` for a given email (dedup check against existing records): call `_send_ipn_welcome_email(email, txn_id)`.
4. `Refunded`/`Reversed`: logged but no special action taken (no subscriber removal — handled manually).

### Welcome email

*   Sent via SendGrid REST API using `SENDGRID_API_KEY` env var.
*   No-op if `SENDGRID_API_KEY` is not set (logs a warning instead).
*   Deduplication: checks existing purchase records before sending — re-delivery of the same IPN won't send a second email.
*   From address and template body are hardcoded in `_send_ipn_welcome_email()` in `app.py`.

### Purchase log format (`logs/autodesk_purchases.json`)

```json
[
  {
    "timestamp": "2026-05-15T12:34:56.789012",
    "email": "buyer@example.com",
    "txn_id": "abc123",
    "status": "Completed",
    "gross": "19.99",
    "app_id": "your-app-id"
  }
]
```

### Testing locally

Simulate an IPN POST with curl:
```sh
curl -X POST http://localhost:5000/api/autodesk-ipn \
  -d "buyer_adsk_account=test@example.com&appId=YOUR_APP_ID&txn_id=test001&payment_status=Completed&mc_gross=19.99&txn_type=web_accept"
```
Expected response: HTTP 200, empty body.

## Site Purpose
An experimental platform ("Vibe Coding" project) serving as a landing page to sell simple, high-quality CAD tools—starting with a Fusion 360 Timing Belt Pulley Generator—at highly affordable prices compared to the cost of custom coding them from scratch.