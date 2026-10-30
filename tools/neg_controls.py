"""Negative controls: break one formula or check at a time; the tests meant to
catch it must fail; then restore. From the repo root, with the test interpreter
(the controls run `python -m pytest` with it, and the STEP tests need OCP):

  C:/Users/cmyer/Documents/PulleyWebApp/.venv312/Scripts/python.exe tools/neg_controls.py
      [name filter ...] [--all] [--whole-suite]

The controls are this file's BREAKS: name -> (file, the text, its broken form).
The runner is cct_common.testing.controls (0.25.2, from Bevel Gear Designer's
pilot; in a big function, only the tests that reached the broken line): by default only the controls whose check changed since each was last
caught; each on the *tests* that ran what it breaks (tests/suite.py records
them), cheapest first, with -x, the first that fails ending it, the whole suite
as the fallback (SCOPE or MISSED); CCT_TEST_CPUS lanes, each breaking its own
copy of the tree. A filter runs every control whose name holds it; --all runs
them all (tools/nightly_test.py does, every night).

A starter set (2026-10-21): the breaks that matter most, not yet every check."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import affected  # noqa: E402,F401  (configures cct_common.testing.affected with the pulley's rules)
import nightly_test  # noqa: E402
from cct_common.testing import controls  # noqa: E402

PG, SE, SF, SS = ("geometry/pulley_geometry.py", "exporters/step_exporter.py", "geometry/spoke_fit.py",
                  "geometry/set_screw.py")
AP, EP = "app.py", "edge_proxy.py"
BREAKS = {  # name: (file, old, new); old must occur exactly once
    # the pulley itself
    "pitch diameter": (PG, "return (numTeeth * pitch_mm) / math.pi", "return (numTeeth * pitch_mm) / (2 * math.pi)"),
    "outside diameter: the pitch line offset": (PG, "return getPitchDiameter(numTeeth, pitch_mm) - 2.0 * offset_mm",
                                                "return getPitchDiameter(numTeeth, pitch_mm) - offset_mm"),
    "teeth from the outside diameter": (PG, "return round((outerDia_mm + 2.0 * offset_mm) * math.pi / pitch_mm)",
                                        "return round(outerDia_mm * math.pi / pitch_mm)"),
    "RPP pitch diameter": (PG, "    PD    = number_of_teeth * pitch / math.pi", "    PD    = number_of_teeth * pitch / 3.0"),
    # hub screws (ADR-013, ADR-018; the hex hole corner-up, owner 2026-10-18)
    "hex screw hole corner-up": (SE, "[(-along, across) for across, along in", "[(across, along) for across, along in"),
    "screw angles on a hex bar": (SE, "hex_bar=sp is not None and sp.kind == 'hex')", "hex_bar=False)"),
    "set screw: a captured nut's clearance hole": (SS, '"diameter": screws.clearance_diameter(size)}, screws.nut(size)',
                                                   '"diameter": screws.get(size).major_diameter}, screws.nut(size)'),
    "set screw: the old captured-nut box": (SS, '        "nut" if args.get(f"{prefix}hub_captured_nut") == "1" else "thread")',
                                            '        "thread")'),
    # spokes (ADR-010)
    "spokes: hub wall round the bore": (SF, "    if s['hub_od'] < bore + 2 * WALL - 1e-9:", "    if False:"),
    # (not "the web between hub and rim": _valid's MIN_WEB test is an early exit, as the void
    # builder refuses a web under 0.5 mm itself; with it gone nothing changes, so no test can fail)
    # the site: charging, the edge, bug reports
    "the STL is charged": (AP, "@app.route('/download/stl')\n@charges.charged('stl')\n", "@app.route('/download/stl')\n"),
    "edge: the secret must match": (EP, "return bool(self.secret) and hmac.compare_digest(got.encode(), self.secret.encode())",
                                    "return bool(got)"),
    "bug reports go to support@": (AP, "_smtp_send('support@cheapcadtools.com'", "_smtp_send('info@cheapcadtools.com'"),
    "bug reports: Reply-To only a single address": (AP, "    if reply_to and _ONE_ADDRESS_RE.match(reply_to):",
                                                    "    if reply_to:"),
    # refused settings: labelled, on the page's field, with a value (geometry/labels.py, 2026-10-21)
    "labels: the fix is a value, not the range's text": ("geometry/labels.py", '    return f"{v:g}"',
                                                         '    return f"{lo:g} to {hi:g}"'),
    "labels: the refusal names the page's field": ("geometry/labels.py", "    return SETTINGS[base][1].format(n=n)",
                                                   "    return key"),
    "labels: pulley 2's settings only in a drive": ("geometry/labels.py",
                                                    '    if prefix and not _on(args.get("dual")):\n        return False',
                                                    "    if False:\n        return False"),
    "labels: the agent API names the label": ("geometry/labels.py",
                                              'return f"{label(m.group(1))} ({m.group(1)}) " if base in SETTINGS else m.group(0)',
                                              "return m.group(0)"),
    "labels: downloads are checked": (AP, "(path.startswith('/download/') and not path.startswith('/download/result/'))",
                                      "False"),
    "labels: the page keeps the refusal's fix": ("templates/index.html", "fix: data.field && data.fix != null",
                                                 "fix: false && data.fix != null"),
    # belt heights: ISO 5296-1 Table 4a (2026-10-21)
    "belt height: MXL is ISO 5296's 1.14": (PG, '"aa": 0.254, "hs":  1.14,', '"aa": 0.254, "hs":  1.65,'),
    # the agent API's belt_teeth (for the Drive Train Designer, 2026-10-21)
    "belt_teeth sets the centre": ("agent.py", "    return dict(query, center_distance=_wire_number(centre))",
                                   "    return query"),
    "belt_teeth: a centre past the limit is refused": ("agent.py", "    if centre is None or centre > c_max:",
                                                       "    if centre is None:"),
    # cct_common 0.25.7's busy overlay (the owner, 2026-10-22; tests/browser/busy_ui.js drives it)
    "busy: Auto-fix closes the warnings at once": ("templates/index.html",
        "  CCTBusy.applyFix(document.getElementById('dims-warn-row'), previewBox(), _applyDimsFix, busyText());",
        "  _applyDimsFix();"),
    "busy: the warnings wait while a value is typed": ("templates/index.html",
        "  if (lines.length) refusals.show(showWarnings, ticket);", "  if (lines.length) showWarnings();"),
    "busy: a refusal of a value changed since never shows": ("templates/index.html",
        "  if (lines.length) refusals.show(showWarnings, ticket);", "  if (lines.length) refusals.show(showWarnings);"),
    "busy: the spoke panel of a value changed since never shows": ("templates/index.html",
        "  _spokeFitGate[n].show(() => _showSpokeFit(n, warn, fit), ticket);",
        "  _spokeFitGate[n].show(() => _showSpokeFit(n, warn, fit));"),
    # the test tooling (cct_common.testing)
    "tests: every suite run can record": ("pytest.ini", " -p cct_common.testing.record", ""),
    "tests: the caches cleared exist": ("pytest.ini", "exporters.step_worker_ss:_versions", "exporters.step_worker_ss:_nothing"),
    "tests: the page's template reaches its tests": ("tools/affected.py", 'TEST_RULES = [("templates/*",',
                                                     'TEST_RULES = [("nothing/*",'),
    "night: every browser harness runs": ("tools/nightly_test.py", "str(ROOT / 'tools' / 'harnesses.py')",
                                          "str(ROOT / 'tools' / 'nothing.py')"),
    "step: a hubless bore's key or flat reaches small_step": ("exporters/step_worker_ss.py",
        "    else:\n        # No hub: the bore's flat or key", "    elif False:\n        # No hub: the bore's flat or key"),
    "stl: one screw on a keyed hub, before its lobes": ("exporters/step_exporter.py",
        "return screws.set_screw_angles(1 if keyed else min(screw_count, 2)",
        "return screws.set_screw_angles(min(screw_count, 2)"),
    "idle: the start-up is work until the page has opened": ("templates/index.html", "let pageBusy = 1;",
                                                              "let pageBusy = 0;"),
    "idle: a fetch out keeps the page busy": ("templates/index.html",
        "    pageBusy++;\n    return _fetch(...a).finally(() => { pageBusy--; _lastWork",
        "    return _fetch(...a).finally(() => { _lastWork"),
    "a blank belt height is its default": (AP, "belt_height   = max(1.0, _safe_float(args.get('belt_height'), 10.0))",
                                           "belt_height   = max(1.0, float(args.get('belt_height', 10.0)))"),
    "a blank number downloads as its default": (AP, "                                           if not _labels.blank_number(k, v)])",
                                                "                                           ])"),
    "step: a metal flange's groove radius": ("exporters/step_worker_ss.py",
        "    _R_tr = R_OD - tooth_ht        # both kinds (the metal one's was unset: every metal flange STEP failed)\n\n    if flange_3dprint:\n",
        "\n    if flange_3dprint:\n        _R_tr = R_OD - tooth_ht\n"),
    # cct_common 0.26.0: the copy is a release, /api/health says which, the admin opens designs in the app
    "health: names its cct_common": (AP, "    return jsonify(status='ok', version=APP_VERSION, **health_fields())",
                                      "    return jsonify(status='ok', version=APP_VERSION)"),
    "admin: a design opens in the app": (AP, "download_failures=_failure_store, app_url='https://cheapcadtools.com/tools/pulleys')",
                                         "download_failures=_failure_store)"),
    "vendored: a copy edited in place fails": ("cct_common/forksafe.py", '    if hasattr(os, "register_at_fork"):',
                                               '    if hasattr(os, "register_at_fork") and True:'),
    "backup: its run is counted": ("db_offsite_backup.py",
                                   '        with job_use(_env("DATABASE_URL"), "cct-db-backup", "backup"):',
                                   '        with __import__("contextlib").nullcontext():'),
    # the business settings win (cct_common 0.29.0; campaigns/2026-10-27-signup-grant-10.md, phase 3)
    "settings: the signup grant is the setting's": ("accounts_setup.py", "    accounts = AccountStore(tokens)\n",
                                                    "    accounts = AccountStore(tokens, signup_grant=20)\n"),
    "settings: the buy page is the setting's": (AP, "charges.attach(app, _accounts_state,  # per-export token charging",
                                                "charges.attach(app, _accounts_state, buy_url='/account/buy',  # per-export token charging"),
    "settings: the packs are the setting's": (AP, "        'paypal': PayPalConfig(env('PAYPAL_CLIENT_ID')",
        "        'packs': __import__('cct_common.payments').payments.DEFAULT_PACKS,\n        'paypal': PayPalConfig(env('PAYPAL_CLIENT_ID')"),
    "page: the Download window's prices are the offer's": ("templates/index.html",
        "tokens: tokensOn ? cctTokens.price(t.fmts[0][0]) : null", "tokens: tokensOn ? 2 : null"),
    "page: tokens.js asks at the site's root": ("templates/index.html", '<script src="/static/cct/tokens.js" data-api-base="/">',
                                                '<script src="/static/cct/tokens.js">'),
    "page: the shared layout loads": ("templates/index.html", '  <link rel="stylesheet" href="/static/cct/cct_layout.css">\n', ""),
    # the 3D view on createViewer, one failure one row (cct_common 0.31.1), the screw and nub checks
    "3D: dragging orbits the part's z": ("templates/index.html",
        "    THREE, OrbitControls, canvas, maxPixelRatio: 2,",
        "    THREE, OrbitControls: class extends OrbitControls { constructor(c, d) { const u = c.up.clone();"
        " c.up.set(0, 1, 0); super(c, d); c.up.copy(u); } }, canvas, maxPixelRatio: 2,"),
    "3D: Top keeps +y up": ("templates/index.html", "    snaps: { top: [[0, 0, 1], [0, 1, 0]],", "    snaps: { top: [0, 0, 1],"),
    "downloads: a failed job is reported with its id": ("templates/index.html",   # 0.31.1's order, undone
        "        _reportDlFailure({ status: job._lost ? 404 : null, error: (job._lost ? 'poll: ' : '') + (job.error || '') });\n"
        "        hideProgressModal();",
        "        hideProgressModal();\n"
        "        _reportDlFailure({ status: job._lost ? 404 : null, error: (job._lost ? 'poll: ' : '') + (job.error || '') });"),
    "downloads: a start the server kept isn't reported again": ("templates/index.html",
        "  e.kept = r.headers.get('X-CCT-Download-Failure') === 'kept';", "  e.kept = false;"),
    "screws: a new size starts the insert hole over": ("templates/index.html",
        "  document.getElementById(`hub${n}_screw_hole_dia`).value = '';\n  onHubChange(n);", "  onHubChange(n);"),
    "nubs: in the top flange's hole, the panel warns": (AP, "    if r_nubs > r_hole + 1e-4:", "    if True:"),
    "night: a skip fails the night":("tests/conftest.py", "    if reason not in NIGHT_SKIPS:", "    if False:"),
    "release: +0.0.1": ("tools/release.py", "    return f'{a}.{b}.{c + 1}'", "    return f'{a}.{b + 1}.{c}'"),
}


if __name__ == "__main__":
    os.environ.setdefault("QUEUE_DISABLED", "1")                  # what the day runs and the nightly set
    os.environ.setdefault("SMALL_STEP_BIN", nightly_test.SMALL_STEP)
    sys.exit(controls.main(BREAKS, sys.argv[1:]))
