// Drives the admin dashboard (cct_common.admin, /admin) in headless Chrome
// over CDP: who gets in, the subscriber list and its column sorting and
// search, the grant/adjust dialog, the refund dialog (quote, fee, the
// provider call), bug-report delete and the status tab.
//
// Needs a running app on a database seeded by admin_seed.py (run from the
// repo root; DATABASE_URL at a SQLite file also turns on the bug-report
// store; no PayPal keys, so the refund reaches the "not configured" answer
// and changes nothing):
//
//   python tests/browser/admin_seed.py <tmp>/cct.sqlite3 > <tmp>/cookies.json
//   TOKENS_ENABLED=1 DATABASE_URL=<tmp>/cct.sqlite3 ADMIN_EMAILS=admin@example.com \
//     PULLEY_LOG_DIR=<tmp> QUEUE_DISABLED=1 python app.py --port 5098 --no-debug
//   node tests/browser/admin_ui.js http://127.0.0.1:5098 <tmp>/cookies.json
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const [BASE, COOKIES] = process.argv.slice(2);
const cookies = JSON.parse(fs.readFileSync(COOKIES, 'utf8'));
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9345;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-adm-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1400,1000', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); };

async function main() {
  // Who gets in — plain HTTP, no browser needed.
  const get = (p, c) => fetch(BASE + p, { redirect: 'manual', headers: c ? { Cookie: `cct_session=${c}` } : {} });
  let r = await get('/admin');
  check('signed out: sent to the sign-in page', r.status === 302 && /\/account\/sign-in\?next=\/admin$/.test(r.headers.get('location')),
        [r.status, r.headers.get('location')]);
  r = await get('/account/sign-in?next=/admin');
  const signIn = await r.text();
  check('sign-in page: email form (and Google, where configured)', r.status === 200 && signIn.includes('Email me a sign-in link'), r.status);
  r = await get('/account/login?next=/admin');
  check('link page without a link: on to the sign-in page', r.status === 302 && /\/account\/sign-in\?next=\/admin$/.test(r.headers.get('location')),
        [r.status, r.headers.get('location')]);
  r = await get('/admin', cookies.plain);
  const notAdmin = await r.text();
  check('signed in, not an admin: told which account', r.status === 403 && notAdmin.includes('plain@example.com') && !notAdmin.includes('CCT Admin'), r.status);
  r = await get('/admin/api/accounts', cookies.plain);
  check('not an admin: API 404 too', r.status === 404, r.status);
  r = await fetch(BASE + '/admin/api/accounts/x/grant', { method: 'POST', body: '{"amount":5,"reason":"x"}',
    headers: { Cookie: `cct_session=${cookies.admin}`, 'Content-Type': 'application/json', Origin: 'https://evil.example' } });
  check('admin, but from another site: refused', r.status === 403, r.status);

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
  // First column of each row of a tab's table, and a row by its email.
  const col = (tab, n = 0) => js(`[...document.querySelectorAll('#${tab} tbody tr')].map(r => r.cells[${n}].textContent)`);
  const rowOf = (tab, email) => `[...document.querySelectorAll('#${tab} tbody tr')].find(r => r.textContent.includes('${email}'))`;
  const heading = (tab, label) => js(`[...document.querySelectorAll('#${tab} th')].find(h => h.firstChild.textContent === '${label}').click()`);
  const typeIn = (sel, v) => js(`(() => { const e = document.querySelector('${sel}'); e.value = ${JSON.stringify(v)};
    e.dispatchEvent(new Event('input', {bubbles:true})); })()`);

  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
  await send('Network.setCookie', { name: 'cct_session', value: cookies.admin, url: BASE });
  await send('Page.navigate', { url: BASE + '/admin' });
  await waitFor("document.querySelectorAll('#subs tbody tr').length > 0");
  await js('window.confirm = () => true');          // the refund and delete confirmations

  // ── subscribers ──
  check('five subscribers', (await js("document.querySelector('#subs-count').textContent")) === '5 subscribers',
        await js("document.querySelector('#subs-count').textContent"));
  check('title names the app', await waitFor("document.querySelector('#title').textContent === 'Timing Pulleys — Admin'"),
        await js("document.querySelector('#title').textContent"));
  check('newest first by default', (await col('subs'))[0] === 'carol@example.com', await col('subs'));
  const bob = await js(`(() => { const r = ${rowOf('subs', 'bob@example.com')}; return {
    now: [...r.cells[3].firstChild.children].map(d => d.textContent).join(' | '), life: r.cells[4].textContent,
    uses: [...r.cells[5].querySelectorAll('li')].map(l => l.textContent),
    refund: [...r.cells[6].querySelectorAll('button')].map(b => b.textContent) }; })()`);
  check('bob: tokens now split free / bought', bob.now === '58 | 8 free · 50 bought', bob.now);
  check('bob: lifetime is free + purchased', bob.life === '70', bob.life);
  check('bob: only the last 5 of 6 uses', bob.uses.length === 5, bob.uses);
  check('uses name the tool, tokens and format', /^pulleys · 2 tokens · \d{4}-\d\d-\d\d \d\d:\d\d \(SVG\)$/.test(bob.uses[0]), bob.uses[0]);
  check('bob (bought tokens) gets Refund…', JSON.stringify(bob.refund) === '["Grant / adjust","Refund…"]', bob.refund);
  const alice = await js(`${rowOf('subs', 'alice@example.com')}.cells[6].textContent`);
  check('alice (no purchases) gets no Refund…', alice === 'Grant / adjust', alice);

  await heading('subs', 'Email');
  check('Email sorts A→Z', JSON.stringify(await col('subs')) === JSON.stringify(
    ['admin@example.com', 'alice@example.com', 'bob@example.com', 'carol@example.com', 'plain@example.com']), await col('subs'));
  await heading('subs', 'Email');
  check('again reverses it', (await col('subs'))[0] === 'plain@example.com', await col('subs'));
  await heading('subs', 'Lifetime tokens');
  check('Lifetime sorts high first', JSON.stringify((await col('subs', 4)).slice(0, 2)) === '["120","70"]', await col('subs', 4));
  await heading('subs', 'Last 5 uses');
  check('Last uses sorts most recent first', (await col('subs'))[0] === 'bob@example.com', await col('subs'));
  check('…with no uses last', (await col('subs', 5)).slice(2).every(v => v === 'none'), await col('subs', 5));
  await heading('subs', 'Last 5 uses');
  check('reversed: oldest first', (await col('subs'))[0] === 'alice@example.com', await col('subs'));
  check('…and no uses still last', (await col('subs', 5)).slice(2).every(v => v === 'none'), await col('subs', 5));
  check('sorted column shows its arrow', (await js("document.querySelector('#subs th .arrow').parentNode.firstChild.textContent")) === 'Last 5 uses');

  await typeIn('#subs-q', 'ALI');
  check('search is case-blind', JSON.stringify(await col('subs')) === '["alice@example.com"]', await col('subs'));
  check('count follows the search', (await js("document.querySelector('#subs-count').textContent")) === '1 subscriber');

  // grant / adjust
  await js(`${rowOf('subs', 'alice@example.com')}.querySelector('button').click()`);
  check('grant dialog opens on alice', (await js("document.querySelector('#dlg-grant').open && document.querySelector('#grant-who').textContent"))
        === 'alice@example.com — 11 tokens now');
  await js("document.querySelector('#grant-amount').value = '5'; document.querySelector('#grant-go').click()");
  await waitFor("document.querySelector('#grant-msg').textContent");
  check('grant needs a reason', /reason/.test(await js("document.querySelector('#grant-msg').textContent")),
        await js("document.querySelector('#grant-msg').textContent"));
  await js("document.querySelector('#grant-reason').value = 'goodwill'; document.querySelector('#grant-go').click()");
  await waitFor("/balance now/.test(document.querySelector('#grant-msg').textContent)");
  check('grant saved', (await js("document.querySelector('#grant-msg').textContent")) === 'Done — balance now 16.');
  await waitFor("!document.querySelector('#dlg-grant').open");
  check('table shows the new balance', (await js(`${rowOf('subs', 'alice@example.com')}.cells[3].firstChild.firstChild.textContent`)) === '16');
  check('a grant is lifetime tokens too', (await js(`${rowOf('subs', 'alice@example.com')}.cells[4].textContent`)) === '25');
  await js(`${rowOf('subs', 'alice@example.com')}.querySelector('button').click()`);
  await js("document.querySelector('#grant-amount').value = '-6'; document.querySelector('#grant-reason').value = 'correction'; document.querySelector('#grant-go').click()");
  await waitFor("/balance now/.test(document.querySelector('#grant-msg').textContent)");
  check('adjust down', (await js("document.querySelector('#grant-msg').textContent")) === 'Done — balance now 10.');
  await waitFor("!document.querySelector('#dlg-grant').open");
  check('an adjust down is not lifetime', (await js(`${rowOf('subs', 'alice@example.com')}.cells[4].textContent`)) === '25');

  // ── sales, reached from bob's Refund… ──
  await typeIn('#subs-q', 'bob');
  await js(`${rowOf('subs', 'bob@example.com')}.querySelectorAll('button')[1].click()`);
  await waitFor("document.querySelectorAll('#sales tbody tr').length > 0");
  check('Refund… opens Sales filtered to bob', (await js("document.querySelector('#tab-sales').classList.contains('on') && document.querySelector('#sales-q').value")) === 'bob@example.com');
  const sale = await js("[...document.querySelector('#sales tbody tr').cells].map(c => c.textContent)");
  check('the order row', sale.slice(1, 6).join('|') === 'bob@example.com|paypal|50|$5.00|completed', sale);
  await js("document.querySelector('#sales tbody tr button').click()");
  await waitFor("document.querySelectorAll('#refund-body .row').length === 4");
  const q = () => js("[...document.querySelectorAll('#refund-body .row b')].map(b => b.textContent)");
  check('quote: 50 unused tokens worth $5.00, no fee known', JSON.stringify(await q()) === '["50","$5.00","− $0.00","$5.00"]', await q());
  check('fee unknown is said', /did not report a fee/.test(await js("document.querySelector('#refund-fee-src').textContent")));
  await typeIn('#refund-fee', '39');
  await waitFor(`document.querySelectorAll('#refund-body .row b')[3].textContent === '$4.61'`);
  check('an entered fee comes off', JSON.stringify(await q()) === '["50","$5.00","− $0.39","$4.61"]', await q());
  await js("document.querySelector('#refund-go').click()");
  await waitFor("document.querySelector('#refund-msg').textContent");
  check('no PayPal keys: says so', /paypal isn't configured/.test(await js("document.querySelector('#refund-msg').textContent")),
        await js("document.querySelector('#refund-msg').textContent"));
  const after = await js("fetch('/admin/api/accounts').then(r => r.json()).then(d => d.accounts.find(a => a.email === 'bob@example.com').balance)");
  check('…and takes nothing back', after === 58, after);
  await js("document.querySelector('#dlg-refund').close()");
  await typeIn('#sales-q', 'nobody');
  check('sales search', (await js("document.querySelector('#sales-count').textContent")) === '0 orders');

  // ── bug reports ──
  await js("document.querySelector('nav [data-tab=bugs]').click()");
  await waitFor("document.querySelectorAll('#bugs tbody tr').length === 2");
  check('two bug reports, newest first', JSON.stringify((await col('bugs', 2)).map(s => s.split('design')[0]))
        === '["Flange too thin","Belt teeth look wrong"]', await col('bugs', 2));
  await js(`${rowOf('bugs', 'Flange too thin')}.querySelector('button').click()`);
  await waitFor("document.querySelectorAll('#bugs tbody tr').length === 1");
  check('delete removes it', (await js("document.querySelector('#bugs-count').textContent")) === '1 report');
  const left = await js("fetch('/admin/api/bugs').then(r => r.json()).then(d => d.bugs.map(b => b.report_id))");
  check('…from the database', JSON.stringify(left) === '["rep-1"]', left);

  // ── status ──
  await js("document.querySelector('nav [data-tab=status]').click()");
  await waitFor("document.querySelectorAll('#status .row').length > 5");
  const st = await js("Object.fromEntries([...document.querySelectorAll('#status .row')].map(r => [r.firstChild.textContent, r.lastChild.textContent]))");
  check('status: app and version', st.App === 'Timing Pulleys 2.0.2', st.App);
  check('status: database', st.Database === 'sqlite — OK', st.Database);
  check('status: accounts', st.Accounts === '5', st.Accounts);
  check('status: admins', st.Admins === 'admin@example.com', st.Admins);

  finish(errors);
  ws.close(); chrome.kill();
}

function finish(errors) {
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : '  -> ' + String(JSON.stringify(r.detail)).slice(0, 600)}`);
  console.log(`page errors: ${errors.length ? JSON.stringify(errors.slice(0, 5)) : 'none'}`);
  console.log(`${results.filter(r => r.ok).length}/${results.length} passed`);
}
main().catch(e => { console.error(e); finish([]); chrome.kill(); process.exit(1); });
