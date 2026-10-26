// cct_common's busy overlay and Auto-fix (cct_busy.js, 0.25.7; the owner, 2026-10-22),
// in headless Chrome over CDP, as Worm Gear Designer's busy_ui.js:
// - a redraw, 2D and 3D, one pulley and two, shows "Drawing the pulley…" large,
//   white on near-black, centred over the preview, and takes it down when the
//   picture or the model (both pulleys and the belt) is in;
// - Auto-fix's Apply closes the warnings at once, the overlay showing the redraw;
// - a value invalid only part-way through typing shows no warning; one left
//   invalid shows it 2 s after the last keystroke; leaving the field shows it at once;
// - the spoke card's Auto-fit closes its panel at once too;
// - a refusal of a value typed over before its answer came back never shows (0.25.8).
// The preview's requests are held back (CDP Fetch) to look at the overlay, and the
// Dimensions check's answers to time them against the typing.
//
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5197 --no-debug
//   node tests/browser/busy_ui.js http://127.0.0.1:5197 [screenshot folder]
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE, SHOTS] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
// Chrome picks a free debug port and writes it to DevToolsActivePort: harnesses run side by side
const devPort = () => { try { return Number(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]); } catch (e) { return 0; } };
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-busy-'));
const chrome = spawn(CHROME, ['--headless=new', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1400,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

async function main() {
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${devPort()}/json/list`)).json()).find(t => t.type === 'page'); }
    catch (e) { await sleep(200); }
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let id = 0; const pending = new Map(); const errors = [];
  const held = []; let holding = false;
  const dimsHeld = []; let holdingDims = false;   // the Dimensions check's answers, held once answered
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === 'Fetch.requestPaused') {
      const dims = m.params.request.url.includes('/api/dimensions');
      if (dims ? holdingDims : holding) (dims ? dimsHeld : held).push(m.params.requestId);
      else send('Fetch.continueRequest', { requestId: m.params.requestId });
    }
  });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const js = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error(expr.slice(0, 80) + ' -> ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text));
    return r.result.result.value;
  };
  const waitFor = async (expr, ms = 10000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(100); }
    return false;
  };
  const load = async url => {
    await send('Page.navigate', { url });
    await waitFor("document.readyState === 'complete' && typeof buildParams === 'function'");
    await sleep(1500);
  };
  const shot = async name => {
    if (!SHOTS) return;
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(SHOTS, name), Buffer.from(r.result.data, 'base64'));
  };
  // a keystroke: input only (still typing); finished: input then change (a list, Enter)
  const type = (id, v) => js(`(() => { const e = document.getElementById('${id}'); e.focus(); e.value = ${JSON.stringify(v)};
    e.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  const busy = () => js("CCTBusy.shown(document.getElementById('preview-box'))");
  const warned = () => js(`(() => { const r = document.getElementById('dims-warn-row');
    return !r.classList.contains('hidden') && document.querySelectorAll('#dims-warnings li').length > 0; })()`);
  const val = id => js(`document.getElementById('${id}').value`);

  // Hold the preview's requests (2D picture, 3D model) until released.
  await send('Fetch.enable', { patterns: [{ urlPattern: '*api/preview?*' }, { urlPattern: '*api/preview-stl*' },
                                          { urlPattern: '*api/dimensions?*', requestStage: 'Response' }] });
  const release = async () => {
    holding = false;
    for (const rid of held.splice(0)) await send('Fetch.continueRequest', { requestId: rid });
  };
  const waitHeld = async (n = 1) => { for (let i = 0; i < 100 && held.length < n; i++) await sleep(50); return held.length >= n; };
  const look = () => js(`(() => {
    const o = document.querySelector('#preview-box .cct-busy'), c = o && o.querySelector('.cct-busy-card');
    if (!o || o.hidden) return null;
    const s = getComputedStyle(c), r = c.getBoundingClientRect(), b = document.getElementById('preview-box').getBoundingClientRect();
    return { text: c.textContent.trim(), size: parseFloat(s.fontSize), weight: s.fontWeight, color: s.color,
             bg: s.backgroundColor, opacity: getComputedStyle(o).opacity, w: r.width, h: r.height,
             dx: Math.abs((r.left + r.right) / 2 - (b.left + b.right) / 2), dy: Math.abs((r.top + r.bottom) / 2 - (b.top + b.bottom) / 2) };
  })()`);
  const lookShown = async () => {               // past the fade-in (0.15 s + 0.2 s)
    for (let i = 0; i < 40; i++) { const l = await look(); if (l && Number(l.opacity) === 1) return l; await sleep(50); }
    return look();
  };

  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });   // headless: focus and blur as a visitor's
  await load(BASE + '/');
  await js("localStorage.clear()");
  await load(BASE + '/?family=HTD&pitch=5M&teeth=20&bore=8');
  check('the design opens drawn, no overlay',
        await waitFor("document.getElementById('preview-img').complete && !CCTBusy.shown(document.getElementById('preview-box'))", 10000));

  // ── a 2D redraw ──
  holding = true;
  await js("(() => { const e = document.getElementById('bore'); e.value = '9'; e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true})); })()");
  check('2D: a change asks for the picture again (held)', await waitHeld());
  let l = await lookShown();
  check('2D: the overlay says "Drawing the pulley…"', l && l.text === 'Drawing the pulley…', l);
  check('…large, bold, white on near-black, fully shown',
        l && l.size >= 20 && Number(l.weight) >= 700 && l.color === 'rgb(255, 255, 255)' && l.bg === 'rgb(15, 23, 42)' && Number(l.opacity) === 1, l);
  check('…a card centred over the preview', l && l.w >= 250 && l.h >= 60 && l.dx < 2 && l.dy < 2, l);
  check('…and the footer no longer says "Loading 3D…"', !(await js("document.getElementById('preview-footer').textContent.includes('Loading 3D')")));
  await shot('busy_2d.png');
  await release();
  check('2D: the picture in, the overlay gone', await waitFor('!CCTBusy.shown(document.getElementById("preview-box"))', 8000));

  // ── a 3D redraw, one pulley ──
  holding = true;
  await js("(() => { const fb = document.getElementById('feature_build'); if (!fb.checked) fb.click(); })()");
  check('3D: the model asked for (held)', await waitHeld());
  l = await lookShown();
  check('3D: the overlay over the 3D view, "Drawing the pulley…"', l && l.text === 'Drawing the pulley…' && l.dx < 2 && l.dy < 2, l);
  await shot('busy_3d.png');
  await release();
  check('3D: the model in, the overlay gone', await waitFor('!CCTBusy.shown(document.getElementById("preview-box"))', 30000));

  // ── two pulleys: down only when both pulleys and the belt are in ──
  holding = true;
  await js("(() => { const d = document.getElementById('dual_enable'); if (!d.checked) d.click(); })()");
  check('two pulleys: both models asked for (held)', await waitHeld(2));
  l = await lookShown();
  check('two pulleys: "Drawing the pulleys…"', l && l.text === 'Drawing the pulleys…', l);
  const first = held.shift();
  holding = false;
  await send('Fetch.continueRequest', { requestId: first });
  await sleep(2500);
  check('…one pulley in: still up', await busy());
  await release();
  check('…both in: gone', await waitFor('!CCTBusy.shown(document.getElementById("preview-box"))', 30000));

  // ── typing: the warnings wait ──
  await load(BASE + '/?family=HTD&pitch=5M&teeth=20&bore=8&belt_height=15&clearance_height=8');
  await js("document.getElementById('dims-details').open = true");
  await sleep(1500);
  check('a design with a wide enough face: no warning', !(await warned()));
  await js(`window.__warnSeen = false; new MutationObserver(() => {
    if (document.querySelectorAll('#dims-warnings li').length) window.__warnSeen = true;
  }).observe(document.getElementById('dims-warnings'), { childList: true })`);
  await type('clearance_height', '1');                 // 1: a face too narrow…
  await sleep(600);
  await type('clearance_height', '10');                // …on the way to 10
  await sleep(3500);
  check('typing 10 through a too-narrow 1: no warning at any point', !(await js('window.__warnSeen')) && !(await warned()));

  await type('clearance_height', '1');
  await sleep(1000);
  check('1 left as typed: no warning 1 s on', !(await warned()));
  await shot('typing_1s_no_warning.png');
  check('…the warning 2 s after the last keystroke, with Auto-fix', await waitFor('(' + fixShownExpr() + ')', 4000));
  await type('clearance_height', '10');
  await sleep(1500);
  check('a valid value takes the warning down at once', !(await warned()));

  await type('clearance_height', '1');
  await sleep(400);
  check('too narrow, still typing: no warning yet', !(await warned()));
  await js("document.getElementById('clearance_height').blur()");
  check('leaving the field: the warning at once', await waitFor('(' + fixShownExpr() + ')', 1500));
  await shot('autofix_before.png');

  // ── Auto-fix ──
  holding = true;
  const applied = await js(`(() => { document.getElementById('dims_fix').click();
    return { closed: document.getElementById('dims-warn-row').classList.contains('hidden'),
             busy: CCTBusy.shown(document.getElementById('preview-box')) }; })()`);
  check('Apply: the warnings close at once, the overlay up', applied.closed && applied.busy, applied);
  check('…the redraw under way (held)', await waitHeld());
  await sleep(500);
  // (the fixed design's own check may answer meanwhile: nothing to warn of, an empty row)
  check('…and they stay closed while it draws', !(await warned()) && await busy());
  await shot('autofix_applied.png');
  await release();
  check('drawn with the fixed clearance, the overlay gone',
        await waitFor('!CCTBusy.shown(document.getElementById("preview-box"))', 8000) && Number(await val('clearance_height')) > 1,
        await val('clearance_height'));
  await sleep(1500);
  check('…and no warning left', !(await warned()));

  // ── the spoke card's Auto-fit ──
  await js(`(() => {
    const set = (k, v) => { document.getElementById('spokes1_' + k).value = v; };
    document.getElementById('spokes1_enabled').checked = true;
    set('hub_od', 16); set('rim_depth', 2); set('width', 5); set('fillet_tip', 6); set('fillet_base', 1.5); set('count', 4);
    document.getElementById('clearance_height').blur(); onSpokesChange(1); })()`);
  const fitBtn = "document.querySelector('#spokes1_overlap_warn .spoke-fit-btn')";
  check('spokes that don\'t fit: the panel with Auto-fit', await waitFor(`!!${fitBtn} && document.getElementById('spokes1_overlap_warn').style.display === 'block'`, 8000));
  holding = true;
  await js(`${fitBtn}.click()`);
  const spokeClosed = () => js("(() => { const w = document.getElementById('spokes1_overlap_warn'); return w.classList.contains('hidden') || w.style.display === 'none'; })()");
  check('Auto-fit: the panel closes at once, the overlay up', (await spokeClosed()) && await busy());
  await release();
  check('…drawn, the overlay gone, the panel still closed',
        await waitFor('!CCTBusy.shown(document.getElementById("preview-box"))', 8000) && await spokeClosed());

  // ── a refusal of a value changed since never shows (0.25.8, Bevel's find) ──
  // 1's answer comes back just after the 0 of 10 is typed, and 10's takes 3 s: past the
  // 2 s wait, 1's refusal would show if the check's ticket didn't drop it.
  await load(BASE + '/?family=HTD&pitch=5M&teeth=20&bore=8&belt_height=15&clearance_height=8');
  await js("document.getElementById('dims-details').open = true");
  await sleep(1500);
  await js(`window.__warnSeen = false; new MutationObserver(() => {
    if (document.querySelectorAll('#dims-warnings li').length) window.__warnSeen = true;
  }).observe(document.getElementById('dims-warnings'), { childList: true })`);
  holdingDims = true;
  await type('clearance_height', '1');
  let ok = false;
  for (let i = 0; i < 60 && !dimsHeld.length; i++) await sleep(50);
  check('typing 1: its check answered (held)', dimsHeld.length === 1, dimsHeld.length);
  await type('clearance_height', '10');
  await send('Fetch.continueRequest', { requestId: dimsHeld.shift() });   // 1's answer, after the 0
  for (let i = 0; i < 40 && !dimsHeld.length; i++) await sleep(50);
  ok = dimsHeld.length === 1;
  await sleep(2600);                                                       // 10's answer held past 2 s
  check('…10 asked again, its answer slow', ok);
  check('…1\'s refusal never shows, typed over before it came back', !(await js('window.__warnSeen')) && !(await warned()));
  holdingDims = false;
  for (const rid of dimsHeld.splice(0)) await send('Fetch.continueRequest', { requestId: rid });
  await sleep(1500);
  check('…and 10, valid, no warning', !(await js('window.__warnSeen')) && !(await warned()), await val('clearance_height'));

  check('no page errors', errors.length === 0, errors);
}
function fixShownExpr() {
  return "!document.getElementById('dims_fix').classList.contains('hidden') && !document.getElementById('dims-warn-row').classList.contains('hidden')";
}

main().catch(e => check('harness ran', false, e.stack)).finally(() => {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  ' + JSON.stringify(r.detail ?? '')}`);
  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  process.exit(failed ? 1 : 0);
});
