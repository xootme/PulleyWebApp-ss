// tokens.js — the page side of the token model, one copy for every CCT app
// (cct_common/static, 0.29.0; served as static/cct/tokens.js). It began as
// Timing Pulley Generator's static/tokens.js (its ADR-008) and went from app to
// app (Sprocket ADR-050, Gear ADR-040, Worm ADR-021, Bevel ADR-030, Planetary
// ADR-027); the per-app differences are now options.
//
// Options, on the script tag:
//   data-api-base   where this app's own routes are: "" (the default: relative,
//                   for a page under /tools/<app>/) or "/" (an app whose routes
//                   sit at the site's root, as the pulley app's do).
//
// Where it asks:
//   api/account, api/design, api/tokens/quote, api/offer   this app's balance,
//       designs, prices and offer (the shared ledger and business settings,
//       read through this app), at data-api-base;
//   /api/account/providers, /api/account/login-link, /api/account/logout
//       absolute on purpose: one login for every app. On cheapcadtools.com the
//       site sends these root paths to the pulley app, whose sign-in sets the
//       site-wide cct_session cookie; on this app's own run.app address they
//       are this app's (the same routes, the same database).
//
// The offer (the signup grant, prices, packs) comes from api/offer, the
// business settings (cct_common.settings): "Get 20 free tokens when you sign
// up." says whatever the grant is; without an answer it names no number.
//
// Does nothing unless the server has tokens on: /api/account answers 404
// when TOKENS_ENABLED is off, and then every download keeps its old path.
//
// With tokens on, a download goes:
//   prepare()  sign in if needed, register the on-screen design (so every
//              format of it is one purchase), ask /api/tokens/quote what it
//              costs, and confirm a paid download — offering the formats the
//              purchase includes as checkboxes;
//   start      the page triggers the download as before, with design_id added
//              (withDesign);
//   watch()    downloads run in hidden iframes, so their answer can't be read;
//              the server's download_signal cookie ("<ms>-<status>") shows when
//              the response arrived and its status. Only then do the included
//              downloads run — firing them earlier could beat the charge to
//              the server and get them billed too.
//
// The page supplies window.cctFullDesign() (every raw input on screen).
//
// Saved designs (0.34.0, while the server's saved_designs setting is on:
// api/account says saved_designs): the page may also supply
//   window.cctDesignThumbnail()   a picture of the design (a data URL or a
//                                 canvas), sent with api/design at 320x240;
//   window.cctDesignName()        its name for My designs ("HTD 5M 20T");
//   window.cctApplyDesign(params) set the page to a saved design's params.
// The token dialog links My designs (account/designs); a page opened with
// ?saved=<id> asks api/designs/<id> and applies it, by the hook or, without
// one, by reloading with the params as the query; saveDesign() keeps the
// design on screen (the Download window's "Save to My designs").
//
// Buying (0.35.0), while the server's settings say so (api/offer):
//   inline_checkout  "Buy tokens" is a panel on this page: the packs on sale
//                    (the account's last one first), then Stripe's Apple Pay,
//                    Google Pay and Link buttons and card fields, or PayPal's
//                    buttons. Paid, the download that was short goes on. Its
//                    routes are the site's (/api/payments/...), as sign-in's are.
//   price_in_money   money beside tokens: "4 tokens = 20¢" (money(n)).
//
// The account page (0.38.0), while api/account says account_page: the token
// dialog links it, /account/ (absolute: one page for every app), in a new tab.
//
// Share links (0.42.0): a page opened with ?shared=<code> asks api/shared/<code>
// (public, no sign-in) and opens that copy of the design, by cctApplyDesign or
// by reloading with its params, with a line saying it's a shared design.
// Another tool's link opens in that tool.
//
// Referrals (0.49.0), while api/shared says referral: the link's code is kept
// in this page's memory only (no cookie, no storage) and goes with a sign-in
// started here (the email link's request, the OAuth start's ?ref=), so a new
// account made in this visit counts for the sharer. A page that applies the
// design by reloading carries it once as ?cct_ref=, taken off at load.
//
// "My account" (0.47.0), while the offer says my_account_button: the header's
// "Sign in" reads "My account". Signed out it opens sign-in, and after it the
// account page (while the offer says account_page; else back here). Signed
// in, with the account page on, "My account" opens it in a new tab beside
// the balance and Sign out, and the email moves onto that page; with it off,
// the signed-in header is today's. "Sign out" hides below 480 px then.
//
// Printer settings (0.41.0), while api/account says printer: {units,
// print_compensation_mm} (each null for "the tool's default"). They're
// window.cctPrinterSettings, and the page's hook
//   window.cctApplyPrinterSettings(settings)
// is called once, only on a fresh start: an address with no query (no
// ?saved=, no shared link, no design in it), so an opened design keeps its
// own values. The compensation is for the STL only (the owner, 0.48.0).
// One range for every app (0.54.0): cctTokens.printCompensation is {min,
// max, step} (0 to 1.00 mm in 0.01s, as cct_common.printer_settings), and
// cctTokens.clampCompensation(value, cap) brings the account's value inside
// it and under an app's own tooth-size cap, rounded down to the 0.01 grid
// (null stays null), so the hook can use it rather than refuse it.
//
// Rewards (0.51.0, step 6), only while the rewards setting is on: api/account
// says rewards: {lines} ("Bonus waiting: …", the progress toward the next
// bonus) for the "Your tokens" dialog, whose free-tokens note then says
// they're used after bought ones; /api/payments/config says rewards: {pct,
// first}, and the buy panel shows the offer above the packs and the bonus on
// each; a payment's answer carries bonus (and first_used) for the thank-you.
//
// The token dialog (0.36.0), while the newsletter_opt_in setting is on
// (api/account says newsletter): the newsletter checkbox, as the person last
// left it; a change saves at once (api/account/newsletter).
(function () {
  'use strict';

  // data-api-base on this script's tag: "" (relative) or "/" (the site's root).
  const script = document.currentScript;
  const BASE = (script && script.dataset && script.dataset.apiBase) || '';
  const api = path => BASE + path;

  const T = {
    enabled: false,
    unavailable: false,
    signedIn: false,
    email: '',
    balance: 0,
    designId: null,
    buyUrl: '',
    offer: null,                      // api/offer: the grant, prices, packs (null until read)
    savedDesigns: false,              // api/account: the saved_designs setting is on
    newsletter: null,                 // api/account: {subscribed, since, label, label_version} while on
    accountPage: false,               // api/account: the account_page setting is on
    printer: null,                    // api/account: {units, print_compensation_mm} while on
    _designJson: null,
  };

  // ── account state and the header box ──────────────────────────────────────

  async function refresh() {
    let r;
    try {
      r = await fetch(api('api/account'), { credentials: 'same-origin' });
    } catch (e) {
      return;                         // offline: leave the page as it is
    }
    if (r.status === 404) {
      T.enabled = false;
    } else if (r.status === 503) {
      T.enabled = true; T.unavailable = true; T.signedIn = false;
    } else if (r.status === 401) {
      T.enabled = true; T.unavailable = false; T.signedIn = false; T.email = ''; T.balance = 0;
      T.savedDesigns = false; T.newsletter = null; T.accountPage = false; T.printer = null; T.rewards = null;
    } else if (r.ok) {
      const d = await r.json();
      T.enabled = true; T.unavailable = false; T.signedIn = true;
      T.email = d.email; T.balance = d.balance;
      // For the balance dialog: free vs bought, and when each goes (both
      // count from the last sign-in — cct_common.accounts inactivity rules).
      T.free = d.free || 0; T.purchased = d.purchased || 0;
      T.accountExpires = d.account_expires || null;
      if (d.buy_url !== undefined) T.buyUrl = d.buy_url || '';
      T.savedDesigns = !!d.saved_designs;
      T.newsletter = d.newsletter || null;
      T.accountPage = !!d.account_page;
      T.rewards = d.rewards || null;
      T.printer = d.printer ? Object.freeze({ ...d.printer }) : null;
      applyPrinter();
    }
    render();
  }

  // 3D Printer Compensation's range: the same numbers as printer_settings.py
  // (a test keeps them equal), and the clamp an app's hook can use (0.54.0).
  const PRINT_COMPENSATION = Object.freeze({ min: 0, max: 1, step: 0.01 });
  function clampCompensation(value, cap) {
    if (value === null || value === undefined) return null;
    const { min, max, step } = PRINT_COMPENSATION;
    const hi = cap === null || cap === undefined ? max : Math.max(min, Math.min(max, +cap));
    const v = Math.min(Math.max(+value, min), hi);
    return Math.round(Math.floor(v / step + 1e-9) * step * 100) / 100;
  }

  // The account's printer settings, for the page (0.41.0): its hook, once, and
  // only when nothing is being opened (an address with no query).
  let _printerApplied = false;
  function applyPrinter() {
    window.cctPrinterSettings = T.printer;
    if (!T.printer || _printerApplied || location.search) return;
    _printerApplied = true;
    if (typeof window.cctApplyPrinterSettings === 'function') {
      try { window.cctApplyPrinterSettings(T.printer); } catch (e) { /* the page's own; nothing to undo */ }
    }
  }

  // One expiry rule for every token (cct_common.accounts): they end with the
  // account after 5 years without a sign-in, and each sign-in resets that.
  const EXPIRY_NOTE = 'Unused tokens expire after 5 years without a sign-in — ' +
                      'each time you sign in, the clock resets.';

  // The offer, from the business settings. Read once; null if it can't be.
  let _offer = null;
  function loadOffer() {
    if (!_offer) {
      _offer = fetch(api('api/offer'), { credentials: 'same-origin' })
        .then(r => (r.ok ? r.json() : null))
        .catch(() => null)
        .then(d => { T.offer = d; render(); return d; });     // the header follows the offer (0.47.0)
    }
    return _offer;
  }

  // "Get 20 free tokens when you sign up." with the grant as set; no number
  // when the offer couldn't be read, and no line for a grant of 0.
  function signupOffer() {
    const o = T.offer;
    if (!o || typeof o.signup_grant !== 'number') return 'Get free tokens when you sign up.';
    if (o.signup_grant <= 0) return '';
    return `Get ${o.signup_grant} free token${o.signup_grant === 1 ? '' : 's'} when you sign up.`;
  }

  // Tokens a format costs at the offer's prices (svg/dxf/png → 2D), or null.
  function price(fmt) {
    const o = T.offer;
    if (!o || !o.tier_prices) return null;
    const f = String(fmt || '').toLowerCase();
    const tier = (o.format_tier && o.format_tier[f]) || (f === 'png' ? '2d' : f);
    return typeof o.tier_prices[tier] === 'number' ? o.tier_prices[tier] : null;
  }

  function render() {
    const box = document.getElementById('cct-account');
    if (!box) return;
    box.hidden = !T.enabled;
    if (!T.enabled) return;
    box.replaceChildren();                // drawn again when the offer arrives (0.47.0)
    if (T.unavailable) {
      box.append(el('span', 'cct-account-note', 'Accounts unavailable'));
    } else if (!T.signedIn) {
      const mine = myAccount();
      const b = el('button', 'cct-btn cct-btn-primary', mine ? 'My account' : 'Sign in');
      b.type = 'button';
      b.addEventListener('click', () => signIn(mine && accountPageOn() ? '/account/' : null));
      box.append(b);
    } else {
      const page = myAccount() && accountPageOn();
      if (page) {
        const a = el('a', 'cct-btn cct-btn-primary cct-account-mine', 'My account');
        a.href = '/account/'; a.target = '_blank'; a.rel = 'noopener';
        box.append(a);
      } else {
        box.append(el('span', 'cct-account-email', T.email));
      }
      // The balance is a button: it opens the token dialog (balance, when
      // the tokens expire, buy more).
      const bal = el('button', 'cct-account-balance', `${T.balance} token${T.balance === 1 ? '' : 's'}`);
      bal.type = 'button';
      bal.id = 'cct-balance';
      bal.title = 'Your tokens — balance, expiry dates, buy more';
      bal.addEventListener('click', () => tokenDialog());
      box.append(bal);
      const out = el('button', 'cct-btn cct-btn-link' + (page ? ' cct-hide-narrow' : ''), 'Sign out');
      out.type = 'button';
      out.addEventListener('click', signOut);
      box.append(out);
    }
  }

  // The offer's switches for the header (0.47.0).
  const myAccount = () => !!(T.offer && T.offer.my_account_button);
  const accountPageOn = () => !!(T.offer && T.offer.account_page);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ── dialogs ───────────────────────────────────────────────────────────────

  // Resolves with the clicked button's value (null for Cancel / Escape /
  // clicking outside). `body` is a node; buttons are {label, value, primary}.
  // onOpen(close), if given, runs once it's shown: a dialog that closes itself
  // (the buy panel, once paid).
  function dialog(title, body, buttons, onOpen) {
    return new Promise(resolve => {
      const overlay = el('div', 'cct-dialog-overlay');
      const box = el('div', 'cct-dialog');
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      box.append(el('h3', 'cct-dialog-title', title), body);
      const row = el('div', 'cct-dialog-buttons');
      const close = value => {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        resolve(value);
      };
      for (const b of buttons) {
        const btn = el('button', 'cct-btn' + (b.primary ? ' cct-btn-primary' : ''), b.label);
        btn.type = 'button';
        btn.addEventListener('click', () => (b.onClick ? b.onClick(close, box) : close(b.value)));
        row.append(btn);
      }
      box.append(row);
      overlay.append(box);
      overlay.addEventListener('mousedown', e => { if (e.target === overlay) close(null); });
      const onKey = e => { if (e.key === 'Escape') close(null); };
      document.addEventListener('keydown', onKey);
      document.body.append(overlay);
      const first = box.querySelector('input, .cct-btn-primary');
      if (first) first.focus();
      if (onOpen) onOpen(close);
    });
  }

  function message(title, text) {
    return dialog(title, el('p', 'cct-dialog-text', text), [{ label: 'OK', value: true, primary: true }]);
  }

  // "Continue with Google / Microsoft / GitHub": whichever the server has
  // set up (GET /api/account/providers). Fetched once; none on failure.
  let _providers = null;
  async function providers() {
    if (_providers) return _providers;
    try {
      const r = await fetch('/api/account/providers', { credentials: 'same-origin' });
      _providers = r.ok ? (await r.json()).providers || [] : [];
    } catch (e) {
      _providers = [];
    }
    return _providers;
  }

  // Referrals (0.49.0): the share code this page was opened from, or null.
  let shareRef = null;
  {
    const q = new URLSearchParams(location.search), c = q.get('cct_ref');
    if (c !== null) {
      if (/^[2-9a-hjkmnp-z]{10}$/.test(c)) shareRef = c;
      q.delete('cct_ref');
      history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash);
    }
  }

  // next (0.47.0): where to land after signing in; this page by default.
  async function signIn(next) {
    const back = next || location.pathname + location.search;
    const body = el('div');
    const [list] = await Promise.all([providers(), loadOffer()]);
    if (list.length) {
      const next = encodeURIComponent(back);
      const col = el('div', 'cct-oauth');
      for (const p of list) {
        const a = el('a', `cct-btn cct-oauth-btn cct-oauth-${p.id}`);
        // The provider's logo: an inline SVG from our own server (cct_common.oauth.ICONS).
        if (p.icon) a.insertAdjacentHTML('beforeend', p.icon);
        a.append(el('span', '', `Continue with ${p.label}`));
        a.href = `${p.start}?next=${next}` + (shareRef ? `&ref=${shareRef}` : '');
        col.append(a);
      }
      body.append(col, el('div', 'cct-oauth-or', 'or'));
    }
    body.append(el('p', 'cct-dialog-text',
      'Enter your email and we\'ll send you a sign-in link. No password needed.'));
    const input = el('input', 'cct-input');
    input.type = 'email';
    input.placeholder = 'you@example.com';
    input.autocomplete = 'email';
    const status = el('p', 'cct-dialog-status');
    body.append(input, status);
    const offerText = signupOffer();
    if (offerText) body.append(el('p', 'cct-dialog-offer', offerText));
    body.append(el('p', 'cct-dialog-note', EXPIRY_NOTE));
    const send = async (close, box) => {
      const email = input.value.trim();
      if (!email) { status.textContent = 'Enter your email address.'; return; }
      status.textContent = 'Sending…';
      let r;
      try {
        r = await fetch('/api/account/login-link', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(shareRef ? { email, next: back, ref: shareRef } : { email, next: back }),
        });
      } catch (e) {
        status.textContent = 'Network error — please try again.';
        return;
      }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { status.textContent = d.error || `Something went wrong (${r.status}).`; return; }
      box.querySelector('.cct-dialog-buttons').remove();
      input.remove();
      status.textContent = '';
      body.querySelector('.cct-dialog-text').textContent =
        `Check your inbox at ${email}. The link works once and expires in 15 minutes.`;
      const ok = el('button', 'cct-btn cct-btn-primary', 'OK');
      ok.type = 'button';
      ok.addEventListener('click', () => close(true));
      const row = el('div', 'cct-dialog-buttons');
      row.append(ok);
      box.append(row);
      ok.focus();
    };
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') body.parentNode.querySelector('.cct-btn-primary').click();
    });
    return dialog('Sign in to download', body, [
      { label: 'Cancel', value: null },
      { label: 'Email me a link', primary: true, onClick: send },
    ]);
  }

  // ── money beside tokens (0.35.0: the price_in_money setting) ─────────────
  // "20¢", "$1.20", "$5"; '' while the setting is off (or the offer unread).
  function money(n) {
    const o = T.offer || {};
    if (!o.price_in_money || !o.cents_per_token) return '';
    const c = n * o.cents_per_token;
    return c < 100 ? `${c}\u00a2` : `$${(c / 100).toFixed(c % 100 ? 2 : 0)}`;
  }
  const tokensText = n => `${n} token${n === 1 ? '' : 's'}` + (money(n) ? ` = ${money(n)}` : '');

  const _fmtDate = secs => new Date(secs * 1000).toLocaleDateString(undefined,
    { year: 'numeric', month: 'long', day: 'numeric' });

  // The token dialog, from the balance in the header: how many, when they
  // expire (pushed out by every sign-in), and a way to buy more.
  async function tokenDialog() {
    await refresh();                                  // fresh balance and dates
    const body = el('div');
    const big = el('p', 'cct-token-count', `${T.balance}`);
    big.append(el('span', null, ` token${T.balance === 1 ? '' : 's'}` + (money(T.balance) ? ` = ${money(T.balance)}` : '')));
    body.append(big);
    if (T.accountExpires) {
      body.append(el('p', 'cct-token-expiry', `Unused tokens expire ${_fmtDate(T.accountExpires)}`));
    }
    body.append(el('p', 'cct-dialog-note',
      'That date moves out every time you sign in — to 5 years from your latest sign-in.'));
    if (T.free) {
      body.append(el('p', 'cct-dialog-note', T.rewards
        ? `${T.free} of your tokens ${T.free === 1 ? 'is a free one' : 'are free ones'}, including bonus tokens: ` +
          'they\'re used after the ones you buy, and can\'t be refunded.'
        : `${T.free} of your tokens ${T.free === 1 ? 'is' : 'are'} free ones: they're used first, ` +
          'and free tokens can\'t be refunded.'));
    }
    for (const line of (T.rewards && T.rewards.lines) || []) body.append(el('p', 'cct-dialog-note cct-reward', line));
    if (!T.buyUrl) body.append(el('p', 'cct-dialog-note', 'Buying tokens isn\'t open yet.'));
    if (T.savedDesigns) {
      const p = el('p', 'cct-dialog-note'), a = el('a', null, 'My designs');
      a.href = api('account/designs'); a.target = '_blank'; a.rel = 'noopener';
      p.append(a, document.createTextNode(': everything you\'ve downloaded or saved.'));
      body.append(p);
    }
    if (T.newsletter) body.append(newsletterBox());
    if (T.accountPage) {
      const p = el('p', 'cct-dialog-note'), a = el('a', null, 'Your account');
      a.href = '/account/'; a.target = '_blank'; a.rel = 'noopener';
      p.append(a, document.createTextNode(': your history, sign-ins and settings.'));
      body.append(p);
    }
    const buttons = [{ label: 'Close', value: null }];
    if (T.buyUrl) {
      buttons.push({ label: 'Buy tokens', primary: true,
        onClick: close => { window.open(T.buyUrl, '_blank', 'noopener'); close(true); } });
    }
    return dialog('Your tokens', body, buttons);
  }

  // The newsletter checkbox (the newsletter_opt_in setting): ticked as the
  // person left it, and a change is kept at once.
  function newsletterBox() {
    const wrap = el('div', 'cct-newsletter');
    const label = el('label', 'cct-check'), box = el('input'), status = el('p', 'cct-dialog-note');
    box.type = 'checkbox';
    box.checked = !!T.newsletter.subscribed;
    box.addEventListener('change', async () => {
      const want = box.checked;
      box.disabled = true;
      status.textContent = 'Saving…';
      try {
        const d = await postJson(api('api/account/newsletter'),
                                 { subscribed: want, label_version: T.newsletter.label_version });
        T.newsletter = { ...T.newsletter, subscribed: !!d.subscribed, since: d.since };
        box.checked = !!d.subscribed;
        status.textContent = 'Saved';
      } catch (e) {
        box.checked = !want;
        status.textContent = 'Couldn\'t save that. Please try again.';
      }
      box.disabled = false;
    });
    label.append(box, el('span', null, ' ' + T.newsletter.label));
    wrap.append(label, status);
    return wrap;
  }

  async function signOut() {
    await fetch('/api/account/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
    await refresh();
  }

  // ── the buy panel (0.35.0: the inline_checkout setting) ───────────────────

  let _checkout = null;                 // /api/payments/config, read once a purchase
  function checkoutConfig() {
    if (!_checkout) {
      _checkout = fetch('/api/payments/config', { credentials: 'same-origin' })
        .then(r => (r.ok ? r.json() : null)).catch(() => null);
    }
    return _checkout;
  }

  function loadScript(src) {
    return new Promise((ok, fail) => {
      const tag = document.createElement('script');
      tag.src = src; tag.onload = ok; tag.onerror = () => fail(new Error('Couldn\'t load the payment buttons.'));
      document.head.append(tag);
    });
  }

  async function postJson(url, data) {
    const r = await fetch(url, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data || {}) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(d.error || `Something went wrong (${r.status}).`); e.status = r.status; throw e; }
    return d;
  }

  // After paying: what was added, and the bonus while the rewards are on (step 6).
  function thanks(d) {
    if (d.bonus) return `Thank you — ${d.credited} tokens added, plus ${d.bonus} bonus tokens.`;
    if (d.first_used) return `Thank you — ${d.credited} tokens added. This card or PayPal account has already had its first-purchase bonus.`;
    return `Thank you — ${d.credited} tokens added.`;
  }

  const SETTLE_POLL_MS = 2000, SETTLE_WAIT_MS = 60000;
  const PROVIDER_HEADS = { stripe: 'Card, Apple Pay, Google Pay', paypal: 'PayPal' };

  // Resolves 'paid' once the tokens are in (the panel closes itself), null if
  // closed first.
  function buyPanel(needed, balance, cfg) {
    const packs = (cfg.packs || []).filter(p => cfg[p.provider]);
    const body = el('div');
    body.append(el('p', 'cct-dialog-text', needed == null
      ? `You don't have enough tokens for this download (balance: ${balance}).`
      : `This download needs ${tokensText(needed)}; you have ${balance}.`));
    const buttons = new Map();
    const pct = (cfg.rewards && cfg.rewards.pct) || 0;          // step 6: what the next pack comes with
    const bonus = tokens => Math.floor(tokens * pct / 100);
    if (pct) {
      body.append(el('p', 'cct-dialog-text cct-reward', cfg.rewards.first
        ? `Your first pack comes with ${pct}% extra tokens.` : `Your next pack comes with ${pct}% extra tokens.`));
      if (cfg.rewards.first) body.append(el('p', 'cct-dialog-note', 'Once per account and once per card or PayPal account.'));
    }
    for (const provider of ['stripe', 'paypal']) {
      const mine = packs.filter(p => p.provider === provider);
      if (!mine.length) continue;
      const row = el('div', 'cct-packs');
      for (const p of mine) {
        const b = el('button', 'cct-pack', `${p.price} · ${p.tokens} tokens` + (bonus(p.tokens) ? ` + ${bonus(p.tokens)} bonus` : ''));
        b.type = 'button';
        b.setAttribute('aria-pressed', 'false');
        b.addEventListener('click', () => choose(p));
        buttons.set(p.id, b);
        row.append(b);
      }
      body.append(el('p', 'cct-dialog-note', PROVIDER_HEADS[provider]), row);
    }
    const pay = el('div', 'cct-pay');
    const status = el('p', 'cct-dialog-status');
    body.append(pay, status, el('p', 'cct-dialog-note', EXPIRY_NOTE));
    const say = text => { status.textContent = text; };
    let current = null, closePanel = null;

    const paid = async d => {
      _checkout = null;                 // the last pack and balance have moved on
      await refresh();
      say(thanks(d));
      if (closePanel) closePanel('paid');
    };

    // Ask our server until Stripe says the payment went through (the webhook
    // credits it too, exactly once, if the page goes away).
    async function settle(id) {
      const t0 = Date.now();
      for (;;) {
        let r = null, d = {};
        try {
          r = await fetch('/api/payments/stripe/intent/' + encodeURIComponent(id) + '/confirm', {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' });
          d = await r.json().catch(() => ({}));
        } catch (e) { /* a blip: ask again */ }
        if (r && r.ok && d.status === 'paid') return paid(d);
        if (r && !r.ok && r.status < 500) { say(d.error || 'The payment didn\'t go through. You haven\'t been charged.'); return; }
        if (Date.now() - t0 >= SETTLE_WAIT_MS) {
          say('Payment received — your tokens will appear in a moment. Start the download again then.');
          return;
        }
        say('Payment received — your tokens arrive in a moment…');
        await new Promise(res => setTimeout(res, SETTLE_POLL_MS));
      }
    }

    async function stripeFor(p) {
      if (!window.Stripe) await loadScript('https://js.stripe.com/v3/');
      const intent = await postJson('/api/payments/stripe/intent', { pack: p.id });
      if (current !== p) return;
      const stripe = window.Stripe(cfg.stripe.publishable_key);
      const elements = stripe.elements({ clientSecret: intent.client_secret });
      const express = el('div', 'cct-pay-express'), card = el('div', 'cct-pay-card');
      const go = el('button', 'cct-btn cct-btn-primary', `Pay ${p.price}`);
      go.type = 'button';
      pay.append(express, card, go);
      const confirm = async () => {
        say('Paying…');
        const { error } = await stripe.confirmPayment({ elements, clientSecret: intent.client_secret,
          redirect: 'if_required', confirmParams: { return_url: location.href } });
        if (error) { say(error.message || 'The payment didn\'t go through. You haven\'t been charged.'); return; }
        await settle(intent.id);
      };
      const wallets = elements.create('expressCheckout');
      wallets.on('confirm', confirm);
      wallets.mount(express);
      elements.create('payment').mount(card);
      go.addEventListener('click', async () => {
        go.disabled = true;
        const { error } = await elements.submit();
        if (error) say(error.message); else await confirm();
        go.disabled = false;
      });
    }

    async function paypalFor(p) {
      if (!window.paypal) {
        await loadScript('https://www.paypal.com/sdk/js?client-id=' + encodeURIComponent(cfg.paypal.client_id)
                         + '&currency=USD&intent=capture&components=buttons');
      }
      if (current !== p) return;
      const box = el('div', 'cct-pay-paypal');
      pay.append(box);
      window.paypal.Buttons({
        createOrder: async () => (await postJson('/api/payments/paypal/orders', { pack: p.id })).id,
        onApprove: async data => {
          try {
            await paid(await postJson('/api/payments/paypal/orders/' + encodeURIComponent(data.orderID) + '/capture'));
          } catch (e) { say(e.message); }
        },
        onCancel: () => say('Payment cancelled. You haven\'t been charged.'),
        onError: () => say('PayPal ran into a problem. You haven\'t been charged.'),
      }).render(box);
    }

    async function choose(p) {
      current = p;
      for (const [id, b] of buttons) b.setAttribute('aria-pressed', String(id === p.id));
      pay.replaceChildren();
      say('');
      try {
        await (p.provider === 'stripe' ? stripeFor(p) : paypalFor(p));
      } catch (e) {
        if (current === p) say(e.message || 'Couldn\'t start the payment. You haven\'t been charged.');
      }
    }

    // The pack this account bought last; else the smallest that covers it.
    const short = needed == null ? 1 : needed - balance;
    const first = packs.find(p => p.id === cfg.last_pack) || packs.find(p => p.tokens >= short) || packs[packs.length - 1];
    return dialog('Buy tokens', body, [{ label: 'Cancel', value: null }], close => {
      closePanel = close;
      if (first) choose(first);
    });
  }

  // Back from a payment Stripe had to finish on its own page (a bank's
  // check): ?payment_intent=... — confirm it here and say so.
  async function finishRedirect() {
    const q = new URLSearchParams(location.search);
    const id = q.get('payment_intent');
    if (!id || !T.signedIn) return;
    for (const k of ['payment_intent', 'payment_intent_client_secret', 'redirect_status']) q.delete(k);
    history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash);
    try {
      const d = await postJson('/api/payments/stripe/intent/' + encodeURIComponent(id) + '/confirm');
      await refresh();
      if (d.status === 'paid') await message('Thank you', `${thanks(d).replace(/^Thank you — /, '')} Start the download again.`);
      else await message('Payment received', 'Your tokens will appear in a moment.');
    } catch (e) { await message('Payment', e.message); }
  }

  // Not enough tokens: the buy panel while it's on (resolves 'paid' once
  // they're in), else the dialog that opens the buy page.
  async function buyDialog(needed, balance, buyUrl) {
    if (T.offer && T.offer.inline_checkout) {
      const cfg = await checkoutConfig();
      if (cfg && cfg.inline && (cfg.stripe || cfg.paypal)) return buyPanel(needed, balance, cfg);
    }
    const text = needed == null
      ? `You don't have enough tokens for this download (balance: ${balance}).`
      : `This download needs ${needed} token${needed === 1 ? '' : 's'}; you have ${balance}.`;
    if (!buyUrl) return message('Not enough tokens', text + ' Buying tokens isn\'t open yet.');
    const body = el('div');
    body.append(el('p', 'cct-dialog-text', text), el('p', 'cct-dialog-note', EXPIRY_NOTE));
    return dialog('Not enough tokens', body, [
      { label: 'Cancel', value: null },
      { label: 'Buy tokens', primary: true,
        onClick: close => { window.open(buyUrl, '_blank', 'noopener'); close(true); } },
    ]);
  }

  // ── the design this page shows ────────────────────────────────────────────

  // The page's picture of its design at 320x240, at most 40 KB (the server's
  // limit): WebP, else JPEG. null without the hook or when it can't be drawn.
  const THUMB_W = 320, THUMB_H = 240, THUMB_MAX = 40000;
  async function thumbnail() {
    if (typeof window.cctDesignThumbnail !== 'function') return null;
    try {
      let src = await window.cctDesignThumbnail();
      if (!src) return null;
      if (typeof src === 'string') {
        const img = new Image();
        await new Promise((ok, fail) => { img.onload = ok; img.onerror = fail; img.src = src; });
        src = img;
      }
      const w = src.naturalWidth || src.width, h = src.naturalHeight || src.height;
      if (!w || !h) return null;
      const c = document.createElement('canvas');
      c.width = THUMB_W; c.height = THUMB_H;
      const k = Math.min(THUMB_W / w, THUMB_H / h);
      c.getContext('2d').drawImage(src, (THUMB_W - w * k) / 2, (THUMB_H - h * k) / 2, w * k, h * k);
      for (const [type, q] of [['image/webp', 0.8], ['image/jpeg', 0.8], ['image/jpeg', 0.5]]) {
        const url = c.toDataURL(type, q);
        if (url.startsWith('data:' + type) && (url.length - url.indexOf(',') - 1) * 3 / 4 <= THUMB_MAX) return url;
      }
    } catch (e) { /* no picture: the design is still kept */ }
    return null;
  }

  async function ensureDesign() {
    if (typeof window.cctFullDesign !== 'function') return null;
    const design = window.cctFullDesign();
    const json = JSON.stringify(design);
    if (json === T._designJson && T.designId) return T.designId;
    const body = { design };
    if (T.savedDesigns) {                             // what My designs shows, and where this page is
      body.thumb = await thumbnail();
      try { body.name = typeof window.cctDesignName === 'function' ? window.cctDesignName() : null; }
      catch (e) { body.name = null; }
      body.page = location.pathname;
      body.title = document.title;
    }
    const r = await fetch(api('api/design'), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) return null;
    T.designId = (await r.json()).design_id;
    T._designJson = json;
    return T.designId;
  }

  // Price of the whole registered design at one format's tier — the
  // download window's zip. null when signed out or the check fails.
  async function quoteDesign(fmt) {
    if (!T.enabled || !T.signedIn) return null;
    const did = await ensureDesign();
    if (!did) return null;
    let r;
    try {
      r = await fetch(api('api/tokens/quote'), {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design_id: did, fmt }),
      });
    } catch (e) { return null; }
    if (r.status === 401) { await refresh(); return null; }
    if (!r.ok) return null;
    const q = await r.json();
    T.balance = q.balance; T.buyUrl = q.buy_url || ''; render();
    return q;
  }

  // Keep the design on screen in My designs without downloading it.
  // {ok: true} or {ok: false, error}.
  async function saveDesign() {
    if (!T.savedDesigns) return { ok: false, error: 'Saved designs aren\'t on.' };
    const did = await ensureDesign();
    if (!did) return { ok: false, error: 'Couldn\'t save this design — please try again.' };
    try {
      const r = await fetch(api('api/designs'), {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design_id: did }),
      });
      const d = await r.json().catch(() => ({}));
      return r.ok ? { ok: true } : { ok: false, error: d.error || `Couldn't save it (${r.status}).` };
    } catch (e) {
      return { ok: false, error: 'Couldn\'t reach the server — please try again.' };
    }
  }

  // ?saved=<id>: open a saved design here (My designs' Open). Another tool's
  // goes to that tool; a page without window.cctApplyDesign is reloaded with
  // the params as its query, as the admin's "Open this design" link is.
  async function openSaved() {
    const q = new URLSearchParams(location.search);
    const id = q.get('saved');
    if (!id || !T.enabled) return;
    if (!T.signedIn) { await signIn(); return; }
    let r;
    try { r = await fetch(api('api/designs/' + encodeURIComponent(id)), { credentials: 'same-origin' }); }
    catch (e) { return; }
    const d = await r.json().catch(() => ({}));
    if (r.status === 409 && d.open) { location.replace(d.open); return; }
    if (!r.ok) { await message('Saved design', 'That saved design isn\'t here any more.'); return; }
    q.delete('saved');
    if (typeof window.cctApplyDesign === 'function') {
      await window.cctApplyDesign(d.params);
      history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash);
    } else {
      location.replace(location.pathname + '?' + new URLSearchParams(d.params) + location.hash);
    }
  }

  // ?shared=<code>: open a shared design here (0.42.0). The link is public: no
  // sign-in. What the person changes is their own copy.
  async function openShared() {
    const q = new URLSearchParams(location.search);
    const code = q.get('shared');
    if (!code) return;
    let r;
    try { r = await fetch(api('api/shared/' + encodeURIComponent(code)), { credentials: 'same-origin' }); }
    catch (e) { return; }
    const d = await r.json().catch(() => ({}));
    if (r.status === 409 && d.open) { location.replace(d.open); return; }
    if (!r.ok) { await message('Shared design', d.error || 'This design isn\'t shared any more.'); return; }
    if (d.referral) shareRef = code;
    q.delete('shared');
    if (typeof window.cctApplyDesign === 'function') {
      await window.cctApplyDesign(d.params);
      history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash);
      sharedBanner(d.banner, d.note);
    } else {
      const p = new URLSearchParams(d.params);
      if (d.referral) p.set('cct_ref', code);
      location.replace(location.pathname + '?' + p + location.hash);
    }
  }

  // note (0.43.0): the sharer's note, only when they ticked "Include my note".
  function sharedBanner(text, note) {
    if (!text) return;
    const bar = el('div', 'cct-shared-banner'), x = el('button', 'cct-btn cct-btn-link', '×');
    bar.setAttribute('role', 'status');
    x.type = 'button';
    x.setAttribute('aria-label', 'Close');
    x.addEventListener('click', () => bar.remove());
    const words = el('span', null, text);
    if (note) words.append(el('span', 'cct-shared-note', 'Note from the person who shared it: ' + note));
    bar.append(words, x);
    document.body.append(bar);
  }

  function withDesign(url) {
    if (!T.enabled || !T.designId || /[?&]design_id=/.test(url)) return url;
    return url + (url.includes('?') ? '&' : '?') + 'design_id=' + encodeURIComponent(T.designId);
  }

  // ── one download ──────────────────────────────────────────────────────────

  const LABEL = { '2d': '2D file', stl: 'STL', step: 'STEP' };
  const HELD = { '2d': 'the 2D files', stl: 'the STL', step: 'the STEP' };

  // Resolves to {extras: [...]} to go ahead (the extras to run once the
  // download is through), or null to stop. `extras` offered here are
  // {label, run}; items with `always: true` run after every successful
  // download of this kind without being offered (e.g. a rim layer).
  // bought: the tokens for this very download were just bought in the buy
  // panel, so it goes on without asking again.
  async function prepare({ path, params, label, extras = [], bought = false }) {
    const always = extras.filter(x => x.always);
    const offered = extras.filter(x => !x.always);
    if (!T.enabled) return { extras: always };
    if (T.unavailable) {
      await message('Accounts unavailable',
        'Downloads are paused while accounts are unavailable. Please try again shortly.');
      return null;
    }
    if (!T.signedIn) { await signIn(); return null; }

    const did = await ensureDesign();
    if (did) params.design_id = did;
    let r;
    try {
      r = await fetch(api('api/tokens/quote'), {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, params }),
      });
    } catch (e) {
      await message('Network error', 'Couldn\'t reach the server — please try again.');
      return null;
    }
    if (r.status === 401) { await refresh(); await signIn(); return null; }
    if (!r.ok) { await message('Download', `Couldn't price this download (${r.status}).`); return null; }
    const q = await r.json();
    T.balance = q.balance; T.buyUrl = q.buy_url || ''; render();

    if (q.cost > q.balance) {
      if (await buyDialog(q.cost, q.balance, q.buy_url) !== 'paid') return null;
      return prepare({ path, params, label, extras, bought: true });
    }
    if (q.cost === 0) return { extras: always };      // already unlocked: no questions
    if (bought) return { extras: always.concat(offered) };

    const body = el('div');
    const what = label || LABEL[q.tier] || 'this file';
    body.append(el('p', 'cct-dialog-text',
      `Downloading ${what} for this design costs ${tokensText(q.cost)}. ` +
      `Balance: ${q.balance} → ${q.balance - q.cost}.`));
    if (q.held_tier) {
      body.append(el('p', 'cct-dialog-note',
        `You already paid for ${HELD[q.held_tier]} of this design, so this is only the difference.`));
    }
    const boxes = [];
    if (offered.length) {
      body.append(el('p', 'cct-dialog-text', q.redownload === 'forever'
        ? 'Also download, included:' : 'Also download, included for 24 hours:'));
      for (const x of offered) {
        const lab = el('label', 'cct-check');
        const cb = el('input');
        cb.type = 'checkbox';
        cb.checked = true;
        lab.append(cb, document.createTextNode(' ' + x.label));
        body.append(lab);
        boxes.push([cb, x]);
      }
    }
    const go = await dialog(`Download ${what}`, body, [
      { label: 'Cancel', value: null },
      { label: `Download (${tokensText(q.cost)})`, value: true, primary: true },
    ]);
    if (!go) return null;
    return { extras: always.concat(boxes.filter(([cb]) => cb.checked).map(([, x]) => x)) };
  }

  function signal() {
    const m = document.cookie.match(/(?:^|; )download_signal=([^;]*)/);
    return m ? m[1] : null;
  }

  function runExtras(extras) {
    extras.forEach((x, i) => setTimeout(x.run, 700 * (i + 1)));
  }

  // Wait for a hidden-iframe download's answer (see the header comment).
  function watch(prev, extras) {
    const started = Date.now();
    const poll = setInterval(async () => {
      const cur = signal();
      if (cur && cur !== prev) {
        clearInterval(poll);
        const status = parseInt(cur.split('-')[1] || '200', 10);
        if (status === 401) { await refresh(); signIn(); return; }
        if (status === 402) { await refresh(); buyDialog(null, T.balance, T.buyUrl); return; }
        if (status < 400) runExtras(extras);
        refresh();
      } else if (Date.now() - started > 120000) {
        clearInterval(poll);
      }
    }, 300);
  }

  // For fetch()-started jobs: shows sign-in / buy for a refused start.
  // Returns true if it handled the response.
  async function handleRefusal(r) {
    if (r.status !== 401 && r.status !== 402 && r.status !== 503) return false;
    const d = await r.clone().json().catch(() => ({}));
    await refresh();
    if (r.status === 401) signIn();
    else if (r.status === 402) buyDialog(d.needed, d.balance, d.buy_url);
    else message('Accounts unavailable', 'Downloads are paused — please try again shortly.');
    return true;
  }

  const start = () => refresh().then(() => (T.enabled ? loadOffer() : null));   // no offer without tokens
  const ready = (document.readyState === 'loading')
    ? new Promise(res => document.addEventListener('DOMContentLoaded', () => start().then(res)))
    : start();
  // A saved design opens once the page's own scripts (its hooks) have run.
  if (/[?&]payment_intent=/.test(location.search)) ready.then(finishRedirect).catch(() => {});
  if (/[?&]shared=/.test(location.search)) {
    const whenLoaded = document.readyState === 'complete' ? Promise.resolve()
      : new Promise(res => window.addEventListener('load', res, { once: true }));
    Promise.all([ready, whenLoaded]).then(openShared).catch(() => {});
  }
  if (/[?&]saved=/.test(location.search)) {
    const whenLoaded = document.readyState === 'complete' ? Promise.resolve()
      : new Promise(res => window.addEventListener('load', res, { once: true }));
    Promise.all([ready, whenLoaded]).then(openSaved).catch(() => {});
  }

  window.cctTokens = {
    ready,
    isEnabled: async () => { await ready; return T.enabled; },
    get enabled() { return T.enabled; },
    get signedIn() { return T.signedIn; },
    get unavailable() { return T.unavailable; },
    get balance() { return T.balance; },
    get buyUrl() { return T.buyUrl; },
    get offer() { return T.offer; },
    get savedDesigns() { return T.savedDesigns; },
    get newsletter() { return T.newsletter; },
    get accountPage() { return T.accountPage; },
    get printer() { return T.printer; },
    printCompensation: PRINT_COMPENSATION, clampCompensation,
    refresh, signIn, tokenDialog, prepare, withDesign, signal, watch, runExtras, handleRefusal,
    ensureDesign, quoteDesign, buyDialog, message, loadOffer, signupOffer, price, saveDesign, openSaved, openShared,
    money, tokensText,
  };
})();
