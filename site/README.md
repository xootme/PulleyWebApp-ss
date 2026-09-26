# site/

WordPress page content kept under version control. Each `*.wp.html` file is
one Custom HTML block, pasted (or published over SSH with WP-CLI) as the
whole content of its page; `*.preview.html` opens it standalone in a browser.

- `privacy-policy.wp.html` — page ID 3, /privacy-policy/ (rewritten for the
  token model; publish at launch, not before — the live tool is still the
  pre-token version on Render)
- `terms.wp.html` — a new page, /terms/ (none exists yet; WooCommerce's
  draft "Refund and Returns Policy", page 10, can be deleted or pointed
  here). Same timing: publish at launch. [Bracketed] text marks decisions
  still to make (refund rules, tokens on account closure)

## Published 2026-09-26 — "no local version"

Pages 75 (Pulleys), 94 (Standalone Tools) and 98 (CAD Plug-Ins) edited to say
there is no longer a local version; products 142 (FreeCAD add-in licence) and
91 (Pro Plan subscription) set to Draft. Originals of every changed page are in
`backup-2026-09-26/` (restore with `wp post update <ID> <file>`). Note: page 75's
URL, /tools/pulleys/, is routed by Cloudflare to the app, so visitors never see
that WordPress page.

Still to update at launch (token era): Home — "No account required"; About —
"in the Beta now, so it's all free"; CAD Plug-Ins — "subscriptions sold within
the CAD software's marketplace".

## Cloudflare Worker (live since 2026-09-26)

- `worker-cloudrun.js` — the live `cct-tools-router` script (Cloud Run + edge secret).
- `worker-backup-2026-09-26.js` — the previous Render script.
- `cf_deploy_worker.py new|rollback` — uploads either one through the Cloudflare API:
  ```
  { gcloud --configuration=cheapcadtools secrets versions access latest --secret=CLOUDFLARE_API_TOKEN; echo;
    gcloud --configuration=cheapcadtools secrets versions access latest --secret=EDGE_SECRET; echo; } \
    | python site/cf_deploy_worker.py rollback
  ```
