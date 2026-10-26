"""The page's idle hook (index.html's pageIdle), which every browser harness's page load
waits on: the page's own script, run in Node with a fetch held open, a clock and timers
the test moves. Sprocket's page once said idle while its start-up tables loaded (night of
2026-10-22): here the start-up, a fetch out, a debounced redraw not yet run and the
drawing overlay each keep it busy."""
import json
import re
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

RUN = r"""
const vm = require('vm');
const src = require('fs').readFileSync(process.argv[2], 'utf8');
let clock = 1000, overlay = false, release = null, nextId = 0;
const timers = new Map();
const box = {
  fetch: () => new Promise(r => { release = r; }),
  performance: { now: () => clock },
  setTimeout: (f) => { const id = ++nextId; timers.set(id, f); return id; },
  clearTimeout: (id) => timers.delete(id),
  CCTBusy: { shown: () => overlay },
  previewBox: () => null,
};
box.window = box;
vm.createContext(box);
vm.runInContext(src, box);
const idle = () => vm.runInContext('pageIdle()', box);
(async () => {
  const seen = {};
  seen.starting = idle();
  vm.runInContext('pageBusy--', box);                 // window.onload's last step
  seen.opened = idle();
  const p = vm.runInContext("window.fetch('/api/dimensions')", box);
  seen.fetchOut = idle();
  release({}); await p;
  seen.justAnswered = idle();
  clock += 300;
  seen.quietAfter = idle();
  const id = vm.runInContext('later(() => {}, 300)', box);
  seen.redrawOwed = idle();
  vm.runInContext(`cancelLater(${id})`, box);
  seen.redrawCancelled = idle();
  vm.runInContext('later(() => {}, 300)', box);
  for (const [k, f] of [...timers]) { timers.delete(k); f(); }
  seen.redrawRan = idle();
  clock += 300;
  seen.redrawQuiet = idle();
  overlay = true;
  seen.overlay = idle();
  console.log(JSON.stringify(seen));
})();
"""


@pytest.fixture
def hook(client, monkeypatch, tmp_path):
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    page = client.get('/').get_data(as_text=True)
    scripts = [s for s in re.findall(r"<script>(.*?)</script>", page, re.S) if "function pageIdle()" in s]
    assert len(scripts) == 1, "the page has one idle hook"
    path = tmp_path / "hook.js"
    path.write_text(scripts[0], encoding="utf-8")
    return path


@pytest.mark.skipif(not NODE, reason="needs Node (the browser harnesses' runtime)")
def test_page_idle_counts_start_up_fetches_redraws_and_the_overlay(hook, tmp_path):
    run = tmp_path / "run.js"
    run.write_text(RUN, encoding="utf-8")
    out = subprocess.run([NODE, str(run), str(hook)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert seen == {
        "starting": False,          # the start-up is work until the page has opened
        "opened": True,
        "fetchOut": False,          # a request still out
        "justAnswered": False,      # its answer read, the next step not yet scheduled
        "quietAfter": True,
        "redrawOwed": False,        # a debounced redraw not yet run
        "redrawCancelled": True,
        "redrawRan": False,
        "redrawQuiet": True,
        "overlay": False,           # "Drawing the pulley…" up
    }


def test_page_settles_its_start_up(client, monkeypatch):
    """window.onload's last step ends the start-up's work."""
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    page = client.get('/').get_data(as_text=True)
    assert "historyReady = true; refreshUndoUI(); pageBusy--;" in page
