# Web Provisioning & Deployment (Final Architecture)

**Platform:** Render.com (Containerized Cloud Hosting)  
**Primary URL:** `https://cheapcadtools.com/tools/pulleys`  
**Repo:** `https://github.com/xootme/PulleyWebApp`  
**Branch:** `main`

---

## 1. Current Architecture: Cloudflare Worker + Render

Tools are served at `cheapcadtools.com/tools/<slug>` using a Cloudflare Worker to route requests
from the main domain to individual Render services. GreenGeeks continues to serve the main
`cheapcadtools.com` website — the Worker intercepts only paths it recognises and passes everything
else through to GreenGeeks unchanged.

**How it works:**
1. **GitHub** acts as the source of truth — every push to `main` triggers a Render redeploy.
2. **Render** hosts each tool as an independent web service (Python/Flask + gunicorn).
3. **Cloudflare Worker** (`cct-tools-router`) intercepts all requests to `cheapcadtools.com/*`
   and routes tool paths to the correct Render service.

```
User → cheapcadtools.com/tools/pulleys
     → Cloudflare Worker (cct-tools-router)
     → pulleywebapp.onrender.com
     → Response served at cheapcadtools.com/tools/pulleys
```

---

## 2. Deployment Checklist

Because we have linked the **Windows Credential Manager** to the local environment, deployments are fully automated once the checklist is complete.

### Step 1 — Review the diff
Run the following to see everything that changed since the last commit:

```bash
git diff --stat HEAD
git diff HEAD
```

### Step 2 — Update docs to match the diff
Before committing, ensure these files reflect every user-facing change:

| File | Update when… |
|---|---|
| `static/Pulley1_help.html` | Pulley 1 panel controls, download options, or bore limits change |
| `static/Pulley2_help.html` | Pulley 2 panel controls, download options, or bore limits change |
| `static/TwoPulleyDrive_help.html` | Drive bar, spacing/ratio controls, or belt export options change |
| `ToDo.md` | Any backlog item is completed or a new item is added |
| `web_provisioning.md` | Deployment process or infrastructure changes |

**Checklist:**
- [ ] New or changed features described in the relevant help file(s)
- [ ] Removed features or "coming soon" stubs cleared from help files
- [ ] Minimum/maximum input values updated if limits changed
- [ ] Download format options (SVG / DXF) accurate in all three help files
- [ ] `ToDo.md` Completed section updated

### Step 3 — Run performance benchmarks

Before pushing, record a benchmark snapshot against the current code.
Results are appended to `Perf_History.csv` (committed alongside the code change).

```bash
.venv312/Scripts/python record_benchmarks.py
```

The script prints a summary table and appends one row per benchmark to `Perf_History.csv`
tagged with the current commit hash and timestamp.  Review the table for unexpected
regressions before proceeding — a 2× slowdown in an STL test is worth investigating.

**Checklist:**
- [ ] `record_benchmarks.py` ran without errors (exit 0)
- [ ] No test regressed more than ~30% vs the previous entry in `Perf_History.csv`
- [ ] `Perf_History.csv` staged for commit

### Step 4 — Commit and push

```bash
git add .
git commit -m "Describe your update"
git push origin main
```
*Render will detect the push and go live within ~2 minutes.*

---

## 3. Infrastructure Settings

### Cloudflare Worker — `cct-tools-router`
- **Route:** `cheapcadtools.com/*` (catches all requests — Worker decides pass-through vs. route)
- **Zone:** `cheapcadtools.com`

Worker script routes the following paths to Render; everything else passes through to GreenGeeks:

```javascript
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Tool routing table — add one line per new tool
    const tools = {
      '/tools/pulleys': 'https://pulleywebapp.onrender.com',
    };

    if (path === '/tools' || path === '/tools/') {
      return fetch('https://tools-hub.onrender.com/', request);
    }

    for (const [prefix, origin] of Object.entries(tools)) {
      if (path.startsWith(prefix)) {
        const stripped = path.slice(prefix.length) || '/';
        const target = new URL(origin);
        target.pathname = stripped;
        target.search = url.search;
        return fetch(new Request(target.toString(), request));
      }
    }

    // Render-only paths (static assets, API, downloads) — route to Render unconditionally
    const renderOnly = ['/static/', '/api/', '/download/', '/preview/'];
    if (renderOnly.some(p => path.startsWith(p))) {
      return fetch('https://pulleywebapp.onrender.com' + path + url.search, request);
    }

    // Everything else — pass through to GreenGeeks
    return fetch(request);
  }
}
```

> **Important:** The route must be `cheapcadtools.com/*` (not `/tools*`). The Worker handles
> pass-through to GreenGeeks for all non-tool paths, so this is safe.

### Render Configuration (per tool service)
- **Runtime:** Python 3
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn app:app`
- **Custom Domain:** none required — Worker handles routing

### DNS (Cloudflare)
No CNAME record for `tools` is required. The Worker runs on the root domain proxy.

---

## 4. Adding a New Tool

1. Create the tool as a standalone Flask app in its own GitHub repo
2. Deploy to Render (connect the new repo, start command: `gunicorn app:app`)
3. Add one line to the Worker's `tools` object:
   ```javascript
   '/tools/gears': 'https://gearapp.onrender.com',
   ```
4. Click **Save and Deploy** in the Cloudflare Worker editor
5. The tool is live at `cheapcadtools.com/tools/gears`

See `tools_hub_architecture.html` for the full automated deployment plan (GitHub Actions + registry).

---

## 5. Architecture Lessons

- **Cloudflare Workers ≠ Page Rules.** Workers have 100,000 free requests/day with no script
  limit. The 3-rule cap applies only to Page Rules — a separate product.
- **Route must be `/*` not `/tools*`.** Static assets (`/static/`), API calls (`/api/`), and
  downloads (`/download/`) are requested at the root path by the browser. The Worker must see
  these to forward them to Render.
- **GreenGeeks pass-through is safe.** The Worker's final `return fetch(request)` forwards
  unmatched requests to GreenGeeks unchanged — the main website is unaffected.
- **Tool isolation.** Each tool is a separate Render service. A crash in one tool does not
  affect any other tool or the main website.
