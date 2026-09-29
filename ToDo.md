# ToDo — Timing Pulley Generator

Pruned 2026-09-23: items verified done or superseded against the code were
removed (see git history of this file for the old health check, metadata
and test-gap sections).

---

## Token Model (replaces subscription licensing)

**Plan:** the app is free in every CAD program; only file outputs cost tokens.
One account works across all CAD programs (Fusion, FreeCAD, SolidWorks, web).

| Output | Tokens | Price |
|---|---|---|
| 2D (SVG, DXF) | 2 | $0.10 |
| STL | 3 | $0.15 |
| STEP | 4 | $0.20 |

### Decisions (ADR-008)
- [x] ADR-008 written: tiers include lower tiers (STEP 3 ⊃ STL 2 ⊃ 2D 1); one purchase unlocks the **whole design**; unlocks last **24 h**; upgrade pays the **difference**; **signup tokens**, no weekly allowance; sign-in by **email link and OAuth** (Microsoft, Google, GitHub — not Apple)
- [x] Pack sizes and prices (2026-09-26, revised same day): **1 token = 5¢** — PayPal $2 = 40 and $5 = 100; Stripe $5 = 100, $10 = 200, $25 = 500; downloads 2D 2 · STL 3 · STEP 4 (revised from 2/4/6 the same day: each tier adds one token); signup grant 20. Change packs with `TOKEN_PACKS`
- [ ] Signup grant size (placeholder: 10)
- [ ] Autodesk App Store channel: can it sell token packs, or does it stay a subscription / go away?

### Accounts and ledger
- [x] `cct_common.tokens` (canonical repo): SQLite ledger — tier pricing, 24 h unlocks, upgrades, refund-on-failure, idempotent credits, atomic spend; 29 tests in `tests/test_tokens.py`
- [x] cct_common 0.7.0 synced here (`9638c58`); `sync_cct_common.py` now copies only cct_common's last commit
- [x] Postgres backend behind the same `TokenStore` interface (cct_common 0.11.0): `DATABASE_URL` set = Postgres, unset = the SQLite file; writes serialise on an advisory lock, so two servers can't both spend the last token. Tests run on both (`CCT_TEST_POSTGRES=...`); container smoke-tested against a local Postgres
- [x] `cct_common.accounts` + `cct_common.account_routes` (cct_common 0.7.0, `b8835e9`): linked identities, email sign-in links (15 min, single use, rate-limited, confirm-button page), hashed revocable sessions (web cookie 30 d, add-in device token 1 y), account page data/history, account deletion; 38 tests
- [x] `cct_common.sqlite_db`: WAL + `synchronous=FULL`, `integrity_check()`, online `backup()`
- [x] Wired into `app.py` via `accounts_setup.py`, **off unless `TOKENS_ENABLED=1`**: store at `logs/accounts.sqlite3`, email-link sign-in through Resend (dev without a key logs the link to `logs/server_errors.log`), startup integrity check → 503 on account routes if it fails, signup grant from `TOKENS_SIGNUP_GRANT`; 11 tests in `tests/test_accounts_setup.py`
- [x] When switching it on in production: set `TOKENS_ENABLED=1`, `CCT_ACCOUNTS_MODE=live` (Secure cookies; never logs sign-in links) and `RESEND_API_KEY` together — done at launch 2026-09-26 (LAUNCH.md, production deploy)
- [x] Sign-in UI in the page (email box, account box with balance, sign out) — see Charging exports
- [x] OAuth sign-in (Microsoft, Google, GitHub) on `AccountStore.sign_in` — cct_common 0.13.0 `oauth.py`: code flow + PKCE, state in the database and bound to the browser by a cookie, only provider-verified emails (Microsoft work accounts need the `xms_edov` claim — nOAuth). Each provider shows in the sign-in dialog once `<P>_CLIENT_ID`/`<P>_CLIENT_SECRET` are set
- [x] Add-in / AI-agent device sign-in (cct_common 0.10.0, `ea50d87`): `POST /api/account/device/start` + `/poll`, approval page `/account/device` (signs in by email first if needed); 10-minute consonant-only codes, polling secret hashed, token issued once, slow-down on fast polling, per-IP rate limit
- [x] Daily token limit per device token, on by default (100 / rolling 24 h, `TOKENS_DEVICE_DAILY_BUDGET`): chosen at approval, changed or removed on `/account/devices` (which also shows each token's 24-h spend and disconnects it); past it that token gets 429 `DAILY_LIMIT_REACHED` and the owner one email a day saying how to change it; browser sessions unlimited
- [ ] Fusion/FreeCAD add-ins: with the hosted app the server can't copy files into `Downloads/CCT_Import` — have the add-ins watch the Downloads folder for CCT-marked files and unpack the download window's zips (test in Fusion)
- [ ] AI agents: headless API + OpenAPI (see Agent / Headless API Access), then an MCP server using device-code sign-in; list it in MCP registries
- [ ] GitHub: no ID token — fetch the user from the API; request `user:email` and take the verified primary address from `/user/emails`
- [ ] Register OAuth apps (owner action, all free): Microsoft Entra admin center (any org directory + personal accounts; add the `xms_edov` optional ID-token claim); Google Cloud Console (project `cheapcadtools`, external consent screen); GitHub → Settings → Developer settings → OAuth Apps. Redirect URI for each: `<site>/account/oauth/<google|microsoft|github>/callback` — the test site's now, cheapcadtools.com's at launch
- [x] Charge-at-download checkboxes: a STEP purchase offers the included STL/SVG/DXF
- [ ] WooCommerce pack purchase → webhook credits the account (reuse the HMAC-verified webhook pattern in `cct_common.licensing`)
- [ ] **PayPal Micropayments for packs of $5 and under** (decided 2026-09-25; business PayPal account already exists). Rate 4.99% + 9¢ vs standard 2.9%/3.49% + 30–49¢ — on a $5 pack about 34¢ instead of 45–66¢
  - [ ] Owner: apply for Micropayments pricing on the business account (it's opt-in and must be approved)
  - [ ] Owner: check whether the rate applies to *every* payment on that account — it's worse than standard above ~$12, so larger packs may need a second PayPal account or to go through Stripe/standard PayPal
  - [x] Built (cct_common 0.14.0 `payments.py`): `/account/buy` with PayPal buttons (Orders API) for $2/$5 and Stripe Checkout for $5+; server-priced orders recorded in `payments`; credit only for our order at exactly its price, ref `<provider>:<order id>` so capture/return and webhook credit once; PayPal webhooks verified with PayPal, Stripe's by HMAC (5 min); refunds and chargebacks take back the refunded share, capped at the pack. No provider libraries
  - [x] PayPal **sandbox** on the test site (app client `BAAAj9n-…`, webhook `20112976ET130170R`); 2026-09-26 sandbox card purchase of the $2 pack: capture credited 20 once, PayPal's webhook arrived 12 s later, verified, no second credit
  - [ ] PayPal live: a **Live** app in the developer dashboard (its own client id, secret, webhook) and `PAYPAL_LIVE=1`; apply for the Micropayments rate first
  - [ ] Test a sandbox refund (sandbox.paypal.com as the business sandbox account → Refund) — the 20 tokens should come off
  - [ ] Owner: Stripe account (test mode first) → secret key into `STRIPE_SECRET_KEY`; webhook `<site>/api/payments/stripe/webhook` (checkout.session.completed, checkout.session.async_payment_succeeded, charge.refunded, charge.dispute.created) → signing secret into `STRIPE_WEBHOOK_SECRET`
  - [ ] Test end to end in the PayPal sandbox before going live; set `TOKENS_BUY_URL` to the buy page
- [ ] Account page: balance, purchase history, per-export history
- [x] Admin dashboard: balances, grants/refunds, sales — done: cct_common.admin, ADR-012
- [x] Inactivity (ADR-008, cct_common 0.9.0, `6647737`): free tokens expire after 2 years without a sign-in (spent first; purchased never expire); accounts with no purchased tokens close after 5 years; 30-day reminder emails first; any sign-in resets; daily run in `accounts_setup.py`; notice in the sign-in and buy dialogs and on the balance
- [x] Inactivity revised 2026-09-26 (ADR-008, cct_common 0.15.0): purchased tokens end with the account — every account closes after 5 years without a sign-in and its remaining tokens expire; the reminder names the tokens at stake and offers a refund of unused purchased ones
- [ ] **Security:** PulleyWebApp-ss is a PUBLIC repo, and `app.py`'s `_smtp_send` docstring says an earlier Resend API key was committed to git history — confirm in Resend that the key now in use (Secret Manager `RESEND_API_KEY`, Render env) is not that one; revoke the old key if it still exists
- [ ] Lawyer: can purchased tokens expire with the account in every state we sell to (California bars gift-certificate expiry), and do unused balances need unclaimed-property reporting?
- [ ] Have the terms of service state the inactivity rule (and check it against the states you sell into)
- [x] Refund policy (2026-09-26, terms §4): on request, the **unused purchased** tokens only (never more than the balance; free tokens used first), valued at the price paid, **less the payment processor's fees**; to the original payment method where the provider still allows (~6 months), otherwise another way. Same rule when we close an account (not fraud), on shutdown, and when a customer closes their own
- [x] **Do before the first refund request — refund tooling:** a refund issued in the PayPal/Stripe dashboard takes back tokens in proportion to the *money* refunded, so a fee-reduced refund leaves a few tokens behind — add an admin action that refunds the net amount through the provider's API and removes exactly the unused purchased tokens (`purchased_remaining`) — done 2026-09-26: the Refund button on the admin dashboard's Sales tab (cct_common.admin); issue refunds there, not in the provider dashboards. A refund take-back no longer counts as spending, so it takes purchased tokens, not free ones
- [x] Admin dashboard (cct_common.admin, 2026-09-26) replaces admin_dashboard.html: set `ADMIN_EMAILS` on test and production (default xootme@gmail.com); then retire the bearer-token `/api/admin/*` routes in app.py (health, metrics, constraints, downloads, subscribers, sales, licences, bug-report admin) and their CORS preflight — nothing calls them now — done 2026-09-27: ADMIN_EMAILS set on both services; the /api/admin/* routes retired (only /api/admin/licences remains, with the desktop licensing routes)

### Database backups (must be live before charging real money)
Render-era backups are out of scope — hosting moves to Google Cloud Run (see Hosting),
and the off-server copy goes to Cloud Storage from there.
- [x] `cct_common.db_backup` (0.8.0): hourly online backup of `accounts.sqlite3` into `logs/backups/hourly`, first of each day kept in `logs/backups/daily`, pruned to 7 days / 90 days, each copy verified with `integrity_check()` (deleted if bad) and stored as one self-contained file; a failed or missing (>2 h) backup alerts; safe with several gunicorn workers
- [x] Wired in `accounts_setup.py` whenever accounts are on (not under `PULLEY_TESTING`); alerts go to the error log and to `BACKUP_ALERT_EMAIL` if set
- [x] Startup `integrity_check()`: on failure the account routes answer 503, the file is left as found, and the same alert fires
- [x] Off-server copy (2026-09-27): `db_offsite_backup.py` — the Postgres database dumped whole (schema from the catalog + a CSV per table), encrypted, uploaded to `gs://cheapcadtools-backups/postgres/…` as the service account; round-trip tests against Postgres (`tests/test_db_offsite_backup.py`). **Owner: the one-time setup in RESTORE.md** (BACKUP_KEY secret + offline copy, bucket permission, the Cloud Run job, the daily schedule)
- [x] Encrypted before upload (Fernet; key `BACKUP_KEY` in Secret Manager, never in the bucket — keep an offline copy)
- [ ] Set `BACKUP_ALERT_EMAIL` in production
- [x] Written restore procedure: RESTORE.md (Neon point-in-time first; then restore into an empty database, `replay-order` for purchases newer than the backup, check, switch over)
- [ ] Practise a restore from the off-server copy before launch, then quarterly
- [ ] After the move to Postgres (Neon): turn on point-in-time restore and keep the off-server copies as a second line

### Charging exports
**Before turning `TOKENS_ENABLED` on in production:** a way to buy tokens (`TOKENS_BUY_URL` + purchase webhook), the add-ins, and the production settings (`CCT_ACCOUNTS_MODE=live`, `RESEND_API_KEY`).
- [x] `charging.py`: all 15 `/download/*` routes, the 3 add-in API routes and both async STEP jobs are charged (2D 1 / STL 2 / STEP 3); tokens taken before generating and refunded when the route answers with an error status or the job raises; an unaffordable async job is refused up front
- [x] One identity per on-screen design: the page registers it (`POST /api/design`) and sends `design_id`; the server accepts it only if the download's own parameters belong to that design (flange aliases, `p2_` names, number formats), else prices the download as its own design — a made-up id unlocks nothing
- [x] 401 `SIGN_IN_REQUIRED`, 402 `NOT_ENOUGH_TOKENS` (needed/balance), 503 when accounts are unhealthy; `X-CCT-Tokens-Charged` / `X-CCT-Tokens-Balance` headers on success
- [x] Weekly trial limits (add-in `register_trial_download`, queue session limit, `/api/fp-token`) apply only while tokens are off
- [x] 20 tests in `tests/test_charging.py` (negative controls: refund, design match, async charge)
- [x] **Async STEP result files**: `/download/<job_id>.step` (8-hex job ids, never deleted) is gone. Both STEP jobs and the download window's zip now store their output through `results.py` at `/download/result/<192-bit token>/<file>`, deleted after an hour; leftover `<jobid>.step` files are removed at startup; 6 tests in `tests/test_results.py`
- [x] Page (`static/tokens.js` + hooks in `index.html`): header account box (sign in / email, balance, sign out); sign-in dialog; the full on-screen design registered and `design_id` added to every download; each download priced first (`POST /api/tokens/quote`, since hidden-iframe downloads can't read their answer) with a confirm dialog offering the included formats as ticked checkboxes; included downloads run only once the paid one is answered (`download_signal` cookie) or its STEP job is done, so they can't beat the charge; buy dialog (`TOKENS_BUY_URL`); tokens off = the old page
- [x] **Download window** (replaces the per-part menus): one Download button per pulley card opens one window — parts list (everything shown, ticked), then a section per tier under a labelled divider (3 tokens · CAD shape / 2 tokens · 3D printing shape / 1 token · 2D drawings) with a box per format; the button shows the tokens the ticks cost; the 2D view offers drawings only. Everything goes into one zip (`bundles.py`, `POST /api/download/bundle`), charged once for the whole design at the highest tier ticked, built in the background from the existing routes; every file must belong to the registered design; a failed part refunds the lot; zip links carry a random 192-bit token and expire after an hour
- [x] Browser test `tests/browser/tokens_ui.js`: 29 checks with tokens on, 4 with tokens off; 8 zip tests in `tests/test_charging.py` (negative control on the design check)
- [ ] Remove the now-unused per-file token flow (`_cctGuardedDownload`'s priced path, `cctTokens.prepare`/`watch`, the included-downloads table) once the window has been used for a while — the old single-file download functions still back the add-in and remain as builders
- [ ] Add-ins: sign in (device token), send `Authorization: Bearer`, handle 401/402

### No local installs (ADR-008, decided 2026-09-24)
Every export runs on the server; the add-ins use the hosted app.
- [ ] Point the Fusion and FreeCAD add-ins at the hosted app instead of launching the local desktop app (port 5154)
- [ ] Add-in sign-in via device token (see Accounts and ledger)

### Retire once tokens are live
- [ ] Desktop build: `build_release*.py`, `prepare_release.py`, PyInstaller specs, launchers, `releases/`
- [ ] `licence.lic` / PyArmor licence flow, `/api/provision`, `subscribers.json`, the desktop licence-key activation routes
- [ ] Weekly trial download limit (`register_trial_download`)
- [ ] Dev backdoor (see Before Public Launch) — goes with the launcher licence check

### Hosting: moving to Google Cloud Run (decided 2026-09-25)
Render can't scale while a disk is attached (only one instance allowed), and every state
file (`logs/*.json`, queue sessions, trial counts) lives on that disk.
- [x] Host chosen: **Google Cloud Run** — billed only while handling requests (~$0.000024/vCPU-s), monthly free grant 180k vCPU-s / 360k GiB-s / 2M requests, scales to zero. (Azure Container Apps was chosen 2026-09-24 and dropped: the Microsoft account is Microsoft 365, not Azure. Also priced 2026-09-23: Railway, Render Pro; AWS App Runner closed to new customers.)
- [x] Google Cloud set up (2026-09-25): project `cheapcadtools` (number 925396938485, no organization), billing active, **$20/month budget alert**; Cloud Run, Cloud Build, Artifact Registry, Secret Manager and Cloud Storage enabled; backup bucket `gs://cheapcadtools-backups` (US multi-region, Standard, public access prevented, uniform access, lifecycle: delete after 90 days); `gcloud` on the dev PC signed in as xootme@gmail.com under its own named configuration `cheapcadtools` (Cloud Run region us-central1) — the default configuration used by the GA4 MCP project `ga4-mcp-495711` is untouched
- [ ] Optional later: Cloud Identity Free on cheapcadtools.com → an organization to own the project (move it in without redeploying)
- [x] Test service `pulley-test` on Cloud Run (https://pulley-test-925396938485.us-central1.run.app): `token-model` branch, service account `pulley-run`, 2 vCPU / 2 GiB, concurrency 4 (`WEB_CONCURRENCY=4`), max 3 instances, `TOKENS_ENABLED=1` in dev mode (no email — sign-in links go to Cloud Logging), `DATABASE_URL` from Secret Manager, `RESULTS_BUCKET=cheapcadtools-results`. End to end on 2026-09-25: sign in → 10 tokens → STL+SVG zip −2 → STEP upgrade −1 → STEP again free; files served from the bucket; 40 signed-in calls all 200
- [x] Fixed on the way: Postgres pool opened before gunicorn forked (preload_app) was shared by all workers, and Neon closes idle connections — cct_common 0.11.2 keys pools by process and checks each connection before use; warnings/errors now also go to stderr on Cloud Run (the log file vanished with the server); `.gcloudignore` so deploys upload 88 files, not the whole repo
- [x] Resend API key into Secret Manager (`RESEND_API_KEY`), then `CCT_ACCOUNTS_MODE=live` on the service — real sign-in emails, Secure cookies — done at launch (LAUNCH.md)
- [x] Free signup tokens for at most 2 new accounts per network per day (`TOKENS_SIGNUPS_PER_IP_PER_DAY`, default 2). Later accounts are still made — colleagues behind one office IP can all sign up and buy — with no free tokens and a note saying why. IPv6 counts per /64; sign-ins to existing accounts never count; IP kept hashed for a day. The account routes now take the IP from `request.remote_addr` via ProxyFix (`PROXY_HOPS`, default 1 = Cloud Run's own address) — they used the first X-Forwarded-For entry, which the visitor can forge
- [ ] When cheapcadtools.com moves in front of Cloud Run (Cloudflare Worker or a load balancer), set `PROXY_HOPS` to match and check `remote_addr` is the visitor's IP — too low and everyone shares the proxy's IP (the 2-signups limit would then block real customers), too high and the IP can be forged
- [x] Google sign-in live on the test site (client `925396938485-j3e1…apps.googleusercontent.com`, secret in Secret Manager); signed in end to end 2026-09-25
- [x] GitHub sign-in live on the test site (OAuth app client `Ov23liD7sewt5PZUURBB`); signed in end to end 2026-09-26. The service pins `GOOGLE_CLIENT_SECRET:1` — version 2 was the GitHub secret saved in the wrong slot (moved, and disabled there)
- [ ] Microsoft sign-in: register in Entra (see above), secret into `MICROSOFT_CLIENT_SECRET`, client id → `MICROSOFT_CLIENT_ID`
- [ ] At launch, so Google's consent screen says "CheapCAD Tools" instead of the site's address: verify cheapcadtools.com in Google Search Console; Branding page — name, logo, home page, privacy policy and terms links (both on cheapcadtools.com), authorized domain cheapcadtools.com; add the cheapcadtools.com redirect URI to the client; submit for brand verification (email-only scopes: no security review). Microsoft likewise shows "unverified" until a verified publisher domain is set
- [x] Privacy policy page on cheapcadtools.com (needed for Google/Microsoft branding, and for accounts in general) — alongside the terms of service — published 2026-09-26 (LAUNCH.md #7)
- [x] Custom domain for cheapcadtools.com's tool path (Cloud Run domain mapping or a load balancer), then move traffic from Render — done via the Cloudflare Worker (LAUNCH.md #6)
- [x] Secrets (Resend key, PayPal/Stripe keys, OAuth client secrets) in Secret Manager, mounted as env vars — not in the repo or plain env — done (LAUNCH.md production deploy)
- [x] Google OAuth app registered in the same project's console — done (Google sign-in live)
- [x] Ledger, accounts and registered designs on Postgres when `DATABASE_URL` is set
- [x] Result files (zips, async STEP) in Cloud Storage when `RESULTS_BUCKET` is set; finished jobs' status in the database when `DATABASE_URL` is set, so a status poll on another server finds it; exports run inside their request (`QUEUE_DISABLED=1` in the image — Cloud Run throttles CPU after the response). Job ids are 144-bit (were 8 hex digits, and the status answer carries the file link). Two containers sharing one Postgres: started on A, polled on B — works
- [x] Results bucket `gs://cheapcadtools-results` (us-central1, public access prevented, no soft delete, lifecycle: delete after 1 day); service account `pulley-run@cheapcadtools.iam.gserviceaccount.com` for Cloud Run with Storage Object Admin on that bucket only
- [ ] Queue sessions and trial counts stay per-server — fine on Cloud Run with the queue off and tokens on (trial limits only apply with tokens off)
- [x] Database: **Neon** Postgres project `cheapcadtools` (AWS US East 2, Postgres 17, database `neondb`), pooled connection string in Secret Manager as `DATABASE_URL`; `pulley-run` can read it. Checked through the pooler (advisory lock in a transaction, no prepared statements) and the full TokenStore on the direct host
- [x] Neon: move to the paid plan (7-day restore window) before real purchases are in the ledger — check the free plan's restore window — done 2026-09-26 (LAUNCH.md #8)
- [ ] Neon's pooler rejects the `options=-csearch_path` startup parameter — fine for the app (it never sets one), but tests against Neon must use the direct host
- [x] Dockerfile (python:3.14-slim + Cairo, non-root user, `PORT` from Cloud Run) and an allow-list `.dockerignore` — image 695 MB; the full suite passes inside it (1293 passed, 30 skipped)
- [x] `bin/small_step_linux` rebuilt from small_step 0.3.0 (static musl, `7c21b7e`): the committed Linux binary was still 0.2.0 from June, so Render has been serving STEP without the July geometry fixes — RELEASE.md's "rebuild on every push" step was missed
- [ ] Production (Render, `main`) still runs the 0.2.0 binary — ship the 0.3.0 binary to `main` when you next deploy
- [ ] One request per instance (exports are CPU-bound); max instances as a cost cap; decide min instances (cold start vs idle cost)
- [ ] Re-evaluate whether the session queue is still needed once autoscaling is in place
- [ ] Update `gunicorn.conf.py` comment — Render Standard is 2 GB now, not 1 GB

---

## Marketing — sample files

Post sample parts that say they were generated with CheapCAD Tools. Every download already
embeds the design (`/* CCT:{...} */`), and the web app's Import button restores it — so each
listing can say "open this file in CCT to change the tooth count/bore".

### Prepare
- [ ] Choose a sample set covering the main families (HTD, GT, T, imperial) plus spokes/flange/nubs showpieces
- [ ] For each: STEP + STL + DXF, a rendered image, and a short description with one "made with CCT" link
- [ ] Per-site referral links (`?ref=grabcad`, `?ref=printables`, …) so analytics shows which site sends customers
- [ ] Pick a licence — CC BY keeps the attribution attached (Printables/Thingiverse ask per upload)
- [ ] Read each site's self-promotion rules before posting; lead with a useful part, not an ad

### Where to post
- [ ] **GrabCAD** (STEP)
- [ ] **Printables**, **MakerWorld**, **Thingiverse**, **Thangs**, **Cults3D** (STL)
- [ ] **Onshape public documents**, **TraceParts / 3D ContentCentral** (STEP)
- [ ] **Autodesk Fusion community gallery / forum** — link back to the App Store listing
- [ ] **Sketchfab** — interactive 3D embed for the website
- [ ] **Reddit:** r/functionalprint, r/3Dprinting, r/Fusion360, r/FreeCAD, r/robotics; DXF samples for r/lasercutting, r/hobbycnc, r/CNC
- [ ] **Hackaday.io** / **Instructables** — a build write-up with the files attached
- [ ] **FreeCAD forum** — alongside the FreeCAD workbench

---

## Marketing — free, automatable

Priority order: 1 → 2 → (4 + 5 together with the token ledger). Automate content generation,
not community posting — bulk auto-posting breaks most sites' rules.

### 1. Programmatic SEO pages (highest value)
- [ ] Generate a static landing page per common config (e.g. "HTD-5M 20 tooth pulley 8 mm bore"): preview image, dimension table (OD, pitch diameter, teeth), free 2D preview, "Download STEP — 3 tokens" and "Open in CCT to customize" buttons
- [ ] Choose the config set (family × pitch × common tooth counts × common bores) — hundreds of long-tail pages
- [ ] Auto-generated `sitemap.xml` + schema.org structured data (Product / SoftwareApplication)
- [ ] Submit the sitemap to Google Search Console

### 2. Every file shared is an advert
- [ ] "Made with CheapCAD Tools" in STEP product name (`rename_step_product`), DXF comment, STL header
- [ ] "Share this design" button → link that restores the design (`loadParamsFromUrl` already exists)
- [ ] Branded filenames (e.g. `HTD-5M-20T_cct.step`)
- [ ] (Optional) small engraved logo, removable as a paid option

### 3. Embeddable free calculators
- [ ] Belt length, center distance, drive ratio, tooth-count picker — reuse the existing geometry code
- [ ] Embeddable via `<iframe>`/JS snippet, each with a link back to the tool

### 4. Automated email (Resend already wired)
- [ ] Onboarding sequence after signup: day 0 (how tokens work), day 3 (spokes/flanges), day 7 (open your file in Fusion/FreeCAD)
- [ ] Low-balance and unused-token reminders
- [ ] Monthly "what's new" built from release notes

### 5. Referral tokens
- [ ] "Give 10, get 10" tokens per referred signup — runs on the token ledger, costs tokens not cash

### 6. Auto-rendered video and images
- [ ] Headless Three.js or Blender renders turntable videos/images of showcase pulleys from STL
- [ ] Scheduled posting to YouTube Shorts and Pinterest via their free APIs

### 7. Model-site uploads
- [ ] Script that builds each upload package (files, images, description, `?ref=` link)
- [ ] API upload where supported (Thingiverse, Sketchfab, Onshape public docs); post manually or in small batches elsewhere (GrabCAD, Printables)

### 8. Mention alerts (reply by hand)
- [ ] F5Bot (Reddit / Hacker News) + Google Alerts for "timing pulley generator", "GT2 pulley STL", "pulley STEP file", etc.
- [ ] Reply manually with genuinely helpful answers — never auto-reply

### 9. AI-assistant discovery
- [ ] Once the headless API + OpenAPI spec exist (see Agent / Headless API Access), publish an MCP server and list it in MCP registries — every agent call is a paid export

### 10. One-time listings
- [ ] AlternativeTo, Product Hunt launch, GitHub awesome-lists (3D printing / CAD), FreeCAD Addon Manager, Autodesk App Store listing keywords

---

## Bug reports → GitHub (do before adding the token to Render)
Production files no GitHub issues today: Render has no `FEEDBACK_GITHUB_PAT`.
- [x] `/api/report-bug` fixed (2026-09-26): the GitHub issue comes from `cct_common.bug_report` (description only — no design, no address); the notification email goes through Resend with the address but not the design; the full report is kept in the log and, with `DATABASE_URL`, in the `bug_reports` table (`bug_store.py`), so it survives Cloud Run servers; desktop forwarding removed. Tests: `tests/test_bug_report_privacy.py` (5 of 6 fail against the old route)
- [x] Admin dashboard: read bug reports from the `bug_reports` table as well as the log, and add a delete button (a deletion request must reach the database copy) — done: Bug reports tab, ADR-012
- [x] Then on Render: `FEEDBACK_GITHUB_PAT` (fine-grained, Issues read/write on `xootme/cct-feedback` only) and `FEEDBACK_GITHUB_REPO=xootme/cct-feedback` — done on Cloud Run instead (LAUNCH.md #4b)
- [x] cct_common's bug reporter verified live 2026-09-24: a report from E-Box Designer became xootme/cct-feedback#1 with no design or email in it

## Before Public Launch
- [x] **Remove dev backdoor password `'xoot'`** — the server side is already gone (dropped by `cct_common.licensing`). Still present in:
  - `packaging/launcher.py:60` and `packaging/launcher_ss.py:55` (still grant access locally)
  - Fusion add-in `DEV_BACKDOOR_KEY`: `Fusion Addins/PulleyWebApp/PulleyWebApp.py`, `CCT_Addins/fusion360/TimingPulley/PulleyWebApp.py`, and its `dist/PulleyWebApp` + `dist/PulleyWebAppTrial` copies — done 2026-09-27 in both launchers and both add-in sources (backups of the add-in files were kept outside the repo)
- [ ] Rebuild the Fusion add-in `dist/PulleyWebApp` and `dist/PulleyWebAppTrial` copies — they still carry the `'xoot'` backdoor key (the server ignores it); don't hand-edit build output
- [ ] Add a test that `/api/provision` returns 403 for an unknown user (no test covers it) — or drop it with the token model

## Release and manual checks
The last desktop release (1.2.1, 2026-07-01) predates the cct_common migration (2026-07-16 → 07-23).
- [ ] New desktop release: `build_release_ss.py` → GitHub Release → `prepare_release.py` → Render env vars → add-in update banner appears in Fusion
- [ ] Manual (can't be checked from code): Render shows the latest commit live; 3D preview renders in a browser
- [ ] Manual: Fusion add-in provisions, launches the desktop app, and auto-imports STEP/STL from both the desktop and online apps
- [ ] Manual: FreeCAD add-in download + import
- [ ] `exporters/addin_helpers.py` is tested (`tests/test_addin_helpers.py`) but neither add-in uses it — wire it in or delete it

## CAD add-in update flow — use cct_common's shutdown/live-reload APIs
Both add-ins still force-kill the desktop server instead of calling `POST /api/shutdown`
(see EBoxDesigner D036 for the zombie-reloader failure this avoids).
- [ ] **Fusion 360:** `PulleyWebApp.py:372` runs `taskkill /F /IM PulleyApp.exe` → call `/api/shutdown` first
- [ ] **FreeCAD:** `cct_pulley/commands.py:373` (`taskkill`) and `cct_pulley/server.py:123` (`terminate()`) → same change
- [ ] Confirm an open browser tab reloads via `/_cct_live_reload.js` across an add-in-triggered update

## Help pictures, spoke fit, print compensation (2026-09-26)
Done: help pictures + hover pop-ups (ADR-011), "Apply to both pulleys" in Advanced, spoke
settings fitted with warning + Auto-fit (ADR-010), 3D Print Compensation as a true offset
(ADR-009). Tests: `test_help_pictures.py`, `test_spoke_fit.py`, `test_print_compensation.py`,
`tests/browser/help_ui.js`.
- [x] **Backlash "Tight" does nothing** — `effective_backlash = max(0.0, backlash)` in
      `generate_imperial_groove` (and similar clamps) turns every negative backlash into 0, so
      Tight = Standard on Imperial, T, AT, GT, RPP while the panel says e.g. "Offset: −0.340 mm".
      Decide the fix, then add Tight back to the Backlash help picture. — FIXED 2026-09-27: negative backlash narrows the groove, stopping before the floor closes (tests/test_backlash_tight.py); Tight is back in the Backlash picture
- [ ] Release note: designs with 3D Print Compensation > 0 change shape (ADR-009).
- [ ] Release note: Imperial, T and AT designs using Backlash **Tight** now get the narrower groove Tight always promised (it had the Standard groove) — STL and STEP both change. Set-screw holes are sized by how the screw holds (ADR-013; STL now, STEP later).
- [ ] 3D hub lock: with spokes on, the 3D Hub OD field copies the spokes' typed Hub OD, so until
      Auto-fit is clicked the 3D hub uses the typed value while the spokes use the fitted one.
- [ ] Point `fuzz_pulley.py` at `geometry/spoke_fit.py`: the geometry must never raise on its output.
- [ ] `/api/validate-spoke-fillets` is no longer called by the page — remove once nothing else uses it.
- [ ] Share links don't carry "Apply to both pulleys" (settings values only).
- [ ] 3D Mode help picture is cropped from old (Ver. 0.1) screenshots — retake from the current app.
- [x] Hub (3D) pictures: Hub Height/OD, Retention Method, Set Screw Size, Number of Screws
      (one per method — the hover follows the Retention Method), Flat Depth, Keyway W and
      Hub Depth (3D preview + STL sections). The pop-up now shows pictures at their own size.
- [x] Set-screw holes sized by how the screw holds (ADR-013, 2026-09-27): threaded = self-tapping bore from the new *Threaded screw holes* dialog (round % / hex %, E-Box defaults), captured nut = ISO 273 clearance hole + that size's nut, heat-set insert = entered hole; M2–M10 and inch sizes, Custom; old links keep their nominal holes
- [ ] **STEP set-screw holes (small_step)** — handed over in SMALL_STEP_HANDOFF.md: small_step still cuts `hub_screw_dia` (nominal) for every hold — give it the hole from `geometry/set_screw.py` (round diameter, or a radial hex prism with a corner up for hex; clearance for nuts; the insert hole) and the named nut's pocket. Held until the small_step agent is ready; update Hub_help's STEP note when done
- [x] Rebuild the Hub help pictures for ADR-013: `hub_screw_size.svg` shows nominal holes (its build script fetches `hub_screw_dia` links, which still cut those), and `hub_retention.svg` has no heat-set insert — done 2026-09-27 (build_hub.py fetches the set-screw sources itself now)
- [x] A hub OD restored from a link or file is replaced by 2 × bore (`_initHubDefaults` only spares a hand-typed value) — seen restoring hub_od=26 with an 8 mm bore — FIXED 2026-09-27: link/file values and typed values are kept (tests/browser/screw_ui.js)
- [x] **Fixed: standard set-screw holes went through both sides of the hub** in the STL and 3D
      preview (both trimesh paths in `exporters/step_exporter.py`, `hole_len = 2·R_hub + 2`);
      now OD → bore only, matching small_step's STEP and the cadquery path. Help text that
      said "through the full hub diameter" / "through-hole" corrected.
      Test: `tests/test_hub_setscrew.py` (negative-controlled).
- [ ] D-Shaft / Keyway "Add Set Screw" options have no pictures of their own yet.
- [x] Flanges pictures: type (metal vs 3D print), 3D-print shape, metal shape (the Angle /
      Rim Radius hover follows the 3D Print box), top flange separate, gluing nubs, print
      supports. Help text corrected: Flange Height is the thickness at the teeth (not the lip);
      supports are fins inside a tube (not ribs from the pulley OD); downloads section rewritten
      (no separate metal plate or flange STEP downloads exist).
- [x] **"Flange cuts hub" does nothing** — `flange{n}_cuts_hub` is never sent to the server and
      nothing reads it, but the help says it decides whether the flange trims the hub or the
      reverse. Wire it up or remove the box (and its help paragraph). No picture until then. — removed 2026-09-27: its warning was never shown, and the app already keeps the flange whole (the hub starts on top); help rewritten
- [ ] Metal flanges: `/download/flange-stl` always returns both plates (`which='both'`,
      "-flanges"); fine now that the page never asks it for one plate — check nothing else does.
- [x] Several flanged STLs are not watertight (metal assembly, 3D-print merged / separate-top
      pulley, support assembly — see `tools/help_illustrations/flange/`); overlapping bodies
      rather than one union, probably. Check whether slicers mind. — FIXED 2026-09-27: metal plate profile, bore overcut, printed flanges unioned with the pulley (tests/test_flange_watertight.py); the metal assembly stays three closed parts
- [x] Spoke Height (3D) picture; help corrected (it is the web's thickness, centred, with pockets
      above and below — the faces are not left solid).
- [ ] Help pictures still missing: the Download window. Other help pages still say "3D Features"
      (the old name for 3D Mode).
- [x] **Captured-nut hub STL is not watertight** (HTD 5M 24T, bore 12, hub OD 26 × 12, M5 ×1
      captured nut — `tools/help_illustrations/hub/screw_nut.stl`); the other four retention
      methods are. Slicers usually repair it, but find where the nut pocket leaves an open edge. — FIXED 2026-09-27: the nut pocket overlaps the bore by _POCKET_OVERLAP (tests/test_set_screw_holes.py)

## Splined bores — what's next (ADR-017)
- [ ] **Set screw into a spline root**: reuse `cct_common.screws` and `geometry/set_screw.py`; the only
      new part is the angle — aim the screw at the middle of a space (hub slot) so it bottoms on the
      shaft's root (the shaft's minor) rather than a tooth flank. Common code for every splined part.
- [ ] **Clamp hub** (slot + screw boss): a slot through the hub wall and a tangential screw across it —
      holds the part axially and takes out backlash at once; best for printed gears. The most work:
      the boss, the slot through a spline, screw sizes from `cct_common.screws`. Common code.
- [ ] small_step: §7 + §8 of SMALL_STEP_HANDOFF (splined bore, fitted, with the ring counterbore)
- [ ] STEP of the sample shaft and the splined washer (both are extrusions; see §8)
- [ ] A metal flange plate or separate top flange over the ring's face: warned today; cut the plate /
      flange with the counterbore instead?

## small_step known issues
Full repros in `C:\Users\cmyer\Documents\small_step\STEP_SOLUTIONS.md`.
- [ ] FreeCAD rejects complex pulley profiles — small_step emits no SURFACE_CURVE/PCURVE (§5, "open obligation"). Fusion/eDrawings are fine.
- [ ] Wire-order `EDGE_LOOP` bug on a spoke + metal-flange STD-2M 74T pulley — "NOT YET INVESTIGATED" (2026-07-22 entry)
- [ ] Self-crossing unfilleted spoke void in the "hub overlap" regime (wide spoke vs hub radius) — "NOT fixed" (2026-07-22 bowtie entry).
      Likely avoided now: the spoke fit (ADR-010) rejects self-crossing openings and fits around them — confirm with the repro.

## Load Testing Dashboard
`record_benchmarks.py` and the WSL gunicorn concurrency check exist; the simulator does not.
- [ ] High-load simulation with a real-time dashboard
  - Adjustable number of simulated concurrent users
  - Per-user behaviour: download frequency, 2D/3D parameter changes, belt/hub/spoke modifications
  - Live view of each simulated user's current action
  - Requests/sec, error rate, p95 latency, per-endpoint breakdown

## Agent / Headless API Access
Nothing below exists yet. One API serves agents and CAD plugins alike.

**Core API:**
- [ ] `GET /api/capabilities` — families, pitches, output formats, hub/spoke feature flags
- [ ] `GET /api/describe?family=X&pitch=Y` — parameter constraints and download URL template
- [x] JSON errors on all 400 responses — the SVG/DXF routes still return plain text (`app.py` 968, 981, 1028, 1041, 1074, 1086, 1119) — done 2026-09-27: `_api_error`; tracebacks go to the log, never the response (tests/test_api_errors.py)
- [ ] OpenAPI 3.0 spec at `/api/openapi.json`
- [ ] (Optional) MCP server wrapper

**CAD plugin prerequisites:**
- [ ] CORS on `/api/` and `/download/` routes — today only `/api/admin/` and `/api/subscribers/` have it
- [ ] `Content-Security-Policy: frame-ancestors` for embedded-UI mode (webview/iframe in a CAD task pane)
- [ ] API versioning (`/api/v1/`) — plugins ship and rarely update
- [ ] Freeze public parameter names (`bore`, `teeth`, etc.)

**Three plugin integration modes (all share the same server API):**
1. **Embedded UI** — iframe/webview of `cheapcadtools.com/tools/pulleys`
2. **Direct download** — plugin reads CAD context, calls `/download/step|dxf|stl`, imports the file
3. **Agent-mediated** — CAD-native AI reads the OpenAPI spec and calls endpoints

---

## CAD Plugin Roadmap (excluding Fusion 360 and Onshape)

### SolidWorks — Listener App
`solidworks_listener/listener.py` + `SolidWorksListener.spec` exist. Tray app, file-watch
import via `GetActiveObject("SldWorks.Application")` → `OpenDoc6()`; Flask mirrors downloads
via `_mirror_to_solidworks()`.
- [ ] Test with an actual SolidWorks installation — verify `OpenDoc6` param types (byref VARIANTs in pywin32)
- [ ] Handle SolidWorks launching *after* the listener (`listener.py:80` only attaches once, no retry)
- [ ] Optionally auto-launch SolidWorks (`win32com.client.Dispatch`)
- [ ] Installer (NSIS / Inno Setup) or zip; startup entry in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- [ ] Distribution: direct download from `cheapcadtools.com`

### FreeCAD — Workbench
Built: `CCT_Addins/FreeCAD/TimingPulley` (`InitGui.py`, `package.xml`, `cct_pulley/`, tests).
- [ ] Publish through the FreeCAD Addon Manager

### AutoCAD — Listener App or .NET Plugin
No code yet.
- [ ] Decide: listener app (COM `AutoCAD.Application` → `Import`, same pattern as SolidWorks) or .NET plugin (`PaletteSet` + WebView2, `FileSystemWatcher`)
- [ ] Build the chosen option
- [ ] Distribution: Autodesk App Store (same account as Fusion) or direct download

### Other Platforms (research only)
- **Inventor** — COM automation like AutoCAD; could reuse the listener approach
- **Rhino / Grasshopper** — RhinoCommon Python; a component could call `/download/step`
- **CATIA, NX** — heavyweight SDKs; skip for v1
