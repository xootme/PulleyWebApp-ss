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
 *   waitFor(re, ms)  the last match of `re` (global) in the log, waiting up to ms (null)
 *   stop()      ends the Python and everything it started (taskkill /T on Windows)
 * }
 * options: app (the app's folder), prefix, shared (a RegExp or its source; none:
 * every root path is a stray), env (added to process.env; DATABASE_URL,
 * RESEND_API_KEY, CCT_MODE, CCT_ACCOUNTS_MODE and EDGE_SECRET are dropped, as the
 * harnesses did), python (else CCT_SITE_PYTHON, else the app's .venv314 or .venv),
 * module and attr (the app's "app.app"), name (the log folder's prefix),
 * startMs (how long it may take to listen, 60 s).
 */
"use strict";
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DROP = ["DATABASE_URL", "RESEND_API_KEY", "CCT_MODE", "CCT_ACCOUNTS_MODE", "EDGE_SECRET"];

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

// The request lines of one kind ("app", "shared", "stray"): "METHOD path",
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
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `${name}-site-`));
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
    if (exited !== null) throw new Error(`the site didn't start (exit ${exited}):\n${log()}`);
    if (Date.now() - t0 > startMs) {
      killTree(proc);
      throw new Error(`the site didn't listen in ${startMs / 1000} s:\n${log()}`);
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
    async waitFor(re, ms = 10000) {
      const g = new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g");
      for (const t1 = Date.now(); ;) {
        const m = log().match(g);
        if (m) return m[m.length - 1];
        if (Date.now() - t1 > ms) return null;
        await sleep(150);
      }
    },
    stop: () => killTree(proc),
    get exited() { return exited; },
  };
}

module.exports = { startSite, requestLines };
