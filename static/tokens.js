// tokens.js — the page side of the token model (ADR-008, charging.py).
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
(function () {
  'use strict';

  const T = {
    enabled: false,
    unavailable: false,
    signedIn: false,
    email: '',
    balance: 0,
    designId: null,
    buyUrl: '',
    _designJson: null,
  };

  // ── account state and the header box ──────────────────────────────────────

  async function refresh() {
    let r;
    try {
      r = await fetch('/api/account', { credentials: 'same-origin' });
    } catch (e) {
      return;                         // offline: leave the page as it is
    }
    if (r.status === 404) {
      T.enabled = false;
    } else if (r.status === 503) {
      T.enabled = true; T.unavailable = true; T.signedIn = false;
    } else if (r.status === 401) {
      T.enabled = true; T.unavailable = false; T.signedIn = false; T.email = ''; T.balance = 0;
    } else if (r.ok) {
      const d = await r.json();
      T.enabled = true; T.unavailable = false; T.signedIn = true;
      T.email = d.email; T.balance = d.balance;
    }
    render();
  }

  function render() {
    const box = document.getElementById('cct-account');
    if (!box) return;
    box.hidden = !T.enabled;
    if (!T.enabled) return;
    box.textContent = '';
    if (T.unavailable) {
      box.append(el('span', 'cct-account-note', 'Accounts unavailable'));
    } else if (!T.signedIn) {
      const b = el('button', 'cct-btn cct-btn-primary', 'Sign in');
      b.type = 'button';
      b.addEventListener('click', () => signIn());
      box.append(b);
    } else {
      box.append(el('span', 'cct-account-email', T.email));
      const bal = el('span', 'cct-account-balance', `${T.balance} token${T.balance === 1 ? '' : 's'}`);
      bal.id = 'cct-balance';
      box.append(bal);
      const out = el('button', 'cct-btn cct-btn-link', 'Sign out');
      out.type = 'button';
      out.addEventListener('click', signOut);
      box.append(out);
    }
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ── dialogs ───────────────────────────────────────────────────────────────

  // Resolves with the clicked button's value (null for Cancel / Escape /
  // clicking outside). `body` is a node; buttons are {label, value, primary}.
  function dialog(title, body, buttons) {
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
    });
  }

  function message(title, text) {
    return dialog(title, el('p', 'cct-dialog-text', text), [{ label: 'OK', value: true, primary: true }]);
  }

  async function signIn() {
    const body = el('div');
    body.append(el('p', 'cct-dialog-text',
      'Enter your email and we\'ll send you a sign-in link. No password needed.'));
    const input = el('input', 'cct-input');
    input.type = 'email';
    input.placeholder = 'you@example.com';
    input.autocomplete = 'email';
    const status = el('p', 'cct-dialog-status');
    body.append(input, status);
    const send = async (close, box) => {
      const email = input.value.trim();
      if (!email) { status.textContent = 'Enter your email address.'; return; }
      status.textContent = 'Sending…';
      let r;
      try {
        r = await fetch('/api/account/login-link', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, next: location.pathname + location.search }),
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

  async function signOut() {
    await fetch('/api/account/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
    await refresh();
  }

  function buyDialog(needed, balance, buyUrl) {
    const text = needed == null
      ? `You don't have enough tokens for this download (balance: ${balance}).`
      : `This download needs ${needed} token${needed === 1 ? '' : 's'}; you have ${balance}.`;
    if (!buyUrl) return message('Not enough tokens', text + ' Buying tokens isn\'t open yet.');
    return dialog('Not enough tokens', el('p', 'cct-dialog-text', text), [
      { label: 'Cancel', value: null },
      { label: 'Buy tokens', primary: true,
        onClick: close => { window.open(buyUrl, '_blank', 'noopener'); close(true); } },
    ]);
  }

  // ── the design this page shows ────────────────────────────────────────────

  async function ensureDesign() {
    if (typeof window.cctFullDesign !== 'function') return null;
    const design = window.cctFullDesign();
    const json = JSON.stringify(design);
    if (json === T._designJson && T.designId) return T.designId;
    const r = await fetch('/api/design', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ design }),
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
      r = await fetch('/api/tokens/quote', {
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
  async function prepare({ path, params, label, extras = [] }) {
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
      r = await fetch('/api/tokens/quote', {
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

    if (q.cost > q.balance) { await buyDialog(q.cost, q.balance, q.buy_url); return null; }
    if (q.cost === 0) return { extras: always };      // already unlocked: no questions

    const body = el('div');
    const what = label || LABEL[q.tier] || 'this file';
    body.append(el('p', 'cct-dialog-text',
      `Downloading ${what} for this design costs ${q.cost} token${q.cost === 1 ? '' : 's'}. ` +
      `Balance: ${q.balance} → ${q.balance - q.cost}.`));
    if (q.held_tier) {
      body.append(el('p', 'cct-dialog-note',
        `You already paid for ${HELD[q.held_tier]} of this design, so this is only the difference.`));
    }
    const boxes = [];
    if (offered.length) {
      body.append(el('p', 'cct-dialog-text', 'Also download, included for 24 hours:'));
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
      { label: `Download (${q.cost} token${q.cost === 1 ? '' : 's'})`, value: true, primary: true },
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

  const ready = (document.readyState === 'loading')
    ? new Promise(res => document.addEventListener('DOMContentLoaded', () => refresh().then(res)))
    : refresh();

  window.cctTokens = {
    ready,
    isEnabled: async () => { await ready; return T.enabled; },
    get enabled() { return T.enabled; },
    get signedIn() { return T.signedIn; },
    get unavailable() { return T.unavailable; },
    get balance() { return T.balance; },
    get buyUrl() { return T.buyUrl; },
    refresh, signIn, prepare, withDesign, signal, watch, runExtras, handleRefusal,
    ensureDesign, quoteDesign, buyDialog, message,
  };
})();
