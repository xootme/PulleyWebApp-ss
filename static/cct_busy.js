/* cct_busy.js — the "Drawing the parts…" overlay every CCT app shows while its
 * preview is redrawn, and Auto-fix's Apply (cct_common 0.25.7; the owner,
 * 2026-10-22: after Auto-fix "the auto-fix dialog does not disappear (it
 * should)", and the "Drawing the …" pill "is not that visible, make the area
 * larger, use higher contrast colors. This should be common code for all the
 * apps").
 *
 * The overlay covers the preview box: a dimmed scrim and, centred on it, a
 * large card, bold white on near-black (17:1, both page themes alike), with a
 * spinner and the app's own words. It fades in after a moment, so a quick
 * redraw never flickers it; it never takes the mouse, so the view still turns.
 * Its look is cct_theme.css's (.cct-busy, .cct-busy-card, …).
 *
 * Copy into the app's static/ (as cct_theme.css) and load before the page's
 * script. The host is the preview box (position: relative, as every app's is):
 *
 *   CCTBusy.show(box, "Drawing the pair…");   // the redraw starts
 *   CCTBusy.hide(box);                        // it ends: drawn, refused or failed
 *   CCTBusy.shown(box)                        // for the browser harnesses
 *
 *   // Auto-fix's Apply: the Invalid panel closes at once and the overlay
 *   // shows until the redraw that follows calls hide().
 *   CCTBusy.applyFix(panel, box, () => applyFix(currentFix), "Drawing the pair…");
 *
 * The overlay keeps the class .cct-busy (one per host) for the harnesses.
 *
 * While someone types, a value is often invalid on its way to a valid one
 * ("1" on the way to "12"): the owner, the same day, "there should be a 2
 * second delay to let the user complete their entry before the dialog pops
 * up". The app routes its Invalid panel through one gate:
 *
 *   const gate = CCTBusy.refusalGate();      // watches the page's typing
 *   gate.show(() => showInvalidPanel(data)); // a refusal: now, or 2 s after the last keystroke
 *   gate.cancel();                           // a valid answer: the waiting refusal is dropped
 *
 * A keystroke in a text or number field restarts the wait; Enter, Tab,
 * leaving the field, or a choice from a list, a tick or a spinner's arrows
 * (a "change") is a finished entry and shows a waiting refusal at once.
 * TYPING_DELAY_MS is the one delay every app uses.
 */
(function (root) {
  "use strict";

  const DEFAULT_TEXT = "Drawing the parts…";

  function overlay(host) {
    for (const c of host.children) if (c.classList.contains("cct-busy")) return c;
    const doc = host.ownerDocument;
    const o = doc.createElement("div");
    o.className = "cct-busy";
    o.hidden = true;
    o.setAttribute("role", "status");
    o.setAttribute("aria-live", "polite");
    const card = doc.createElement("div");
    card.className = "cct-busy-card";
    const spin = doc.createElement("span");
    spin.className = "cct-busy-spinner";
    spin.setAttribute("aria-hidden", "true");
    const text = doc.createElement("span");
    text.className = "cct-busy-text";
    card.append(spin, text);
    o.append(card);
    host.append(o);
    return o;
  }

  function show(host, text) {
    const o = overlay(host);
    o.querySelector(".cct-busy-text").textContent = text || DEFAULT_TEXT;
    o.hidden = false;
    host.setAttribute("aria-busy", "true");
  }

  function hide(host) {
    for (const c of host.children) if (c.classList.contains("cct-busy")) c.hidden = true;
    host.removeAttribute("aria-busy");
  }

  const shown = (host) => [...host.children].some((c) => c.classList.contains("cct-busy") && !c.hidden);

  // Apply an Auto-fix: close the panel that offered it now, not when the
  // server next answers, and show the overlay until the redraw ends. If the
  // fix itself throws, nothing redraws: take the overlay down and the panel
  // back up.
  function applyFix(panel, host, apply, text) {
    if (panel) panel.classList.add("hidden");
    show(host, text);
    const undo = () => { hide(host); if (panel) panel.classList.remove("hidden"); };
    let out;
    try { out = apply(); } catch (e) { undo(); throw e; }
    if (out && typeof out.then === "function") out.catch(undo);
    return out;
  }

  const TYPING_DELAY_MS = 2000;
  const TYPED = new Set(["text", "number", "search", "tel", "url", "email", ""]);
  const typed = (t) => t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && TYPED.has(t.type || "")));

  // The page's Invalid panel, held back while an entry is part-way typed.
  // opts (for tests): root (document), delay, now, setTimeout, clearTimeout.
  function refusalGate(opts = {}) {
    const doc = opts.root || root.document;
    const delay = opts.delay ?? TYPING_DELAY_MS;
    const now = opts.now || (() => Date.now());
    const later = opts.setTimeout || ((f, ms) => root.setTimeout(f, ms));
    const stop = opts.clearTimeout || ((id) => root.clearTimeout(id));
    let lastKey = -Infinity, pending = null, timer = null;

    const fire = () => {
      if (timer !== null) { stop(timer); timer = null; }
      const f = pending;
      pending = null;
      if (f) f();
    };
    const arm = () => {
      if (timer !== null) stop(timer);
      timer = later(fire, Math.max(0, lastKey + delay - now()));
    };
    const finished = () => { lastKey = -Infinity; if (pending) fire(); };

    doc.addEventListener("input", (e) => {
      if (!typed(e.target)) return;               // a list, a tick, a slider: complete at once (their change)
      lastKey = now();
      if (pending) arm();                         // every keystroke restarts the wait
    }, true);
    doc.addEventListener("change", finished, true);
    doc.addEventListener("focusout", finished, true);
    doc.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === "Tab") finished(); }, true);

    return {
      show(f) {
        pending = f;
        if (now() - lastKey >= delay) fire();
        else arm();
      },
      cancel() {
        pending = null;
        if (timer !== null) { stop(timer); timer = null; }
      },
      get waiting() { return pending !== null; },
    };
  }

  const api = { show, hide, shown, applyFix, refusalGate, DEFAULT_TEXT, TYPING_DELAY_MS };
  root.CCTBusy = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
