/* cct_autofix.js — the Auto-fix buttons every CCT app shows beside a warning
 * (cct_common 0.58.0; the owner, 2026-11-05: "when possible provide up to
 * three options to auto fix a problem (it gives options without
 * overwhelming)", and CCTCommon's proposal, every choice as recommended).
 *
 * Up to three options, each the shape of /api/v1/check's `fixes`
 * (cct_common.autofix): {set, changes, ...}. None: the box is hidden. One:
 * "Auto-fix". Two or three: "Auto-fix (a)", "(b)", "(c)", in the order
 * given (the app's recommended first). Each option's changes are shown as
 * text beside its button, not only on hover (phones have none), and in its
 * title. More than three are never shown (MAX).
 *
 * Load from static/cct/ after cct_busy.js. The box is the app's own element
 * where its Auto-fix button was:
 *
 *   cctAutofix.show(box, data.fixes, (fix, i) => applySet(fix.set), {
 *     panel: invalidPanel,          // closes at once on Apply (CCTBusy.applyFix)
 *     host: previewBox,             // the overlay shows there until the redraw ends
 *     busyText: "Drawing the pair…",
 *     buttonClass: "spoke-fit-btn", // the app's own button look
 *   });
 *   cctAutofix.show(box, []);       // nothing to fix: hidden
 *
 * Without host (or without CCTBusy) Apply calls apply(fix, i) directly.
 * Its look is cct_theme.css's (.cct-autofix, .cct-autofix-option,
 * .cct-autofix-changes); show returns the buttons, for the harnesses.
 */
(function (root) {
  "use strict";

  const MAX = 3;
  const LETTERS = ["a", "b", "c"];

  // The buttons' words for n options.
  function labels(n) {
    n = Math.min(Math.max(n | 0, 0), MAX);
    if (n === 1) return ["Auto-fix"];
    return LETTERS.slice(0, n).map((l) => `Auto-fix (${l})`);
  }

  function changesText(fix) {
    const c = fix && fix.changes;
    return Array.isArray(c) ? c.join("; ") : (c ? String(c) : "");
  }

  function show(box, fixes, apply, opts) {
    opts = opts || {};
    const doc = box.ownerDocument || root.document;
    const list = (Array.isArray(fixes) ? fixes : [])
      .filter((f) => f && f.set && Object.keys(f.set).length).slice(0, MAX);
    const words = labels(list.length);
    const buttons = [];
    const rows = list.map((fix, i) => {
      const row = doc.createElement("div");
      row.className = "cct-autofix-option";
      const b = doc.createElement("button");
      b.type = "button";
      b.className = ["cct-autofix-btn", opts.buttonClass || ""].join(" ").trim();
      b.textContent = words[i];
      const text = changesText(fix);
      if (text) b.title = "Changes: " + text;
      b.addEventListener("click", () => {
        const run = () => apply(fix, i);
        const busy = root.CCTBusy;
        if (opts.host && busy) return busy.applyFix(opts.panel || null, opts.host, run, opts.busyText);
        if (opts.panel) opts.panel.classList.add("hidden");
        return run();
      });
      const said = doc.createElement("span");
      said.className = "cct-autofix-changes";
      said.textContent = text;
      row.append(b, said);
      buttons.push(b);
      return row;
    });
    box.replaceChildren(...rows);
    box.classList.add("cct-autofix");
    box.classList.toggle("hidden", !rows.length);
    box.hidden = !rows.length;
    return buttons;
  }

  const api = { show, labels, MAX };
  root.cctAutofix = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
