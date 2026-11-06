// A design opens from its link exactly, for everyone: the link is laid over the page's
// defaults, never over the visitor's saved settings (CCT_App_Baseline §3, "links open the
// exact design"; Sprocket's link_ui.js). Before 2026-11-06 the page restored the saved
// settings first, so every key a link left out came from whoever opened it. A plain visit
// still comes back as it was left.
//
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5197 --no-debug
//   node tests/browser/link_ui.js http://127.0.0.1:5197
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const SOFTWARE_GL = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'];   // as dims_ui.js
const devPort = () => { try { return Number(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]); } catch (e) { return 0; } };
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-link-'));
const chrome = spawn(CHROME, ['--headless=new', '--remote-debugging-port=0', ...SOFTWARE_GL,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1400,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

// The fields a link leaves out below, as the page shows them.
const FIELDS = `(() => ({ bore: document.getElementById('bore').value, hub1_od: document.getElementById('hub1_od').value,
  dual: document.getElementById('dual_enable').checked, flange1: document.getElementById('flange1_enabled').checked,
  units: document.getElementById('units').value, teeth: document.getElementById('teeth').value }))()`;

async function main() {
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
  });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const js = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error(expr.slice(0, 80) + ' -> ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text));
    return r.result.result.value;
  };
  // Every page load waits for the page's own idle hook (index.html's pageIdle), never a fixed sleep.
  const settled = async (what, ms = 60000) => {
    const t = Date.now();
    while (Date.now() - t < ms) {
      if (await js("document.readyState === 'complete' && typeof pageIdle === 'function' && pageIdle()").catch(() => false)) return;
      await sleep(150);
    }
    const waits = await js("typeof pageWaits === 'function' ? pageWaits() : null").catch(() => null);
    throw new Error(`the page never settled in ${ms / 1000} s (${what}); it waits on ${JSON.stringify(waits)}`);
  };
  const load = async url => { await send('Page.navigate', { url }); await settled(url); };
  const LINK = BASE + '/?family=HTD&pitch=5M&teeth=24';

  await send('Runtime.enable'); await send('Page.enable');
  await load(BASE + '/');
  await js('localStorage.clear()');
  await load(LINK);
  const fresh = await js(FIELDS);           // the link on a page with nothing saved: the defaults
  check('the link sets what it names', fresh.teeth === '24', fresh);

  // someone's own saved design, unlike the defaults in every field the link leaves out
  await load(BASE + '/');
  await js(`(() => { const s = collectSettings(); Object.assign(s, { teeth: '40', bore: '12', hub1_od: '33',
    dual_enable: !${fresh.dual}, flange1_enabled: ${fresh.flange1} ? '0' : '1', units: '${fresh.units === 'in' ? 'mm' : 'in'}' });
    localStorage.setItem('pulley_last', JSON.stringify(s)); })()`);
  await load(LINK);
  const linked = await js(FIELDS);
  check('a link opens the same design over saved settings', JSON.stringify(linked) === JSON.stringify(fresh), { fresh, linked });

  // a plain visit comes back as it was left (the link saved its design over the old one, so save again)
  await js(`(() => { const s = collectSettings(); Object.assign(s, { teeth: '40', bore: '12' });
    localStorage.setItem('pulley_last', JSON.stringify(s)); })()`);
  await load(BASE + '/');
  const plain = await js(FIELDS);
  check('a plain visit restores the saved settings', plain.teeth === '40' && plain.bore === '12', plain);

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
  process.exitCode = results.every(r => r.ok) && !errors.length ? 0 : 1;
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
