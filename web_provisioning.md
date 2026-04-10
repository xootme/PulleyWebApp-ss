# Web Provisioning & Deployment (Final Architecture)

**Platform:** Render.com (Containerized Cloud Hosting)  
**Primary URL:** `https://tools.cheapcadtools.com`  
**Repo:** `https://github.com/xootme/PulleyWebApp`  
**Branch:** `main`

---

## 1. Current Architecture: Subdomain Strategy

We have moved away from subfolder routing (`/tools/pulleys`) and GreenGeeks CGI to a dedicated subdomain strategy. This bypasses Cloudflare's free-tier rule limits and GreenGeeks' restrictive LiteSpeed environment.

**How it works:**
1. **GitHub** acts as the source of truth.
2. **Render** automatically deploys every push to the `main` branch.
3. **Cloudflare DNS** routes `tools.cheapcadtools.com` to Render via a CNAME record.

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

### Step 3 — Commit and push

```bash
git add .
git commit -m "Describe your update"
git push origin main
```
*Render will detect the push and go live within ~2 minutes.*

---

## 3. Infrastructure Settings

### DNS (Cloudflare)
The following record is required for the subdomain to function:
*   **Type:** `CNAME`
*   **Name:** `tools`
*   **Target:** `pulleywebapp.onrender.com`
*   **Proxy Status:** Proxied (Orange Cloud)

### Render Configuration
*   **Runtime:** `Python 3`
*   **Build Command:** `pip install -r requirements.txt`
*   **Start Command:** `gunicorn app:app`
*   **Custom Domain:** `tools.cheapcadtools.com` (Added in Render Settings)

---

## 4. Why we chose this (Architecture Lessons)

*   **Bypassing LiteSpeed (GreenGeeks):** Shared hosts kill Python processes that output to `stderr` and break `venv` permissions during security sweeps. Render's isolated containers prevent this.
*   **Removing ProxyFix:** Since we aren't using a subfolder reverse-proxy, the complex `ProxyFix` middleware and `SCRIPT_NAME` hacks were removed from `app.py` for better stability.
*   **Cloudflare Rule Limits:** Cloudflare only allows 3 free Page Rules. Using a subdomain saves these rules for other critical site functions.

---

## 5. Adding New Tools
To add a new tool (e.g., "Gear Generator") to the Hub:
1. Create the new code in a subfolder or as a Flask Blueprint.
2. Update `app.py` with the new route.
3. Push to GitHub.
4. The new tool will instantly be available at `tools.cheapcadtools.com/gears`.
