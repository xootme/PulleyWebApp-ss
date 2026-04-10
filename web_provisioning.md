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

## 2. One-Command Deployment

Because we have linked the **Windows Credential Manager** to the local environment, deployments are fully automated.

**To deploy a change:**  
From your local terminal (WSL, Git Bash, or VS Code):

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
