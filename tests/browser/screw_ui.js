// Drives the hub set-screw controls and the Threaded screw holes dialog
// (ADR-013) in headless Chrome over CDP: the size list, what each hold sends
// and says it will cut, the dialog's settings and examples, saving them, and
// restoring a design from a link — old (diameter only) and new (named size).
//
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5097 --no-debug
//   node tests/browser/screw_ui.js http://127.0.0.1:5097
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9346;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-screw-'));
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
  const setSel = (sel, v) => js(`(() => { const e = document.querySelector('${sel}'); e.value = ${JSON.stringify(v)};
    e.dispatchEvent(new Event('change', {bubbles:true})); e.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  const text = sel => js(`document.querySelector('${sel}').textContent`);
  const shown = sel => js(`!document.querySelector('${sel}').classList.contains('hidden') && document.querySelector('${sel}').offsetParent !== null`);
  const params = () => js('buildParams()');
  const cuts = async want => { await waitFor(`document.querySelector('#hub1_screw_cuts').textContent.includes(${JSON.stringify(want)})`, 5000); return text('#hub1_screw_cuts'); };

  await send('Runtime.enable'); await send('Page.enable');
  await load(BASE + '/');
  await js("localStorage.clear()");
  await load(BASE + '/');
  // 3D mode, hub 1 on and open
  await js(`(() => { const fb = document.getElementById('feature_build'); if (!fb.checked) fb.click();
    const h = document.getElementById('hub1_enabled'); if (!h.checked) h.click();
    if (!document.getElementById('hub1-body').classList.contains('open')) toggleHub(1); })()`);
  await sleep(800);

  // ── sizes ──
  const opts = await js("[...document.querySelectorAll('#hub1_screw_size option')].map(o => o.value)");
  check('size list: metric, inch and Custom', ['M2', 'M2.5', 'M10', '#2-56', '1/4-20', 'Custom'].every(v => opts.includes(v)), opts);
  check('same list on Pulley 2',
    JSON.stringify(await js("[...document.querySelectorAll('#hub2_screw_size option')].map(o => o.value)")) === JSON.stringify(opts));
  check('Retention is the set screw only: D-Shaft and Keyway moved to Bore Shape',
    JSON.stringify(await js("[...document.querySelectorAll('#hub1_retention option')].map(o => o.value)"))
      === '["set_screw_nut","set_screw_std","set_screw_insert","none"]');
  check('grouped Metric / Inch', JSON.stringify(await js("[...document.querySelectorAll('#hub1_screw_size optgroup')].map(g => g.label)")) === '["Metric","Inch"]');

  // ── threaded ──
  await setSel('#hub1_retention', 'set_screw_std'); await setSel('#hub1_screw_size', 'M5'); await setSel('#hub1_screw_count', '1');
  let p = await params();
  check('threaded sends the size, hold and the thread settings',
    p.hub_screw_size === 'M5' && p.hub_screw_hold === 'thread' && p.hub_screw_dia === 5 && p.hub_captured_nut === '0'
    && p.screw_hole_shape === 'round' && p.thread_engagement === '50' && p.hex_flat === '82', p);
  check('threaded: Cuts says the bore', /^Cuts: Ø4\.57 mm threaded hole/.test(await cuts('4.57')), await text('#hub1_screw_cuts'));
  check('no hole-diameter row for a named threaded screw', !(await shown('#hub1_screw_hole_row')));

  // ── the dialog ──
  // Beside the 3D Mode fields, as Sprocket and Gear Designer have it (2026-10-01 audit, row 13).
  check('drive bar button (3D mode)', await js("(() => { const b = document.getElementById('thread_btn'); return !!b && b.textContent === 'Threaded screw holes' && b.offsetParent !== null && !!b.closest('#belt_height_row'); })()"));
  await js("document.getElementById('thread_btn').click()");
  check('dialog opens', await waitFor("getComputedStyle(document.getElementById('thread-overlay')).display === 'flex'"));
  check('round: the round picture', (await js("document.getElementById('thread_picture').src")).endsWith('/static/help/thread_round.svg')
    && await waitFor("document.getElementById('thread_picture').complete && document.getElementById('thread_picture').naturalWidth > 0"));
  await waitFor("document.getElementById('thread_example').textContent.includes('M5')");
  check('examples from the server', (await text('#thread_example')) === 'Threaded holes: M3 → Ø2.73 mm · M5 → Ø4.57 mm · #6-32 → Ø3.02 mm', await text('#thread_example'));
  await setSel('#thread_engagement', '100');
  await waitFor("document.getElementById('thread_example').textContent.includes('4.13')");
  check('engagement updates the examples', (await text('#thread_example')).includes('M5 → Ø4.13 mm'), await text('#thread_example'));
  check('…and the hub note', (await cuts('4.13')).includes('Ø4.13'), await text('#hub1_screw_cuts'));
  check('…and what is sent', (await params()).thread_engagement === '100');
  await js("document.querySelector('input[name=screw_hole_shape][value=hex]').click()");
  await waitFor("document.getElementById('thread_example').textContent.includes('hex')");
  check('hex: flats row instead of engagement', (await shown('#hex_flat_row')) && !(await shown('#thread_engagement_row')));
  check('hex: the hex picture', (await js("document.getElementById('thread_picture').src")).endsWith('/static/help/thread_hex.svg')
    && await waitFor("document.getElementById('thread_picture').complete && document.getElementById('thread_picture').naturalWidth > 0"));
  check('hex examples', (await text('#thread_example')).includes('M5 → 4.10 mm hex (across flats)'), await text('#thread_example'));
  check('hex is sent', (await params()).screw_hole_shape === 'hex');
  const note = () => text('#thread_default_note');
  check('differs from the default: says so', (await note()).includes('your default: Round, engagement 50%'), await note());
  await js("[...document.querySelectorAll('#thread_default_note a')].find(a => a.textContent === 'use my default').click()");
  await waitFor("document.getElementById('thread_example').textContent.includes('4.57')");
  const d = await params();
  check('use my default: round, 50 %, 82 %', d.screw_hole_shape === 'round' && d.thread_engagement === '50' && d.hex_flat === '82', d);
  check('…and says it matches', (await note()).startsWith('These are your default settings.'), await note());
  check('Save as my default is off when they match', await js("document.getElementById('thread_save_default').disabled"));
  await setSel('#thread_engagement', '70');
  await js("document.getElementById('thread_save_default').click()");
  check('Save as my default', (await note()).startsWith('These are your default settings.')
    && JSON.parse(await js("localStorage.getItem('pulley_thread_default')")).engagement === 70, await note());
  check('factory settings link offered', (await note()).includes('factory settings'));
  await js("document.querySelector('#thread-box .bug-btn-submit').click()");     // Done
  check('Done closes it', await waitFor("getComputedStyle(document.getElementById('thread-overlay')).display === 'none'"));

  // ── captured nut: never threaded ──
  await setSel('#hub1_retention', 'set_screw_nut');
  p = await params();
  check('nut sends hold=nut and no thread settings', p.hub_screw_hold === 'nut' && p.hub_captured_nut === '1'
    && !('thread_engagement' in p) && !('screw_hole_shape' in p), p);
  check('nut: Cuts says clearance hole and nut', (await cuts('clearance')) === 'Cuts: Ø5.50 mm clearance hole · pocket for a 8 × 4 mm nut', await text('#hub1_screw_cuts'));

  // ── heat-set insert ──
  await setSel('#hub1_retention', 'set_screw_insert');
  check('insert: hole row, labelled for the insert', (await shown('#hub1_screw_hole_row')) && (await text('#hub1_screw_hole_label')).startsWith('Insert hole'));
  check('insert: started at a typical size', (await js("document.getElementById('hub1_screw_hole_dia').value")) === '6.8');
  await setSel('#hub1_screw_hole_dia', '6.4');
  p = await params();
  check('insert sends the hole, no thread settings', p.hub_screw_hold === 'insert' && p.hub_screw_hole_dia === 6.4 && !('thread_engagement' in p), p);
  check('insert: Cuts', (await cuts('6.40')) === 'Cuts: Ø6.40 mm insert hole', await text('#hub1_screw_cuts'));

  // ── Custom ──
  await setSel('#hub1_retention', 'set_screw_std'); await setSel('#hub1_screw_size', 'Custom');
  await setSel('#hub1_screw_hole_dia', '');
  await setSel('#hub1_screw_size', 'Custom');
  check('Custom: hole row labelled Hole, started at 3.0', (await text('#hub1_screw_hole_label')).startsWith('Hole')
    && (await js("document.getElementById('hub1_screw_hole_dia').value")) === '3.0');
  p = await params();
  check('Custom sends its hole as the diameter', p.hub_screw_size === 'Custom' && p.hub_screw_hole_dia === 3 && p.hub_screw_dia === 3, p);
  check('hub wall note uses the Custom diameter', (await text('#hub1_info')).includes('Min height: 6 mm'), await text('#hub1_info'));

  // ── a D-flat bore (Size card) with its screw (Retention) ──
  await setSel('#bore1_shape', 'flat');
  await setSel('#hub1_retention', 'set_screw_insert'); await setSel('#hub1_screw_size', '#6-32');
  p = await params();
  check('D-flat + screw: inch size, insert, one screw on the flat',
    p.hub_screw_size === '#6-32' && p.hub_screw_hold === 'insert' && p.hub_screw_count === 1 && p.hub_flat_depth > 0, p);
  check('…the count is fixed at one and says where',
    await js("document.getElementById('hub1_screw_count').disabled")
    && (await text('#hub1_shape_note')).includes('on the D-flat'), await text('#hub1_shape_note'));
  await setSel('#bore1_shape', 'round');

  // ── saved and restored ──
  await setSel('#hub1_retention', 'set_screw_std'); await setSel('#hub1_screw_size', '#8-32');
  await load(BASE + '/');
  check('reload keeps the size', (await js("document.getElementById('hub1_screw_size').value")) === '#8-32');
  check('reload keeps the thread setting', (await js("document.getElementById('thread_engagement').value")) === '70');
  check('reload shows the Cuts note', /^Cuts: Ø3\.\d\d mm threaded hole/.test(await cuts('Cuts')), await text('#hub1_screw_cuts'));

  // ── designs from links ──
  const base = 'family=HTD&pitch=5M&teeth=24&bore=8&hub_od=26&hub_height=12';
  await load(`${BASE}/?${base}&hub_screw_dia=5&hub_screw_count=1&hub_captured_nut=1`);
  check('old link (diameter only): captured nut M5', (await js("document.getElementById('hub1_retention').value")) === 'set_screw_nut'
    && (await js("document.getElementById('hub1_screw_size').value")) === 'M5');
  await load(`${BASE}/?${base}&hub_screw_size=%236-32&hub_screw_hold=insert&hub_screw_hole_dia=5.2&hub_screw_count=1&screw_hole_shape=hex&hex_flat=88`);
  check('new link: insert, #6-32, its hole', (await js("document.getElementById('hub1_retention').value")) === 'set_screw_insert'
    && (await js("document.getElementById('hub1_screw_size').value")) === '#6-32'
    && (await js("document.getElementById('hub1_screw_hole_dia').value")) === '5.2');
  check('new link: the design\'s thread settings come with it', (await js('threadShape()')) === 'hex'
    && (await js("document.getElementById('hex_flat').value")) === '88');
  // the link's hub OD survives opening the panel (it used to become 2 × bore)
  const openHub = () => js("(() => { const b = document.getElementById('hub1-body'); if (b.classList.contains('open')) toggleHub(1); toggleHub(1); })()");
  await openHub();
  check('link: hub OD kept when the panel opens', (await js("document.getElementById('hub1_od').value")) === '26',
        await js("document.getElementById('hub1_od').value"));
  await setSel('#hub1_od', '31');
  await openHub();
  check('typed hub OD kept when the panel reopens', (await js("document.getElementById('hub1_od').value")) === '31',
        await js("document.getElementById('hub1_od').value"));
  await js('openThreadDialog()');
  await waitFor("document.getElementById('thread_default_note').textContent.includes('your default')");
  check('…the dialog shows the design against your default',
    (await note()) === 'This design: Hex, flats 88% · your default: Round, engagement 70% · use my default · factory settings', await note());
  check('opening a design leaves your default alone', JSON.parse(await js("localStorage.getItem('pulley_thread_default')")).engagement === 70);

  // ── a new design starts from your default ──
  await js("localStorage.removeItem('pulley_last')");      // what Reset does
  await load(BASE + '/');
  check('Reset: a new design starts from your default', (await js('threadShape()')) === 'round'
    && (await js("document.getElementById('thread_engagement').value")) === '70');

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
