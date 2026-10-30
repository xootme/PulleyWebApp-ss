/*
 * cct_common/testing/cdp.js — Chrome over CDP for the browser harnesses of
 * the Gear family (Gear, Worm, Bevel, Planetary, Sprocket, GearBeltChain),
 * and the rule that a harness ends with a verdict or fails (0.26.1).
 *
 * Each app carried its own copy of tests/browser/cdp.js, and every copy
 * awaited the CDP socket's open (or a reply) with nothing listening for it
 * to fail. Kill Chrome at the wrong moment and the socket closed, the last
 * live handle went with it, the event loop drained, and Node exited 0: no
 * output, or half the checks, read as a pass by a runner that judges the
 * exit code; or, where a harness's own proxy kept the loop alive, a hang
 * to the runner's timeout (campaign 2026-10-26-cdp-silent-exit: proved in
 * all six). E-Box's lib/cdp.js and PCB Importer's cdp_socket.js fixed
 * their own; this is the fix for the Gear lineage, one copy for all six.
 *
 * So, from here:
 *  - checker().finish(page) is the only way out with exit 0: it prints
 *    "ALL PASS" (or "N FAILED", exit 1) as the last line.
 *  - Anything that ends the run first exits 2 with
 *    "FAIL  harness crashed: <why>" as the LAST line (any stack goes
 *    before it): Chrome failing to start or exiting, the socket erroring
 *    or closing before it opens, not opening in `openMs`, closing or
 *    erroring after (every pending send() rejects too), a send() with no
 *    answer in `sendMs`, an uncaught exception or unhandled rejection, and
 *    the event loop running out of work with no verdict (beforeExit).
 * The runner's own rules (a last "ALL PASS" line, a timeout per harness)
 * stay in each app's tools/test_all.py.
 *
 * Each app's tests/browser/cdp.js becomes a few lines naming its page:
 *
 *   module.exports = require("../../cct_common/testing/cdp.js").forApp({
 *     name: "gear", idle: "gearIdle", leave: "focusout",
 *     downloadFiles: "gearDownloadFiles", partParam: "gear",
 *   });
 *
 * and the harnesses keep `const { openPage, checker, sleep } = require("./cdp")`.
 * forApp's options (the defaults are Gear's):
 *   name           the temp profile's prefix ("<name>-cdp-")
 *   idle           window.<idle>() is true once the page has applied every change;
 *                  window.<idle>Why(), if the page has one, says what it still
 *                  waits on (a string, or anything JSON), and a settle that
 *                  fails prints it with the requests still open (0.32.1)
 *   waitIdleMs     waitIdle()'s default timeout (20 s)
 *   idleThrows     waitIdle() throws when it runs out, rather than returning false
 *   settleMs       how long opening or reloading the page may take to settle (60 s)
 *   leave          the event setValue() fires after 'input', as leaving the
 *                  field does: "focusout" (Gear's refusal gate), "change"
 *                  (Sprocket), or none
 *   downloadFiles  window.<downloadFiles>() lists the download window's files;
 *                  gives the page files(), fileFor() and fetchFile()
 *   partParam      fileFor(path, 2)'s parameter for the second part ("gear")
 *   extend         ({ evaluate, waitIdle, send, sleep }) => { extra helpers }
 *   openMs         the CDP socket must open within this (15 s)
 *   sendMs         every CDP call must be answered within this (120 s)
 *   chrome         [command, ...args] to start in place of Chrome (the tests' fake)
 * Env: CCT_CHROME, the Chrome to run (else the desktop's).
 * tests/test_cdp_harness.py drives every way out against a fake Chrome.
 */
"use strict";
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

// CCT_CHROME on a test host (the msi one), else the desktop's Chrome.
const CHROME = process.env.CCT_CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const chromes = new Set();      // every Chrome this run started, killed on a crash
let ended = false;              // a verdict has been printed

// `detail` (a stack) goes first: the verdict is always the LAST line.
function crashed(why, detail) {
  if (ended) return;
  ended = true;
  if (detail) console.log(String(detail));
  console.log(`FAIL  harness crashed: ${why}`);
  for (const c of chromes) { try { c.kill(); } catch { /* already gone */ } }
  process.exit(2);
}
const firstLine = (e) => String((e && e.message) || e).split("\n")[0];
process.on("uncaughtException", (e) => crashed(firstLine(e), e && e.stack));
process.on("unhandledRejection", (e) => crashed(firstLine(e), e && e.stack));
// Only a drained loop gets here (process.exit() never does): the run stopped
// waiting for something that will never come, and printed no verdict.
process.on("beforeExit", () => crashed("the run ran out of work before its verdict"));

function forApp({
  name = "gear", idle = "gearIdle", waitIdleMs = 20000, idleThrows = false, settleMs = 60000,
  leave = null, downloadFiles = null, partParam = "gear", extend = null,
  openMs = 15000, sendMs = 120000, chrome = null,
} = {}) {
  // `port` is accepted for the harnesses' sake but not used: each Chrome
  // takes a free port of its own (--remote-debugging-port=0) and writes it to
  // DevToolsActivePort in its profile, so a harness can never talk to another
  // run's browser that is still shutting down on a fixed port. `settle`
  // overrides settleMs for this page's first settle (Worm's).
  async function openPage(url, { width = 1200, height = 900, settle = settleMs } = {}) {
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), `${name}-cdp-`));
    const [command, ...prefix] = chrome || [CHROME];
    // SwiftShader gives headless Chrome a software WebGL for the 3D preview.
    const proc = spawn(command, [...prefix, "--headless=new", "--remote-debugging-port=0",
      `--user-data-dir=${profile}`, `--window-size=${width},${height}`,
      "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "about:blank"], { stdio: "ignore" });
    chromes.add(proc);
    let closing = false;
    proc.on("error", (e) => crashed(`Chrome did not start (${command}): ${e.message}`));
    proc.on("exit", (code, signal) => {
      chromes.delete(proc);
      if (!closing) crashed(`Chrome exited (${signal || code}) before the verdict`);
    });

    let port = null;
    for (let i = 0; i < 100 && !port; i++) {
      try {
        port = Number(fs.readFileSync(path.join(profile, "DevToolsActivePort"), "utf8").split("\n")[0]);
      } catch { /* not written yet */ }
      if (!port) await sleep(100);
    }
    if (!port) throw new Error("Chrome didn't start (no DevToolsActivePort)");
    let target;
    for (let i = 0; i < 50 && !target; i++) {
      try {
        const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch { /* not up yet */ }
      if (!target) await sleep(200);
    }
    if (!target) throw new Error(`Chrome on port ${port} has no page`);

    // The socket opens, or the run fails: on an abrupt drop Node's WebSocket
    // fires 'error' before 'close' (E-Box's lesson), so both are heard.
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(
        `the CDP socket did not open in ${openMs / 1000} s (${target.webSocketDebuggerUrl})`)), openMs);
      const fail = (what) => () => { clearTimeout(timer); reject(new Error(
        `the CDP socket ${what} before it opened (${target.webSocketDebuggerUrl})`)); };
      ws.addEventListener("open", () => { clearTimeout(timer); resolve(); });
      ws.addEventListener("error", fail("failed"));
      ws.addEventListener("close", fail("closed"));
    });

    let id = 0;
    const pending = new Map();          // id -> { resolve, reject }
    const errors = [];
    const serverErrors = [];            // 5xx answers, once watchServer() is on
    let watching = false;
    const open = new Map();             // requestId -> { method, url, t0 }: sent, not yet finished
    const listeners = new Map();        // CDP event -> handlers (on())
    // Open, then lost without close() asking: every waiting call fails, then the run.
    const lost = (why) => {
      if (closing) return;
      for (const p of pending.values()) p.reject(new Error(why));
      pending.clear();
      crashed(why);
    };
    ws.addEventListener("error", () => lost("the CDP socket failed before the verdict"));
    ws.addEventListener("close", (e) => lost(`the CDP socket closed before the verdict (code ${e.code})`));
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id).resolve(msg); pending.delete(msg.id); }
      for (const f of listeners.get(msg.method) || []) f(msg.params);
      if (msg.method === "Runtime.exceptionThrown") {
        // The text alone is "Uncaught (in promise)"; the description has the error.
        const d = msg.params.exceptionDetails;
        errors.push(d.exception && d.exception.description ? `${d.text} ${d.exception.description}` : d.text);
      }
      if (watching && msg.method === "Network.responseReceived" && msg.params.response.status >= 500)
        serverErrors.push(`${msg.params.response.status} ${msg.params.response.url}`);
      if (msg.method === "Network.requestWillBeSent")       // a redirect keeps its id and its start
        open.set(msg.params.requestId, { method: msg.params.request.method, url: msg.params.request.url,
                                         t0: (open.get(msg.params.requestId) || {}).t0 || Date.now() });
      if (msg.method === "Network.loadingFinished" || msg.method === "Network.loadingFailed")
        open.delete(msg.params.requestId);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
        errors.push(msg.params.args.map((a) => a.value ?? a.description).join(" "));   // an object: its description
    });
    // A page that stops answering (a test turn's limits, a hung renderer) fails
    // the run and says where, not hang it (Planetary's two minutes).
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      if (ws.readyState !== WebSocket.OPEN) {
        reject(new Error(`CDP ${method}: the socket is no longer open`));
        return;
      }
      const mid = ++id;
      const timer = setTimeout(() => {
        pending.delete(mid);
        reject(new Error(`CDP ${method} had no answer in ${sendMs / 1000} s (${JSON.stringify(params).slice(0, 120)})`));
      }, sendMs);
      pending.set(mid, {
        resolve: (msg) => { clearTimeout(timer); resolve(msg); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
    const evaluate = async (expr) =>
      (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }))
        .result.result.value;
    // Wait until the page has applied every change (window.<idle>): the
    // debounce has fired, its requests are back and any 3D model has loaded.
    const waitIdle = async (timeout = waitIdleMs) => {
      const t0 = Date.now();
      await sleep(30);
      while (Date.now() - t0 < timeout) {
        // Not idle until this page's script has run: before that there is no
        // idle hook, and a draw is still to come.
        if (await evaluate(`!!window.${idle} && window.${idle}()`)) return true;
        await sleep(50);
      }
      if (idleThrows) throw new Error(`the page was still drawing after ${timeout / 1000} s`);
      return false;
    };
    // What the page still waits on, for a settle that failed: the requests
    // sent and not finished, the longest open first, and window.<idle>Why()'s
    // answer. Gear reproduced a bare "never settled" 72 times to learn it was
    // load (2026-10-30, its ADR-053). A page that no longer answers says so.
    const waitingOn = async () => {
      const now = Date.now();
      const reqs = [...open.values()].sort((a, b) => a.t0 - b.t0);
      const lines = reqs.slice(0, 10).map((r) =>
        `  still open: ${r.method} ${r.url.slice(0, 200)} (${((now - r.t0) / 1000).toFixed(1)} s)`);
      if (reqs.length > 10) lines.push(`  ... and ${reqs.length - 10} more`);
      if (!reqs.length) lines.push("  no request still open");
      const why = `${idle}Why`;
      const asked = evaluate(`(async () => { try {
          if (typeof window.${why} !== "function") return null;
          const v = await window.${why}();
          return typeof v === "string" ? v : JSON.stringify(v);
        } catch (e) { return "threw " + e; } })()`);
      const answer = await Promise.race([asked.catch((e) => `no answer (${firstLine(e)})`),
                                         sleep(5000).then(() => "no answer in 5 s")]);
      lines.push(answer === null ? `  no window.${why}()` : `  window.${why}(): ${answer}`);
      return lines.join("\n");
    };
    // A page that opens must settle (Sprocket Designer's 2026-10-22 night: on a
    // loaded machine its harnesses went on before the page had its tables and
    // handlers). Not settled in time is a failure, never a page to test. The
    // first line says so; the next ones what it still waits on.
    const settled = async (what, ms) => {
      let idleNow = false;
      try { idleNow = await waitIdle(ms); } catch { /* idleThrows: said below */ }
      if (!idleNow) throw new Error(`${what}: the page never settled in ${ms / 1000} s\n${await waitingOn()}`);
    };
    // Reload and wait for the new document's first drawing. The old document is
    // marked, so its idle answer can't be taken for the new one's. The settle
    // timeout is reload(ms) or reload({ timeout }).
    const reload = async (opt) => {
      const timeout = typeof opt === "number" ? opt : (opt && opt.timeout) || settleMs;
      await evaluate(`window.__oldDocument = true; location.reload()`);
      const t0 = Date.now();
      let fresh = false;
      while (!fresh && Date.now() - t0 < settleMs) {
        try { fresh = await evaluate(`!window.__oldDocument`); } catch { /* mid-navigation */ }
        if (!fresh) await sleep(30);
      }
      if (!fresh) throw new Error(`reload: still the old document after ${settleMs / 1000} s`);
      await settled("reload", timeout);
    };
    // Set a control's value the way a user edit does (fires 'input'), then the
    // app's `leave` event as leaving the field does (Gear's 'focusout' only its
    // refusal gate hears; Sprocket's 'change'), so a refusal shows at once,
    // then wait until the page is idle again. Typing part-way: busy_ui.js.
    const setValue = async (elId, value) => {
      await evaluate(`(() => { const el = document.getElementById(${JSON.stringify(elId)});
        el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event('input'));
        ${leave ? `el.dispatchEvent(new Event(${JSON.stringify(leave)}));` : ""} })()`);
      await waitIdle();
    };
    // Rendered and on screen — not just the `hidden` flag, which CSS can override.
    const visible = (elId) => evaluate(`(() => { const el = document.getElementById(${JSON.stringify(elId)});
        return !!el && el.offsetParent !== null && el.getBoundingClientRect().height > 0; })()`);
    // Tick or untick a box (the page listens for 'change' on checkboxes).
    const setCheck = async (elId, on) => {
      await evaluate(`(() => { const el = document.getElementById(${JSON.stringify(elId)});
        el.checked = ${!!on}; el.dispatchEvent(new Event('change')); })()`);
      await waitIdle();
    };
    // Open a collapsible card (Advanced, Spokes, Layer Cake) by its header.
    const openSection = async (key) => {
      await evaluate(`(() => { const b = document.getElementById(${JSON.stringify(key + "-body")});
        if (!b.classList.contains('open'))
          document.querySelector('.hub-header[data-toggle="${key}"]').click(); })()`);
    };
    const screenshot = async (file) => {
      const png = await send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(file, Buffer.from(png.result.data, "base64"));
    };
    // Every file the download window would fetch with every box ticked, and
    // fetching one of them: "<status> <content-disposition>".
    const downloads = {};
    if (downloadFiles) {
      downloads.files = () => evaluate(`window.${downloadFiles}()`);
      downloads.fileFor = async (p, part = 1) =>
        (await downloads.files()).find((f) => f.path === p && (f.params[partParam] === "2") === (part === 2));
      downloads.fetchFile = (f) => evaluate(`fetch(${JSON.stringify(f.path)} + '?' +
          new URLSearchParams(${JSON.stringify(f.params)}))
          .then(r => r.status + ' ' + r.headers.get('content-disposition'))`);
    }

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Network.enable");        // the requests still open, for a settle that fails
    await send("Page.navigate", { url });
    await sleep(1500);

    // The harness's own end: neither the socket closing nor Chrome exiting is a crash now.
    const close = () => {
      closing = true;
      try { ws.close(); } catch { /* already closed */ }
      try { proc.kill(); } catch { /* already gone */ }
    };
    await settled(`open ${url}`, settle);
    // Record every server answer of 500 or more in serverErrors.
    const watchServer = () => { watching = true; return send("Network.enable"); };
    // Handle a CDP event (e.g. Fetch.requestPaused, to hold a request back).
    const on = (method, f) => listeners.set(method, [...(listeners.get(method) || []), f]);
    return { send, evaluate, setValue, waitIdle, reload, visible, screenshot, errors, close,
             serverErrors, watchServer, on, setCheck, openSection, ...downloads,
             ...(extend ? extend({ evaluate, waitIdle, send, sleep }) : {}) };
  }

  return { openPage, checker, sleep };
}

function checker() {
  let failures = 0;
  const check = (ok, what) => {
    console.log(`${ok ? "PASS" : "FAIL"}  ${what}`);
    if (!ok) failures++;
  };
  const finish = (page) => {
    check(page.errors.length === 0, `no page errors (${page.errors.join(" | ") || "none"})`);
    page.close();
    ended = true;
    console.log(failures ? `${failures} FAILED` : "ALL PASS");
    process.exit(failures ? 1 : 0);
  };
  return { check, finish };
}

module.exports = { forApp, checker, sleep };
