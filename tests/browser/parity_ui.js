// The page details that keep Timing Pulley Generator in line with Sprocket and
// Gear Designer (the 2026-10-01 UI audit), driven end to end in headless Chrome
// over CDP (same approach as help_ui.js — no Playwright):
//
//   * a first visit titles the left panel "Pulley 1" while Two Pulley Drive is on
//   * the line under the 2D preview is rebuilt on leaving 3D Mode, and names
//     pulley 1, pulley 2 and the drive
//   * unticking and re-ticking Two Pulley Drive keeps the centre distance
//   * the drive bar's right-hand group, and the mm / inch switch (cct_units.js)
//
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5099 --no-debug
//   node tests/browser/parity_ui.js http://127.0.0.1:5099
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-parity-'));
const chrome = spawn(CHROME, ['--headless=new', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1500,1000',
  '--use-angle=swiftshader', '--enable-unsafe-swiftshader', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

async function main() {
  // Chrome picks a free port and writes it to DevToolsActivePort.
  let port = null;
  for (let i = 0; i < 100 && !port; i++) {
    try { port = Number(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]); }
    catch (e) { await sleep(100); }
  }
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page'); }
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
  const waitFor = async (expr, ms = 15000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(150); }
    return false;
  };
  const text = elId => js(`document.getElementById('${elId}').textContent.trim()`);
  const val = elId => js(`document.getElementById('${elId}').value`);
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: BASE + '/' });
  await waitFor("document.readyState === 'complete' && typeof onDualToggle === 'function'");
  await sleep(2500);

  // ── 1. first visit: "Pulley 1" beside "Pulley 2" ────────────────────────────
  check('first visit: Two Pulley Drive is on', await js(`document.getElementById('dual_enable').checked`));
  const t1 = await text('panel1-title');
  check('first visit: left panel says "Pulley 1"', t1 === 'Pulley 1', t1);

  // ── 1b. 3D Mode (rows 13, 32–35) ────────────────────────────────────────────
  check('row 35: a fresh page greys the unticked Hub card',
        await js(`!document.getElementById('hub1_enabled').checked && document.getElementById('hub1-body').style.opacity === '0.45'`));
  await js(`(() => { const f = document.getElementById('feature_build'); f.checked = true; onFeatureBuildToggle(); })()`);
  await waitFor(`document.getElementById('preview-footer').textContent.includes('drag to orbit')`, 30000);
  await sleep(2500);
  check('row 13: Import stays in 3D Mode', await js(`document.getElementById('import_btn').offsetParent !== null`));
  const cards = await js(`[...document.querySelectorAll('#panel1 .card')].filter(c => c.offsetParent !== null)
    .map(c => (c.querySelector(':scope > h2, :scope > .hub-header') || c).textContent.replace(/[▲▼ⓘ]/g, '').replace(/\\s+/g, ' ').trim())`);
  check('row 33: 3D cards Hub → Spokes → Flanges → Download', JSON.stringify(cards) === JSON.stringify(['Hub', 'Spokes', 'Flanges', 'Download']), cards);
  check('row 32: the Drive / Pulley 1 / Pulley 2 picker', await js(`document.getElementById('view3d_which').offsetParent !== null`));
  const shown = () => js(`(() => { const v = []; threeScene.traverse(o => { if (o.isMesh && o.userData.part !== undefined && o.visible) v.push(o.userData.part); }); return v.sort().join(); })()`);
  check('Drive: both pulleys and the belt', (await shown()) === '1,2,belt', await shown());
  await js(`(() => { const s = document.getElementById('view3d_which'); s.value = '2'; s.dispatchEvent(new Event('change')); })()`);
  check('Pulley 2: just pulley 2', (await shown()) === '2', await shown());
  await js(`(() => { const s = document.getElementById('view3d_which'); s.value = '1'; s.dispatchEvent(new Event('change')); })()`);
  check('Pulley 1: just pulley 1', (await shown()) === '1', await shown());
  await js(`(() => { const s = document.getElementById('view3d_which'); s.value = 'drive'; s.dispatchEvent(new Event('change')); })()`);
  await js(`(() => { const f = document.getElementById('feature_build'); f.checked = false; onFeatureBuildToggle(); })()`);
  await sleep(1200);

  // ── 2. the status line names both pulleys and the drive ─────────────────────
  const line2d = await text('preview-footer');
  check('status line: pulley 1, pulley 2, belt, centre',
        /P1: \d+T/.test(line2d) && /P2: \d+T/.test(line2d) && /-tooth belt/.test(line2d) && / C [\d.]+ mm/.test(line2d), line2d);

  // ── 3. leaving 3D Mode puts the 2D status line back ─────────────────────────
  await js(`(() => { const f = document.getElementById('feature_build'); f.checked = true; onFeatureBuildToggle(); })()`);
  check('3D Mode: the 3D view writes its own line',
        await waitFor(`document.getElementById('preview-footer').textContent.includes('drag to orbit')`, 30000),
        await text('preview-footer'));
  await js(`(() => { const f = document.getElementById('feature_build'); f.checked = false; onFeatureBuildToggle(); })()`);
  await sleep(1500);
  const back = await text('preview-footer');
  check('leaving 3D Mode: the 2D line is back', !back.includes('3D') && !back.includes('orbit') && /P1: \d+T/.test(back), back);

  // ── 4. re-ticking Two Pulley Drive keeps the centre distance ────────────────
  await js(`(async () => { document.getElementById('center_distance').value = 120; await correctCenterDistance(); })()`);
  await sleep(800);
  const c0 = parseFloat(await val('center_distance'));
  check('a centre distance near 120 mm is set', Math.abs(c0 - 120) < 3, c0);
  await js(`(() => { const d = document.getElementById('dual_enable'); d.checked = false; onDualToggle(); })()`);
  await sleep(800);
  check('one pulley: left panel says "Pulley"', (await text('panel1-title')) === 'Pulley');
  await js(`(() => { const d = document.getElementById('dual_enable'); d.checked = true; onDualToggle(); })()`);
  await sleep(2500);
  const c1 = parseFloat(await val('center_distance'));
  check('re-ticked: the centre distance is kept', Math.abs(c1 - c0) < 0.01, { before: c0, after: c1 });
  check('re-ticked: left panel says "Pulley 1"', (await text('panel1-title')) === 'Pulley 1');

  // ── 5. the drive bar: units · Import · this app's help · ⓘ (rows 9–13) ────
  const right = () => js(`[...document.querySelector('.drive-bar-right').children]
    .filter(e => e.offsetParent !== null).map(e => e.id || e.className.split(' ')[0]).join()`);
  check('drive bar right, 2D: units, Import, Laser/Waterjet, ⓘ',
        (await right()) === 'drive-units,import_btn,drive-info-btn,btn-help', await right());
  check("Import's icon is 📂", await js(`document.getElementById('import_btn').textContent.startsWith('\u{1F4C2}')`));

  // ── 6. the mm / inch switch (row 10, cct_common's cct_units.js) ─────────────
  const nativeText = elId => js(`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').get.call(document.getElementById('${elId}'))`);
  const typeIn = (elId, s) => js(`(() => { const el = document.getElementById('${elId}');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, '${s}');
    el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  const bound = await js(`[...document.querySelectorAll('label')].filter(l => /\\(mm/.test(l.textContent) && !l.querySelector('.unit-label')).map(l => l.textContent.trim())`);
  check('every "(mm)" label carries the unit label', bound.length === 0, bound);
  const nBound = await js(`[...document.querySelectorAll('input')].filter(i => i._cctUnits).length`);
  check('the length fields are bound', nBound > 30, nBound);
  await js(`document.getElementById('bore').value = 8`);
  const undoBefore = await js(`document.getElementById('undo_btn').disabled`);
  await js(`(() => { const u = document.getElementById('units'); u.value = 'in'; u.dispatchEvent(new Event('change')); })()`);
  await sleep(1200);
  check('inch: Bore shows 0.3150 in', (await nativeText('bore')) === '0.3150', await nativeText('bore'));
  check('inch: the script still reads 8 mm', (await val('bore')) === '8', await val('bore'));
  check('inch: the labels say in', await js(`document.querySelector('label[for="bore"] .unit-label').textContent === 'in'`));
  check('inch: the status line is in inches', / in · PD [\d.]+ in/.test(await text('preview-footer')), await text('preview-footer'));
  check('inch: Pitch diameter in inches', /in$/.test(await text('pitch-circle-info')), await text('pitch-circle-info'));
  check('the switch is not an undo step', (await js(`document.getElementById('undo_btn').disabled`)) === undoBefore);
  await typeIn('bore', '0.5');
  await sleep(600);
  check('typed 0.5 in: the design gets 12.7 mm', (await val('bore')) === '12.7' && (await js(`collectSettings().bore`)) === '12.7', await val('bore'));
  await sleep(1200);
  await send('Page.reload'); await sleep(500);
  await waitFor("document.readyState === 'complete' && typeof onDualToggle === 'function'");
  await sleep(3000);
  check('reload: still inch', (await val('units')) === 'in');
  check('reload: Bore shows 0.5000 in, is 12.7 mm', (await nativeText('bore')) === '0.5000' && (await val('bore')) === '12.7',
        [await nativeText('bore'), await val('bore')]);
  await js(`(() => { const u = document.getElementById('units'); u.value = 'mm'; u.dispatchEvent(new Event('change')); })()`);
  await sleep(800);
  check('back to mm: Bore shows 12.7', (await nativeText('bore')) === '12.7', await nativeText('bore'));

  // ── 7. panels and preview, as Sprocket and Gear Designer (rows 19, 22, 25–30) ──
  check('row 19: Num. Teeth and Outer Dia. side by side, labels above',
        await js(`!!document.getElementById('teeth').closest('.size-row') && !!document.getElementById('od').closest('.size-row')`));
  const lock = await js(`document.getElementById('belt-lock-info').textContent.replace(/\\s+/g, ' ').trim()`);
  check('row 22: "Belt: …" over "Locked to Pulley 1"', /^Belt: \S.* Locked to Pulley 1$/.test(lock), lock);
  const tabs = await js(`[...document.querySelectorAll('button.preview-tab')].filter(b => b.offsetParent !== null).map(b => b.id).join()`);
  check('row 25: one Belt toggle, no 2D tab', tabs === 'tab-belt', tabs);
  await js(`document.getElementById('tab-belt').click()`);
  await sleep(1500);
  check('row 25: Belt toggles the belt view on', await js(`previewTab === 'belt' && document.getElementById('tab-belt').classList.contains('active')`));
  await js(`document.getElementById('tab-belt').click()`);
  await sleep(1500);
  check('…and off again, back to the pulleys', await js(`previewTab === 'pulley'`));
  check('row 26: no OD readout', !(await js(`!!document.getElementById('od-label')`)));
  // Row 27: the drive preview (a PNG): pulley 1 light blue, pulley 2 light red.
  const tints = await js(`new Promise(res => { const im = document.getElementById('preview-img');
    const go = () => { const c = document.createElement('canvas'); c.width = im.naturalWidth; c.height = im.naturalHeight;
      const g = c.getContext('2d'); g.drawImage(im, 0, 0); const d = g.getImageData(0, 0, c.width, c.height).data;
      let blue = 0, red = 0;
      for (let i = 0; i < d.length; i += 16) {
        if (Math.abs(d[i] - 214) < 4 && Math.abs(d[i + 1] - 232) < 4 && Math.abs(d[i + 2] - 245) < 4) blue++;
        if (Math.abs(d[i] - 243) < 4 && Math.abs(d[i + 1] - 211) < 4 && Math.abs(d[i + 2] - 207) < 4) red++;
      }
      res({ blue, red }); };
    if (im.complete && im.naturalWidth) go(); else im.onload = go; })`);
  check('row 27: pulley 1 light blue, pulley 2 light red', tints.blue > 100 && tints.red > 100, tints);
  const dims = await js(`(() => { const s = document.querySelector('#dims-details > summary'); const cs = getComputedStyle(s);
    return { upper: cs.textTransform, colour: cs.color }; })()`);
  check('row 30: Dimensions heading as the cards\'', dims.upper === 'uppercase' && dims.colour === 'rgb(0, 120, 212)', dims);

  check('no page errors', errors.length === 0, errors);
  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 400)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  const failed = results.filter(r => !r.ok).length;
  console.log(`${results.length - failed}/${results.length} passed`);
  process.exitCode = failed ? 1 : 0;
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
