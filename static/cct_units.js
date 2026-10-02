/* cct_units.js — the mm / inch switch every CCT app shares (cct_common).
 *
 * The model is always millimetres; inch is display and entry only (E-Box
 * Designer's rule, now every app's). Copy this file into the app's static/
 * (as cct_theme.css is copied) and load it before the page's own script:
 *
 *     <script src="/static/cct_units.js"></script>
 *     CCTUnits.init({ select: document.getElementById("units") });
 *
 * A length field is an <input class="len"> and its label carries
 * <span class="unit-label">mm</span>, which the switch rewrites. Two ways to
 * read and write one, sharing one conversion:
 *
 *   Explicit (Sprocket and Gear Designer): the script asks lenMm(el) for the
 *   field in mm and writes it with setLen(el, mm); convert() redraws every
 *   field when the switch changes. A field converted by the switch keeps its
 *   exact mm (data-mm) until the user edits it, so mm -> inch -> mm doesn't
 *   drift.
 *
 *   Bound (Timing Pulley Generator, whose script reads .value directly in
 *   hundreds of places): bind(el) makes the field's .value, .min, .max and
 *   .step mean mm to every script, while the person sees and types the chosen
 *   units. In mm the field behaves exactly as an unbound one.
 *
 * Outputs (readouts, dimensions) use fmt(mm).
 */
(function (root) {
  "use strict";

  const INCH = 25.4;
  const IN_DIGITS = 4;            // inch fields and readouts: 0.0001 in

  let select = null;              // the page's mm / inch <select>
  let shown = "mm";               // the units the length fields show right now
  let doc = root.document || null;
  const bound = new Set();

  // The switch's setting. Read live from the <select>, as the apps always
  // have; `shown` is what the fields still display until convert() runs.
  const units = () => (select ? select.value : shown);
  const inch = () => units() === "in";
  const toMm = (v) => (inch() ? v * INCH : v);
  const fromMm = (mm) => (inch() ? mm / INCH : mm);
  const tidy = (mm, digits = 4) => String(Number(Number(mm).toFixed(digits)));
  const fmt = (mm, mmDigits = 2) =>
    (inch() ? `${(mm / INCH).toFixed(IN_DIGITS)} in` : `${Number(mm).toFixed(mmDigits)} mm`);

  function init(opts = {}) {
    if (opts.select !== undefined) select = opts.select;
    if (opts.document !== undefined) doc = opts.document;
    shown = opts.shown || (select ? select.value : "mm");
    relabel();
  }

  function relabel() {
    if (!doc) return;
    for (const el of doc.querySelectorAll(".unit-label")) el.textContent = shown;
  }

  // The fields already show `u` (a restored page, an imported file): say so
  // without converting them.
  function assume(u) {
    shown = u;
    relabel();
  }

  // ── Explicit fields ───────────────────────────────────────────────────────
  function lenMm(el) {
    const raw = el.value.trim();
    if (raw === "") return null;
    if (el.dataset.mm !== undefined && el.dataset.shown === el.value) {
      return Number(el.dataset.mm).toFixed(4);
    }
    const v = parseFloat(raw);
    return Number.isFinite(v) ? toMm(v).toFixed(4) : raw;   // let the server reject junk
  }

  function setLen(el, mm, digits) {
    el.value = inch() ? (mm / INCH).toFixed(IN_DIGITS) : tidy(mm, digits ?? 4);
    el.dataset.mm = String(mm);
    el.dataset.shown = el.value;
  }

  // Forget a field's exact mm (its value was just set some other way).
  function forget(el) {
    delete el.dataset.mm;
    delete el.dataset.shown;
  }

  // ── Bound fields ──────────────────────────────────────────────────────────
  function native(el, name) {
    for (let o = Object.getPrototypeOf(el); o; o = Object.getPrototypeOf(o)) {
      const d = Object.getOwnPropertyDescriptor(o, name);
      if (d && d.get) return d;
    }
    return null;
  }

  function bind(el) {
    if (!el || el._cctUnits) return el;
    const nv = native(el, "value");
    const st = { mm: null, text: null, attrs: {} };
    el._cctUnits = st;
    // The page is drawn in mm before the switch is touched: what's there is mm.
    Object.defineProperty(el, "value", {
      configurable: true,
      get() {
        const t = nv.get.call(el);
        if (shown !== "in" || t.trim() === "") return t;
        if (st.mm !== null && t === st.text) return st.mm;
        const v = parseFloat(t);
        return Number.isFinite(v) ? tidy(v * INCH) : t;
      },
      set(x) {
        const s = x === null || x === undefined ? "" : String(x);
        const v = parseFloat(s);
        if (shown !== "in" || s.trim() === "" || !Number.isFinite(v)) {
          st.mm = null;
          nv.set.call(el, s);
          return;
        }
        st.mm = s;
        st.text = (v / INCH).toFixed(IN_DIGITS);
        nv.set.call(el, st.text);
      },
    });
    for (const name of ["min", "max", "step"]) {
      const na = native(el, name);
      if (!na) continue;
      st.attrs[name] = { na, mm: na.get.call(el) };
      Object.defineProperty(el, name, {
        configurable: true,
        get() { return st.attrs[name].mm; },
        set(x) {
          st.attrs[name].mm = x === null || x === undefined ? "" : String(x);
          showAttr(el, name);
        },
      });
    }
    el.classList && el.classList.add("len");
    bound.add(el);
    return el;
  }

  function showAttr(el, name) {
    const a = el._cctUnits.attrs[name];
    const v = parseFloat(a.mm);
    let out = a.mm;
    if (shown === "in" && a.mm !== "" && a.mm !== "any" && Number.isFinite(v)) {
      out = name === "step" ? "any" : (v / INCH).toFixed(IN_DIGITS);
    }
    a.na.set.call(el, out);
  }

  // Every length a label marks "(mm)" in `scope` becomes a bound field, and its
  // "mm" a unit label: for a page whose markup predates the switch.
  function bindMmLabels(scope) {
    const out = [];
    for (const label of scope.querySelectorAll("label")) {
      const id = label.htmlFor;
      const field = id ? scope.ownerDocument
        ? scope.ownerDocument.getElementById(id) : scope.getElementById(id)
        : label.querySelector("input");
      if (!field || field.type !== "number") continue;
      if (!markLabel(label)) continue;
      out.push(bind(field));
    }
    return out;
  }

  // "(mm)" in a label's own text → "(<span class=unit-label>mm</span>)".
  function markLabel(label) {
    if (label.querySelector(".unit-label")) return true;
    const walker = label.ownerDocument.createTreeWalker(label, 4 /* NodeFilter.SHOW_TEXT */);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const i = n.data.indexOf("(mm");
      if (i < 0) continue;
      const after = n.splitText(i + 1);          // "(" | "mm…"
      const rest = after.splitText(2);           // "mm" | "…"
      const span = label.ownerDocument.createElement("span");
      span.className = "unit-label";
      span.textContent = shown;
      after.replaceWith(span);
      void rest;
      return true;
    }
    return false;
  }

  // ── The switch ────────────────────────────────────────────────────────────
  // Redraw every length field (and unit label) in the switch's units.
  function convert(to) {
    const target = to || units();
    if (target === shown) return false;
    const from = shown;
    // Bound fields: their mm, read in the units they show.
    const keep = [...bound].map((el) => [el, el.value]);
    // Explicit fields: their exact mm when untouched since the last switch.
    const fields = doc ? [...doc.querySelectorAll("input.len")].filter((el) => !el._cctUnits) : [];
    shown = target;
    for (const [el, mm] of keep) {
      el.value = (target === "mm" && mm !== "" && Number.isFinite(parseFloat(mm))) ? tidy(mm) : mm;
      for (const name of Object.keys(el._cctUnits.attrs)) showAttr(el, name);
    }
    for (const el of fields) {
      const v = parseFloat(el.value);
      if (!Number.isFinite(v)) continue;
      const mm = (el.dataset.mm !== undefined && el.dataset.shown === el.value)
        ? Number(el.dataset.mm)
        : (from === "in" ? v * INCH : v);
      el.value = target === "in" ? (mm / INCH).toFixed(IN_DIGITS) : tidy(mm);
      el.dataset.mm = String(mm);
      el.dataset.shown = el.value;
    }
    relabel();
    return true;
  }

  const api = {
    INCH, IN_DIGITS, init, units, inch, toMm, fromMm, fmt, tidy, relabel, assume,
    lenMm, setLen, forget, bind, bindMmLabels, convert,
    get shown() { return shown; },
  };
  root.CCTUnits = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
