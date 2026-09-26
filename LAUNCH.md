# Launch runbook — the token model on Google Cloud Run

Moves cheapcadtools.com/tools/pulleys from Render (free trial model) to
Cloud Run (accounts + tokens, ADR-008). Nothing here is live until step 6.
Written 2026-09-26; update as steps are done.

## Status

| # | Blocker | Who | State |
|---|---------|-----|-------|
| 1 | Bug reports put email + design in GitHub issues | Claude | **done** (`dc2658a`) |
| 2 | Forged X-Forwarded-Host could hijack sign-in links | Claude | **done** (`df5461d`, `edge_proxy.py`) |
| 3 | Test site shared the production database | Claude | **done** (2026-09-26): test site on `cct_test` (secret `DATABASE_URL_TEST`); production `neondb` emptied of the test-era data with the owner's go-ahead (backup kept locally) |
| 4 | Buying tokens: PayPal live, Micropayments rate | Owner | **live app done** (2026-09-26; webhook `6P162379A7315931K` created by API); Micropayments rate still to request |
| 4b | Bug-report GitHub token | Owner | **done** (2026-09-26; fine-grained, private access to `cct-feedback` only — checked) |
| 5 | Google/GitHub sign-in on the real domain | Owner | **done** (2026-09-26; Google checked for both addresses; GitHub is checked at the end-to-end sign-in) |
| 6 | Cloudflare Worker: route `/account/`, send edge headers, point at Cloud Run | Claude, via API token | token works; rollback copy `site/worker-backup-2026-09-26.js`; switch after production passes |
| 7 | Privacy policy + terms | Owner, then Claude publishes | **ready** (2026-09-26: store and standalone text removed — nothing was ever sold); publish at launch; a lawyer's read recommended |
| 8 | Neon paid plan (7-day restore) | Owner | **done** (2026-09-26; Launch plan, history window 7 days) |
| 9 | Admin dashboard reads bug reports from the database | Claude | open (not blocking) |

## Owner steps

### 4 — PayPal live
1. developer.paypal.com → **Live** toggle → Apps & Credentials → Create App ("CheapCAD Tools", Merchant).
2. Client ID → give to Claude. Secret → Secret Manager, new secret **`PAYPAL_LIVE_CLIENT_SECRET`** (Claude creates the empty slot).
3. On that app: Webhooks → Add Webhook → `https://cheapcadtools.com/api/payments/paypal/webhook`,
   events: Payment capture completed / refunded / reversed → Webhook ID to Claude.
4. Ask PayPal for the **Micropayments** rate on the business account (before or soon after launch).

### 4b — Bug-report issues
A fine-grained GitHub token with Issues: read/write on `xootme/cct-feedback` only →
Secret Manager, new version of **`FEEDBACK_GITHUB_PAT`** (slot exists). Revoke the
token that was pasted into chat on 2026-09-24 and make a fresh one.

### 5 — Sign-in on the real domain
Add a second redirect URI to each existing client (keep the test one):
- Google: console.cloud.google.com/auth/clients?project=cheapcadtools → the client →
  `https://cheapcadtools.com/account/oauth/google/callback`
- GitHub: github.com/settings/developers → CheapCAD Tools → Add redirect URI →
  `https://cheapcadtools.com/account/oauth/github/callback`

### 6 — Cloudflare Worker (`cct-tools-router`)
1. Worker → Settings → Variables and Secrets → add **secret** `EDGE_SECRET` with the
   value of Secret Manager's `EDGE_SECRET` (open it in the console → Actions → View secret value).
2. Replace the script with the one below, **after** the production service is deployed
   (Claude: step "Deploy production" below) — it points at that service.
3. Save and deploy. Rollback: the previous script is in `CCT_Architecture.md` §10.

```javascript
// cct-tools-router — routes the tools to Cloud Run, everything else to GreenGeeks.
// The app trusts the forwarding headers only with the X-CCT-Edge secret (edge_proxy.py).
const PULLEYS = 'https://pulley-925396938485.us-central1.run.app';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/tools' || path === '/tools/') {
      return fetch('https://tools-hub.onrender.com/', request);
    }
    if (path.startsWith('/tools/pulleys')) {
      return toApp(request, env, PULLEYS, path.slice('/tools/pulleys'.length) || '/', url);
    }
    // The app's own root paths: assets, API, downloads, sign-in and buy pages.
    const appPaths = ['/static/', '/api/', '/download/', '/preview/', '/admin', '/account/'];
    if (appPaths.some(p => path.startsWith(p))) {
      return toApp(request, env, PULLEYS, path, url);
    }
    return fetch(request);                       // GreenGeeks (WordPress)
  },
};

function toApp(request, env, origin, path, url) {
  const target = new URL(origin);
  target.pathname = path;
  target.search = url.search;
  const headers = new Headers(request.headers);
  // Set, not append: whatever the visitor sent in these is discarded.
  headers.set('X-Forwarded-For', request.headers.get('CF-Connecting-IP') || '');
  headers.set('X-Forwarded-Host', url.host);
  headers.set('X-Forwarded-Proto', 'https');
  headers.set('X-CCT-Edge', env.EDGE_SECRET);
  return fetch(target.toString(), {
    method: request.method,
    headers,
    body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
    redirect: 'manual',                        // the app's redirects go to the browser
  });
}
```

Note `/account/` is new: sign-in links, the Google/GitHub return address, the
buy page and the add-in approval page all live there. WordPress's own
account page is `/my-account/`, so nothing collides.

### 7 — Policy pages
Decide the store refund rule (terms §4 bracket); say "publish" — Claude sets the
effective dates, replaces page 3, creates /terms/, drafts page 53, purges the cache.

### 8 — Neon
console.neon.tech → the project → Billing → the Launch plan (7-day restore window).

## Deploy production (Claude, once 4–5 are done)

```bash
gcloud --configuration=cheapcadtools run deploy pulley --source . --region us-central1 \
  --service-account pulley-run@cheapcadtools.iam.gserviceaccount.com \
  --cpu 2 --memory 2Gi --concurrency 4 --max-instances 10 --min-instances 0 --timeout 300 \
  --set-env-vars "WEB_CONCURRENCY=4,TOKENS_ENABLED=1,CCT_ACCOUNTS_MODE=live,CCT_BUG_REPORT_MODE=live,\
RESULTS_BUCKET=cheapcadtools-results,SITE_URL=https://cheapcadtools.com/tools/pulleys,\
GOOGLE_CLIENT_ID=925396938485-j3e1ga1nl83ajllacf0mgkcl4bfad1vb.apps.googleusercontent.com,\
GITHUB_CLIENT_ID=Ov23liD7sewt5PZUURBB,PAYPAL_CLIENT_ID=BAAB9kB2oNSLBZtrCHK-iig6iMmBtVZnWJZaxsRRrjIUKeowPlVEzxPrLpM4QpUKVyKvV4Llhpk5Af5688,PAYPAL_WEBHOOK_ID=6P162379A7315931K,\
PAYPAL_LIVE=1,FEEDBACK_GITHUB_REPO=xootme/cct-feedback" \
  --set-secrets "DATABASE_URL=DATABASE_URL:latest,RESEND_API_KEY=RESEND_API_KEY:latest,\
GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:1,GITHUB_CLIENT_SECRET=GITHUB_CLIENT_SECRET:latest,\
PAYPAL_CLIENT_SECRET=PAYPAL_LIVE_CLIENT_SECRET:latest,EDGE_SECRET=EDGE_SECRET:latest,\
FEEDBACK_GITHUB_PAT=FEEDBACK_GITHUB_PAT:latest" \
  --allow-unauthenticated
```

Then, before switching the Worker: sign in, buy the smallest pack with a real card,
download, refund it, and check the ledger and the bug-report path end to end on the
`pulley-…run.app` address.

## After the switch
- Merge `token-model` into `main` and push only when retiring Render (it deploys Render).
- Watch Cloud Logging for errors for the first days; Render stays as the rollback
  (revert the Worker script) until the new site has run cleanly for a week.
