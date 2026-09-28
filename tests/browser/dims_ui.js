// Drives the Dimensions panel under the preview (the Sprocket app's; the
// server side is /api/dimensions, tested in tests/test_dimensions.py) in
// headless Chrome over CDP: the table, the spec warnings, Auto-fix, the
// drive table for two pulleys, and that the panel remembers being open.
//
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5197 --no-debug
//   node tests/browser/dims_ui.js http://127.0.0.1:5197 [screenshot.png]
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE, SHOT] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9347;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-dims-'));
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
  const waitFor = async (expr, ms = 10000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(150); }
    return false;
  };
  const load = async url => {
    await send('Page.navigate', { url });
    await waitFor("document.readyState === 'complete' && typeof buildParams === 'function'");
    await sleep(1500);
  };
  const setVal = (sel, v) => js(`(() => { const e = document.querySelector('${sel}'); e.value = ${JSON.stringify(v)};
    e.dispatchEvent(new Event('change', {bubbles:true})); e.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  const warnings = () => js("[...document.querySelectorAll('#dims-warnings li')].map(li => li.textContent)");
  const settle = () => sleep(1200);
  let w;
  const rowText = () => js("[...document.querySelectorAll('#dims-table tr')].map(r => r.textContent)");

  await send('Runtime.enable'); await send('Page.enable');
  await load(BASE + '/');
  await js("localStorage.clear()");
  await load(BASE + '/?family=HTD&pitch=5M&teeth=20&bore=8');

  // ── the table ──
  check('panel sits under the preview', await js("document.getElementById('preview-box').nextElementSibling.id === 'dims-results'"));
  await js("document.getElementById('dims-details').open = true");
  await waitFor("document.querySelectorAll('#dims-table tr').length > 3");
  const rows = await rowText();
  check('pitch diameter 31.83 mm', rows.some(r => r.startsWith('Pitch diameter') && r.includes('31.83 mm')), rows);
  check('outside diameter 30.69 mm', rows.some(r => r.startsWith('Outside diameter') && r.includes('30.69 mm')), rows);

  // ── a new design's clearance height is the spec's minimum face width ──
  const ch = () => js("document.getElementById('clearance_height').value");
  await js("localStorage.clear()");
  await load(BASE + '/');
  // ISO 5294 Table 4, MXL unflanged: a 10 mm belt is past the table's 6.4 mm
  // (+2.5), so the face is 12.5 mm and the clearance 2.5
  await setVal('#family', 'Imperial'); await setVal('#pitch', 'MXL');
  await js("document.getElementById('belt_height').value = '10'; onBeltHeightInput()");
  await waitFor("document.getElementById('clearance_height').value === '2.5'", 8000);
  check('new design: clearance makes the face the spec minimum (MXL 10 mm: 12.5)', (await ch()) === '2.5', await ch());
  await settle();
  w = await warnings();
  check('…so a new design has no face-width warning', !w.some(x => x.includes('face')), w);
  await js("document.getElementById('belt_height').value = '4.8'; onBeltHeightInput()");
  await waitFor("document.getElementById('clearance_height').value === '2.3'", 8000);
  check('follows the belt width (MXL 4.8 mm: 7.1)', (await ch()) === '2.3', await ch());
  await setVal('#clearance_height', '1');
  await settle();
  await js("document.getElementById('belt_height').value = '12'; onBeltHeightInput()");
  await settle(); await settle();
  check('a typed clearance stays', (await ch()) === '1', await ch());
  await js("localStorage.clear()");
  await load(BASE + '/?family=HTD&pitch=5M&teeth=20&bore=8&belt_height=15&clearance_height=0.7');
  await settle();
  check('a link keeps its clearance', (await ch()) === '0.7', await ch());
  await js("localStorage.clear()");
  await load(BASE + '/');
  await setVal('#family', 'T'); await setVal('#pitch', 'T5');
  await js("document.getElementById('belt_height').value = '16'; onBeltHeightInput()");
  await settle(); await settle();
  check('no published figure (T5): belt/20', (await ch()) === '0.8', await ch());
  await load(BASE + '/');
  check('a reload keeps it automatic', await js('clearanceAuto'));

  // ── a face-width warning and Auto-fix ──
  await js("localStorage.clear()");
  await load(BASE + '/?family=HTD&pitch=5M&teeth=20&bore=8');
  await setVal('#belt_height', '15'); await setVal('#clearance_height', '0.5');
  await settle();
  w = await warnings();
  check('narrow face warns with its source', w.some(x => x.includes('15.5 mm wide') && x.includes('ISO 13050 Table 17')), w);
  check('Auto-fix offered', await js("!document.getElementById('dims_fix').classList.contains('hidden')"));
  await js("document.getElementById('dims_fix').click()");
  await settle();
  check('Auto-fix set the clearance height', (await js("document.getElementById('clearance_height').value")) === '6', await js("document.getElementById('clearance_height').value"));
  w = await warnings();
  check('…and the warning cleared', !w.some(x => x.includes('face')), w);
  check('Auto-fix hidden again', await js("document.getElementById('dims_fix').classList.contains('hidden')"));

  // ── two pulleys: the drive table and drive warnings ──
  await js("(() => { const d = document.getElementById('dual_enable'); if (!d.checked) d.click(); })()");
  // Lock Ratio would hold 3:1 and put Pulley 2 back to 60 teeth
  await js("(() => { const l = document.getElementById('lock_ratio'); if (l.checked) l.click(); })()");
  await setVal('#p2_teeth', '100');
  await setVal('#center_distance', '30');
  await settle(); await settle();
  const drive = await js("[...document.querySelectorAll('#drive-dims tr')].map(r => r.textContent)");
  check('drive table: wrap and teeth in mesh', drive.some(r => r.startsWith('Wrap on the smaller pulley'))
    && drive.some(r => r.startsWith('Teeth in mesh')), drive);
  check('two columns: Pulley 1 and Pulley 2', (await js("[...document.querySelectorAll('#dims-table th')].map(t => t.textContent).join(',')")) === ',Pulley 1,Pulley 2');
  w = await warnings();
  check('few teeth in mesh warns', w.some(x => x.includes('teeth in mesh')), w);
  check('no flanges on a two-pulley drive warns', w.some(x => x.includes('Neither pulley has flanges')), w);

  // ── remembered open ──
  await js("document.getElementById('dims-details').open = true; rememberDimsOpen()");
  await load(BASE + '/');
  check('the panel stays open across a reload', await js("document.getElementById('dims-details').open"));

  if (SHOT) {
    await load(BASE + '/?family=HTD&pitch=5M&teeth=20&bore=8&dual=true&p2_teeth=60&center_distance=45');
    await settle();
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(SHOT, Buffer.from(shot.result.data, 'base64'));
  }
  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
