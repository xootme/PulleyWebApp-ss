// Drives Undo / Redo (the Sprocket app's) in headless Chrome over CDP with
// real mouse and key events: a new page has nothing to undo, one typed field
// or one click is one step, the tooltips name the change, Ctrl+Z / Ctrl+Y,
// a new change drops the redo, and the page's own follow-ups (an automatic
// clearance height) fold into the step that caused them.
//
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5197 --no-debug
//   node tests/browser/undo_ui.js http://127.0.0.1:5197
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9348;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-undo-'));
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
    await waitFor('historyReady', 8000);
    await sleep(500);
  };
  // A real click: pointer events land where a user's would.
  const click = async sel => {
    const r = await js(`(() => { const e = document.querySelector('${sel}'); e.scrollIntoView({block:'center'});
      const b = e.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased'])
      await send('Input.dispatchMouseEvent', { type, x: r[0], y: r[1], button: 'left', clickCount: 1 });
  };
  // Typing into a field: focus, the new value (as keystrokes), then leave it.
  const type = async (sel, value) => {
    await click(sel);
    await js(`document.querySelector('${sel}').select()`);
    await send('Input.insertText', { text: String(value) });
    await sleep(500);
    await js(`document.querySelector('${sel}').blur()`);
    await sleep(1200);
  };
  const key = async (k, code) => {
    await js('document.activeElement && document.activeElement.blur()');
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, code, modifiers: 2, windowsVirtualKeyCode: k.toUpperCase().charCodeAt(0) });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, modifiers: 2, windowsVirtualKeyCode: k.toUpperCase().charCodeAt(0) });
    await sleep(1500);
  };
  const val = sel => js(`document.querySelector('${sel}').value`);
  const steps = () => js('undoStack.length');
  const idle = () => waitFor('!restoringHistory', 5000);

  await send('Runtime.enable'); await send('Page.enable');
  await load(BASE + '/');
  await js("localStorage.clear()");
  await load(BASE + '/?family=HTD&pitch=5M&teeth=20&bore=8');

  check('a new page has nothing to undo', await js("document.getElementById('undo_btn').disabled && document.getElementById('redo_btn').disabled"));

  // ── one typed field is one step, named in the tooltip ──
  await type('#teeth', 24);
  check('typing Teeth is one step', (await steps()) === 1, await steps());
  const title = await js("document.getElementById('undo_btn').title");
  check('the tooltip names it', /20 → 24/.test(title) && title.includes('(Ctrl+Z)'), title);

  // several updates while in one field are still one step
  await click('#bore');
  await js("document.querySelector('#bore').select()");
  await send('Input.insertText', { text: '9' }); await sleep(700);
  await js("document.querySelector('#bore').select()");
  await send('Input.insertText', { text: '10' }); await sleep(700);
  await js("document.querySelector('#bore').blur()"); await sleep(1200);
  check('one visit to a field is one step, however many edits', (await steps()) === 2, await steps());

  // ── undo, redo ──
  await click('#undo_btn'); await idle(); await sleep(800);
  check('Undo puts the bore back', (await val('#bore')) === '8', await val('#bore'));
  await click('#undo_btn'); await idle(); await sleep(800);
  check('Undo again puts Teeth back', (await val('#teeth')) === '20', await val('#teeth'));
  check('…and nothing is left to undo', await js("document.getElementById('undo_btn').disabled"));
  await click('#redo_btn'); await idle(); await sleep(800);
  check('Redo brings Teeth back', (await val('#teeth')) === '24', await val('#teeth'));
  check('Redo tooltip names the next change', /Redo .*8 → 10/.test(await js("document.getElementById('redo_btn').title")),
        await js("document.getElementById('redo_btn').title"));

  // ── keys ──
  await key('z', 'KeyZ'); await idle();
  check('Ctrl+Z undoes', (await val('#teeth')) === '20', await val('#teeth'));
  await key('y', 'KeyY'); await idle();
  check('Ctrl+Y redoes', (await val('#teeth')) === '24', await val('#teeth'));

  // ── a new change forks the future ──
  await type('#bore', '12');
  check('a new change drops the redo', await js("document.getElementById('redo_btn').disabled"));

  // ── one click is one step, knock-ons included ──
  const before = await steps();
  const dualWas = await js("document.getElementById('dual_enable').checked");
  await click('#dual_enable'); await sleep(2500);
  check('toggling Two Pulley Drive is one step', (await steps()) === before + 1, [before, await steps()]);
  await click('#undo_btn'); await idle(); await sleep(1200);
  check('Undo puts it back', (await js("document.getElementById('dual_enable').checked")) === dualWas);
  check('…with the Pulley 2 panel to match',
    (await js("document.getElementById('panel2').classList.contains('hidden')")) === !dualWas);

  // ── the automatic clearance height folds into the change that moved it ──
  await js("localStorage.clear()");
  await load(BASE + '/?family=Imperial&pitch=MXL&teeth=20&bore=5');
  await waitFor("document.getElementById('clearance_height').value === '2.5' || document.getElementById('clearance_height').value !== '0.5'", 6000);
  await js("(() => { const fb = document.getElementById('feature_build'); if (!fb.checked) fb.click(); })()");
  await sleep(2500);
  const s0 = await steps(), ch0 = await val('#clearance_height');
  await type('#belt_height', '4.8');
  await sleep(1500);
  check('belt width and its automatic clearance are one step', (await steps()) === s0 + 1, [s0, await steps()]);
  check('…the clearance followed', (await val('#clearance_height')) === '2.3', await val('#clearance_height'));
  await click('#undo_btn'); await idle(); await sleep(1500);
  check('Undo puts both back', (await val('#belt_height')) === '10' && (await val('#clearance_height')) === ch0,
        [await val('#belt_height'), await val('#clearance_height'), ch0]);

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
