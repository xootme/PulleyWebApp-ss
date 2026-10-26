// Drives the 3D drive view's ⟳ Turn (geometry/belt_motion.py, as Sprocket's
// turn_ui.js) in headless Chrome over CDP with real clicks. Both pulleys turn
// the same way, pulley 2 at z1/z2 of pulley 1; the belt runs round them.
//
// Checks the button (Drive view only, blue while pressed, hidden on the
// Pulley 1 view and on one pulley), that pressing it turns the drive (u > 0,
// pulley 2 at rates[1]·u, a belt tooth on pulley 1 carried round with it)
// and pressing again stops it; that one tooth on, every belt piece is exactly
// at the next one's rest place (so the loop never drifts); on a small-to-large
// GT2, a 1:1 GT2 and an HTD. Negative controls: the same checks fed a wrong
// ratio, or a belt standing still, must fail.
//
// The server's side, in mesh with the pulleys: tests/test_turn.py.
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5197 --no-debug
//   node tests/browser/turn_ui.js http://127.0.0.1:5197
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
// Chrome picks a free debug port and writes it to DevToolsActivePort: harnesses run side by side
const devPort = () => { try { return Number(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]); } catch (e) { return 0; } };
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-turn-'));
const chrome = spawn(CHROME, ['--headless=new', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1600,1000',
  '--use-angle=swiftshader', '--enable-unsafe-swiftshader', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

const DRIVES = [
  { name: 'GT2 16:40', q: 'family=GT&pitch=2M&teeth=16&bore=5&dual=true&p2_teeth=40&p2_bore=8&center_distance=80.6379' },
  { name: 'GT2 20:20', q: 'family=GT&pitch=2M&teeth=20&bore=5&dual=true&p2_teeth=20&p2_bore=5&center_distance=60' },
  { name: 'HTD 5M 18:30', q: 'family=HTD&pitch=5M&teeth=18&bore=8&dual=true&p2_teeth=30&p2_bore=10&center_distance=122.1265' },
];

async function main() {
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${devPort()}/json/list`)).json()).find(t => t.type === 'page'); }
    catch (e) { await sleep(200); }
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => {   // a socket that never opens would end node silently, with no output
    ws.addEventListener('open', r);
    ws.addEventListener('error', () => j(new Error(`the CDP socket failed (${ws.url})`)));
  });
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
  // Every page load waits for the page's own idle hook (index.html's pageIdle: the start-up,
  // its fetches and debounced redraws, the drawing overlay), or fails saying the page never
  // settled. A fixed sleep let Sprocket's harnesses go on mid-start-up (night of 2026-10-22).
  const settled = async (what, ms = 60000) => {
    const t = Date.now();
    while (Date.now() - t < ms) {
      if (await js("document.readyState === 'complete' && typeof pageIdle === 'function' && pageIdle()").catch(() => false)) return;
      await sleep(150);
    }
    throw new Error(`the page never settled in ${ms / 1000} s (${what})`);
  };
  const waitFor = async (expr, ms = 10000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(150); }
    return false;
  };
  const click = async sel => {
    const r = await js(`(() => { const e = document.querySelector('${sel}'); e.scrollIntoView({block:'center'});
      const b = e.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased'])
      await send('Input.dispatchMouseEvent', { type, x: r[0], y: r[1], button: 'left', clickCount: 1 });
  };
  const turn = () => js('window.pulleyTurn()');
  const shown = sel => js(`(() => { const e = document.querySelector('${sel}'); return !!e && e.offsetParent !== null; })()`);
  const pressed = () => js("document.getElementById('spin_btn').getAttribute('aria-pressed')");
  const near = (a, b, tol) => Math.abs(a - b) <= tol;
  // A drive in 3D, its pulleys and belt loaded.
  const open = async q => {
    await send('Page.navigate', { url: `${BASE}/?${q}` });
    await settled(q);
    await js("(() => { const fb = document.getElementById('feature_build'); if (!fb.checked) fb.click(); })()");
    return waitFor(`(() => { const s = window.pulleyTurn && window.pulleyTurn();
      return s && s.belt && s.pieces && s.angles[0] !== null && s.angles[1] !== null; })()`, 60000);
  };
  // Every piece k at piece (k - whole)'s rest place, within tol (the belt runs back along s).
  const onRest = (now, rest, whole, tol = 1e-3) => now.every(([x, y, z], k) => {
    const [rx, ry, rz] = rest[(k - whole + rest.length * 4) % rest.length];
    return Math.hypot(x - rx, y - ry, z - rz) <= tol;
  });
  // The pulley angles the drive should have at u, given rates.
  const anglesOk = (s, rates) => near(s.angles[0], rates[0] * s.u, 1e-9) && near(s.angles[1], rates[1] * s.u, 1e-9);

  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: BASE + '/' });
  await settled(BASE + '/');
  await js('localStorage.clear()');

  for (const [di, d] of DRIVES.entries()) {
    const [z1, z2] = [+/teeth=(\d+)/.exec(d.q)[1], +/p2_teeth=(\d+)/.exec(d.q)[1]];
    check(`${d.name}: the drive loads in 3D`, await open(d.q));
    let s = await turn();
    check(`${d.name}: the Drive view shows ⟳ Turn, not pressed`, await shown('#spin_btn') && await pressed() === 'false'
          && /Turn/.test(await js("document.getElementById('spin_btn').textContent")));
    check(`${d.name}: rates 1 and z1/z2, the same way`, s.rates && s.rates[0] === 1 && near(s.rates[1], z1 / z2, 1e-12), s.rates);
    // to the centre's 4 decimals in the link (the app's whole-belt slack is 0.01 tooth)
    check(`${d.name}: path is belt teeth × pitch`, near(s.belt.L, s.belt.n * s.belt.pitch, 0.01 * s.belt.pitch), s.belt);

    // rest, then exactly one tooth of pulley 1 on
    await js('window.pulleySetTurn(0)');
    const rest = (await turn()).pieces;
    await js(`window.pulleySetTurn(${2 * Math.PI / z1})`);
    s = await turn();
    check(`${d.name}: one tooth on, every piece is at the next one's rest place`, onRest(s.pieces, rest, 1),
          [s.pieces[0], rest[rest.length - 1]]);
    check(`${d.name}: …and the pulleys turned one groove each`, anglesOk(s, s.rates)
          && near(s.angles[0], 2 * Math.PI / z1, 1e-9) && near(s.angles[1], 2 * Math.PI / z2, 1e-9), s.angles);
    // Negative controls: the same checks must fail on a wrong ratio or a belt left at rest.
    check(`${d.name}: negative control — a wrong ratio fails the angle check`,
          z1 === z2 ? !anglesOk(s, [1, 0.5]) : !anglesOk(s, [1, 1]));
    check(`${d.name}: negative control — a belt standing still fails the rest-place check`, !onRest(rest, rest, 1));

    // a tooth on pulley 1's arc is carried round with the pulley (half a tooth on)
    const half = Math.PI / z1;
    await js(`window.pulleySetTurn(${half})`);
    s = await turn();
    const o = s.origins[0];
    const ang = ([x, y]) => Math.atan2(y - o[1], x - o[0]);
    const r = ([x, y]) => Math.hypot(x - o[0], y - o[1]);
    const R1 = z1 * s.belt.pitch / (2 * Math.PI);
    // a piece seated on pulley 1 at rest and still on it half a tooth later: well inside its wrap
    const k = rest.findIndex((p, i) => Math.abs(r(p) - R1) < 3 && Math.PI - Math.abs(Math.atan2(p[1] - o[1], p[0] - o[0])) < 0.6);
    const turned = ((ang(s.pieces[k]) - ang(rest[k]) + 3 * Math.PI) % (2 * Math.PI)) - Math.PI;
    check(`${d.name}: a belt tooth on pulley 1 turns with it`, k >= 0 && near(turned, half, 2e-3), [k, turned, half]);

    // press: it turns; pulley 2 keeps its ratio; press again: it stops
    await js('window.pulleySetTurn(0)');
    await click('#spin_btn');
    await sleep(1200);
    s = await turn();
    check(`${d.name}: pressing Turn turns the drive`, await pressed() === 'true' && s.spinning && s.u > 0.2, s.u);
    check(`${d.name}: …blue while pressed`, await js("getComputedStyle(document.getElementById('spin_btn')).backgroundColor") === 'rgb(0, 120, 212)');
    check(`${d.name}: …pulley 2 at rates[1]·u`, anglesOk(s, s.rates), [s.u, s.angles]);
    if (di === 0) {
      // the Pulley 1 view: one part alone, no Turn, shown at rest
      await js("(() => { const w = document.getElementById('view3d_which'); w.value = '1'; w.dispatchEvent(new Event('change')); })()");
      await sleep(400);
      const u1 = (await turn()).u;
      await sleep(600);
      check('the Pulley 1 view hides Turn, at rest and still', !(await shown('#spin_btn')) && u1 === 0 && (await turn()).u === 0);
      await js("(() => { const w = document.getElementById('view3d_which'); w.value = 'drive'; w.dispatchEvent(new Event('change')); })()");
      await sleep(800);
      check('back on the Drive view it turns again', await shown('#spin_btn') && (await turn()).u > 0);
    }
    await click('#spin_btn');
    const u0 = (await turn()).u;
    await sleep(700);
    check(`${d.name}: pressing again stops it`, await pressed() === 'false' && (await turn()).u === u0);
  }

  // one pulley: no drive, no Turn
  await send('Page.navigate', { url: `${BASE}/?family=GT&pitch=2M&teeth=20&bore=5` });
  await settled('one pulley');
  check('one pulley: Turn is not offered', !(await shown('#spin_btn')));

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
  if (errors.length || results.some(r => !r.ok)) process.exitCode = 1;
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
