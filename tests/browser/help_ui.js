// Drives the help and settings UI added with the help pictures, end to end in
// headless Chrome over CDP (same approach as tokens_ui.js — no Playwright):
//
//   * hover pictures: a label pops up its picture beside it and hides on leave
//     (a for= label, a pulley-2 label, and a data-picture label)
//   * "Apply to both pulleys" (Advanced): only shown with two pulleys; ticking
//     copies the pulley you ticked it on; changes then sync both ways; untick stops it
//   * spoke fit: settings that don't fit show the warning with an Auto-fit
//     button; Auto-fit puts the fitted values in and the warning goes
//
// Needs a running app (tokens off is fine):
//   QUEUE_DISABLED=1 python app.py --port 5099 --no-debug
//   node tests/browser/help_ui.js http://127.0.0.1:5099
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE] = process.argv.slice(2);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9345;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-help-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1500,1000', 'about:blank']);
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
  const waitFor = async (expr, ms = 15000) => {
    const t = Date.now();
    while (Date.now() - t < ms) { try { if (await js(expr)) return true; } catch (e) {} await sleep(150); }
    return false;
  };
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: BASE + '/' });
  await waitFor("document.readyState === 'complete' && typeof checkSpokeFit === 'function'");
  await sleep(800);

  // ── 1. hover pictures ───────────────────────────────────────────────────────
  const hover = async (selector, picture) => {
    await js(`document.querySelector('${selector}').dispatchEvent(new MouseEvent('mouseenter'))`);
    const shown = await waitFor(`(() => { const p = document.querySelector('.feature-pic');
      return p.classList.contains('open') && p.querySelector('img').src.endsWith('${picture}'); })()`, 3000);
    const inView = await js(`(() => { const r = document.querySelector('.feature-pic').getBoundingClientRect();
      return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; })()`);
    await js(`document.querySelector('${selector}').dispatchEvent(new MouseEvent('mouseleave'))`);
    const hidden = await waitFor(`!document.querySelector('.feature-pic').classList.contains('open')`, 3000);
    check(`hover ${selector}: shows ${picture}, in view, hides on leave`, shown && inView && hidden,
          { shown, inView, hidden });
  };
  await hover('label[for="bore"]', '/static/help/bore.svg');
  await hover('label[for="p2_backlash_preset"]', '/static/help/backlash.svg');
  await hover('label.lock-ratio-label', '/static/help/lock_ratio.svg');
  await hover('label[for="spokes2_fillet_tip"]', '/static/help/fillets.svg');   // spokes_ keys
  await js(`(() => { const f = document.getElementById('feature_build');         // 3D panels
    if (!f.checked) f.click(); return true; })()`);
  await sleep(500);
  await hover('label[for="hub1_retention"]', '/static/help/hub_retention.svg'); // hub_ keys
  await hover('label[for="hub2_height"]', '/static/help/hub_size.svg');
  await hover('label[for="bore1_shape"]', '/static/help/bore_shape.svg');         // bore_ keys (a macro)
  await hover('label[for="spline1_minor"]', '/static/help/spline_straight.svg');  // spline_ keys
  await hover('label[for="spline2_m"]', '/static/help/spline_involute.svg');
  // Number of Screws shows the picture for the selected Retention Method
  for (const [method, pic] of [['set_screw_nut', 'hub_screw_count_nut'], ['set_screw_std', 'hub_screw_count_std']]) {
    await js(`(() => { const r = document.getElementById('hub1_retention'); r.value = '${method}';
      r.dispatchEvent(new Event('change')); return true; })()`);
    await hover('label[for="hub1_screw_count"]', `/static/help/${pic}.svg`);
  }
  // Flanges: the shape picture follows the pulley's 3D Print box
  for (const [printed, pic] of [[true, 'flange_shape_3dp'], [false, 'flange_shape_metal']]) {
    await js(`(() => { const c = document.getElementById('flange1_3dprint'); c.checked = ${printed}; return true; })()`);
    await hover('label[for="flange1_angle"]', `/static/help/${pic}.svg`);
  }
  await hover('label[for="flange2_nub_count"]', '/static/help/flange_nubs.svg');
  await hover('label[for="spokes1_height"]', '/static/help/spoke_height.svg');
  await hover('label[data-picture$="flange_type.svg"]', '/static/help/flange_type.svg');
  // wide pictures show at their own size (text stays readable), capped to the window
  await js(`document.querySelector('label[for="hub1_retention"]').dispatchEvent(new MouseEvent('mouseenter'))`);
  await waitFor(`document.querySelector('.feature-pic').classList.contains('open') &&
                 document.querySelector('.feature-pic img').complete`, 3000);
  const shown = await js(`(() => { const i = document.querySelector('.feature-pic img');
    return [i.getBoundingClientRect().width, i.naturalWidth, innerWidth]; })()`);
  check('pop-up shows the picture at its own size (or the window width)',
        Math.abs(shown[0] - Math.min(shown[1], 1000, shown[2] - 24 - 14)) < 16, shown);
  await js(`document.querySelector('label[for="hub1_retention"]').dispatchEvent(new MouseEvent('mouseleave'))`);
  await js(`(() => { const f = document.getElementById('feature_build');
    if (f.checked) f.click(); return true; })()`);
  await sleep(500);
  check('hover labels are marked', await js(`document.querySelectorAll('label.has-picture').length >= 20`),
        await js(`document.querySelectorAll('label.has-picture').length`));

  // ── 2. Apply to both pulleys ────────────────────────────────────────────────
  const visible = sel => js(`getComputedStyle(document.querySelector('${sel}')).display !== 'none'`);
  const setDual = on => js(`(() => { const d = document.getElementById('dual_enable');
    if (d.checked !== ${on}) { d.checked = ${on}; onDualToggle(); } return true; })()`);
  const open = n => js(`document.getElementById('advanced${n}_body').style.display = 'block'`);
  await setDual(false); await open(1);
  check('both-pulleys box hidden with one pulley', !(await visible('label.adv-both')));
  await setDual(true); await open(1); await open(2);
  check('both-pulleys box shown with two pulleys', await visible('label.adv-both'));

  const v = id => js(`document.getElementById('${id}').value`);
  const setField = (id, value) => js(`(() => { const el = document.getElementById('${id}'); el.value = '${value}';
    el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input')); return true; })()`);
  await setField('print_extra', '0.15');
  await setField('p2_print_extra', '0.3');
  await js(`(() => { const c = document.getElementById('adv_both_1'); c.checked = true;
    c.dispatchEvent(new Event('change')); return true; })()`);
  check('ticking copies pulley 1 to pulley 2', (await v('p2_print_extra')) === '0.15', await v('p2_print_extra'));
  check('the two boxes are one setting', await js(`document.getElementById('adv_both_2').checked`));
  await setField('clearance_preset', 'LOOSE');
  check('pulley 1 change reaches pulley 2', (await v('p2_clearance_preset')) === 'LOOSE', await v('p2_clearance_preset'));
  await setField('p2_backlash_preset', 'CUSTOM'); await setField('p2_backlash_custom', '0.07');
  check('pulley 2 change reaches pulley 1',
        (await v('backlash_preset')) === 'CUSTOM' && (await v('backlash_custom')) === '0.07',
        [await v('backlash_preset'), await v('backlash_custom')]);
  check('custom row shown on the other pulley too',
        await js(`!document.getElementById('backlash_custom_row').classList.contains('hidden')`));
  await js(`(() => { const c = document.getElementById('adv_both_2'); c.checked = false;
    c.dispatchEvent(new Event('change')); return true; })()`);
  await setField('print_extra', '0.05');
  check('unticked: pulleys are independent again', (await v('p2_print_extra')) === '0.15', await v('p2_print_extra'));
  // leave Advanced as it was for the next part
  for (const p of ['', 'p2_']) {
    await setField(p + 'clearance_preset', 'STANDARD'); await setField(p + 'backlash_preset', 'STANDARD');
    await setField(p + 'print_extra', '0');
  }

  // ── 3. spoke fit warning and Auto-fit (the reported case) ────────────────────
  await setField('p2_teeth', '30'); await sleep(600);
  await js(`(() => {
    const set = (k, val) => { document.getElementById('spokes2_' + k).value = val; };
    document.getElementById('spokes2_enabled').checked = true;
    set('hub_od', 16); set('rim_depth', 2); set('width', 5); set('fillet_tip', 6);
    set('fillet_base', 1.5); set('count', 4);
    onSpokesChange(2); return true; })()`);
  const warned = await waitFor(`(() => { const w = document.getElementById('spokes2_overlap_warn');
    return w.style.display === 'block' && w.textContent.includes('Fillet Tip: 6'); })()`);
  check('unfitting spokes: warning names the change', warned,
        await js(`document.getElementById('spokes2_overlap_warn').textContent`));
  check('typed value is kept until Auto-fit', (await v('spokes2_fillet_tip')) === '6', await v('spokes2_fillet_tip'));
  const btn = await js(`!!document.querySelector('#spokes2_overlap_warn .spoke-fit-btn')`);
  check('warning has an Auto-fit button', btn);
  if (btn) {
    await js(`document.querySelector('#spokes2_overlap_warn .spoke-fit-btn').click()`);
    const tip = parseFloat(await v('spokes2_fillet_tip'));
    check('Auto-fit puts the fitted value in', tip > 0 && tip < 6, tip);
    check('Auto-fit leaves the others alone',
          (await v('spokes2_fillet_base')) === '1.5' && (await v('spokes2_count')) === '4');
    const cleared = await waitFor(`document.getElementById('spokes2_overlap_warn').style.display === 'none'`);
    check('warning goes once the settings fit', cleared);
  }
  // a pulley too small for any spokes: warning, no button
  await setField('pitch', '3M'); await setField('p2_teeth', '12'); await sleep(600);
  await js(`(() => { document.getElementById('spokes2_hub_od').value = 30; onSpokesChange(2); return true; })()`);
  const none = await waitFor(`(() => { const w = document.getElementById('spokes2_overlap_warn');
    return w.style.display === 'block' && w.textContent.includes('No spokes fit'); })()`);
  check('too small: says no spokes fit', none, await js(`document.getElementById('spokes2_overlap_warn').textContent`));
  check('too small: no Auto-fit button', !(await js(`!!document.querySelector('#spokes2_overlap_warn .spoke-fit-btn')`)));

  // ── Escape closes each dialog, and throws nothing ─────────────────────────────
  // A real key press, not a synthetic event: the page's keydown listeners must
  // all run without error (one used to call the removed closeSummary()).
  const escape = async () => {
    for (const type of ['keyDown', 'keyUp'])
      await send('Input.dispatchKeyEvent', { type, key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
    await sleep(300);
  };
  const isShown = id => js(`(() => { const el = document.getElementById('${id}');
    return !!el && getComputedStyle(el).display !== 'none'; })()`);
  const before = errors.length;
  await js(`document.querySelector('.btn-help[data-help2d]').click()`);
  check('Escape: help opens', await waitFor(`getComputedStyle(document.getElementById('help-overlay')).display !== 'none'`, 3000));
  await escape();
  check('Escape: closes help', !(await isShown('help-overlay')));
  await js(`openBugReport()`); await sleep(300);
  check('Escape: bug report opens', await isShown('bug-report-overlay'));
  await escape();
  check('Escape: closes the bug report', !(await isShown('bug-report-overlay')));
  await js(`openImportDialog()`); await sleep(300);
  check('Escape: import opens', await isShown('import-overlay'));
  await escape();
  check('Escape: closes import', !(await isShown('import-overlay')));
  check('Escape: no page errors', errors.length === before, errors.slice(before));

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 400)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
