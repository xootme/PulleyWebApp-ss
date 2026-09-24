// Drives the token UI (static/tokens.js) end to end in headless Chrome over
// CDP — no Playwright needed; Node's built-in WebSocket speaks CDP directly.
//
// Needs a running app. With tokens on, a fresh log dir (the test expects a
// new account with the 10-token signup grant) and no Resend key, so the
// sign-in link lands in <log dir>/server_errors.log:
//
//   TOKENS_ENABLED=1 PULLEY_LOG_DIR=<tmp> QUEUE_DISABLED=1 python app.py --port 5099 --no-debug
//   node tests/browser/tokens_ui.js http://127.0.0.1:5099 <tmp>/server_errors.log <tmp>/dl
//
// And with tokens off (same command without TOKENS_ENABLED), add `off`:
//   node tests/browser/tokens_ui.js http://127.0.0.1:5099 <tmp>/server_errors.log <tmp>/dl off
//
// It checks sign-in, the STEP purchase dialog and its included downloads,
// that every other download of the same design (pulley 2, belt, flange,
// assembly, all parts) comes free by the server's ledger, that a changed
// design costs again, and the buy dialog at zero balance.
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE, ERRLOG, DLDIR, MODE] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9342;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-tok-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1400,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

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
  const waitFor = async (expr, ms = 20000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(250); }
    return false;
  };
  const fire = expr => js(`setTimeout(() => { ${expr}; }, 0); true`);
  const dialogTitle = () => js("document.querySelector('.cct-dialog-title')?.textContent || ''");
  const clickPrimary = () => js("document.querySelector('.cct-dialog .cct-btn-primary').click()");
  const history = () => js("fetch('/api/account/history?limit=200').then(r => r.json())");
  const balance = () => js("fetch('/api/account').then(r => r.json()).then(d => d.balance)");
  const load = async url => {
    await send('Page.navigate', { url });
    await waitFor("document.readyState === 'complete'");
    await js('window.cctTokens ? window.cctTokens.ready : true');
    await sleep(800);
  };

  await send('Runtime.enable'); await send('Page.enable');
  await send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: DLDIR });
  await load(BASE + '/');

  if (MODE === 'off') {
    check('tokens off: account box hidden', await js("document.getElementById('cct-account').hidden"));
    const sig = "(document.cookie.match(/(?:^|; )download_signal=([^;]*)/) || [])[1] || ''";
    const prev = await js(sig);
    await fire("downloadSVG(1)");
    await waitFor(`(${sig}) !== ${JSON.stringify(prev)}`, 20000);
    const now = await js(sig);
    check('tokens off: SVG downloads with no dialog (answered 200)',
          (await dialogTitle()) === '' && now !== prev && now.endsWith('-200'), { prev, now });
    finish(errors); ws.close(); chrome.kill(); return;
  }

  // 1. signed out
  check('signed out: header offers Sign in', await js("document.querySelector('#cct-account .cct-btn-primary')?.textContent === 'Sign in'"));
  await fire("downloadSVG(1)");
  await waitFor("!!document.querySelector('.cct-dialog-title')");
  check('signed out: download opens the sign-in dialog', (await dialogTitle()) === 'Sign in to download');
  await js("document.querySelector('.cct-input').value = 'ui-test@example.com'");
  await clickPrimary();
  await waitFor("document.querySelector('.cct-dialog-text')?.textContent.startsWith('Check your inbox')");
  check('sign-in dialog: link sent', await js("document.querySelector('.cct-dialog-text').textContent.includes('ui-test@example.com')"));
  await clickPrimary();
  const log = fs.readFileSync(ERRLOG, 'utf8');
  const links = [...log.matchAll(/(http:\/\/\S+\/account\/login\?token=[A-Za-z0-9_-]+(?:&next=\S+)?)/g)].map(m => m[1]);
  check('sign-in link logged (dev, no Resend key)', links.length > 0);
  await load(links[links.length - 1]);
  await js("document.querySelector('button[type=submit]').click()");
  await sleep(1500);
  await waitFor("document.readyState === 'complete'");
  await js('window.cctTokens.ready'); await sleep(1000);
  check('signed in: header shows email and 10 tokens',
        await js("document.querySelector('.cct-account-email')?.textContent === 'ui-test@example.com' && document.getElementById('cct-balance')?.textContent === '10 tokens'"),
        await js("document.getElementById('cct-account').textContent"));

  // 2. a full design: two pulleys, 3D features, spokes and flanges on pulley 1
  await js(`(() => {
    const on = id => { const e = document.getElementById(id); if (e && !e.checked) e.click(); };
    on('feature_build'); on('dual_enable'); on('spokes1_enabled'); on('flange1_enabled');
  })()`);
  await sleep(2500);

  let before = (await history()).length;
  await fire("downloadSTEP(1)");
  await waitFor("!!document.querySelector('.cct-dialog-title')");
  const stepDialog = await js("document.querySelector('.cct-dialog').innerText");
  check('STEP: confirm dialog prices 3 tokens', stepDialog.includes('costs 3 tokens') && stepDialog.includes('10 → 7'), stepDialog);
  check('STEP: offers STL, SVG and DXF, all ticked',
        await js("[...document.querySelectorAll('.cct-check')].map(l => l.textContent.trim() + ':' + l.querySelector('input').checked).join(',') === 'STL:true,SVG:true,DXF:true'"));
  await clickPrimary();
  await waitFor("document.getElementById('progress-modal-overlay').style.display === 'none'", 120000);
  await sleep(6000);                                   // included downloads run 0.7 s apart
  let h = (await history()).slice(0, (await history()).length - before);
  const spend = h.filter(r => r.kind === 'spend');
  const free = h.filter(r => r.kind === 'download').map(r => r.fmt).sort();
  check('STEP purchase: one spend of 3', spend.length === 1 && spend[0].amount === -3 && spend[0].fmt === 'step', spend);
  check('STEP purchase: STL, SVG, DXF followed free', JSON.stringify(free) === JSON.stringify(['dxf', 'stl', 'svg']), h);
  check('balance 7 after STEP', (await balance()) === 7);

  // 3. every other download of this design is free, with no dialog
  const freeCalls = [
    ['pulley 2 STL', 'downloadSTL(2)'], ['pulley 2 SVG', 'downloadSVG(2)'], ['pulley 2 DXF', 'downloadDXF(2)'],
    ['belt SVG', 'downloadBeltSVG()'], ['belt DXF', 'downloadBeltDXF()'], ['belt STL', 'downloadBeltSTL()'],
    ['all-parts DXF', 'downloadAllDXF()'], ['all-parts SVG', 'downloadAllSVG()'],
    ['flange STL', "downloadFlange(1, 'top')"], ['flange assembly STL', 'downloadFlangeAssembly(1)'],
    ['all-parts STEP', "startAsyncDownload('all-step')"],
  ];
  for (const [name, call] of freeCalls) {
    before = (await history()).length;
    await fire(call);
    await sleep(1200);
    const title = await dialogTitle();
    if (title) { check(`${name}: free, no dialog`, false, 'dialog: ' + title + ' | ' + await js("document.querySelector('.cct-dialog').innerText")); await js("document.querySelector('.cct-dialog-overlay').remove()"); continue; }
    await waitFor("document.getElementById('progress-modal-overlay').style.display !== 'flex'", 120000);
    await waitFor("document.getElementById('loading-overlay').style.display !== 'flex'", 120000);
    await sleep(1500);
    const rows = (await history()).slice(0, (await history()).length - before);
    check(`${name}: free, no dialog`, rows.length >= 1 && rows.every(r => r.amount === 0), rows);
  }
  check('balance still 7', (await balance()) === 7);

  // 4. a changed design costs again; running out shows the buy dialog
  await js("document.getElementById('teeth').value = String(Number(document.getElementById('teeth').value) + 2); document.getElementById('teeth').dispatchEvent(new Event('input', {bubbles:true})); document.getElementById('teeth').dispatchEvent(new Event('change', {bubbles:true}))");
  await sleep(1500);
  await fire("downloadSVG(1)");
  await waitFor("!!document.querySelector('.cct-dialog-title')");
  check('changed design: SVG priced at 1 token', (await js("document.querySelector('.cct-dialog').innerText")).includes('costs 1 token'),
        await js("document.querySelector('.cct-dialog').innerText"));
  await clickPrimary(); await sleep(3000);
  check('balance 6', (await balance()) === 6);
  for (const t of [4, 6]) {                         // two more STEP purchases: 6 -> 3 -> 0
    await js(`document.getElementById('teeth').value = String(Number(document.getElementById('teeth').value) + ${t}); document.getElementById('teeth').dispatchEvent(new Event('change', {bubbles:true}))`);
    await sleep(1200);
    await fire("downloadSTEP(1)");
    await waitFor("!!document.querySelector('.cct-dialog-title')");
    await js("document.querySelectorAll('.cct-check input').forEach(c => { c.checked = false; })");
    await clickPrimary();
    await waitFor("document.getElementById('progress-modal-overlay').style.display === 'none'", 120000);
    await sleep(1500);
  }
  check('balance 0 after two more STEP', (await balance()) === 0, await balance());
  await js(`document.getElementById('teeth').value = String(Number(document.getElementById('teeth').value) + 2); document.getElementById('teeth').dispatchEvent(new Event('change', {bubbles:true}))`);
  await sleep(1200);
  await fire("downloadSTL(1)");
  await waitFor("!!document.querySelector('.cct-dialog-title')");
  check('out of tokens: buy dialog', (await dialogTitle()) === 'Not enough tokens',
        await js("document.querySelector('.cct-dialog')?.innerText"));
  check('header shows 0 tokens', await js("document.getElementById('cct-balance')?.textContent === '0 tokens'"));

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
