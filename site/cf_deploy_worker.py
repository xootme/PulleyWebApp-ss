"""stdin: CLOUDFLARE_API_TOKEN, newline, EDGE_SECRET (the latter only for the
new script). Uploads a Worker script to cct-tools-router.
  python cf_deploy_worker.py new       -> site/worker-cloudrun.js + EDGE_SECRET binding
  python cf_deploy_worker.py rollback  -> site/worker-backup-2026-09-26.js, no bindings
Prints no secrets."""
import json
import sys

import requests

SITE = r"C:\Users\cmyer\Documents\PulleyWebApp-ss\site"
API = "https://api.cloudflare.com/client/v4"
mode = sys.argv[1]
lines = sys.stdin.read().strip().splitlines()
h = {"Authorization": f"Bearer {lines[0].strip()}"}
acct = requests.get(f"{API}/accounts", headers=h, timeout=20).json()["result"][0]["id"]

if mode == "new":
    script = open(f"{SITE}\\worker-cloudrun.js", "rb").read()
    bindings = [{"type": "secret_text", "name": "EDGE_SECRET", "text": lines[1].strip()}]
elif mode == "rollback":
    script = open(f"{SITE}\\worker-backup-2026-09-26.js", "rb").read()
    bindings = []
else:
    sys.exit("new | rollback")

meta = {"main_module": "worker.js", "compatibility_date": "2026-04-10", "bindings": bindings}
r = requests.put(f"{API}/accounts/{acct}/workers/scripts/cct-tools-router", headers=h, timeout=60,
                 files={"metadata": (None, json.dumps(meta), "application/json"),
                        "worker.js": ("worker.js", script, "application/javascript+module")})
body = r.json()
print(mode, "upload:", r.status_code, "success:", body.get("success"),
      "errors:", [e.get("message") for e in body.get("errors", [])])
st = requests.get(f"{API}/accounts/{acct}/workers/scripts/cct-tools-router/settings",
                  headers=h, timeout=20).json().get("result") or {}
print("bindings now:", [(b.get("name"), b.get("type")) for b in st.get("bindings", [])])
