// Import and links apply a design's settings AND the panels that follow them.
//
// Bug 2026-10-02: importing a drive with metal flanges on pulley 1 and printed
// flanges on pulley 2 ticked pulley 2's "3D Print" but left its panel showing
// the metal options — the link / import path (_applyUrlParams) set the inputs
// without the refresh restoring saved settings does (onFlangeChange). This
// imports real files: it downloads a design's STL from the app (the design is
// embedded in it), opens a blank page, imports the file through the Import
// dialog, and checks each pulley's flange panel shows its own type's options;
// then the same through a link.
//
// Needs a running app (tokens off):
//   QUEUE_DISABLED=1 python app.py --port 5197 --no-debug
//   node tests/browser/import_ui.js http://127.0.0.1:5197
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const BASE = process.argv[2];
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9397;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-import-'));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cct-import-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1500,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

const DRIVE = 'family=RPP&pitch=5M&teeth=20&bore=8&belt_height=10&dual=true&p2_teeth=30&p2_bore=10&center_distance=120';
const METAL = n => { const p = n === 2 ? 'p2_' : '';
  return `${p}flange_enabled=1&${p}flange_3dprint=0&${p}flange_rim_radius=7.5&${p}flange_plate_height=1`; };
const PRINTED = n => { const p = n === 2 ? 'p2_' : '';
  return `${p}flange_enabled=1&${p}flange_3dprint=1&${p}flange_rim_radius=4.8&${p}flange_height=1.5&` +
         `${p}flange_top_separate=1&${p}flange_nubs_enabled=1&${p}flange_supports_enabled=1`; };
const CASES = [
  ['metal on 1, printed on 2 (the reported file)', `${DRIVE}&${METAL(1)}&${PRINTED(2)}`, { 1: 'metal', 2: 'printed' }],
  ['printed on 1, metal on 2', `${DRIVE}&${PRINTED(1)}&${METAL(2)}`, { 1: 'printed', 2: 'metal' }],
];

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
  });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const js = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error(expr.slice(0, 80) + ' -> ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text));
    return r.result.result.value;
  };
  await send('Runtime.enable');
  await send('DOM.enable');
  const blank = async () => {
    await send('Page.navigate', { url: `${BASE}/` });
    await sleep(2500);
    await js('(() => { try { localStorage.clear(); } catch (e) {} })()');
    await send('Page.navigate', { url: `${BASE}/` });
    await sleep(3000);
  };
  const to3d = async () => { await js("document.getElementById('feature_build').checked || document.getElementById('feature_build').click()"); await sleep(1500); };
  const shown = elId => js(`(() => { const e = document.getElementById('${elId}'); return !!e && e.style.display !== 'none'; })()`);
  const panels = async (label, want) => {
    for (const n of [1, 2]) {
      const printed = want[n] === 'printed';
      const s = { checked: await js(`document.getElementById('flange${n}_3dprint').checked`),
                  metalOpts: await shown(`flange${n}_metal_opts`), printOpts: await shown(`flange${n}_3dp_opts`),
                  nubs: await shown(`flange${n}_nub_section`) };
      check(`${label}: pulley ${n} is ${want[n]} — its options shown, the other type's hidden`,
            s.checked === printed && s.printOpts === printed && s.metalOpts === !printed, s);
      if (printed) check(`${label}: pulley ${n}'s separate-top nub section shown`, s.nubs, s);
    }
  };

  for (const [name, query, want] of CASES) {
    // a file the app made, holding this design
    await blank();
    const stl = await js(`fetch('/download/stl?${query}&pulley=2').then(async r => {
      const b = new Uint8Array(await r.arrayBuffer()); let s = ''; for (const x of b) s += String.fromCharCode(x); return [r.status, btoa(s)]; })`);
    check(`${name}: the app exports the design`, stl[0] === 200, stl[0]);
    const file = path.join(tmp, `design-${results.length}.stl`);
    fs.writeFileSync(file, Buffer.from(stl[1], 'base64'));

    // import it on a blank page
    await blank();
    await js('openImportDialog()');
    const doc = await send('DOM.getDocument', { depth: -1 });
    const q = await send('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector: '#import-file-input' });
    await send('DOM.setFileInputFiles', { nodeId: q.result.nodeId, files: [file] });
    await js('executeImport()');
    await sleep(2500);
    check(`${name}: imported (dialog closed, two pulleys)`,
          (await js("document.getElementById('import-overlay').style.display")) === 'none'
          && await js("document.getElementById('dual_enable').checked"),
          await js("document.getElementById('import-status').textContent"));
    await to3d();
    await panels(`import · ${name}`, want);

    // and through a link (the same path)
    await blank();
    await send('Page.navigate', { url: `${BASE}/?${query}` });
    await sleep(3000);
    await to3d();
    await panels(`link · ${name}`, want);
  }

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + JSON.stringify(r.detail)}`);
  console.log('page errors:', errors.length ? errors.slice(0, 5) : 'none');
  const passed = results.filter(r => r.ok).length;
  console.log(`${passed}/${results.length} passed`);
  process.exitCode = passed === results.length && !errors.length ? 0 : 1;
}

main().catch(e => { console.error(e); chrome.kill(); process.exit(2); });
