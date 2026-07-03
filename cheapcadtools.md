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
*   **`wp_kses_post` strips `<style>` tags:** When calling `wp_update_post()` from PHP CLI (no logged-in user with `unfiltered_html`), WordPress sanitizes content and strips `<style>` blocks, leaving the CSS text visible on the page. Fix: wrap the update with `kses_remove_filters()` / `kses_init_filters()`. Always use a PHP script uploaded via SFTP rather than shell heredocs when content contains single quotes (e.g. font names like `'Times New Roman'`).
*   **Prefer PHP scripts over `wp post update` for complex content:** Upload content to `/tmp/file.txt` and a PHP script to `/tmp/update.php`, then `php /tmp/update.php`. This sidesteps all shell quoting and kses issues in one pattern.

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
    *   WooCommerce 10.7.0
    *   License Manager for WooCommerce (lmfwc)
*   **Cleanup:** Default WooCommerce and WordPress pages (Cart, Checkout, Shop, Sample Page, etc.) were deleted to keep the site lean. Akismet was removed.

## Brand Identity & Colors
*   **Primary Accent (Deep Red):** `#761516`
*   **Background / Complementary (Silver/Gray):** `#eaebed` (Complements the metallic/slate logo lettering).
*   **Text:** `#000000` (Black), `#ffffff` (White).

## SSH Key Details

The GreenGeeks server has **two** authorized SSH keys:
*   **ED25519** (`claude-code` key): public key at `C:\Users\cmyer\Downloads\claude-code.pub`; private key at `~/.ssh/id_ed25519_greengeeks`. **Note:** this key is rejected by the server in practice — use the RSA key instead.
*   **RSA key** (no passphrase): saved at `~/.ssh/id_rsa_greengeeks` (chmod 600). Passphrase was stripped with `ssh-keygen -p -P 'cKylNZ^JPBkFsluc' -N '' -f ~/.ssh/id_rsa_greengeeks`. This is the key that actually works.

**Always use `~/.ssh/id_rsa_greengeeks`** for SSH and paramiko connections.

The paramiko pattern should use:
```python
key_path = os.path.expanduser('~/.ssh/id_rsa_greengeeks')
k = paramiko.RSAKey.from_private_key_file(key_path)
```

## WooCommerce Store

### Store State
*   WooCommerce 10.7.0 — very recent; uses **block-based product templates** (not classic PHP templates).
*   Store may have a "coming soon" mode active: `<meta name='woo-coming-soon-page' content='yes'>` appears in rendered HTML. If the store isn't behaving correctly, check WooCommerce → Settings → General → Store notice / coming soon mode and disable it.
*   No custom WooCommerce templates exist in the blockbase theme directory — WooCommerce uses its own block template engine.

### Products
| ID | Slug | Title | Price | Status |
|---|---|---|---|---|
| 142 | `freecad-timing-pulley-addin` | FreeCAD Timing Pulley Addin — 1 Year Licence | $9.99 | Virtual, published |

*   Product URL: `https://cheapcadtools.com/product/freecad-timing-pulley-addin/`
*   Product thumbnail: attachment ID **145** (`PulleyWebApp_Product1.png`), `_thumbnail_id = 145` set on post 142.
*   Thumbnails generated at: 100×100, 150×150, 300×226, 300×300, 600×453, 768×579.
*   Image file URL: `https://cheapcadtools.com/wp-content/uploads/2026/07/PulleyWebApp_Product1.png`
*   **Product image display:** WooCommerce 10.7's block template requires images to be added to the **product gallery** (the gallery tab in the product editor, which populates `_product_image_gallery` post meta) — setting `_thumbnail_id` alone is not sufficient. The gallery block in the block template reads from the gallery meta; without it, no image renders even when `has-post-thumbnail` appears in the body class.

### Page Structure
| ID | Slug | Parent | URL | Purpose |
|---|---|---|---|---|
| 141 | `tools` | (none) | `/tools/` | Tools index page |
| 75 | `pulleys` | 141 | `/tools/pulleys/` | FreeCAD Pulley landing (free app entry via CloudFlare → Render) |
| 142 | `freecad-timing-pulley-addin` | (none, WC product) | `/product/freecad-timing-pulley-addin/` | WooCommerce purchase page |
| 147 | `cart` | (none) | `/cart/` | WooCommerce Cart |
| 148 | `checkout` | (none) | `/checkout/` | WooCommerce Checkout |
| 149 | `my-account` | (none) | `/my-account/` | WooCommerce My Account |

**CloudFlare proxy:** `https://cheapcadtools.com/tools/pulleys` → Render Flask app (free tier entry point).

### WP-CLI Gotchas
*   **Loose slug matching:** `wp post list --post_name=tools` does NOT do an exact match — it runs a SQL `LIKE '%tools%'` and returns any post whose slug contains "tools". Always verify with a direct DB query:
    ```bash
    wp db query "SELECT ID, post_title, post_name FROM wp_posts WHERE post_name = 'tools' AND post_status = 'publish';"
    ```
*   **MariaDB reserved words:** `separator` is a reserved word in MariaDB. Always backtick-quote it in raw SQL: `` `separator` ``.
*   **LMFW tables:** Cannot be managed via REST API with read-only keys. Use `wp db query` with direct SQL INSERTs for generator creation.

### License Manager for WooCommerce (LMFW)
*   **Generator ID:** 1
*   **Key format:** `CCT-XXXXXX-XXXXXX-XXXXXX-XXXXXX` (prefix CCT-, 4 chunks of 6 alphanumeric)
*   **Expiry:** 365 days from activation
*   **Activations per key:** 1
*   **Linked product:** ID 142 (via `lmfwc_products_generators` table)
*   **API keys (read-only):**
    *   Consumer Key: `ck_594722dce127ada51e83b023440fb9dc2cc8f978`
    *   Consumer Secret: `cs_7228026a35af0635527ba8ebe85ac230c0465ce6`
*   **DB tables:** `lmfwc_licenses`, `lmfwc_generators`, `lmfwc_products_generators`
*   **Insert generator example** (used when REST API is read-only):
    ```sql
    INSERT INTO `lmfwc_generators`
      (`id`, `name`, `charset`, `chunks`, `chunk_length`, `activations_limit`, `expires_in`, `created_at`, `created_by`, `updated_at`, `updated_by`, `is_deleted`, `status`)
    VALUES
      (1, 'CCT Standard', 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789', 4, 6, 1, 365, NOW(), 1, NOW(), 1, 0, 1);
    INSERT INTO `lmfwc_products_generators` (`product_id`, `generator_id`) VALUES (142, 1);
    ```
*   **Note:** `separator` column in `lmfwc_generators` is a MariaDB reserved word — must be backtick-quoted. The column stores the chunk separator character (default `-`).

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
*   **Privacy Policy (ID: 3, `/privacy-policy/`):** General CCT policy covering all products. Native Gutenberg blocks matching site style (white card on `#eaebed`, red `#761516` headings, Roboto). Linked from site footer.
*   **Fusion 360 Privacy Policy (ID: 53, `/privacy-policy/fusion360-privacy-policy/`):** Detailed policy specific to the Fusion 360 add-in (no network connections, local prefs file only). Referenced from the general policy and the Autodesk App Store submission.
*   **Navigation Menu (ID: 11):** Contains static, manual links. The animated logo (ID 62) is set as the official Site Logo and displays on the far left.

## FSE Template Parts
*   **Header (ID: 24, `post_name: header`):** Logo + right-aligned navigation. `wp_theme = blockbase`.
*   **Footer (ID: 132, `post_name: footer`):** Dark `#1a1a1a` bar. Copyright left, Privacy Policy link right. `wp_theme = blockbase`.

**Critical FSE gotchas:**
*   `wp_template_part` posts **must** have the `wp_theme` taxonomy term set to the active theme slug (`blockbase`) or WordPress ignores them and falls back to the theme file. Set it with: `wp post term set <ID> wp_theme blockbase`
*   After `wp_insert_post()` for a template part, always run the term-set command — `tax_input` in the insert array is not reliable from PHP CLI context.
*   The theme default footer (`parts/footer.html`) renders `<!-- wp:pattern {"slug":"blockbase/footer-simple"} /-->` ("Proudly Powered by WordPress"). The custom footer template part overrides it once the taxonomy is set correctly.

## Contact Form Configuration
*   **Form ID:** 18
*   **Fields:** Name, Email, Subject, Message. The message field is mandatory (`[textarea* your-message]`), and the "(optional)" text has been removed.
*   **Technical Note:** When updating CF7 settings via WP-CLI, the `_mail` metadata requires native PHP array serialization via `wp eval` to update safely. Standard bash string injection causes corrupt headers and `mail_failed` errors.

## Outgoing Email — GreenGeeks / WordPress

GreenGeeks delivers outgoing mail via its local MTA. `info@cheapcadtools.com` is the sending address for all CCT transactional email.

### WordPress mu-plugin relay (backup path)

A mu-plugin at `cheapcadtools/wp-content/mu-plugins/cct-email-relay.php` exposes a REST endpoint that uses `wp_mail()` as an HTTP-callable relay:

*   **Endpoint:** `POST https://cheapcadtools.com/wp-json/cct/v1/send-email`
*   **Payload:** `{ "secret": "<PROVISION_SECRET>", "to": "...", "subject": "...", "body": "..." }`
*   **Returns:** `{"ok":true}` or `{"ok":false,"message":"..."}` 
*   **From:** `CheapCAD Tools <info@cheapcadtools.com>` (set in plugin header)

**Cloudflare WAF bypass rule:** Cloudflare blocks datacenter IPs (e.g. Render) before WAF rules fire. A Security Rule in Cloudflare skips all WAF components for requests matching:
```
http.request.uri.path eq "/wp-json/cct/v1/send-email"
```
Action: Skip → All remaining custom rules, All rate limiting rules, All managed rules, All Super Bot Fight Mode Rules.

**Active path:** The Render app uses **Resend** (not this relay) for all transactional email. The mu-plugin relay exists as a fallback. See `CCT_Architecture.md § Email` for the full Render-side sending stack.

### Contact Form 7 mail config

*   **Recipient:** `info@cheapcadtools.com`
*   **Sender:** `wordpress@cheapcadtools.com` (bypasses GreenGeeks anti-spoofing filters — must use a `@cheapcadtools.com` sender)

## OnShape Application Extension

*   **Panel URL:** `https://pulleywebapp.onrender.com/onshape` (served by Flask route `/onshape`)
*   **Template:** `templates/onshape_panel.html` — thin launcher; two buttons: Open Online + Open Local
*   **Receives from OnShape:** `?documentId=...&workspaceId=...&elementId=...` query params (displayed as a short badge; not used for API calls)
*   **Architecture:** Thin panel only — opens the existing web app in a new browser tab. User designs the pulley there, downloads STEP/DXF, and imports via OnShape **File → Import**.

### Dev Portal Registration (one-time)
1. Go to https://dev-portal.onshape.com → **Applications** → **Create new application**
2. **Application type:** Application Extension
3. **Extension URL:** `https://pulleywebapp.onrender.com/onshape`
4. **Context:** Document (so the extension appears in the right-side panel)
5. No OAuth needed — the panel only opens external URLs.
6. After saving, copy the **Client ID** — add it to `cheapcadtools.md` once registered.

### Testing locally
Open `http://localhost:5000/onshape` directly in a browser.

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

*   Sent via **Resend API** (`_smtp_send()` in `app.py`). See CCT_Architecture.md § Email for the full sending stack.
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

## FreeCAD Addin — Install Location

*   **FreeCAD version:** 1.1 (installed at `C:\Program Files\FreeCAD 1.1`)
*   **Mod directory:** `C:\Users\cmyer\AppData\Roaming\FreeCAD\v1-1\Mod\`
*   **Addon source:** `C:\Users\cmyer\Documents\CCT_Addins\FreeCAD\TimingPulley\`
*   **Reinstall command:**
    ```powershell
    Copy-Item "C:\Users\cmyer\Documents\CCT_Addins\FreeCAD\TimingPulley" `
              "C:\Users\cmyer\AppData\Roaming\FreeCAD\v1-1\Mod\TimingPulley" `
              -Recurse -Force
    ```

## FreeCAD Addin — Key URLs
*   **`_PAID_URL`** in `C:\Users\cmyer\Documents\CCT_Addins\FreeCAD\TimingPulley\cct_pulley\panel.py` line 18:
    `https://cheapcadtools.com/product/freecad-timing-pulley-addin/`
    (Updated from the old `/tools/pulleys` URL — "Get Paid App" button now goes to the WooCommerce purchase page.)
*   **Free app URL** (hardcoded in `_open_free`): `https://cheapcadtools.com/tools/pulleys`

## Site Purpose
An experimental platform ("Vibe Coding" project) serving as a landing page to sell simple, high-quality CAD tools—starting with a Fusion 360 Timing Belt Pulley Generator—at highly affordable prices compared to the cost of custom coding them from scratch.