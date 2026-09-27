// Snapshot the app's own 3D preview for a help picture.
//
//   node shoot3d.js <app url> <out.png> <settings.json> [view]
//
// settings.json is a list of steps run in order in the page:
//   ["set", id, value]      set an input/select and fire its input/change event
//   ["check", id, bool]     tick/untick a checkbox and fire click/change
//   ["js", code]            run code in the page (e.g. "onHubChange(1)")
//   ["wait", ms]
// Then the 3D model is awaited, the view snapped (default "oblique") and the
// 3D canvas captured at 2x. Crop/trim is left to the Python builder.
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE, OUT, SETTINGS, VIEW = 'oblique'] = process.argv.slice(2);
const steps = JSON.parse(fs.readFileSync(SETTINGS, 'utf8'));
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9346;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-3d-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader', `--user-data-dir=${profile}`, '--no-first-run',
  '--window-size=1500,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  let target;
  for (let i = 0; i < 80 && !target; i++) {
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
  const waitFor = async (expr, ms = 60000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(250); }
    return false;
  };
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 2, mobile: false });
  await send('Page.navigate', { url: BASE + '/' });
  await waitFor("document.readyState === 'complete' && typeof snapView === 'function'");
  await sleep(1000);

  for (const s of steps) {
    if (s[0] === 'set') await js(`(() => { const el = document.getElementById(${JSON.stringify(s[1])});
      el.value = ${JSON.stringify(String(s[2]))};
      el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input')); return true; })()`);
    else if (s[0] === 'check') await js(`(() => { const el = document.getElementById(${JSON.stringify(s[1])});
      if (el.checked !== ${!!s[2]}) el.click(); return true; })()`);
    else if (s[0] === 'js') await js(`(() => { ${s[1]}; return true; })()`);
    else if (s[0] === 'wait') await sleep(s[1]);
  }

  // wait for the 3D model: the canvas is shown and the scene has a mesh
  const ok = await waitFor(`(() => { const c = document.getElementById('three-canvas');
    if (!c || c.style.display === 'none' || typeof threeScene === 'undefined' || !threeScene) return false;
    let n = 0; threeScene.traverse(o => { if (o.isMesh) n++; }); return n > 0; })()`);
  await sleep(4000);                       // let the latest request replace the first mesh
  await js(`snapView(${JSON.stringify(VIEW)})`);
  await sleep(1500);
  const box = await js(`(() => { const r = document.getElementById('three-canvas').getBoundingClientRect();
    return {x: r.x, y: r.y, width: r.width, height: r.height}; })()`);
  const shot = await send('Page.captureScreenshot', { format: 'png', clip: { ...box, scale: 1 } });
  fs.writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'));
  console.log(`${ok ? 'ok' : 'NO MODEL'}  ${OUT}  ${Math.round(box.width)}x${Math.round(box.height)}  page errors: ${errors.length}`);
  ws.close(); chrome.kill();
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error(e); chrome.kill(); process.exit(1); });
