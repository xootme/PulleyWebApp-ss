// Drives the download window and token UI (static/tokens.js, index.html
// openDownloadWindow) end to end in headless Chrome over CDP — no Playwright
// needed; Node's built-in WebSocket speaks CDP directly.
//
// Needs a running app. With tokens on, a fresh log dir (the test expects a
// new account with the 20-token signup grant) and no Resend key, so the
// sign-in link lands in <log dir>/server_errors.log:
//
//   TOKENS_ENABLED=1 PULLEY_LOG_DIR=<tmp> QUEUE_DISABLED=1 \
//     GOOGLE_CLIENT_ID=test GOOGLE_CLIENT_SECRET=test python app.py --port 5099 --no-debug
//   node tests/browser/tokens_ui.js http://127.0.0.1:5099 <tmp> <python>
//
// And with tokens off (same command without TOKENS_ENABLED), add `off`:
//   node tests/browser/tokens_ui.js http://127.0.0.1:5099 <tmp> <python> off
//
// <python> is the app's interpreter; it is used to read the finished zips
// and, for the buy-dialog check, to lower the test account's balance.
const { spawn, execFileSync } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE, LOGDIR, PY, MODE] = process.argv.slice(2);
const ERRLOG = path.join(LOGDIR, 'server_errors.log');
const APPDIR = path.resolve(__dirname, '..', '..');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9344;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-tok-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1400,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

// Extensions of the files in the newest zip the app has written.
function newestZip() {
  const out = execFileSync(PY, ['-c', `
import glob, os, zipfile, json
zs = sorted(glob.glob(os.path.join(r"${LOGDIR}", "results", "*", "*.zip")), key=os.path.getmtime)
print(json.dumps(sorted(zipfile.ZipFile(zs[-1]).namelist()) if zs else []))`]).toString();
  return JSON.parse(out);
}
const countExt = names => names.reduce((m, n) => { const e = n.split('.').pop(); m[e] = (m[e] || 0) + 1; return m; }, {});

async function main() {
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(t => t.type === 'page'); }
    catch (e) { await sleep(200); }
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let id = 0; const pending = new Map(); const errors = [];
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
      errors.push(m.params.args.map(a => a.value ?? a.description).join(' '));
  });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const js = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error(expr.slice(0, 80) + ' -> ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text));
    return r.result.result.value;
  };
  const fire = expr => js(`setTimeout(() => { ${expr}; }, 0); true`);
  const waitFor = async (expr, ms = 20000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(250); }
    return false;
  };
  const load = async url => {
    await send('Page.navigate', { url });
    await waitFor("document.readyState === 'complete'");
    await js('window.cctTokens ? window.cctTokens.ready : true');
    await sleep(800);
  };
  const dialogTitle = () => js("document.querySelector('.cct-dialog-title')?.textContent || ''");
  const goLabel = () => js("document.querySelector('.cct-dl .cct-btn-primary')?.textContent || ''");
  const tiers = () => js("[...document.querySelectorAll('.cct-tier')].map(t => t.textContent)");
  const parts = () => js("[...document.querySelectorAll('.cct-dl-parts .cct-check')].map(l => l.textContent.trim())");
  const allTicked = () => js("[...document.querySelectorAll('.cct-dl input[type=checkbox]')].every(c => c.checked)");
  const tick = (idsel, on) => js(`(() => { const c = document.getElementById('${idsel}'); if (c.checked !== ${on}) c.click(); })()`);
  const labelSettles = async () => { await sleep(300); await waitFor("!(document.querySelector('.cct-dl .cct-btn-primary')?.textContent || '').includes('…')"); return goLabel(); };
  const openWindow = async () => { await fire('openDownloadWindow()'); await waitFor("!!document.querySelector('.cct-dl')"); return labelSettles(); };
  const closeWindow = () => js("document.querySelector('.cct-dialog-overlay')?.remove()");
  const history = () => js("fetch('/api/account/history?limit=200').then(r => r.json())");
  const balance = () => js("fetch('/api/account').then(r => r.json()).then(d => d.balance)");
  const waitJob = async () => {
    await waitFor("document.getElementById('progress-modal-overlay').style.display === 'flex'", 10000);
    await waitFor("document.getElementById('progress-modal-overlay').style.display !== 'flex'", 240000);
    await sleep(1000);
  };
  const setMode3D = on => js(`(() => { const e = document.getElementById('feature_build'); if (e.checked !== ${on}) e.click(); })()`);
  const bumpTeeth = by => js(`(() => { const t = document.getElementById('teeth'); t.value = String(Number(t.value) + ${by});
    t.dispatchEvent(new Event('input', {bubbles:true})); t.dispatchEvent(new Event('change', {bubbles:true})); })()`);

  await send('Runtime.enable'); await send('Page.enable');
  await load(BASE + '/');
  await js(`(() => { const on = id => { const e = document.getElementById(id); if (e && !e.checked) e.click(); };
    on('feature_build'); on('dual_enable'); on('spokes1_enabled'); on('flange1_enabled');
    on('flange1_3dprint'); on('flange1_top_separate'); })()`);  // separate top flange: a part of its own
  await sleep(2500);

  if (MODE === 'off') {
    check('tokens off: account box hidden', await js("document.getElementById('cct-account').hidden"));
    const label = await openWindow();
    check('tokens off: tiers carry no token counts', JSON.stringify(await tiers()) === JSON.stringify(['CAD shape', '3D printing shape', '2D drawings']), await tiers());
    check('tokens off: button counts files, not tokens', /^Download zip \(\d+ files\)$/.test(label), label);
    await js("document.querySelector('.cct-dl .cct-btn-primary').click()");
    await waitJob();
    const names = newestZip();
    check('tokens off: zip written with every part', names.length >= 10, names);
    finish(errors); ws.close(); chrome.kill(); return;
  }

  // 1. signed out: the window asks for sign-in
  check('signed out: header offers Sign in', await js("document.querySelector('#cct-account .cct-btn-primary')?.textContent === 'Sign in'"));
  check('signed out: window button says Sign in to download', (await openWindow()) === 'Sign in to download');
  await js("document.querySelector('.cct-dl .cct-btn-primary').click()");
  await waitFor("document.querySelector('.cct-dialog-title')?.textContent === 'Sign in to download'");
  check('signed out: sign-in dialog opens', (await dialogTitle()) === 'Sign in to download');
  // The server is started with GOOGLE_CLIENT_ID/SECRET set (see the header):
  // Google is offered, the others (unset) aren't. Not clicked — it leaves for Google.
  check('sign-in dialog offers Continue with Google, and only that',
        await js(`(() => { const b = [...document.querySelectorAll('.cct-dialog .cct-oauth-btn')];
          return b.length === 1 && b[0].textContent === 'Continue with Google'
            && b[0].getAttribute('href').startsWith('/account/oauth/google/start?next=%2F'); })()`));
  check('sign-in dialog: offers 20 free tokens',
        await js("document.querySelector('.cct-dialog .cct-dialog-offer')?.textContent === 'Get 20 free tokens when you sign up.'"));
  check('sign-in dialog: one expiry rule, reset by signing in',
        await js("[...document.querySelectorAll('.cct-dialog .cct-dialog-note')].some(n => n.textContent.includes('expire after 5 years without a sign-in') && n.textContent.includes('clock resets'))"));
  check('sign-in dialog: "or" before the email field',
        await js("!!document.querySelector('.cct-dialog .cct-oauth-or + .cct-dialog-text')"));
  await js("document.querySelector('.cct-input').value = 'ui-test@example.com'");
  await js("document.querySelector('.cct-dialog .cct-btn-primary').click()");
  await waitFor("document.querySelector('.cct-dialog-text')?.textContent.startsWith('Check your inbox')");
  await js("document.querySelector('.cct-dialog .cct-btn-primary').click()");
  const link = [...fs.readFileSync(ERRLOG, 'utf8').matchAll(/(http:\/\/\S+\/account\/login\?token=[A-Za-z0-9_-]+(?:&next=\S+)?)/g)].pop();
  check('sign-in link logged (dev, no Resend key)', !!link);
  await load(link[1]);
  await js("document.querySelector('button[type=submit]').click()");
  await sleep(1500); await waitFor("document.readyState === 'complete'"); await js('window.cctTokens.ready'); await sleep(1000);
  check('signed in: header shows email and 20 tokens',
        await js("document.querySelector('.cct-account-email')?.textContent === 'ui-test@example.com' && document.getElementById('cct-balance')?.textContent === '20 tokens'"));
  // Clicking the balance opens the token dialog: count, expiry dates, the
  // sign-in note. No payment provider in this run, so no Buy button.
  await js("document.getElementById('cct-balance').click()");
  await waitFor("document.querySelector('.cct-dialog-title')?.textContent === 'Your tokens'");
  check('balance click: token dialog shows the count',
        await js("document.querySelector('.cct-token-count')?.textContent.trim() === '20 tokens'"));
  check('balance click: one expiry date',
        await js("/^Unused tokens expire \\w+ \\d+, \\d{4}$/.test(document.querySelector('.cct-token-expiry')?.textContent || '')"));
  check('balance click: says sign-ins push the date out',
        await js("[...document.querySelectorAll('.cct-dialog .cct-dialog-note')].some(n => n.textContent.includes('moves out every time you sign in') && n.textContent.includes('5 years'))"));
  check('balance click: free tokens are marked non-refundable',
        await js("[...document.querySelectorAll('.cct-dialog .cct-dialog-note')].some(n => n.textContent.includes('20 of your tokens are free ones') && n.textContent.includes(\"can't be refunded\"))"));
  check('balance click: no Buy button without a payment provider',
        await js("![...document.querySelectorAll('.cct-dialog .cct-btn')].some(b => b.textContent === 'Buy tokens')"));
  await js("[...document.querySelectorAll('.cct-dialog .cct-btn')].find(b => b.textContent === 'Close').click()");
  await sleep(300);
  await js(`(() => { const on = id => { const e = document.getElementById(id); if (e && !e.checked) e.click(); };
    on('feature_build'); on('dual_enable'); on('spokes1_enabled'); on('flange1_enabled');
    on('flange1_3dprint'); on('flange1_top_separate'); })()`);  // separate top flange: a part of its own
  await sleep(2500);

  // 2. the 3D window
  let label = await openWindow();
  check('3D: three tier sections with token labels', JSON.stringify(await tiers()) ===
        JSON.stringify(['4 tokens · CAD shape', '3 tokens · 3D printing shape', '2 tokens · 2D drawings']), await tiers());
  const p3 = await parts();
  check('3D: parts list has both pulleys, belt and flanges', p3.some(p => p.startsWith('Pulley 1')) && p3.some(p => p.startsWith('Pulley 2')) && p3.includes('Belt') && p3.some(p => p.startsWith('Pulley 1 flanges')), p3);
  check('3D: everything ticked by default', await allTicked());
  check('3D: red note above the prices', await js("(() => { const n = document.querySelector('.cct-dl .cct-dl-costnote'); const t = document.querySelector('.cct-dl .cct-tier'); return !!n && n.textContent === 'Adding more complicated output files increases the token cost.' && getComputedStyle(n).color === 'rgb(192, 57, 43)' && !!(n.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING); })()"));
  check('3D: button shows 4 tokens', label === 'Download zip (4 tokens)', label);
  check('3D: balance line 20 → 16', (await js("document.querySelector('.cct-dl-status').textContent")) === 'Balance: 20 → 16.');
  await tick('cct-fmt-step', false);
  check('untick STEP: 3 tokens', (await labelSettles()) === 'Download zip (3 tokens)');
  await tick('cct-fmt-stl', false);
  check('untick STL: 2 tokens', (await labelSettles()) === 'Download zip (2 tokens)');
  check('untick STL: flange row greyed, says how to include it', await js(`(() => {
    const cb = document.getElementById('cct-part-fl1');
    return cb.disabled && cb.closest('label').classList.contains('cct-check-off')
      && cb.closest('label').textContent.includes('tick STL to include'); })()`));
  await tick('cct-fmt-stl', true); await labelSettles();
  check('re-tick STL: flange row back, still ticked', await js(`(() => {
    const cb = document.getElementById('cct-part-fl1');
    return !cb.disabled && cb.checked && cb.closest('label').textContent.includes('STL only'); })()`));
  await tick('cct-fmt-stl', false); await labelSettles();
  await tick('cct-fmt-svg', false); await tick('cct-fmt-dxf', false);
  check('untick all: nothing to download, disabled', (await labelSettles()) === 'Nothing to download' &&
        await js("document.querySelector('.cct-dl .cct-btn-primary').disabled"));
  for (const f of ['step', 'stl', 'svg', 'dxf']) await tick(`cct-fmt-${f}`, true);
  check('re-tick: 4 tokens again', (await labelSettles()) === 'Download zip (4 tokens)');

  // 3. download: one zip, one charge
  let before = (await history()).length;
  await js("document.querySelector('.cct-dl .cct-btn-primary').click()");
  check('zip: a spinner shows while the files are built',
        await waitFor("document.getElementById('loading-overlay').style.display === 'flex' && document.getElementById('loading-text').textContent.startsWith('Building your files')", 3000));
  await waitJob();
  const h = (await history()).slice(0, (await history()).length - before);
  check('zip: exactly one spend of 4 for STEP', h.length === 1 && h[0].kind === 'spend' && h[0].amount === -4 && h[0].fmt === 'step', h);
  check('balance 16', (await balance()) === 16);
  const names = newestZip();
  const ext = countExt(names);
  check('zip: STEP for both pulleys and the belt', ext.step === 3, names);
  check('zip: STL for both pulleys, belt and flange', ext.stl >= 4, names);
  check('zip: SVG and DXF for both pulleys and belt', ext.svg >= 3 && ext.dxf >= 3, names);
  check('header shows 16 tokens', await waitFor("document.getElementById('cct-balance')?.textContent === '16 tokens'", 5000));

  label = await openWindow();
  check('same design again: already paid', label === 'Download zip (already paid)', label);
  await closeWindow();

  // 4. the 2D window: drawings only
  await setMode3D(false); await sleep(2000);
  label = await openWindow();
  check('2D: only the drawings tier', JSON.stringify(await tiers()) === JSON.stringify(['2 tokens · 2D drawings']), await tiers());
  check('2D: no cost note with a single tier', await js("!document.querySelector('.cct-dl .cct-dl-costnote')"));
  const p2 = await parts();
  check('2D: parts include the whole-drive drawing', p2.some(p => p.startsWith('Whole drive drawing')), p2);
  check('2D: already paid through the STEP purchase', label === 'Download zip (already paid)', label);
  before = (await history()).length;
  await js("document.querySelector('.cct-dl .cct-btn-primary').click()");
  await waitJob();
  const h2 = (await history()).slice(0, (await history()).length - before);
  check('2D zip: free (logged, nothing spent)', h2.length === 1 && h2[0].kind === 'download' && h2[0].amount === 0, h2);
  const n2 = countExt(newestZip());
  check('2D zip: drawings only', !n2.step && !n2.stl && n2.svg >= 3 && n2.dxf >= 4, n2);

  // 5. a changed design costs again
  await bumpTeeth(2); await sleep(1500);
  label = await openWindow();
  check('changed design: 2D zip priced at 2 tokens', label === 'Download zip (2 tokens)', label);
  await closeWindow();

  // 6. short of tokens
  execFileSync(PY, ['-c', `
from cct_common.tokens import TokenStore
s = TokenStore(r"${path.join(LOGDIR, 'accounts.sqlite3')}")
a = s.account_by_email("ui-test@example.com")
s.credit(a, -(s.balance(a) - 1), kind="adjust", detail="browser test")`], { cwd: APPDIR });
  await js('cctTokens.refresh()');
  await setMode3D(true); await sleep(2000);
  label = await openWindow();
  check('low balance: status says what you have', (await js("document.querySelector('.cct-dl-status').textContent")) === 'You have 1 token.',
        await js("document.querySelector('.cct-dl-status').textContent"));
  await js("document.querySelector('.cct-dl .cct-btn-primary').click()");
  await waitFor("document.querySelector('.cct-dialog-title')?.textContent === 'Not enough tokens'");
  check('low balance: buy dialog', (await dialogTitle()) === 'Not enough tokens');

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
