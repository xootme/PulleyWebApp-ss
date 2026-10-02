/* cct_download.js — the one Download window every CCT app shows (cct_common;
 * CCT_App_Baseline §4, the owner 2026-09-30: "one standard, token-charged
 * Download window, the same in every app"). Timing Pulley Generator's window
 * (its ADR-008), moved here so an app registers it rather than copying it.
 *
 * Every part ticked × every format ticked goes into one zip. With charging on,
 * the zip is one purchase of the whole design at the highest tier ticked, and
 * the button shows what the ticks cost as they change; signed-out and
 * short-of-tokens are handled here the same way for every app.
 *
 * Copy into the app's static/ (as cct_theme.css) and load before the page's
 * script. The app supplies only what is its own:
 *
 *   CCTDownload.open({
 *     note:     "Every part ticked goes into one zip.",
 *     parts:    [{ id, label, note?, only? }],       // only: "stl" or ["svg", "dxf"]
 *     tiers:    [{ label, tokens?, fmts: [["svg", "SVG"], …] }],  // a third item, { off, note },
 *                                                    // starts that format unticked with a note
 *     rank:     { step: 3, stl: 2, svg: 1, dxf: 1 }, // the highest tier ticked is charged
 *     filesFor: (part, fmt) => [{ path: "/download/…", params: {…} }],
 *     zipName:  "gear-m2-20T",
 *     charging: null | {                             // the app's account / token client
 *       on, unavailable, signedIn, quote(top), ensureDesign(), signIn(), buyDialog(cost, balance, url) },
 *     submit:   async (body, ui) => …,               // default: POST /api/download/bundle, save the zip
 *   });
 *
 * The window's elements keep fixed ids for the apps' browser harnesses:
 * #dl-window (the overlay), #dl-go (the button), #cct-part-<id>, #cct-fmt-<fmt>.
 * Its look is cct_theme.css's (.cct-dialog, .cct-dl, .cct-tier, …).
 */
(function (root) {
  "use strict";

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const onlyList = (only) => (only ? (Array.isArray(only) ? only : [only]) : null);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // The default: build the zip in one request and save it (the apps without a
  // job queue). The response is the zip itself.
  async function submitSync(body, ui) {
    ui.status("Building your files…");
    const r = await fetch("/api/download/bundle", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.error || `Server returned ${r.status}`);
    }
    const blob = await r.blob();
    const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : `${body.name}.zip`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    const saved = { name: a.download, size: blob.size, files: body.files.length };
    root.cctLastZip = saved;                          // what the harnesses check
    ui.close();
    return saved;
  }

  function open(opts) {
    const { parts, tiers, filesFor } = opts;
    const rank = opts.rank || {};
    const charging = opts.charging && opts.charging.on ? opts.charging : null;
    const submit = opts.submit || submitSync;

    const box = (id, text, note, checked = true) => {
      const lab = el("label", "cct-check");
      const cb = el("input");
      cb.type = "checkbox";
      cb.checked = checked;
      cb.id = id;
      lab.append(cb, document.createTextNode(" " + text));
      if (note) {
        const n = el("span", "cct-dl-note", " " + note);
        n.dataset.note = note;
        lab.append(n);
      }
      return lab;
    };

    const overlay = el("div", "cct-dialog-overlay");
    overlay.id = "dl-window";
    const dlg = el("div", "cct-dialog cct-dl");
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    dlg.append(el("h3", "cct-dialog-title", "Download"), el("p", "cct-dialog-note", opts.note || ""));

    const partsBox = el("div", "cct-dl-parts");
    partsBox.append(el("p", "cct-dl-head", "Parts"));
    for (const pt of parts) {
      const only = onlyList(pt.only);
      const note = pt.note !== undefined ? pt.note : only ? `${only.map((f) => f.toUpperCase()).join(" / ")} only` : "";
      partsBox.append(box(`cct-part-${pt.id}`, pt.label, note));
    }
    dlg.append(partsBox);

    if (charging && tiers.length > 1) {
      dlg.append(el("p", "cct-dl-costnote", "Adding more complicated output files increases the token cost."));
    }
    for (const t of tiers) {
      const head = el("div", "cct-tier");
      head.append(el("span", null, charging && t.tokens ? `${plural(t.tokens, "token")} · ${t.label}` : t.label));
      const row = el("div", "cct-formats");
      for (const [fmt, label, o] of t.fmts) row.append(box(`cct-fmt-${fmt}`, label, o && o.note, !(o && o.off)));
      dlg.append(head, row);
    }

    const status = el("p", "cct-dl-status");
    const cancel = el("button", "cct-btn", "Cancel");
    cancel.type = "button";
    const go = el("button", "cct-btn cct-btn-primary", "Download zip");
    go.type = "button";
    go.id = "dl-go";
    const buttons = el("div", "cct-dialog-buttons");
    buttons.append(cancel, go);
    dlg.append(status, buttons);
    overlay.append(dlg);

    const close = () => { document.removeEventListener("keydown", onKey); overlay.remove(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
    cancel.addEventListener("click", close);
    document.body.append(overlay);

    const ticked = (id) => { const e = document.getElementById(id); return !!(e && e.checked); };
    const selection = () => {
      const fmts = tiers.flatMap((t) => t.fmts.map(([f]) => f)).filter((f) => ticked(`cct-fmt-${f}`));
      const files = [];
      for (const pt of parts) {
        if (!ticked(`cct-part-${pt.id}`)) continue;
        const only = onlyList(pt.only);
        for (const f of fmts) if (!only || only.includes(f)) files.push(...filesFor(pt, f));
      }
      const top = fmts.reduce((a, f) => (!a || (rank[f] || 0) > (rank[a] || 0) ? f : a), null);
      return { files, top };
    };

    // A part that comes in some formats only has nothing to add while they are
    // all unticked: grey it out and say what would bring it back.
    const syncParts = () => {
      for (const pt of parts) {
        const only = onlyList(pt.only);
        if (!only) continue;
        const cb = document.getElementById(`cct-part-${pt.id}`);
        const usable = only.some((f) => ticked(`cct-fmt-${f}`));
        cb.disabled = !usable;
        cb.closest("label").classList.toggle("cct-check-off", !usable);
        const note = cb.closest("label").querySelector(".cct-dl-note");
        if (note) note.textContent = " " + (usable ? note.dataset.note : `tick ${only.map((f) => f.toUpperCase()).join(" or ")} to include`);
      }
    };

    let seq = 0;
    let quote = null;
    const update = async () => {
      const mine = ++seq;
      syncParts();
      const { files, top } = selection();
      quote = null;
      if (!files.length) {
        go.disabled = true;
        go.textContent = "Nothing to download";
        status.textContent = "";
        return;
      }
      go.disabled = false;
      if (!charging) {
        go.textContent = `Download zip (${plural(files.length, "file")})`;
        return;
      }
      if (charging.unavailable) { go.textContent = "Download zip"; status.textContent = "Accounts are temporarily unavailable."; return; }
      if (!charging.signedIn) { go.textContent = "Sign in to download"; status.textContent = ""; return; }
      go.textContent = "Download zip …";
      const q = await charging.quote(top);
      if (mine !== seq) return;                       // a newer tick is already pricing
      quote = q;
      if (!q) { go.textContent = "Download zip"; status.textContent = ""; return; }
      if (q.cost === 0) {
        go.textContent = "Download zip (already paid)";
        status.textContent = "Already unlocked for this design — no tokens used.";
      } else {
        go.textContent = `Download zip (${plural(q.cost, "token")})`;
        status.textContent = q.cost > q.balance
          ? `You have ${plural(q.balance, "token")}.`
          : `Balance: ${q.balance} → ${q.balance - q.cost}.`;
      }
    };
    dlg.addEventListener("change", update);
    update();

    const ui = {
      close,
      status: (text) => { status.textContent = text; },
      enable: () => { go.disabled = false; },
    };
    go.addEventListener("click", async () => {
      const { files } = selection();
      if (!files.length) return;
      const body = { name: opts.zipName, files };
      if (charging) {
        if (charging.unavailable) return;
        if (!charging.signedIn) { close(); charging.signIn(); return; }
        await update();
        if (!quote) return;
        if (quote.cost > quote.balance) { close(); charging.buyDialog(quote.cost, quote.balance, quote.buy_url); return; }
        body.design_id = await charging.ensureDesign();
      }
      go.disabled = true;
      try {
        await submit(body, ui);
      } catch (e) {
        status.textContent = e.message || String(e);
        go.disabled = false;
      }
    });
    return { close, selection };
  }

  // Every file the window would fetch with every box ticked (the harnesses).
  function allFiles({ parts, tiers, filesFor }) {
    const fmts = tiers.flatMap((t) => t.fmts.map(([f]) => f));
    return parts.flatMap((pt) => {
      const only = onlyList(pt.only);
      return fmts.filter((f) => !only || only.includes(f)).flatMap((f) => filesFor(pt, f));
    });
  }

  root.CCTDownload = { open, allFiles, submitSync };
})(typeof window !== "undefined" ? window : globalThis);
