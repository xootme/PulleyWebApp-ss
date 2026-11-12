/*
 * cct_common/testing/site.js — an app as cheapcadtools.com serves it, for the
 * browser harnesses (cct_common 0.56.0). It starts cct_common.testing.site in
 * the app's own Python, which serves the app under its prefix and the site's
 * shared paths at the root, in one process, and writes everything to a log
 * file. The harness's node process then holds only Chrome's CDP socket: no
 * proxy, no pipe. Sprocket found node 24.15 dying with 0xC0000409 about 1 run
 * in 5 when a harness ran its own proxy (http.createServer + http.request +
 * pipe) under the CPU cap; straight to app.py, 0 of 40 (2026-10-31).
 *
 *   const { startSite } = require("../../cct_common/testing/site.js");
 *   const s = await startSite({ app: APPDIR, prefix: "/tools/sprocket",
 *     shared: /^\/(account\/|api\/account\/(providers|login-link|logout)(\?|$))/,
 *     env: { TOKENS_ENABLED: "1", SPROCKET_LOG_DIR: logDir } });
 *   await openPage(s.site + "/tools/sprocket/");
 *   ...
 *   s.stop();
 *
 * startSite(options) -> {
 *   site        "http://127.0.0.1:<port>": the site's root
 *   appPort     the bare app's port (direct calls: health, settings)
 *   logFile     the log file's path; requestsFile, the request lines' (0.56.2)
 *   log()       the log so far: the app's stdout and stderr (and its server's access log)
 *   strays()    "METHOD /path?query" asked of the root and answered 404
 *   takeStrays()  the strays since the last take (tokens_ui's "nothing else asked")
 *   shared()    "METHOD /path" that went to the shared paths
 *   asked()     "METHOD /path" the app was asked, prefix stripped (prefix_ui)
 *   dropped()   "METHOD /path Error: why" for each connection that dropped while its
 *               request was read or answered ("- -": before its first line), the
 *               site's port; appDropped(), the bare app's (0.56.5). A page's
 *               "Failed to fetch" with nothing here never reached the server.
 *   waitFor(re, ms)  the last match of `re` (global) in the log, waiting up to ms (null)
 *   stop()      ends the Python and everything it started (taskkill /T on Windows),
 *               then removes the log folder: read the log before it (0.61.7)
 * }
 * options: app (the app's folder), prefix, shared (a RegExp or its source; none:
 * every root path is a stray), env (added to process.env; DATABASE_URL,
 * RESEND_API_KEY, CCT_MODE, CCT_ACCOUNTS_MODE and EDGE_SECRET are dropped, as the
 * harnesses did), python (else CCT_SITE_PYTHON, else the app's .venv314 or .venv),
 * module and attr (the app's "app.app"), name (the log folder's prefix),
 * startMs (how long it may take to listen, 60 s).
 *
 * The log folder (<name>-site-* in Temp) goes at stop() and at the end of the
 * run by any way out (the exit hook), tried again for up to RM_MS while
 * Windows still holds its files. One left anyway (the run killed) keeps its
 * mark and the first startSite of a later run of the same name sweeps it
 * once a day old; unmarked ones, made before 0.61.7, stay for the owner's
 * decision (campaign 2026-11-11-cdp-profile-leak: 743 left by then).
 */
"use strict";
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DROP = ["DATABASE_URL", "RESEND_API_KEY", "CCT_MODE", "CCT_ACCOUNTS_MODE", "EDGE_SECRET"];

// Each site's log folder until removed, as cdp.js keeps Chrome's profiles (0.61.6).
const folders = new Map();      // folder -> { proc: the site's Python, mark }
const swept = new Set();        // the names whose old folders this run has swept
const MARK = "cct-site-run.json";
const STALE_MS = 24 * 3600 * 1000;
const RM_MS = 10000;
const sleepSync = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
const running = (pid) => {
  try { process.kill(pid, 0); return true; } catch (e) { return e.code === "EPERM"; }
};

function appPython(app) {
  if (process.env.CCT_SITE_PYTHON) return process.env.CCT_SITE_PYTHON;
  const tries = process.platform === "win32"
    ? [[".venv314", "Scripts", "python.exe"], [".venv", "Scripts", "python.exe"]]
    : [[".venv314", "bin", "python"], [".venv", "bin", "python"]];
  for (const t of tries) {
    const p = path.join(app, ...t);
    if (fs.existsSync(p)) return p;
  }
  throw new Error(`no Python for ${app}: give startSite({ python }) or set CCT_SITE_PYTHON`);
}

function killTree(proc) {
  if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
  try {
    if (process.platform === "win32") {
      execFileSync("taskkill", ["/T", "/F", "/PID", String(proc.pid)], { stdio: "ignore", windowsHide: true });
    } else {
      process.kill(-proc.pid, "SIGKILL");
    }
  } catch { /* already gone */ }
}

// Synchronous, for the exit hook: the Python stopped and given up to 3 s to
// go, then the removal tried again for up to RM_MS (Windows frees a killed
// process's files a moment after it ends). A folder that won't go gets its
// mark back (the removal may have taken it first) and is left for the sweep.
function removeFolder(dir) {
  const { proc, mark } = folders.get(dir) || {};
  folders.delete(dir);
  killTree(proc);
  if (proc && proc.pid) for (let i = 0; i < 30 && running(proc.pid); i++) sleepSync(100);
  for (const t0 = Date.now(); ;) {
    try { fs.rmSync(dir, { recursive: true, force: true }); break; } catch { /* held: again */ }
    if (Date.now() - t0 > RM_MS) break;
    sleepSync(200);
  }
  try { if (mark && fs.existsSync(dir)) fs.writeFileSync(path.join(dir, MARK), mark); } catch { /* gone */ }
}
process.on("exit", () => { for (const dir of [...folders.keys()]) removeFolder(dir); });

// At the first startSite of a name: its marked folders over a day old. Another
// harness of the same app may be running, so never a fresh one, and never
// another name's or an unmarked folder.
function sweepFolders(prefix, now = Date.now()) {
  let names;
  try { names = fs.readdirSync(os.tmpdir()); } catch { return; }
  for (const n of names) {
    if (!n.startsWith(prefix)) continue;
    const dir = path.join(os.tmpdir(), n);
    let started;
    try { started = Number(JSON.parse(fs.readFileSync(path.join(dir, MARK), "utf8")).started); } catch { continue; }
    if (!(now - started > STALE_MS)) continue;
    try { fs.rmSync(dir, { recursive: true, force: true, maxRetries: 2, retryDelay: 100 }); } catch { /* next time */ }
  }
}

// The request lines of one kind ("app", "shared", "stray", "dropped",
// "app-dropped"): "METHOD path" (a dropped one's error after it),
// whole lines only (a last one still being written isn't one yet).
function requestLines(text, kind) {
  const whole = text.split("\n").slice(0, -1);
  const out = [];
  for (const line of whole) {
    const m = /^SITE (\S+) (\S+) (.*?)\r?$/.exec(line);
    if (m && m[1] === kind) out.push(`${m[2]} ${m[3]}`);
  }
  return out;
}

async function startSite({ app, prefix, shared = null, env = {}, python = null, module = "app", attr = "app",
                           name = "site", startMs = 60000 } = {}) {
  if (!app || !prefix) throw new Error("startSite needs { app, prefix }");
  if (!swept.has(name)) { swept.add(name); sweepFolders(`${name}-site-`); }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `${name}-site-`));
  const mark = JSON.stringify({ started: Date.now(), pid: process.pid });
  folders.set(dir, { proc: null, mark });
  fs.writeFileSync(path.join(dir, MARK), mark);
  const logFile = path.join(dir, "site.log");
  const requestsFile = path.join(dir, "requests.log");
  fs.writeFileSync(logFile, "");
  fs.writeFileSync(requestsFile, "");
  const runEnv = { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1", ...env };
  for (const k of DROP) if (!(k in env)) delete runEnv[k];
  const args = ["-m", "cct_common.testing.site", "--app", app, "--prefix", prefix, "--log", logFile,
                "--requests", requestsFile, "--module", module, "--attr", attr];
  if (shared) args.push("--shared", shared instanceof RegExp ? shared.source : String(shared));
  // The child's stdout and stderr are the log file itself (a file, never a
  // pipe): node never reads them, and an error before site.py takes them over
  // (a cct_common without it, a bad argument) is in the log too.
  const out = fs.openSync(logFile, "a");
  let proc;
  try {
    proc = spawn(python || appPython(app), args,
                 { cwd: app, env: runEnv, stdio: ["ignore", out, out], windowsHide: true,
                   detached: process.platform !== "win32" });
  } finally {
    fs.closeSync(out);
  }
  folders.set(dir, { proc, mark });
  let exited = null;
  proc.on("exit", (code, signal) => { exited = signal || code; });
  proc.on("error", (e) => { exited = String(e); });
  const read = (f) => { try { return fs.readFileSync(f, "utf8"); } catch { return ""; } };
  const log = () => read(logFile);
  const requests = () => read(requestsFile);
  const t0 = Date.now();
  let ports = null;
  while (!ports) {
    const m = requests().match(/^SITE (\d+)\r?\nAPP (\d+)\r?$/m);
    if (m) { ports = [Number(m[1]), Number(m[2])]; break; }
    if (exited !== null) {
      const text = log();
      removeFolder(dir);
      throw new Error(`the site didn't start (exit ${exited}):\n${text}`);
    }
    if (Date.now() - t0 > startMs) {
      killTree(proc);
      const text = log();
      removeFolder(dir);
      throw new Error(`the site didn't listen in ${startMs / 1000} s:\n${text}`);
    }
    await sleep(100);
  }
  const lines = (kind) => requestLines(requests(), kind);
  let taken = 0;
  return {
    site: `http://127.0.0.1:${ports[0]}`,
    appPort: ports[1],
    logFile,
    requestsFile,
    log,
    strays: () => lines("stray"),
    takeStrays: () => { const all = lines("stray"); const fresh = all.slice(taken); taken = all.length; return fresh; },
    shared: () => lines("shared"),
    asked: () => lines("app"),
    dropped: () => lines("dropped"),
    appDropped: () => lines("app-dropped"),
    async waitFor(re, ms = 10000) {
      const g = new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g");
      for (const t1 = Date.now(); ;) {
        const m = log().match(g);
        if (m) return m[m.length - 1];
        if (Date.now() - t1 > ms) return null;
        await sleep(150);
      }
    },
    stop: () => removeFolder(dir),
    get exited() { return exited; },
  };
}

module.exports = { startSite, requestLines, sweepFolders };
