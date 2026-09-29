// Drives Bore Shape (the Sprocket app's) in the Size card over CDP: Round /
// D-flat / Keyway / Spline, the ISO 14 presets and ISO 4156 modules, the
// bore a spline sets and locks, what the page sends, the Dimensions rows,
// saving and reloading, links old (a D-flat on the hub) and new (a spline),
// and a design saved before Bore Shape (D-Shaft in Retention) moving over.
// ADR-017: the bore is the hole at the default fit (from /api/spline), the
// retaining ring and splined washer, Retention locked to None, and the
// sample shaft and washer in the download window.
//
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5197 --no-debug
//   node tests/browser/bore_ui.js http://127.0.0.1:5197
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9350;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-bore-'));
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
    await waitFor("document.readyState === 'complete' && typeof buildParams === 'function' && SPLINES !== null");
    await sleep(1500);
  };
  const setSel = (sel, v) => js(`(() => { const e = document.querySelector('${sel}'); e.value = ${JSON.stringify(v)};
    e.dispatchEvent(new Event('change', {bubbles:true})); e.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  const val = sel => js(`document.querySelector('${sel}').value`);
  const params = () => js('buildParams()');
  const shown = sel => js(`!document.querySelector('${sel}').classList.contains('hidden')`);
  const settle = () => sleep(600);                 // /api/spline answers after 150 ms
  const text = sel => js(`document.querySelector('${sel}').textContent`);

  await send('Runtime.enable'); await send('Page.enable');
  await load(BASE + '/');
  await js("localStorage.clear()");
  await load(BASE + '/?family=HTD&pitch=5M&teeth=40&bore=12');
  await js("(() => { const d = document.getElementById('dual_enable'); if (d.checked) d.click(); })()");
  await sleep(800);

  // ── the menu ──
  check('Bore Shape: Round, D-flat, Keyway, Spline',
    JSON.stringify(await js("[...document.querySelectorAll('#bore1_shape option')].map(o => o.value)")) === '["round","flat","key","spline"]');
  check('round by default, nothing extra sent', (await val('#bore1_shape')) === 'round'
    && !('bore_shape' in await params()) && !('hub_flat_depth' in await params()));
  await setSel('#bore1_shape', 'flat');
  check('D-flat shows its depth and sends it', await shown('#bore1_flat_fields') && (await params()).hub_flat_depth === 0.5);
  await setSel('#bore1_shape', 'key');
  let p = await params();
  check('Keyway shows W and depth and sends them', await shown('#bore1_key_fields') && p.hub_keyway_w === 4 && p.hub_keyway_h === 2, p);

  // ── straight-sided splines ──
  await setSel('#bore1_shape', 'spline');
  const presets = await js("[...document.querySelectorAll('#spline1_preset option')].map(o => o.value)");
  check('ISO 14 presets: Custom, 15 light and 20 medium', presets.length === 36 && presets.includes('6,23,26,6'), presets.length);
  await setSel('#spline1_preset', '8,32,38,6');
  await settle();
  check('a preset fills N × d × D × B',
    [await val('#spline1_n'), await val('#spline1_minor'), await val('#spline1_major'), await val('#spline1_width')].join() === '8,32,38,6');
  check('the bore is the hole\'s minor at the default fit (H7 middle), and locked',
    (await val('#bore')) === '32.0125' && await js("document.getElementById('bore').disabled"), await val('#bore'));
  check('…the fit and the sample shaft shown', (await text('#spline1_info')).includes('ISO 14:1982 Table 2')
    && (await text('#spline1_info')).includes('sample shaft'), await text('#spline1_info'));
  check('…with a note saying so', await shown('#bore1_note') && (await js("document.getElementById('bore1_note').textContent")).includes('minor diameter'));
  p = await params();
  check('sends bore_shape=spline and its sizes', p.bore_shape === 'spline' && p.spline_type === 'straight'
    && p.spline_n === '8' && p.spline_major === '38' && !('hub_keyway_w' in p), p);
  await setSel('#spline1_width', '7');
  check('a typed size shows Custom', (await val('#spline1_preset')) === '');
  await setSel('#spline1_width', '6');
  check('…and the matching preset again', (await val('#spline1_preset')) === '8,32,38,6');

  // ── involute splines ──
  await setSel('#spline1_type', 'involute');
  check('Involute shows its fields', await shown('#spline1_involute') && !(await shown('#spline1_straight')));
  await setSel('#spline1_pa', '45');
  const mods45 = await js("[...document.querySelectorAll('#spline1_m option')].map(o => o.value)");
  check('45°: ISO 4156 modules 0.25 to 2.5', mods45[0] === '0.25' && mods45[mods45.length - 1] === '2.5', mods45);
  check('45°: fillet root only', await js("document.getElementById('spline1_root').disabled") && (await val('#spline1_root')) === 'fillet');
  await setSel('#spline1_pa', '30'); await setSel('#spline1_m', '1.5'); await setSel('#spline1_z', '20');
  await setSel('#spline1_root', 'flat');
  await settle();
  check('30° flat root, m 1.5 × 20: bore = ISO 4156\'s minor, 28.6718', (await val('#bore')) === '28.6718', await val('#bore'));
  p = await params();
  check('sends the involute', p.spline_type === 'involute' && p.spline_m === '1.5' && p.spline_z === '20' && p.spline_pa === '30', p);

  // ── the server draws it; the Dimensions panel shows it ──
  const status = await js(`fetch('/api/preview?' + new URLSearchParams(buildParams())).then(r => r.status)`);
  check('the 2D preview draws the spline', status === 200, status);
  await js("document.getElementById('dims-details').open = true");
  await waitFor("[...document.querySelectorAll('#dims-table tr')].some(r => r.textContent.startsWith('Spline major'))", 6000);
  const rows = await js("[...document.querySelectorAll('#dims-table tr')].map(r => r.textContent)");
  check('Dimensions: spline minor and major', rows.some(r => r.startsWith('Spline minor') && r.includes('28.67 mm'))
    && rows.some(r => r.startsWith('Spline major') && r.includes('32.25 mm')), rows);   // m(z + 1.5), flat root
  check('Dimensions: the sample shaft, ring and counterbore', rows.some(r => r.startsWith('Sample shaft major'))
    && rows.some(r => r.startsWith('Retaining ring') && r.includes('DIN 471'))
    && rows.some(r => r.startsWith('Ring counterbore diameter')), rows);

  // ── the retaining ring and washer; Retention ──
  p = await params();
  check('the ring on the top face by default, no washer', p.spline_ring === 'top' && !('spline_washer' in p), p);
  check('…named, with its counterbore', (await text('#spline1_ring_info')).includes('DIN 471')
    && (await text('#spline1_ring_info')).includes('McMaster') && (await text('#spline1_ring_info')).includes('counterbore'),
    await text('#spline1_ring_info'));
  await js("document.getElementById('spline1_washer').click()");
  await settle();
  p = await params();
  check('the washer is sent and shown', p.spline_washer === '1' && (await text('#spline1_ring_info')).includes('washer'), p);
  check('Retention is None and locked, with a note', (await val('#hub1_retention')) === 'none'
    && await js("document.getElementById('hub1_retention').disabled") && await shown('#hub1_spline_note'));
  check('no screw is sent', !('hub_screw_size' in p) && !('hub_screw_count' in p), p);
  const parts = await js('_dlParts(true).map(x => x.id)');
  check('download window: the sample shaft and the washer', parts.includes('sh1') && parts.includes('wa1'), parts);
  const files = await js("_dlFiles('sh1', 'stl').concat(_dlFiles('wa1', 'dxf'), _dlFiles('sh1', 'step'))");
  check('…their routes, no STEP', files.length === 2 && files[0].path === '/download/spline-stl' && files[0].params.part === 'shaft'
    && files[1].path === '/download/spline-dxf' && files[1].params.part === 'washer', files);
  const shaft = await js(`fetch('/download/spline-stl?' + new URLSearchParams(_dlFiles('sh1', 'stl')[0].params)).then(r => r.status)`);
  check('…and the shaft downloads', shaft === 200, shaft);
  await setSel('#spline1_ring', 'none');
  check('no ring: the washer box is off, and no washer part', await js("document.getElementById('spline1_washer').disabled")
    && !(await js('_dlParts(true).map(x => x.id)')).includes('wa1'));
  await setSel('#spline1_ring', 'bottom');
  await settle();
  check('bottom face', (await params()).spline_ring === 'bottom' && (await text('#spline1_ring_info')).includes('bottom face'));

  // ── saved and restored ──
  await js('saveSettings()');
  await load(BASE + '/');
  await settle();
  check('reload keeps the involute spline', (await val('#bore1_shape')) === 'spline' && (await val('#spline1_type')) === 'involute'
    && (await val('#spline1_m')) === '1.5' && (await val('#bore')) === '28.6718', [await val('#bore1_shape'), await val('#spline1_m'), await val('#bore')]);
  check('…and its ring and washer', (await val('#spline1_ring')) === 'bottom' && await js("document.getElementById('spline1_washer').checked")
    && (await val('#hub1_retention')) === 'none');
  await setSel('#bore1_shape', 'round');
  check('back to Round: the bore can be typed again', !(await js("document.getElementById('bore').disabled")) && !(await shown('#bore1_note')));
  check('…and Retention is free again', !(await js("document.getElementById('hub1_retention').disabled")) && !(await shown('#hub1_spline_note')));

  // ── links ──
  await js("localStorage.clear()");
  await load(BASE + '/?family=HTD&pitch=5M&teeth=40&bore=10&hub_od=30&hub_height=12&hub_flat_depth=1.2&hub_screw_size=M4&hub_screw_count=1&hub_captured_nut=1');
  check('old link (D-flat on the hub): Bore Shape D-flat', (await val('#bore1_shape')) === 'flat' && (await val('#hub1_flat_depth')) === '1.2');
  check('…and its screw in Retention', (await val('#hub1_retention')) === 'set_screw_nut' && (await val('#hub1_screw_size')) === 'M4');
  await js("localStorage.clear()");
  await load(BASE + '/?family=HTD&pitch=5M&teeth=40&bore=8&bore_shape=spline&spline_type=straight&spline_n=6&spline_minor=26&spline_major=30&spline_width=6&spline_ring=bottom&spline_washer=1');
  await settle();
  check('new link: the spline and its bore', (await val('#bore1_shape')) === 'spline' && (await val('#spline1_minor')) === '26'
    && (await val('#bore')) === '26.0105' && (await val('#spline1_preset')) === '6,26,30,6', await val('#bore'));
  check('…its ring and washer', (await val('#spline1_ring')) === 'bottom' && await js("document.getElementById('spline1_washer').checked"));

  // ── a design saved before Bore Shape ──
  await js(`localStorage.setItem('pulley_last', JSON.stringify(Object.assign(JSON.parse(localStorage.getItem('pulley_last') || '{}'),
    { family: 'HTD', pitch: '5M', teeth: '40', bore: '12', hub1_retention: 'd_shaft', hub1_flat_depth: '0.8',
      hub1_dshaft_screw: true, hub1_dshaft_screw_type: 'set_screw_insert', hub1_dshaft_screw_size: 'M3',
      bore1_shape: undefined })))`);
  await js(`(() => { const s = JSON.parse(localStorage.getItem('pulley_last')); delete s.bore1_shape;
    localStorage.setItem('pulley_last', JSON.stringify(s)); })()`);
  await load(BASE + '/');
  check('old saved D-Shaft retention: Bore Shape D-flat', (await val('#bore1_shape')) === 'flat' && (await val('#hub1_flat_depth')) === '0.8',
        [await val('#bore1_shape'), await val('#hub1_flat_depth')]);
  check('…its screw becomes the Retention', (await val('#hub1_retention')) === 'set_screw_insert' && (await val('#hub1_screw_size')) === 'M3',
        [await val('#hub1_retention'), await val('#hub1_screw_size')]);

  // ── Pulley 2 has its own ──
  await js("(() => { const d = document.getElementById('dual_enable'); if (!d.checked) d.click(); })()");
  await sleep(1500);
  await setSel('#bore2_shape', 'spline');
  await settle();
  p = await params();
  check('Pulley 2: its own spline, prefixed', p.p2_bore_shape === 'spline' && p.p2_spline_type === 'straight'
    && p.p2_spline_ring === 'top' && (await val('#p2_bore')) === '23.0105', [p, await val('#p2_bore')]);
  check('Pulley 2: its own sample shaft', (await js('_dlParts(true).map(x => x.id)')).includes('sh2'));

  // ── Auto-fix: a spline the part can't hold (bug report 2026-09-29) ──
  const fixBtn = "!document.getElementById('dims_fix').classList.contains('hidden')";
  await js("localStorage.clear()");
  await load(BASE + '/?family=STD&pitch=8M&teeth=22&bore=8&bore_shape=spline&spline_type=straight&spline_n=6&spline_minor=23&spline_major=26&spline_width=6&spline_ring=top&hub_od=25&hub_height=10');
  await js("(() => { const d = document.getElementById('dual_enable'); if (d.checked) d.click(); const f = document.getElementById('feature_build'); if (!f.checked) f.click(); const h = document.getElementById('hub1_enabled'); if (!h.checked) h.click(); })()");
  await settle();
  check('the Hub card names the counterbore', (await text('#hub1_info')).includes('counterbore'), await text('#hub1_info'));
  check('Auto-fix is offered', await waitFor(fixBtn, 8000));
  await js("document.getElementById('dims_fix').click()");
  await settle();
  check('…and grows the hub round the ring', (await val('#hub1_od')) === '37.5', await val('#hub1_od'));
  check('…which clears the Hub card', !(await text('#hub1_info')).includes('⚠'), await text('#hub1_info'));
  await load(BASE + '/?family=HTD&pitch=5M&teeth=24&bore=8&bore_shape=spline&spline_type=straight&spline_n=6&spline_minor=23&spline_major=26&spline_width=6&spline_ring=top&hub_od=25&hub_height=10');
  await settle();
  check('a pulley too small for the spline: Auto-fix offered', await waitFor(fixBtn, 8000));
  await js("document.getElementById('dims_fix').click()");
  await settle();
  check('…picks the largest ISO 14 size that fits, and the hub for it',
    (await val('#spline1_preset')) === '6,16,20,4' && (await val('#spline1_minor')) === '16' && (await val('#hub1_od')) === '30.5',
    [await val('#spline1_preset'), await val('#hub1_od')]);
  check('…and the bore follows', (await val('#bore')).startsWith('16.0'), await val('#bore'));

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
