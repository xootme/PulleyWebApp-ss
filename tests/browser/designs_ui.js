// My designs (cct_common 0.34.0 saved designs; the owner approved it 2026-10-30, and it
// ships with the saved_designs setting off), driven with the setting on in this run's own
// account store, end to end in headless Chrome over CDP:
//
//   * the Download window offers "Save to My designs"
//   * a download keeps the design on account/designs, with its name and its picture
//     (index.html's cctDesignName / cctDesignThumbnail)
//   * Open (?saved=<id>) on a blank page gives back every setting of a two-pulley drive
//     (cctApplyDesign; designParams is what a link carries)
//   * printer settings (cct_common 0.41.0, printer_settings on here): the account's units and
//     3D Print Compensation fill a fresh page; a design's address and a value changed in this
//     browser keep their own (cctApplyPrinterSettings)
//
// Needs a running app with tokens on and a fresh log dir, as tokens_ui.js:
//   TOKENS_ENABLED=1 PULLEY_LOG_DIR=<tmp> QUEUE_DISABLED=1 \
//     GOOGLE_CLIENT_ID=test GOOGLE_CLIENT_SECRET=test python app.py --port 5099 --no-debug
//   node tests/browser/designs_ui.js http://127.0.0.1:5099 <tmp> <python>
const { spawn, execFileSync } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE, LOGDIR, PY] = process.argv.slice(2);
const ERRLOG = path.join(LOGDIR, 'server_errors.log');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const SOFTWARE_GL = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'];
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-designs-'));
const downloads = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-designs-dl-'));
const devPort = () => { try { return Number(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]); } catch (e) { return 0; } };
const chrome = spawn(CHROME, ['--headless=new', '--remote-debugging-port=0', ...SOFTWARE_GL,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1400,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

// A two-pulley drive with a setting off its default in most cards (3D Mode, both hubs).
const DESIGN = 'family=HTD&pitch=5M&teeth=22&bore=8&dual=true&p2_teeth=36&p2_bore=10&center_distance=120'
  + '&hub_od=24&hub_height=9&hub_screw_size=M4&hub_screw_hold=nut&hub_screw_count=1'
  + '&p2_hub_od=28&p2_hub_height=10&p2_hub_screw_size=M3&p2_hub_screw_hold=thread&p2_hub_screw_count=2'
  + '&belt_height=12&clearance_height=3&flange_enabled=1&flange_3dprint=1&flange_rim_radius=3';

async function main() {
  execFileSync(PY, ['-c', `
from cct_common.settings import settings_for
settings_for(r"${path.join(LOGDIR, 'accounts.sqlite3')}").set("saved_designs", True, by="designs_ui")`]);
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${devPort()}/json/list`)).json()).find(t => t.type === 'page'); }
    catch (e) { await sleep(200); }
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => {
    ws.addEventListener('open', r);
    ws.addEventListener('error', () => j(new Error(`the CDP socket failed (${ws.url})`)));
  });
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
  const settled = async (what, ms = 60000) => {
    const t = Date.now();
    while (Date.now() - t < ms) {
      if (await js("document.readyState === 'complete' && typeof pageIdle === 'function' && pageIdle()").catch(() => false)) return;
      await sleep(150);
    }
    throw new Error(`the page never settled in ${ms / 1000} s (${what})`);
  };
  const waitFor = async (expr, ms = 20000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(250); }
    return false;
  };
  const load = async url => {
    await send('Page.navigate', { url });
    await waitFor("document.readyState === 'complete'");
    if (await js('!!window.cctTokens')) await js('window.cctTokens.ready');
    await sleep(500);
  };
  const fire = expr => js(`setTimeout(() => { ${expr}; }, 0); true`);
  const labelSettles = async () => { await sleep(300); await waitFor("!(document.querySelector('.cct-dl .cct-btn-primary')?.textContent || '').includes('…')"); };

  await send('Runtime.enable'); await send('Page.enable');
  await send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });

  // sign in (a fresh account: the signup grant pays for the download)
  await load(BASE + '/'); await settled('the page');
  await fire("document.querySelector('#cct-account .cct-btn-primary').click()");
  await waitFor("!!document.querySelector('.cct-dialog .cct-input')");
  await js("document.querySelector('.cct-input').value = 'designs-test@example.com'");
  await js("document.querySelector('.cct-dialog .cct-btn-primary').click()");
  await waitFor("document.querySelector('.cct-dialog-text')?.textContent.startsWith('Check your inbox')");
  await js("document.querySelector('.cct-dialog .cct-btn-primary').click()");
  const link = [...fs.readFileSync(ERRLOG, 'utf8').matchAll(/(http:\/\/\S+\/account\/login\?token=[A-Za-z0-9_-]+(?:&next=\S+)?)/g)].pop();
  await load(link[1]);
  await js("document.querySelector('button[type=submit]').click()");
  await sleep(1500);

  // the design, in 3D Mode
  await load(`${BASE}/?${DESIGN}`); await settled('the design');
  await js("(() => { const f = document.getElementById('feature_build'); if (!f.checked) f.click(); return true; })()");
  await waitFor(`document.getElementById('preview-footer').textContent.includes('drag to orbit')`, 60000);
  await settled('3D Mode');
  check('signed in, saved designs on', await js('cctTokens.signedIn && cctTokens.savedDesigns'));
  const A = await js('designParams()');
  check('its name', (await js('cctDesignName()')) === 'HTD 5M 22T + 36T', await js('cctDesignName()'));
  check('its picture: the 3D view', await js("(async () => { const c = await cctDesignThumbnail(); return !!c && c.toDataURL('image/png').length > 2000; })()"));

  // the Download window offers Save; downloading keeps the design
  await fire('openDownloadWindow()');
  await waitFor("!!document.querySelector('.cct-dl')"); await labelSettles();
  check('the Download window offers Save to My designs',
        await js("[...document.querySelectorAll('.cct-dl button, .cct-dl a')].some(b => b.textContent.includes('Save to My designs'))"));
  await js("document.querySelector('.cct-dl .cct-btn-primary').click()");
  const list = () => js("fetch('/account/designs').then(r => r.text())");
  await waitFor("fetch('/account/designs').then(r => r.text()).then(t => t.includes(\"class='card'\"))", 240000);
  const page = await list();
  check('account/designs: the download is there, by its name', page.includes('HTD 5M 22T + 36T'), page.slice(0, 400));
  const thumb = (page.match(/<img src='(designs\/[0-9a-f]+\/thumb)'/) || [])[1];
  check('…with its picture', !!thumb && await js(`fetch('/account/${thumb}').then(r => r.ok && r.headers.get('content-type').startsWith('image/'))`), thumb);
  const open = (page.match(/<a class='open' href='([^']+)'/) || [])[1];
  check('…and an Open link here', !!open && /^\/\?saved=[0-9a-f]+$/.test(open), open);

  // Open on a blank page: every setting back
  await send('Storage.clearDataForOrigin', { origin: new URL(BASE).origin, storageTypes: 'local_storage,session_storage' });
  await load(BASE + '/'); await settled('a blank page');
  check('a blank page is another design', (await js('designParams()')).teeth !== A.teeth);
  await load(BASE + open); await settled('Open');
  await waitFor(`designParams().teeth === ${JSON.stringify(A.teeth)}`, 20000); await settled('Open');
  const B = await js('designParams()');
  const diff = [...new Set([...Object.keys(A), ...Object.keys(B)])].filter(k => String(A[k]) !== String(B[k]))
    .map(k => `${k}: ${A[k]} -> ${B[k]}`);
  check('Open gives back every setting', diff.length === 0, diff);
  check('…in 3D Mode', await js("document.getElementById('feature_build').checked"));

  // Printer settings (cct_common 0.41.0, behind printer_settings): the account's units and
  // compensation fill a fresh page (index.html's cctApplyPrinterSettings), on both pulleys;
  // an address with a design keeps its own; a value changed in this browser stays.
  execFileSync(PY, ['-c', `
from cct_common.settings import settings_for
settings_for(r"${path.join(LOGDIR, 'accounts.sqlite3')}").set("printer_settings", True, by="designs_ui")`]);
  const saved = await js(`fetch('/api/account/printer', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ units: 'in', print_compensation_mm: 0.2 }) }).then(r => r.status)`);
  check('printer settings saved on the account', saved === 200, saved);
  const mmOf = id => js(`Number(document.getElementById('${id}').value)`);
  const fresh = async what => {
    await send('Storage.clearDataForOrigin', { origin: new URL(BASE).origin, storageTypes: 'local_storage,session_storage' });
    await load(BASE + '/'); await settled(what);
  };
  await fresh('a fresh page');
  await waitFor("document.getElementById('units').value === 'in'", 10000);
  check('a fresh page takes the account\'s units', (await js("document.getElementById('units').value")) === 'in');
  check('…and its compensation, on both pulleys',
        Math.abs(await mmOf('print_extra') - 0.2) < 1e-9 && Math.abs(await mmOf('p2_print_extra') - 0.2) < 1e-9,
        [await mmOf('print_extra'), await mmOf('p2_print_extra')]);
  await send('Storage.clearDataForOrigin', { origin: new URL(BASE).origin, storageTypes: 'local_storage,session_storage' });
  await load(`${BASE}/?${DESIGN}`); await settled('a design\'s address'); await sleep(1500);
  check('an address with a design keeps its own values',
        (await js("document.getElementById('units').value")) === 'mm' && (await mmOf('print_extra')) === 0,
        [await js("document.getElementById('units').value"), await mmOf('print_extra')]);
  await fresh('a fresh page again');
  await waitFor("Number(document.getElementById('print_extra').value) > 0", 10000);
  await js("(() => { const el = document.getElementById('print_extra'); el.value = 0.4; saveSettings(); return true; })()");
  await load(BASE + '/'); await settled('a reload'); await sleep(1500);
  check('a value changed in this browser stays', Math.abs(await mmOf('print_extra') - 0.4) < 1e-9, await mmOf('print_extra'));

  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  if (errors.length) results.push({ ok: false });
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
  process.exitCode = results.every(r => r.ok) ? 0 : 1;
  ws.close(); chrome.kill();
}
main().catch(e => { console.error('FAIL', e); chrome.kill(); process.exit(1); });
