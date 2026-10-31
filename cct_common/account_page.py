"""
account_page.py — the account page, /account/ (0.38.0; the owner's scope of
2026-10-30, CCT_Marketing accounts/Account_Page_Plan.md, step 1).

One page, the same from every app (register_account_routes mounts it), so
on cheapcadtools.com it's https://cheapcadtools.com/account/ (the site's
Worker sends /account/* to the apps, as it does sign-in). It collects what
exists, each section only while its own setting is on:

    Tokens        balance, bought and free, the refund value (with its note),
                  expiry, Buy tokens (the buy panel with inline_checkout, else
                  the buy page), and with rewards the gold strip and "How
                  rewards work" (0.53.0; the owner's redesign of 2026-10-31)
    My designs    the latest few, with saved_designs (and the app's My designs)
    History       the ledger in plain words (history_line), newest first
    Newsletter    the checkbox, with newsletter_opt_in
    App defaults  units and 3D Printer Compensation, with printer_settings
                  (0.41.0; the owner's words, 0.48.0; "App defaults", 0.53.0)
    Sharing       the account's live share links and their opens, with
                  share_links and saved_designs (0.42.0)
    My reports    the account's bug and feature reports and their status,
                  with my_reports (0.44.0)
    Projects      the latest 3 and "All projects (N)", with projects (0.46.0)
    Sign-ins      sign-in methods, browsers (sign one out), AI apps and add-ins
    Your data     "Download my data (.zip)", with data_download (0.45.0; data_export)
    Delete        the account, after typing its email

All of it is behind the account_page business setting, off until the owner
turns it on: while off the page is a plain 404. The actions are the routes
that exist (api/account/logout, sessions, newsletter, delete), sent as JSON.
"""
from __future__ import annotations

import html
import time
from typing import Callable, Optional

HISTORY_FIRST = 20
HISTORY_MORE = 50
HISTORY_MAX = 500
# Under My designs, here and on /account/designs (the owner, 0.48.0).
KEPT_NOTICE = ("Only each design's settings and its picture are kept here, not its files. When you open a "
               "design, its tool makes the STL, STEP, DXF or SVG files again from those settings. A newer "
               "version of a tool can make slightly different files, so keep your own copy of any file you "
               "need exactly as it was.")
# Each section's mark in its heading (0.48.0).
ICONS = {"tokens": "◆", "designs": "▦", "projects": "▤", "sharing": "↗", "history": "≡", "reports": "⚑",
         "newsletter-head": "✉", "printing": "⚙", "sign-ins": "⚿", "data": "⇩", "delete-head": "✕"}
JUMP = (("tokens", "Tokens"), ("designs", "My designs"), ("projects", "Projects"), ("sharing", "Sharing"),
        ("history", "History"), ("reports", "Reports"), ("newsletter-head", "Newsletter"),
        ("printing", "App defaults"), ("sign-ins", "Sign-ins"), ("data", "Your data"))
DESIGNS_SHOWN = 4

# The ledger's app names, as each tool calls itself.
TOOL_TITLES = {
    "pulleys": "Timing Pulley Generator", "sprocket": "Sprocket Designer", "gear": "Gear Designer",
    "worm": "Worm Gear Designer", "bevel": "Bevel Gear Designer", "planetary": "Planetary Gear Designer",
    "ebox": "E-Box Designer",
}
PROVIDER_NAMES = {"paypal": "PayPal", "stripe": "Card"}
TERMS_URL = "https://cheapcadtools.com/terms/"
SUPPORT_EMAIL = "support@cheapcadtools.com"
# The token panel's two notes (0.53.0; the owner's words of 2026-10-31).
REWARDS_HOW = (
    "<h3>How rewards work</h3>"
    "<p><strong>Your first purchase</strong> comes with 25% extra tokens. It's given once per account, and once "
    "per card or PayPal account.</p>"
    "<p><strong>After that:</strong> every 200 tokens you use on downloads earns 10% extra tokens on your next "
    "purchase; every 1,000 earns 20% instead. Only tokens you bought count, not free or bonus tokens, or tokens "
    "given back for a file that failed.</p>"
    "<ul><li>You hold one bonus at a time, and it doesn't add up: a bigger one replaces it. It's added to your "
    "next purchase of any size, then the count carries on.</li>"
    "<li>The first-purchase bonus and a bonus you hold never combine: the bigger one is used, and the other "
    "stays for later.</li>"
    "<li>Bonus tokens are free tokens: used after the ones you buy, and they can't be refunded. If a purchase "
    "is refunded, its bonus is taken back too.</li></ul>"
    f"<p class='tk-dlg-foot'>Full rules: <a href='{TERMS_URL}'>Terms of Service</a>, section 3.</p>")
REFUND_BONUS = ("Free and bonus tokens can't be refunded. If a purchase is refunded or charged back, the bonus "
                "tokens that came with it are taken back too, in the same share.")
REFUND_FREE = "Free tokens can't be refunded."
# Each tool's page on the site (the "fixed" email links it).
TOOL_PAGES = {
    "pulleys": "/tools/pulleys/", "sprocket": "/tools/sprocket/", "gear": "/tools/gears/",
    "worm": "/tools/worm-gears/", "bevel": "/tools/bevel-gears/", "planetary": "/tools/planetary-gears/",
}
BONUS_WHY = {"first": "first purchase", "10": "after 200 paid tokens used", "20": "after 1,000 paid tokens used"}


def _n(n: int) -> str:
    return f"{n:,} token{'' if n == 1 else 's'}"


def _dollars(cents: int) -> str:
    return f"${cents // 100}" + (f".{cents % 100:02d}" if cents % 100 else "")


def _date(ts: float) -> str:
    t = time.gmtime(ts)
    return f"{time.strftime('%b', t)} {t.tm_mday}, {t.tm_year}"


def tool_title(app: Optional[str], titles: Optional[dict] = None) -> str:
    return (titles or {}).get(app) or TOOL_TITLES.get(app or "") or (app or "a tool")


def history_line(row: dict, orders: dict, titles: Optional[dict] = None) -> str:
    """One ledger row in plain words. orders: {"<provider>:<order id>": the
    payments row} for the purchases and bonuses among them."""
    kind, amount, ref = row["kind"], int(row["amount"]), row.get("ref") or ""
    tool = tool_title(row.get("app"), titles)
    fmt = (row.get("fmt") or "").upper() or "File"
    if kind == "spend":
        return f"{fmt} download · {tool} · {_n(-amount)}"
    if kind == "download":
        return f"{fmt} download again · {tool} · free"
    if kind == "refund":
        return f"Download didn't finish · {tool} · {_n(amount)} back"
    if kind == "signup":
        return f"Welcome tokens · {_n(amount)}"
    if kind == "purchase":
        provider = ref.split(":", 1)[0]
        order = orders.get(ref) or {}
        price = f" · {_dollars(order['cents'])}" if order.get("cents") else ""
        return f"Bought {_n(amount)}{price} · {PROVIDER_NAMES.get(provider, provider.capitalize())}"
    if kind == "bonus":
        order = orders.get(ref[len("bonus:"):]) or {}
        why = BONUS_WHY.get(order.get("bonus_rule") or "")
        return f"Bonus tokens{' · ' + why if why else ''} · {_n(amount)}"
    if kind == "promo":
        return f"Free tokens from CheapCADTools · {_n(amount)}"
    if kind == "referral":
        return f"Referral tokens · {_n(amount)}"
    if kind == "expire":
        if "closed" in (row.get("detail") or ""):
            return "Tokens expired · account closed"
        return f"Free tokens expired · {_n(-amount)}"
    if kind == "adjust":
        if "-refund:" in ref:
            return f"Purchase refunded · {_n(-amount)} taken back"
        if ref.startswith("bonusback:"):
            return f"Bonus taken back with a refund · {_n(-amount)}"
        if ref.startswith("referralback:"):
            return f"Referral tokens taken back with a refund · {_n(-amount)}"
        if amount < 0:
            return f"Adjusted by CheapCADTools · {_n(-amount)} removed"
        return f"Added by CheapCADTools · {_n(amount)}"
    return f"{kind.capitalize()} · {_n(abs(amount))}"


def delete_words(on: Callable[[str], bool], lost: str = "") -> str:
    """The delete warning (0.47.1-0.47.2, CCT_WebSite's privacy check): it
    names only the features that are on (on(setting) -> bool), and says what's
    kept as the privacy text's §8 does. `lost`: the tokens sentence, or ""."""
    designs = on("saved_designs")
    gone = ["your email address", "sign-in methods"]
    gone += ["saved designs"] if designs else []
    gone += ["notes"] if designs and on("bookmarks") else []
    gone += ["projects"] if designs and on("projects") else []
    gone += ["printer settings"] if on("printer_settings") else []
    gone += ["newsletter choice"] if on("newsletter_opt_in") else []
    listed = ", ".join(gone[:-1]) + " and " + gone[-1]
    shares = ", ends your share links" if designs and on("share_links") else ""
    records = "past purchases, receipts and token use" if on("receipts") else "past purchases and token use"
    kept = (f"We keep the records of {records}, with no email on them, as financial records, "
            "and a scrambled form of your email address only so a new account with it doesn't get the free "
            "starter tokens again")
    if on("rewards"):
        kept += ", and the card or PayPal identifier, so the first-purchase bonus isn't given again"
    return (f"Deleting your account removes {listed}{shares}, and signs out everywhere. "
            f"{lost}{kept}. This can't be undone.")


_BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
             ("Safari/", "Safari"))
_SYSTEMS = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
            ("Macintosh", "Mac"), ("CrOS", "ChromeOS"), ("Linux", "Linux"))


def browser_name(label: Optional[str]) -> str:
    """'Chrome on Windows' from a user agent: all a browser session keeps of
    it since 0.38.1. A label that's already a name (no "/") stays as it is."""
    ua = label or ""
    if ua and "/" not in ua:
        return ua
    browser = next((name for mark, name in _BROWSERS if mark in ua), None)
    system = next((name for mark, name in _SYSTEMS if mark in ua), None)
    if browser and system:
        return f"{browser} on {system}"
    if system:
        return f"A browser on {system}"
    return browser or "A browser"


_CSS = (                                   # 0.48.0: panels, a jump bar, badges (the owner's review)
    ":root{--ink:#1f2328;--sub:#5f6773;--line:#e2e6eb;--bg:#f5f7fa;--panel:#fff;--accent:#0078d4;"
    "--accent-soft:#e8f2fc;--warm:#fff7e0;--warm-line:#f0d27a;--danger:#b00020}"
    "body{font-family:system-ui,sans-serif;max-width:56rem;margin:0 auto;padding:1.5rem 1rem 3rem;line-height:1.5;"
    "color:var(--ink);background:var(--bg)}h1{font-size:1.6rem;margin:0}"
    "a{color:var(--accent)}"
    ".hero{display:flex;align-items:center;gap:1rem;margin:0 0 1rem}"
    ".avatar{flex:none;width:3.2rem;height:3.2rem;border-radius:50%;display:flex;align-items:center;"
    "justify-content:center;font-size:1.4rem;font-weight:700;color:#fff;"
    "background:linear-gradient(135deg,#0078d4,#5b3fd1)}"
    ".jump{display:flex;flex-wrap:wrap;gap:.4rem;margin:0 0 1.2rem;position:sticky;top:0;z-index:1;"
    "padding:.5rem 0;background:var(--bg)}"
    ".jump a{padding:.2rem .75rem;border:1px solid var(--line);border-radius:999px;background:var(--panel);"
    "color:var(--ink);text-decoration:none;font-size:.88rem}.jump a:hover{border-color:var(--accent)}"
    ".panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1.1rem 1.3rem;"
    "margin:0 0 1rem;box-shadow:0 1px 2px rgba(16,24,40,.04)}"
    "h2{font-size:1.15rem;margin:0 0 .7rem;display:flex;align-items:center;gap:.5rem;scroll-margin-top:3.5rem}"
    "h2 .ic{flex:none;width:1.8rem;height:1.8rem;border-radius:8px;display:inline-flex;align-items:center;"
    "justify-content:center;background:var(--accent-soft);color:var(--accent);font-size:1rem}"
    ".panel.tokens{background:linear-gradient(135deg,#0b5cad,#5b3fd1);color:#fff;border:0}"
    ".panel.tokens .sub,.panel.tokens p{color:rgba(255,255,255,.88)}.panel.tokens h2 .ic{background:rgba(255,255,255,.18);"
    "color:#fff}.panel.tokens .btn{background:#fff;color:#0b5cad;border-color:#fff;font-weight:600}"
    ".panel.danger{border-color:#f1c6c6}.panel.danger h2 .ic{background:#fdecee;color:var(--danger)}"
    # The token panel (0.53.0): the count, bought and free, the refund value, the gold rewards strip.
    ".panel.tokens{padding-bottom:0;overflow:hidden}"
    ".tk-top{display:flex;justify-content:center;align-items:center;gap:1.6rem;flex-wrap:wrap;margin:.3rem 0 .6rem}"
    ".tk-count{font-size:2.8rem;font-weight:800;line-height:1}.tk-count span{font-size:1.3rem;font-weight:600}"
    ".tk-split,.tk-refund{display:flex;flex-direction:column;font-size:.9rem;line-height:1.35;padding-left:1.2rem;"
    "border-left:1px solid rgba(255,255,255,.35)}"
    ".tk-label{font-size:.8rem;opacity:.85}.tk-money{font-size:1.15rem;font-weight:700}"
    "button.tk-star{background:none;border:0;padding:0 .15rem;color:#ffd166;font-size:1.15rem;font-weight:800;"
    "text-decoration:none;cursor:pointer;vertical-align:super;line-height:1}"
    "button.tk-star:focus-visible{outline:2px solid #ffd166;outline-offset:2px;border-radius:4px}"
    ".panel.tokens p.tk-small{text-align:center;font-size:.8rem;color:rgba(255,255,255,.75);margin:.2rem auto .8rem;"
    "max-width:44rem}"
    ".tk-buy{text-align:center;margin:0 0 1rem}"
    ".tk-rewards{display:flex;align-items:center;gap:1rem;margin:0 -1.3rem;padding:.75rem 1.3rem;background:#ffd166;"
    "color:#3d2c00}.panel.tokens .tk-rewards p{margin:0;color:#3d2c00;font-size:.95rem}"
    ".tk-rewards strong{font-size:1.05rem}"
    "button.tk-rbtn{background:#3d2c00;color:#ffd166;border:0;font-weight:700;white-space:nowrap;flex:none}"
    ".tk-dlg{max-width:30rem;border:0;border-radius:12px;padding:1.2rem 1.4rem;box-shadow:0 10px 40px rgba(0,0,0,.3);"
    "color:var(--ink);background:var(--panel)}.tk-dlg::backdrop{background:rgba(10,20,40,.45)}"
    ".tk-dlg h3{margin:0 0 .6rem}.tk-dlg ul{padding-left:1.1rem;font-size:.9rem}"
    ".tk-dlg-foot{font-size:.85rem;color:var(--sub)}.tk-dlg form{text-align:right;margin:0}"
    "@media (max-width:600px){.tk-top{gap:1rem}.tk-count{width:100%;text-align:center}"
    ".tk-split,.tk-refund{border-left:0;padding-left:0;text-align:center}"
    ".tk-rewards{flex-direction:column;align-items:flex-start;gap:.5rem}}"
    ".sub{color:var(--sub);font-size:.9rem}.big{font-size:2.2rem;font-weight:700;margin:.1rem 0 0;line-height:1.2}"
    "button,.btn{font:inherit;font-size:.95rem;padding:.45rem 1.1rem;border-radius:8px;cursor:pointer;"
    "border:1px solid var(--accent);background:var(--accent);color:#fff;text-decoration:none;display:inline-block}"
    ".btn.sm{font-size:.85rem;padding:.25rem .8rem}"
    "button.secondary{background:var(--panel);color:var(--ink);border-color:#c8ced6}"
    "button.danger{background:var(--danger);border-color:var(--danger)}"
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(11.5rem,1fr));gap:.8rem}"
    ".card{border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column;"
    "background:var(--panel);transition:box-shadow .15s}.card:hover{box-shadow:0 4px 14px rgba(16,24,40,.1)}"
    ".card img,.card .ph{display:block;width:100%;aspect-ratio:4/3;object-fit:contain;background:#eef1f5}"
    ".card .ph{display:flex;align-items:center;justify-content:center;color:#999;font-size:.85rem}"
    ".card .in{padding:.55rem .7rem .7rem;display:flex;flex-direction:column;gap:.25rem;flex:1}"
    ".card .name{font-weight:600;font-size:.95rem}.card .acts{display:flex;align-items:center;gap:.6rem;"
    "margin-top:auto;padding-top:.3rem;font-size:.85rem;flex-wrap:wrap}"
    ".badges{display:flex;flex-wrap:wrap;gap:.3rem}.badge{font-size:.75rem;padding:.05rem .5rem;border-radius:999px;"
    "background:var(--accent-soft);color:var(--accent)}.badge.star{background:#fff3cd;color:#8a5a00}"
    ".card .note{font-size:.85rem;color:var(--sub);font-style:italic;overflow:hidden;display:-webkit-box;"
    "-webkit-line-clamp:2;-webkit-box-orient:vertical}"
    ".heads-up{background:var(--warm);border:1px solid var(--warm-line);border-left-width:4px;border-radius:8px;"
    "padding:.6rem .9rem;margin:.8rem 0;font-size:.92rem}"
    ".how{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.6rem;margin:.8rem 0 0;"
    "padding:0;list-style:none}.how li{border:1px dashed #c8ced6;border-radius:8px;padding:.55rem .75rem;"
    "font-size:.9rem}.how strong{display:block}"
    "ul.hist{list-style:none;padding:0;margin:0}ul.hist li{display:flex;gap:1rem;padding:.45rem 0;"
    "border-bottom:1px solid var(--line)}ul.hist li:last-child{border-bottom:0}"
    "ul.hist .when{color:var(--sub);min-width:7.5rem;font-size:.9rem}"
    ".sess{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.5rem 0;"
    "border-bottom:1px solid var(--line)}.sess:last-of-type{border-bottom:0}"
    "label.news{display:flex;gap:.6rem;align-items:flex-start}label.news input{margin-top:.35rem}"
    "input[type=email],input[type=number]{box-sizing:border-box;font:inherit;padding:.4rem .6rem;"
    "border:1px solid #c8ced6;border-radius:8px;background:var(--panel);color:var(--ink)}"
    "input[type=email]{width:100%;max-width:24rem}.msg{min-height:1.4em;font-size:.9rem;color:var(--sub)}"
    ".field{margin:0 0 .9rem}.field b{display:block;margin-bottom:.2rem}"
    ".warn{background:#fff6f6;border:1px solid #f1c6c6;border-radius:8px;padding:.8rem 1rem}"
    "@media (prefers-color-scheme:dark){:root{--ink:#e8e8e8;--sub:#9aa3ad;--line:#2c3138;--bg:#111317;"
    "--panel:#1a1d22;--accent:#4aa3f0;--accent-soft:#1d2c3d;--warm:#2b2617;--warm-line:#6b5a22}"
    ".card img,.card .ph{background:#22252b}.warn{background:#2a1c1e;border-color:#5a2a30}"
    ".panel.danger{border-color:#5a2a30}.badge.star{background:#3a3115;color:#f3cf6b}"
    "input[type=email],input[type=number],button.secondary{border-color:#444}}"
)

_SCRIPT = """<script>
async function call(url, method, body) {
  const r = await fetch(url, { method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || ('Something went wrong (' + r.status + ').'));
  return d;
}
document.querySelectorAll('[data-sign-out]').forEach(b => b.addEventListener('click', async () => {
  await call('/api/account/logout', 'POST', {}).catch(() => {});
  location.href = '/';
}));
document.querySelectorAll('[data-session]').forEach(b => b.addEventListener('click', async () => {
  b.disabled = true;
  try { await call('/api/account/sessions/' + b.dataset.session, 'DELETE'); b.closest('.sess').remove(); }
  catch (e) { b.disabled = false; b.textContent = e.message; }
}));
const news = document.getElementById('newsletter');
if (news) news.addEventListener('change', async () => {
  const msg = document.getElementById('newsletter-msg'), want = news.checked;
  news.disabled = true; msg.textContent = 'Saving…';
  try {
    const d = await call('/api/account/newsletter', 'POST', { subscribed: want, label_version: +news.dataset.version });
    news.checked = !!d.subscribed; msg.textContent = 'Saved';
  } catch (e) { news.checked = !want; msg.textContent = "Couldn't save that. Please try again."; }
  news.disabled = false;
});
const printer = document.getElementById('printer');
if (printer) printer.addEventListener('submit', async e => {
  e.preventDefault();
  const msg = document.getElementById('printer-msg'), f = printer.elements;
  const units = [...printer.querySelectorAll('[name=units]')].find(r => r.checked);
  const comp = f.print_compensation_mm.value;
  try {
    await call('/api/account/printer', 'POST', { units: units ? units.value : null,
                                                 print_compensation_mm: comp === '' ? null : +comp });
    msg.textContent = 'Saved';
  } catch (err) { msg.textContent = err.message; }
});
const del = document.getElementById('delete');
if (del) del.addEventListener('submit', async e => {
  e.preventDefault();
  const msg = document.getElementById('delete-msg');
  try {
    await call('/api/account/delete', 'POST', { confirm_email: del.elements.email.value.trim() });
    document.body.innerHTML = '<h1>Your account has been deleted.</h1><p><a href="/">Back to CheapCADTools</a></p>';
  } catch (err) { msg.textContent = err.message; }
});
document.querySelectorAll('[data-open]').forEach(b => b.addEventListener('click', e => {
  e.preventDefault();
  document.getElementById(b.dataset.open).showModal();
}));
const buy = document.getElementById('buy');
if (buy && buy.dataset.panel) buy.addEventListener('click', async e => {
  if (!window.cctTokens) return;                       // the buy page, as the link says
  e.preventDefault();
  if (await window.cctTokens.buyDialog(null, +buy.dataset.balance, buy.href) === 'paid') location.reload();
});
</script>"""


def _doc(title: str, inner: str, status: int = 200, extra: str = ""):
    from flask import Response
    r = Response(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='referrer' content='no-referrer'><title>{html.escape(title)}</title>"
        f"<link rel='stylesheet' href='/static/cct/cct_theme.css'><style>{_CSS}</style>{extra}</head>"
        f"<body>{inner}</body></html>", status=status, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r


def _orders(tokens, rows: list) -> dict:
    """The payments rows behind the purchases and bonuses among `rows`."""
    ids = {r["ref"].split(":", 1)[1] for r in rows if r["kind"] == "purchase" and ":" in (r["ref"] or "")}
    ids |= {r["ref"].split(":", 2)[2] for r in rows if r["kind"] == "bonus" and (r["ref"] or "").count(":") >= 2}
    if not ids:
        return {}
    try:
        with tokens._read() as db:
            got = db.execute(f"SELECT * FROM payments WHERE order_id IN ({','.join('?' * len(ids))})",
                             tuple(sorted(ids))).fetchall()
    except Exception:                       # no payments table: this app sells nothing
        return {}
    return {f"{r['provider']}:{r['order_id']}": dict(r) for r in got}


def register_account_page(app, accounts, *, buy_url) -> None:
    """GET /account/ (and /account). buy_url() is where "Buy tokens" goes."""
    from flask import redirect, request

    from .account_routes import COOKIE_NAME, current_account_id
    from .accounts import DEAD_ACCOUNT_IDLE_S
    from .settings import setting

    tokens = accounts.tokens

    def on(key: str) -> bool:
        return setting(tokens.path, key) is True

    def has_route(rule: str) -> bool:
        return any(r.rule == rule for r in app.url_map.iter_rules())

    def tokens_section(acct: str) -> tuple:
        """The token panel and its two notes (dialogs, kept outside the panel):
        the count, bought and free, the refund value, the expiry, Buy tokens,
        and with rewards the gold strip (0.53.0; the owner's redesign)."""
        bal, free = tokens.balance(acct), tokens.free_remaining(acct)
        bought, rewards = bal - free, on("rewards")
        value = refund_value(acct) if bought > 0 else None
        top = (f"<div class='tk-count'>{bal:,} <span>token{'' if bal == 1 else 's'}</span></div>"
               f"<div class='tk-split'><span>{bought:,} bought</span><span>{free:,} free</span></div>")
        dialogs = []
        if value is not None:
            top += ("<div class='tk-refund'><span class='tk-label'>Refund value</span>"
                    f"<span class='tk-money'>{_dollars(value)}<button type='button' class='tk-star' "
                    "data-open='refund-note' aria-label='About the refund value'>*</button></span></div>")
            dialogs.append(
                "<dialog id='refund-note' class='tk-dlg'><h3>Refund value</h3>"
                f"<p>What you paid for your {bought:,} unused bought token{'' if bought == 1 else 's'}.</p>"
                "<p><strong>It doesn't include payment processing fees.</strong> PayPal and Stripe generally keep "
                "their fees when a payment is refunded, so a refund is this amount less those fees.</p>"
                f"<p>{REFUND_BONUS if rewards else REFUND_FREE}</p>"
                f"<p>To ask for a refund, email <a href='mailto:{SUPPORT_EMAIL}'>{SUPPORT_EMAIL}</a> from your "
                f"account's email address (<a href='{TERMS_URL}'>Terms of Service</a>, section 4).</p>"
                "<form method='dialog'><button>OK</button></form></dialog>")
        small = [html.escape("Free and bonus tokens are used after the ones you buy, and can't be refunded."
                             if rewards else "Free tokens are used first, and can't be refunded.")]
        last = accounts.last_sign_in(acct)
        if last:
            small.append(f"Unused tokens expire on {_date(last + DEAD_ACCOUNT_IDLE_S)}: 5 years after your "
                         "latest sign-in.")
        out = [f"<h2 id='tokens'>Tokens</h2><div class='tk-top'>{top}</div>",
               f"<p class='tk-small'>{'<br>'.join(small)}</p>"]
        url = buy_url()
        if url:
            panel = " data-panel='1'" if on("inline_checkout") else ""
            out.append(f"<p class='tk-buy'><a class='btn' id='buy' href='{html.escape(url, quote=True)}'{panel} "
                       f"data-balance='{bal}' target='_blank' rel='noopener'>Buy tokens</a></p>")
        else:
            out.append("<p class='tk-small'>Buying tokens isn't open yet.</p>")
        strip = rewards_strip(acct) if rewards else ""
        if strip:
            out.append("<div class='tk-rewards'><button type='button' class='tk-rbtn' data-open='rewards-how'>"
                       f"★ Rewards</button><p>{strip}</p></div>")
            dialogs.append(f"<dialog id='rewards-how' class='tk-dlg'>{REWARDS_HOW}"
                           "<form method='dialog'><button>OK</button></form></dialog>")
        return "".join(out), "".join(dialogs)

    def refund_value(acct: str) -> Optional[int]:
        """What the account paid for its unused bought tokens, in cents: each
        purchase's share at the price it was paid, newest first, before
        fees (payments.account_refund_quote, rewards step 8). None where
        this app sells nothing or no purchase holds them."""
        store = (app.extensions.get("cct_payments") or {}).get("store")
        if store is None:
            return None
        parts = store.account_refund_quote(acct)["parts"]
        return sum(p["value_cents"] for p in parts) if parts else None

    def rewards_strip(acct: str) -> str:
        """The gold strip's line: the first-purchase bonus, the bonus held and
        what beats it, or how many more paid tokens to use (rewards.state)."""
        since = tokens.paid_first_since()
        if since is None:
            return ""
        from .rewards import FIRST_PCT, offer, state
        with tokens._read() as db:
            st, o = state(db, tokens, acct, since), offer(db, tokens, acct, since)
        nxt = st["next"]
        more = lambda: (f"<strong>{nxt['at'] - st['paid_spent']:,} more "
                        f"token{'' if nxt['at'] - st['paid_spent'] == 1 else 's'}</strong>")
        if o["first"]:
            return f"Your first purchase comes with <strong>{FIRST_PCT}% extra</strong> tokens."
        if st["held"]:
            line = f"Your next purchase comes with <strong>{st['held']}% extra</strong>."
            if nxt and nxt["pct"] > st["held"]:
                line += f" Use {more()} to make it {nxt['pct']}%."
            return line
        if nxt:
            return f"Use {more()} and your next purchase comes with {nxt['pct']}% extra."
        return ""

    def designs_section(acct: str) -> str:
        if not (on("saved_designs") and has_route("/account/designs")):
            return ""
        from .saved_designs import SavedDesigns, open_link
        store = SavedDesigns(tokens.path)
        rows, apps = store.list(acct), store.apps()
        titles = {a: v.get("title") for a, v in apps.items() if v.get("title")}
        head = "<h2 id='designs'>My designs</h2>"
        notice = f"<p class='heads-up'><strong>Good to know:</strong> {html.escape(KEPT_NOTICE)}</p>"
        if not rows:
            return head + ("<p class='sub'>Nothing here yet. Designs you download, or save from a tool's "
                           "Download window, show up here.</p>") + notice + how_to()
        marks, sharing = on("bookmarks"), on("share_links") and has_route("/account/designs")
        if marks:                                    # 0.43.0: bookmarked first, then the latest
            rows = sorted(rows, key=lambda r: 0 if r.get("bookmarked") else 1)
        if sharing:
            from .share_links import ShareLinks
            live = ShareLinks(tokens.path).live_for(acct)
        else:
            live = {}
        more = (["bookmark", "note"] if marks else []) + (["share"] if sharing else [])
        more = (", ".join(more[:-1]) + " or " + more[-1] if len(more) > 1 else more[0] if more
                else "add to a project" if on("projects") else "rename or delete")
        cards = []
        for r in rows[:DESIGNS_SHOWN]:
            sid = html.escape(r["id"], quote=True)
            img = (f"<img src='/account/designs/{sid}/thumb' alt=''>" if r["has_thumb"]
                   else "<div class='ph'>No picture</div>")
            tool = tool_title(r["app"], titles)
            star = "★ " if marks and r.get("bookmarked") else ""
            badges = (["<span class='badge star'>Bookmarked</span>"] if star else []) + \
                     (["<span class='badge'>Note</span>"] if marks and r.get("note") else []) + \
                     ([f"<span class='badge'>Shared · opened {live[r['id']]['opens']}×</span>"]
                      if r["id"] in live else [])
            note = (f"<div class='note'>{html.escape(r['note'])}</div>" if marks and r.get("note") else "")
            href = html.escape(open_link(r["app"], r["id"], apps), quote=True)
            cards.append(f"<div class='card'><a href='{href}' tabindex='-1' aria-hidden='true'>{img}</a>"
                         f"<div class='in'><div class='name'>{star}{html.escape(r['name'] or tool + ' design')}</div>"
                         f"<div class='sub'>{html.escape(tool)} · {_date(r['updated_at'])}</div>"
                         + (f"<div class='badges'>{''.join(badges)}</div>" if badges else "") + note +
                         f"<div class='acts'><a class='btn sm' href='{href}'>Open</a>"
                         f"<a href='/account/designs#d-{sid}'>{html.escape(more[0].upper() + more[1:])}</a></div>"
                         "</div></div>")
        return (head + "<div class='grid'>" + "".join(cards) + "</div>"
                f"<p><a href='/account/designs'>All my designs ({len(rows)})</a></p>" + notice + how_to())

    def how_to() -> str:
        """How to bookmark, note, share and group a design, for what's on (0.48.0)."""
        from .saved_designs import NOTE_MAX
        tips = []
        if on("bookmarks"):
            tips.append(("☆ Bookmark", "On My designs, press ☆ Bookmark on a design; press ★ Bookmarked again "
                                      "to remove it. Bookmarked designs come first, here and there."))
            tips.append(("✎ Add a note", f"Under a design on My designs, press Add a note: up to {NOTE_MAX:,} "
                                         "characters, for you alone unless you include it when you share."))
        if on("share_links") and has_route("/account/designs"):
            tips.append(("↗ Share", "Press Share on a design for a link. Anyone with it can open the design in "
                                   "its tool and see its settings and picture, not your email. Stop sharing "
                                   "turns the link off."))
        if on("projects"):
            tips.append(("▤ Projects", "Press Add to project on a design to group designs for one build."))
        if not tips:
            return ""
        return ("<ul class='how'>" + "".join(f"<li><strong>{html.escape(t)}</strong>{html.escape(w)}</li>"
                                              for t, w in tips) + "</ul>")

    def history_section(acct: str) -> str:
        try:
            limit = max(HISTORY_FIRST, min(HISTORY_MAX, int(request.args.get("history", HISTORY_FIRST))))
        except ValueError:
            limit = HISTORY_FIRST
        rows = tokens.history(acct, limit=limit + 1)
        more, rows = len(rows) > limit, rows[:limit]
        head = "<h2 id='history'>History</h2>"
        if not rows:
            return head + "<p class='sub'>No token activity yet.</p>"
        orders = _orders(tokens, rows)
        numbers = receipt_numbers(acct)
        try:
            from .saved_designs import SavedDesigns
            titles = {a: v.get("title") for a, v in SavedDesigns(tokens.path).apps().items() if v.get("title")}
        except Exception:
            titles = {}
        def receipt(r):
            num = numbers.get(r["ref"] or "")          # a purchase's ref, or a refund's take-back's
            if not num:
                return ""
            label = "Receipt" if r["kind"] == "purchase" else "Credit note"
            return f" · <a href='/account/receipts/{html.escape(num, quote=True)}'>{label}</a>"
        items = "".join(f"<li><span class='when'>{_date(r['ts'])}</span>"
                        f"<span>{html.escape(history_line(r, orders, titles))}{receipt(r)}</span></li>" for r in rows)
        link = (f"<p><a href='?history={limit + HISTORY_MORE}#history'>Show more</a></p>" if more else "")
        return head + f"<ul class='hist'>{items}</ul>" + link

    def receipt_numbers(acct: str) -> dict:
        """Its receipts' numbers by what they're for (receipts on), numbering any now due."""
        if not on("receipts"):
            return {}
        from .receipts import Receipts
        store = Receipts(tokens.path, clock=tokens._clock)
        try:
            store.issue_pending()
        except Exception:                   # no payments table: nothing was ever bought here
            return {}
        return store.numbers_for(acct)

    def projects_section(acct: str) -> str:
        if not (on("projects") and on("saved_designs")):
            return ""
        from .projects import SHOWN_ON_ACCOUNT, Projects
        rows = Projects(tokens.path).list(acct)
        head = "<h2 id='projects'>Projects</h2>"
        if not rows:
            return head + ("<p class='sub'>No projects yet. Group designs for one build: add them from "
                           "<a href='/account/designs'>My designs</a>.</p>")
        items = "".join(f"<li><span class='when'>{_date(p['updated_at'])}</span><span><a href='/account/projects/"
                        f"{html.escape(p['id'], quote=True)}'>{html.escape(p['name'])}</a> · {p['designs']} design"
                        f"{'' if p['designs'] == 1 else 's'}</span></li>" for p in rows[:SHOWN_ON_ACCOUNT])
        return head + f"<ul class='hist'>{items}</ul><p><a href='/account/projects'>All projects ({len(rows)})</a></p>"

    def referral_line(acct: str) -> str:
        """Referrals (0.49.0), while on: what the account's links earned."""
        from .referrals import HOW, line, referrals_on, summary
        if not referrals_on(tokens.path):
            return ""
        with tokens._read() as db:
            s = summary(db, acct, tokens._clock())
        return f"<p><strong>{html.escape(line(s))}</strong></p><p class='sub'>{html.escape(HOW)}</p>"

    def sharing_section(acct: str) -> str:
        if not (on("share_links") and on("saved_designs") and has_route("/account/designs")):
            return ""
        from .saved_designs import SavedDesigns
        from .share_links import ShareLinks, link
        live = ShareLinks(tokens.path).live_for(acct)
        head = "<h2 id='sharing'>Sharing</h2>" + referral_line(acct)
        if not live:
            return head + "<p class='sub'>You haven't shared a design yet. Share one from <a href='/account/designs'>" \
                          "My designs</a>.</p>"
        apps = SavedDesigns(tokens.path).apps()
        titles = {a: v.get("title") for a, v in apps.items() if v.get("title")}
        rows = []
        for sid, s in live.items():
            page = (apps.get(s["app"]) or {}).get("page") or f"/tools/{s['app']}/"
            url = link(page, s["code"], request.host_url)
            tool = tool_title(s["app"], titles)
            rows.append(f"<div class='sess'><span>{html.escape(s['name'] or tool + ' design')} · {html.escape(tool)} · "
                        f"opened {s['opens']} time{'' if s['opens'] == 1 else 's'}<br>"
                        f"<a href='{html.escape(url, quote=True)}'>{html.escape(url)}</a></span>"
                        f"<form method='post' action='/account/designs/{html.escape(sid, quote=True)}'>"
                        "<input type='hidden' name='action' value='unshare'>"
                        "<button class='secondary' type='submit'>Stop sharing</button></form></div>")
        return head + "".join(rows)

    def reports_section(acct: str) -> str:
        if not on("my_reports"):
            return ""
        from .reports import ReportStore, status_line
        rows = ReportStore(tokens.path).for_account(acct)
        head = "<h2 id='reports'>My reports</h2>"
        if not rows:
            return head + "<p class='sub'>You haven't sent a report yet. Use \"Report a bug\" in any tool.</p>"
        items = []
        for r in rows:
            tool = tool_title(r["app"])
            words = " — ".join(html.escape(x) for x in (r["seeing"], r["should_see"]) if x)
            items.append(f"<li><span class='when'>{_date(r['created_at'])}</span><span>{html.escape(tool)} · {words}"
                         f"<br><strong>{html.escape(status_line(r, tool))}</strong></span></li>")
        return head + f"<ul class='hist'>{''.join(items)}</ul>"

    def newsletter_section(acct: str) -> str:
        if not on("newsletter_opt_in"):
            return ""
        from .newsletter import LABEL_VERSION, LABELS, Newsletter
        st = Newsletter(tokens.path).state(acct)
        return ("<h2 id='newsletter-head'>Newsletter</h2><label class='news'>"
                f"<input type='checkbox' id='newsletter' data-version='{LABEL_VERSION}'"
                f"{' checked' if st['subscribed'] else ''}><span>{html.escape(LABELS[LABEL_VERSION])}</span>"
                "</label><p class='msg' id='newsletter-msg' role='status'></p>")

    def printer_section(acct: str) -> str:
        if not on("printer_settings"):
            return ""
        from .printer_settings import (COMPENSATION_MAX_MM, COMPENSATION_MIN_MM, COMPENSATION_STEP_MM,
                                       PrinterSettings)
        p = PrinterSettings(tokens.path).get(acct)
        units = "".join(f"<label><input type='radio' name='units' value='{u}'{' checked' if p['units'] == u else ''}>"
                        f" {name}</label> " for u, name in (("mm", "Millimetres"), ("in", "Inches")))
        comp = "" if p["print_compensation_mm"] is None else f"{p['print_compensation_mm']:.2f}"
        return ("<h2 id='printing'>App defaults</h2><form id='printer'>"
                f"<div class='field'><b>Units</b>{units}<br><span class='sub'>Tools open in these units.</span></div>"
                "<div class='field'><label><b>3D Printer Compensation</b><input name='print_compensation_mm' "
                f"type='number' min='{COMPENSATION_MIN_MM:g}' max='{COMPENSATION_MAX_MM:g}' step='{COMPENSATION_STEP_MM:g}' "
                f"value='{comp}' placeholder='the tool&#x27;s default' "
                "style='width:9rem'> mm</label><br><span class='sub'>FDM printers lay plastic down slightly "
                "thicker than drawn, which makes holes and grooves tight. This moves every printed surface into "
                "the material by the amount you set, so the part comes out at its true size: outsides come in "
                "and holes and grooves open up. Typical FDM printers need 0.1 to 0.2 mm; use 0 for resin "
                "printing or CNC machining.</span></div>"
                "<p class='heads-up'><strong>STL files only.</strong> It changes only the STL a tool makes. "
                "SVG, DXF and STEP files stay at their true size. Tools with this setting start with it; the "
                "others ignore it. Leave it empty to use each tool's own default.</p>"
                "<p class='sub'>A design you open from My designs or a shared link keeps its own values.</p>"
                "<button type='submit'>Save</button><p class='msg' id='printer-msg' role='status'></p></form>")

    def sign_ins_section(acct: str) -> str:
        from .oauth import PROVIDERS
        names = {"email": "Email link", **{p: v["label"] for p, v in PROVIDERS.items()}}
        methods = ", ".join(dict.fromkeys(names.get(i["provider"], i["provider"]) for i in accounts.identities(acct)))
        cookie = request.cookies.get(COOKIE_NAME)
        current = accounts.session_id(cookie) if cookie else None
        sessions = accounts.list_sessions(acct)
        out = ["<h2 id='sign-ins'>Sign-ins and connected apps</h2>"]
        if methods:
            out.append(f"<p>You sign in with: {html.escape(methods)}</p>")
        for s in (x for x in sessions if x["kind"] == "web"):
            when = _date(s["last_used_at"] or s["created_at"])
            if s["id"] == current:
                out.append(f"<div class='sess'><span>{html.escape(browser_name(s['label']))} · this browser · "
                           f"last used {when}</span><button class='secondary' type='button' data-sign-out>"
                           "Sign out</button></div>")
            else:
                out.append(f"<div class='sess'><span>{html.escape(browser_name(s['label']))} · last used {when}"
                           f"</span><button class='secondary' type='button' data-session='{int(s['id'])}'>"
                           "Sign out</button></div>")
        devices = sum(1 for x in sessions if x["kind"] == "device")
        if devices:
            out.append(f"<p>AI apps and add-ins connected: {devices} · "
                       "<a href='/account/devices'>Daily limits and disconnecting</a></p>")
        else:
            out.append("<p class='sub'>No AI apps or add-ins are connected.</p>")
        return "".join(out)

    def data_section(acct: str) -> str:
        if not on("data_download"):
            return ""
        return ("<h2 id='data'>Your data</h2><p>Download a copy of everything your account keeps: your saved "
                "designs (settings and pictures), notes, share links, printer settings, token history, orders and "
                "receipts, reports and newsletter choices. CAD files aren't included: open a design in its tool "
                "to download those.</p><form method='post' action='/account/data'>"
                "<button type='submit'>Download my data (.zip)</button></form>")

    def delete_section(acct: str) -> str:
        bal, free = tokens.balance(acct), tokens.free_remaining(acct)
        bought = bal - free
        lost = (f"Your {_n(bal)} remaining" + (f" are lost, including {bought:,} bought ones" if bought > 0
                                                else " are lost") + ", and can't be refunded afterwards: "
                "if you want a refund of unused bought tokens, ask for it before you delete. ") if bal > 0 else ""
        return ("<h2 id='delete-head'>Delete your account</h2><div class='warn'><p>"
                f"{html.escape(delete_words(on, lost))}</p>"
                "<form id='delete'><p><label>Type your email address to confirm<br>"
                "<input type='email' name='email' autocomplete='off' required></label></p>"
                "<button class='danger' type='submit'>Delete my account</button></form>"
                "<p class='msg' id='delete-msg' role='status'></p></div>")

    def panel(section: str) -> str:
        """A section in its panel, its heading marked (0.48.0)."""
        key = section.split("'", 2)[1] if section.startswith("<h2 id='") else ""
        kind = {"tokens": " tokens", "delete-head": " danger"}.get(key, "")
        if key in ICONS:
            section = section.replace(f"<h2 id='{key}'>", f"<h2 id='{key}'><span class='ic' aria-hidden='true'>"
                                                          f"{ICONS[key]}</span>", 1)
        return f"<section class='panel{kind}'>{section}</section>"

    def page():
        if not on("account_page"):
            return _doc("Not found", "<p>That page isn't here.</p>", 404)
        acct = current_account_id()
        if not acct:
            return redirect("/account/sign-in?next=/account/", code=302)
        email = tokens.account_email(acct) or ""
        tok, dialogs = tokens_section(acct)
        sections = [tok, designs_section(acct), projects_section(acct), sharing_section(acct),
                    history_section(acct), reports_section(acct), newsletter_section(acct), printer_section(acct),
                    sign_ins_section(acct), data_section(acct), delete_section(acct)]
        sections = [s for s in sections if s]
        jump = "".join(f"<a href='#{k}'>{t}</a>" for k, t in JUMP if any(f"<h2 id='{k}'>" in s for s in sections))
        head = (f"<div class='hero'><div class='avatar' aria-hidden='true'>{html.escape((email[:1] or '?').upper())}"
                f"</div><div><h1>Your account</h1><p class='sub' style='margin:0'>Signed in as {html.escape(email)} · "
                "<a href='#' data-sign-out>Sign out</a></p></div></div>"
                f"<nav class='jump' aria-label='On this page'>{jump}</nav>")
        body = head + "".join(panel(s) for s in sections) + dialogs
        extra = ("<script src='/static/cct/tokens.js' data-api-base='/' defer></script>"
                 if on("inline_checkout") else "")
        return _doc("Your account", body + _SCRIPT, extra=extra)

    def data_zip():
        """The account's data, made now and sent straight back (data_export)."""
        from flask import Response
        from .account_routes import current_session
        from .data_export import LIMITED, DataExport
        if not on("data_download"):
            return _doc("Not found", "<p>That page isn't here.</p>", 404)
        s = current_session()
        if not s or s.get("kind") != "web":            # a browser session, never an add-in's token
            return redirect("/account/sign-in?next=/account/", code=303)
        export = DataExport(accounts, host_url=request.host_url)
        if not export.allowed(s["account_id"]):
            return _doc("Your data", f"<p>{html.escape(LIMITED)}</p><p><a href='/account/'>Your account</a></p>", 429)
        data = export.build(s["account_id"])
        day = time.strftime("%Y-%m-%d", time.gmtime(accounts._clock()))
        r = Response(data, mimetype="application/zip")
        r.headers["Content-Disposition"] = f"attachment; filename=cheapcadtools-data-{day}.zip"
        r.headers["Cache-Control"] = "no-store"
        return r

    app.add_url_rule("/account/data", endpoint="cct_account_data", view_func=data_zip, methods=["POST"])
    app.add_url_rule("/account/", endpoint="cct_account_page", view_func=page, methods=["GET"])
    app.add_url_rule("/account", endpoint="cct_account_page_bare", view_func=page, methods=["GET"])


__all__ = ["register_account_page", "history_line", "browser_name", "tool_title", "TOOL_TITLES"]
