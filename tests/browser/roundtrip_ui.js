// A design comes back whole: random designs, made through the page's own
// controls, survive Import and a link (bug hunt section 2, and its later hints:
// a checkbox Import doesn't set, a file key that isn't the control's id, a
// preset or dependent menu that doesn't follow the loaded values).
//
// For each seeded design: a blank page, 3D on (and two pulleys half the time),
// then random changes through the controls themselves — click a checkbox, pick
// a menu option, nudge a number — so the page's own handlers shape the design.
// Then:
//   A = the whole design (designParams: what a link carries)
//   every file the Download window makes, in every format, must embed A key for key
//     (an SVG and DXF had no Belt Height, a single belt only its profile: 2026-10-07),
//     and so must every drawing the 2D view makes (its requests leave the hubs out)
//   import the pulley's STL, SVG, DXF and the 2D view's DXF, each on a blank page -> B
//   open it as a link on a blank page                                   -> C
// B and C must equal A key for key, and the same controls must be visible.
//
// Needs a running app (tokens off):
//   QUEUE_DISABLED=1 python app.py --port 5198 --no-debug
//   node tests/browser/roundtrip_ui.js http://127.0.0.1:5198 [--designs 20] [--seed 1] [--steps 30]
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const BASE = process.argv[2];
const arg = (name, dflt) => { const i = process.argv.indexOf(name); return i > 0 ? +process.argv[i + 1] : dflt; };
const DESIGNS = arg('--designs', 20), SEED = arg('--seed', 1), STEPS = arg('--steps', 30);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9398;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-roundtrip-'));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cct-roundtrip-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1500,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// In the page: a seeded random walk over the visible design controls.
const WALKER = `(() => {
  let a = 0;
  const rnd = () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
                      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  const pool = () => [...document.querySelectorAll('.controls-panel input, .controls-panel select')]
    .filter(el => el.id && !el.closest('.download-card') && !['file', 'button', 'text', 'hidden'].includes(el.type)
                  && !el.disabled && el.offsetParent !== null);
  const fire = (el, ...types) => types.forEach(t => el.dispatchEvent(new Event(t, { bubbles: true })));
  window.__walk = {
    seed(s) { a = s >>> 0; },
    rnd,
    // Random walks alone seldom stack features, so each design starts with some on:
    // folded cards opened (their fields are otherwise out of reach), then on each
    // pulley a hub, spokes and flanges (metal or printed) at random, and a bore shape.
    features() {
      const out = [];
      if (typeof FOLD_CARDS !== 'undefined') FOLD_CARDS.forEach(id => toggleCard(id, true));
      for (const n of [1, 2]) {
        if (n === 2 && !document.getElementById('dual_enable').checked) break;
        // Bore Shape is on the Size card, shown in 2D mode only: set it all the same.
        const shape = document.getElementById('bore' + n + '_shape');
        const opts = [...shape.options].filter(o => !o.disabled);
        shape.value = opts[Math.floor(rnd() * opts.length)].value; fire(shape, 'input', 'change');
        out.push(shape.id + ' = ' + shape.value);
        for (const f of ['hub', 'spokes', 'flange']) {
          const el = document.getElementById(f + n + '_enabled');
          if (el && el.offsetParent !== null && !el.disabled && rnd() < 0.6 && !el.checked) { el.click(); out.push(el.id + ' on'); }
        }
        const pr = document.getElementById('flange' + n + '_3dprint');
        if (pr && pr.offsetParent !== null && rnd() < 0.5) { pr.click(); out.push(pr.id + (pr.checked ? ' on' : ' off')); }
      }
      return out;
    },
    step() {
      // now and then flip 3D mode, so the 2D-only cards (teeth, bore, belt) get changed too
      if (rnd() < 0.06) { document.getElementById('feature_build').click();
                          return 'feature_build ' + (document.getElementById('feature_build').checked ? 'on' : 'off'); }
      const els = pool(); if (!els.length) return null;
      const el = els[Math.floor(rnd() * els.length)];
      if (el.type === 'checkbox' || el.type === 'radio') { el.click(); return el.id + (el.checked ? ' on' : ' off'); }
      if (el.tagName === 'SELECT') {
        const opts = [...el.options].filter(o => !o.disabled && o.value !== el.value);
        if (!opts.length) return null;
        el.value = opts[Math.floor(rnd() * opts.length)].value; fire(el, 'input', 'change');
        return el.id + ' = ' + el.value;
      }
      const lo = el.min !== '' ? +el.min : 0, hi = el.max !== '' ? +el.max : Infinity;
      const st = el.step && el.step !== 'any' ? +el.step : 0.1;
      const cur = parseFloat(el.value) || 0;
      let v = cur > 0 ? cur * (0.6 + 0.8 * rnd()) : lo + rnd() * 10;
      if (/teeth$/.test(el.id)) v = Math.min(v, 72);
      v = Math.min(hi, Math.max(lo, Math.round(v / st) * st));
      el.value = +v.toFixed(4); fire(el, 'input', 'change');
      return el.id + ' = ' + el.value;
    },
  };
})()`;
const PARAMS = `designParams()`;
// Every file the Download window would make for this design: each part shown, in every
// format (STEP folded into its one assembly, as the window sends it).
const FILES = `_oneAssembly(_dlParts(true).flatMap(pt => ['step', 'stl', 'svg', 'dxf'].flatMap(f => _dlFiles(pt.id, f))))`;
// ... and in the 2D view, which offers the drawings only.
const FILES_2D = `_dlParts(false).flatMap(pt => ['svg', 'dxf'].flatMap(f => _dlFiles(pt.id, f)))`;
// The design a file embeds (app._design_of): STEP / STL trailer, DXF 999 comment, SVG metadata.
function embedded(buf) {
  const t = buf.toString('latin1');
  const m = t.match(/\/\* CCT:(\{.+?\}) \*\//) || t.match(/999\r?\nCCT:(\{.+\})/) || t.match(/<cct>([\s\S]+?)<\/cct>/);
  if (!m) return null;
  try { const d = JSON.parse(Buffer.from(m[1], 'latin1').toString('utf8')); return d.cct || d; } catch (e) { return null; }
}
// Controls of the view, not the design (not sent with it, so a load needn't keep them):
// Show sample shaft only shows the shaft in the 3D preview.
const VIEW_ONLY = /^spline\d_show_shaft$/;
// Folded cards (Belt Profile, Size) hide fields without changing the design: unfold first.
const VISIBLE = `typeof FOLD_CARDS !== 'undefined' && FOLD_CARDS.forEach(id => toggleCard(id, true)),
  [...document.querySelectorAll('.controls-panel input, .controls-panel select')]
  .filter(el => el.id && !el.closest('.download-card') && el.offsetParent !== null && !${VIEW_ONLY}.test(el.id))
  .map(el => [el.id, el.type === 'checkbox' || el.type === 'radio' ? String(el.checked) : el.value]).sort()`;

// The design's on/off flags (the page writes them 1 / 0, and dual true / false).
const FLAG = /(^dual|_enabled|_3dprint|_top_separate|_washer|_rounded|_ring_top|_ring_bottom|_cb_top|_cb_bottom|_captured_nut|_split|_show_shaft)$/;
const MODE = `document.getElementById('feature_build').checked`;
// A design with a 3D-only feature (hub, flanges, a splined bore) must come back in 3D
// mode, where its cards show and the server checks them. One without may come back in
// either: the mode is then only a view, so visibility is compared in the original's mode.
const has3d = p => ['hub_od', 'p2_hub_od', 'flange_enabled', 'p2_flange_enabled'].some(k => p[k] !== undefined)
                   || p.bore_shape === 'spline' || p.p2_bore_shape === 'spline';

function diffKeys(a, b) {
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort();
  return keys.filter(k => String(a[k] ?? '') !== String(b[k] ?? ''));
}
// Inputs the page tidies on load without changing the part: the centre distance (the
// server builds the whole-belt-tooth one whatever is typed) and a splined pulley's plain
// bore (the spline makes the hole). Such a difference passes only if the server reads
// both designs the same: identical /api/dimensions.
const TIDIED = (k, p) => k === 'center_distance'
  || (k === 'bore' && p.bore_shape === 'spline') || (k === 'p2_bore' && p.p2_bore_shape === 'spline');
async function diffParams(a, b) {
  const keys = diffKeys(a, b);
  if (keys.length && keys.every(k => TIDIED(k, a))) {
    const dims = async p => (await fetch(`${BASE}/api/dimensions?` + new URLSearchParams(p))).text();
    if (await dims(a) === await dims(b)) return [];
  }
  return keys.map(k => `${k}: ${a[k] ?? '∅'} -> ${b[k] ?? '∅'}`);
}
// The visible controls and what each shows: a control the reload hides or shows, or one
// showing another value — a preset or dependent menu that didn't follow the loaded values
// (Sprocket's and Gear's section 2 finds), even where the parameters sent agree.
function diffVisible(a, b) {
  const A = new Map(a), B = new Map(b);
  return [...a.filter(([id]) => !B.has(id)).map(([id]) => `-${id}`),
          ...b.filter(([id]) => !A.has(id)).map(([id]) => `+${id}`),
          ...a.filter(([id, v]) => B.has(id) && B.get(id) !== v).map(([id, v]) => `${id}: ${v} -> ${B.get(id)}`)];
}

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
  await send('Runtime.enable'); await send('DOM.enable');
  const blank = async (query = '') => {
    await send('Storage.clearDataForOrigin', { origin: new URL(BASE).origin, storageTypes: 'local_storage,session_storage' });
    await send('Page.navigate', { url: `${BASE}/${query ? '?' + query : ''}` });
    for (let i = 0; i < 60; i++) { await sleep(250); if (await js(`typeof buildParams === 'function' && document.readyState === 'complete'`).catch(() => false)) break; }
    await sleep(2000);
  };

  const report = []; let exported = 0, refused = 0;
  for (let d = 0; d < DESIGNS; d++) {
    const seed = SEED * 1000 + d;
    await blank();
    await js(WALKER);
    await js(`__walk.seed(${seed})`);
    const steps = [];
    if (!(await js(`document.getElementById('feature_build').checked`))) { await js(`document.getElementById('feature_build').click()`); steps.push('feature_build on'); }
    if (await js(`__walk.rnd() < 0.5`)) { await js(`document.getElementById('dual_enable').click()`); steps.push('dual_enable ' + (await js(`document.getElementById('dual_enable').checked`) ? 'on' : 'off')); }
    await sleep(800);
    steps.push(...await js('__walk.features()'));
    await sleep(1500);
    for (let s = 0; s < STEPS; s++) { const st = await js('__walk.step()'); if (st) steps.push(st); await sleep(200); }
    await sleep(2500);
    const A = await js(PARAMS), visA = await js(VISIBLE), modeA = await js(MODE);
    const qs = new URLSearchParams(A).toString();

    // Fetched here, not in the page: a big pulley's STL is tens of MB, and passing it
    // back through the debugger as base64 hung the run (HTD 20M 24T, 2026-10-02).
    const rec = { seed, steps, A, problems: [] };
    const got = {};
    for (const f of await js(FILES)) {
      const res = await fetch(`${BASE}${f.path}?${new URLSearchParams(f.params)}`);
      const what = `${f.path}${f.params.pulley === '2' ? ' (pulley 2)' : ''}${f.params.with_supports ? ' (supports)' : ''}`
                   + `${f.params.part ? ' ' + f.params.part : ''}${f.params.flange_which ? ' flange' : ''}`;
      if (res.status !== 200) { rec.problems.push(`export refused (${res.status}): ${what}`); continue; }
      const buf = Buffer.from(await res.arrayBuffer());
      const meta = embedded(buf);
      if (!meta) rec.problems.push(`file: ${what} embeds no design`);
      else for (const k of diffKeys(A, meta)) rec.problems.push(`file: ${what} ${k}: ${A[k] ?? '∅'} -> ${meta[k] ?? '∅'}`);
      const ext = f.path.match(/^\/download\/(stl|svg|dxf)$/);
      if (ext && !f.params.pulley && !f.params.with_supports && !got[ext[1]]) got[ext[1]] = buf;
    }
    // The 2D view's drawings: its own requests leave the hubs out, but each file must
    // still embed the whole design (Sprocket's find: a 2D DXF imported as a plain plate).
    if (await js(MODE)) { await js(`document.getElementById('feature_build').click()`); await sleep(2500); }
    if (await js(MODE)) rec.problems.push('could not switch to the 2D view');
    for (const x of diffKeys(A, await js(PARAMS))) rec.problems.push(`2D view designParams: ${x}`);
    for (const f of await js(FILES_2D)) {
      const res = await fetch(`${BASE}${f.path}?${new URLSearchParams(f.params)}`);
      const what = `2D view ${f.path}${f.params.pulley === '2' ? ' (pulley 2)' : ''}${f.params.part ? ' ' + f.params.part : ''}`;
      if (res.status !== 200) { rec.problems.push(`export refused (${res.status}): ${what}`); continue; }
      const buf = Buffer.from(await res.arrayBuffer());
      const meta = embedded(buf);
      if (!meta) rec.problems.push(`file: ${what} embeds no design`);
      else for (const k of diffKeys(A, meta)) rec.problems.push(`file: ${what} ${k}: ${A[k] ?? '∅'} -> ${meta[k] ?? '∅'}`);
      if (f.path === '/download/dxf' && !f.params.pulley) got['2d.dxf'] = buf;
    }
    if (!Object.keys(got).length) refused++;
    for (const [ext, buf] of Object.entries(got)) {
      exported++;
      const file = path.join(tmp, `design-${seed}.${ext}`);
      fs.writeFileSync(file, buf);
      await blank();
      await js('openImportDialog()');
      const doc = await send('DOM.getDocument', { depth: -1 });
      const q = await send('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector: '#import-file-input' });
      await send('DOM.setFileInputFiles', { nodeId: q.result.nodeId, files: [file] });
      await js('executeImport()');
      await sleep(3500);
      const B = await js(PARAMS);
      if (has3d(A) && !(await js(MODE))) rec.problems.push(`import ${ext}: a 3D design opened in 2D mode`);
      if ((await js(MODE)) !== modeA) { await js(`document.getElementById('feature_build').click()`); await sleep(800); }
      const visB = await js(VISIBLE);
      for (const x of await diffParams(A, B)) rec.problems.push(`import ${ext}: ${x}`);
      for (const x of diffVisible(visA, visB)) rec.problems.push(`import ${ext} visible: ${x}`);
    }
    await blank(qs);
    const C = await js(PARAMS);
    if (has3d(A) && !(await js(MODE))) rec.problems.push('link: a 3D design opened in 2D mode');
    if ((await js(MODE)) !== modeA) { await js(`document.getElementById('feature_build').click()`); await sleep(800); }
    const visC = await js(VISIBLE);
    for (const x of await diffParams(A, C)) rec.problems.push(`link: ${x}`);
    for (const x of diffVisible(visA, visC)) rec.problems.push(`link visible: ${x}`);

    // The same link with every on/off flag spelled another way (section 3: website,
    // hand-written and other apps' links say true / yes / on, or false / no / off).
    const ON = ['true', 'yes', 'on', 'TRUE'], OFF = ['false', 'no', 'off'];
    let k = 0; const R = {};
    for (const [key, v] of Object.entries(A))
      R[key] = !FLAG.test(key) ? v : ['1', 'true'].includes(v) ? ON[k++ % ON.length]
             : ['0', 'false'].includes(v) ? OFF[k++ % OFF.length] : v;
    await blank(new URLSearchParams(R).toString());
    for (const x of await diffParams(A, await js(PARAMS))) rec.problems.push(`respelled link: ${x}`);

    report.push(rec);
    const bad = rec.problems.filter(p => !p.startsWith('export refused'));
    const nRefused = rec.problems.length - bad.length;
    console.log(`${bad.length ? 'FAIL' : 'PASS'}  design ${seed} (${steps.length} changes${nRefused ? `, ${nRefused} file(s) refused` : ''})`
                + (bad.length ? '\n      ' + bad.join('\n      ') : ''));
  }
  fs.writeFileSync(path.join(tmp, 'report.json'), JSON.stringify(report, null, 1));
  const failed = report.filter(r => r.problems.some(p => !p.startsWith('export refused'))).length;
  console.log(`report: ${path.join(tmp, 'report.json')}`);
  console.log('page errors:', errors.length ? [...new Set(errors)].slice(0, 5) : 'none');
  console.log(`${DESIGNS - failed}/${DESIGNS} designs came back whole (${exported} files imported, ${refused} designs with no importable file, link only)`);
  process.exitCode = failed || errors.length ? 1 : 0;
  ws.close(); chrome.kill();
}

main().catch(e => { console.error(e); chrome.kill(); process.exit(2); });
