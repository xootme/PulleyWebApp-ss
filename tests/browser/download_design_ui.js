// Every file the Download window offers, for a matrix of designs: does it
// download, and does it belong to the design the page registers?
//
// For each design (a link) in 2D and 3D Mode this takes every part × format the
// window lists (_dlParts / _dlFiles — the window's own code), fetches each file
// (tokens off: 200 and not empty), and records {design: cctFullDesign(), files}.
// The records then go to tests/download_design_check.py — the app's own
// charging.design_matches — because the zip builder refuses any file whose
// parameters the registered design doesn't have (bug 30ae49ecd8: the spline
// shaft's `part`). With --write the records are saved to
// tests/data/download_window_files.json, which tests/test_download_design.py
// checks on every pytest run.
//
// Needs a running app (tokens off):
//   QUEUE_DISABLED=1 python app.py --port 5197 --no-debug
//   node tests/browser/download_design_ui.js http://127.0.0.1:5197 [--write]
//   (PYTHON=<interpreter> if .venv314 isn't beside this checkout)
const { spawn, spawnSync } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const BASE = process.argv[2];
const WRITE = process.argv.includes('--write');
const ROOT = path.join(__dirname, '..', '..');
const PY = process.env.PYTHON || path.join(ROOT, '.venv314', 'Scripts', 'python.exe');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9391;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-dlfiles-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1500,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

const DUAL = 'dual=true&p2_teeth=36&center_distance=160&p2_bore=10';
const SPOKES = 'spokes_enabled=1&spokes_hub_od=22&spokes_rim_depth=5&spokes_width=6&spokes_count=5';
const SPLINE = 'bore_shape=spline&spline_type=straight&spline_n=6&spline_minor=23&spline_major=26&spline_width=6';
// name, link query, modes, extra page actions
const DESIGNS = [
  ['round, one pulley', 'family=HTD&pitch=5M&teeth=24&bore=8', ['2d', '3d']],
  ['drive, round bores', `family=HTD&pitch=5M&teeth=24&bore=8&${DUAL}`, ['2d', '3d']],
  ['D-flat + keyway', `family=HTD&pitch=8M&teeth=30&bore=12&hub_flat_depth=1&${DUAL}&p2_hub_keyway_w=4&p2_hub_keyway_h=1.8`, ['2d', '3d']],
  ['hub, captured nuts', 'family=GT&pitch=2M&teeth=20&bore=5&hub_od=16&hub_height=8&hub_screw_size=M3&hub_screw_count=2&hub_screw_hold=nut', ['3d']],
  ['spokes + rim layer', `family=HTD&pitch=8M&teeth=48&bore=12&${SPOKES}&${DUAL}&p2_spokes_enabled=1&p2_spokes_hub_od=22&p2_spokes_rim_depth=5&p2_spokes_width=6&p2_spokes_count=4`, ['2d', '3d'], 'rim'],
  ['metal flanges on spokes', `family=HTD&pitch=8M&teeth=48&bore=12&${SPOKES}&flange_enabled=1&flange_3dprint=0&flange_rim_radius=4.2&flange_plate_height=1`, ['3d']],
  ['printed flanges: separate top, nubs, supports', 'family=HTD&pitch=5M&teeth=40&bore=8&flange_enabled=1&flange_3dprint=1&flange_top_separate=1&flange_nubs_enabled=1&flange_supports_enabled=1&flange_rim_radius=3', ['3d']],
  // printed in place with supports: no flanges part; the pulley's STL twice, plain and
  // -with-supports (the owner, 2026-10-03)
  ['printed flanges in place + supports', `family=GT&pitch=2M&teeth=30&bore=5&belt_height=6&flange_enabled=1&flange_3dprint=1&flange_top_separate=0&flange_supports_enabled=1&flange_rim_radius=2.5&${DUAL}&p2_flange_enabled=1&p2_flange_3dprint=1&p2_flange_top_separate=1&p2_flange_rim_radius=2.5`, ['3d']],
  ['splines on both + rings + washer', `family=HTD&pitch=8M&teeth=24&belt_height=10&${SPLINE}&spline_ring_top=1&spline_ring_bottom=1&spline_washer=1&${DUAL}&p2_bore_shape=spline&p2_spline_type=involute&p2_spline_m=1.5&p2_spline_z=16&p2_spline_pa=30&p2_spline_root=flat&p2_spline_ring_top=1`, ['2d', '3d']],
  ['hex bar, no counterbore', 'family=HTD&pitch=5M&teeth=40&bore=8&bore_shape=spline&spline_type=hex&spline_af=12.7&spline_series=inch&spline_ring_top=1&spline_cb_top=0&hub_od=40&hub_height=10&hub_screw_size=M4&hub_screw_count=2', ['3d']],
  ['imperial + T', `family=Imperial&pitch=XL&teeth=20&bore=6&${DUAL}`, ['2d']],
  ['T5 drive', `family=T&pitch=T5&teeth=20&bore=6&${DUAL}`, ['3d']],
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

  // Links: a feature key turns it on only when it says so (spokes_enabled=0 used
  // to switch spokes on), and spokes from a link show Gen. Rim Layer.
  await send('Page.navigate', { url: `${BASE}/?family=HTD&pitch=5M&teeth=40&bore=8&spokes_enabled=0&flange_enabled=0` });
  await sleep(3000);
  check('link spokes_enabled=0 / flange_enabled=0: both stay off',
        !(await js("document.getElementById('spokes1_enabled').checked")) && !(await js("document.getElementById('flange1_enabled').checked")));
  await send('Page.navigate', { url: `${BASE}/?family=HTD&pitch=8M&teeth=48&bore=12&${SPOKES}` });
  await sleep(3000);
  check('link with spokes: Gen. Rim Layer is offered',
        (await js("document.getElementById('spokes1_enabled').checked")) && (await js("document.getElementById('rim-layer1-wrap').style.display")) === 'flex');

  const cases = [];
  for (const [name, query, modes, extra] of DESIGNS) {
    for (const mode of modes) {
      await js('(() => { try { localStorage.clear(); } catch (e) {} })()');
      await send('Page.navigate', { url: `${BASE}/?${query}` });
      await sleep(3500);
      if (mode === '3d') { await js("document.getElementById('feature_build').checked || document.getElementById('feature_build').click()"); await sleep(2500); }
      if (extra === 'rim' && mode === '2d') {
        await js(`(() => { for (const n of [1, 2]) { const w = document.getElementById('rim-layer' + n + '-wrap');
          const c = document.getElementById('rim_layer' + n + '_enabled'); if (w && w.style.display !== 'none' && !c.checked) c.click(); } })()`);
      }
      const rec = await js(`(() => {
        const threeD = document.getElementById('feature_build').checked;
        const fmts = threeD ? ['step', 'stl', 'svg', 'dxf'] : ['svg', 'dxf'];
        const files = [];
        // a part's "only" limits the formats the window offers it (cct_download.js onlyList)
        const only = pt => pt.only ? [].concat(pt.only) : null;
        for (const part of _dlParts(threeD))
          for (const fmt of fmts.filter(f => !only(part) || only(part).includes(f)))
            for (const f of _dlFiles(part.id, fmt))
              files.push({ part: part.id, fmt, path: f.path, params: Object.fromEntries(Object.entries(f.params).map(([k, v]) => [k, String(v)])) });
        return { design: cctFullDesign(), files, parts: _dlParts(threeD).map(p => p.id) };
      })()`);
      const label = `${name} [${mode}]`;
      check(`${label}: the window offers files`, rec.files.length > 0, rec.parts);
      // a "Pulley n flanges" part only for a printed top flange made as a separate part,
      // and the pulley's STL with supports beside it only for one printed in place
      const q = new URLSearchParams(query), pre = n => (n === 2 ? 'p2_' : '');
      const sep = n => q.get(pre(n) + 'flange_enabled') === '1' && q.get(pre(n) + 'flange_3dprint') === '1'
                       && q.get(pre(n) + 'flange_top_separate') === '1';
      const sup = n => q.get(pre(n) + 'flange_enabled') === '1' && q.get(pre(n) + 'flange_3dprint') === '1'
                       && q.get(pre(n) + 'flange_top_separate') === '0' && q.get(pre(n) + 'flange_supports_enabled') === '1';
      if (mode === '3d') for (const n of [1, 2]) {
        check(`${label}: pulley ${n} flanges part only for a separate top`, rec.parts.includes('fl' + n) === sep(n), rec.parts);
        const withSup = rec.files.filter(f => f.part === 'p' + n && f.fmt === 'stl' && f.params.with_supports === '1');
        check(`${label}: pulley ${n} STL with supports only with supports in place`, withSup.length === (sup(n) ? 1 : 0), withSup.length);
      }
      // every file downloads (tokens off)
      for (const f of rec.files) {
        const url = f.path + '?' + new URLSearchParams(f.params);
        const res = await js(`fetch(${JSON.stringify(url)}).then(async r => [r.status, (await r.arrayBuffer()).byteLength, r.status >= 400 ? '' : ''])`);
        check(`${label}: ${f.part} ${f.fmt} ${f.path} downloads`, res[0] === 200 && res[1] > 100, res);
      }
      cases.push({ name: label, design: Object.fromEntries(Object.entries(rec.design).map(([k, v]) => [k, String(v)])),
                   files: rec.files.map(f => ({ path: f.path, params: f.params })) });
    }
  }

  // the app's own rule: does each file belong to the registered design?
  const input = JSON.stringify(cases);
  const chk = spawnSync(PY, [path.join(ROOT, 'tests', 'download_design_check.py')], { input, encoding: 'utf8' });
  const lines = (chk.stdout || '').trim().split('\n');
  check('every file belongs to the registered design (charging.design_matches)', chk.status === 0,
        chk.status === 0 ? '' : (lines.join('\n') + (chk.stderr || '')).slice(0, 4000));
  const nFiles = cases.reduce((s, c) => s + c.files.length, 0);
  console.log(`${cases.length} design × mode cases, ${nFiles} files`);
  if (WRITE) {
    const out = path.join(ROOT, 'tests', 'data', 'download_window_files.json');
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, JSON.stringify(cases, null, 1) + '\n');
    console.log('wrote', path.relative(ROOT, out));
  }
  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) if (!r.ok) console.log(`FAIL  ${r.name}  -> ${String(typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail)).slice(0, 4000)}`);
  console.log('page errors:', errors.length ? errors.slice(0, 5) : 'none');
  const passed = results.filter(r => r.ok).length;
  console.log(`${passed}/${results.length} passed`);
  process.exitCode = passed === results.length && !errors.length ? 0 : 1;
}

main().catch(e => { console.error(e); chrome.kill(); process.exit(2); });
