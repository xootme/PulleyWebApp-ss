"""Cross-repo conformance checks: one rule, two implementations.

This subpackage is deliberately unlike the rest of cct_common. Everything
else here is platform code -- Flask helpers, metadata embedding, export
formats -- that knows nothing about any particular tool's domain. These
modules do know: `rect_snap` builds E-Box Designer `Hole` objects and
names functions inside PCB Importer's `importer.js`.

They live here anyway, because of where they can be RUN from. A check
that two repos agree needs to see both, and neither app can see the
other -- but both install cct_common editable, so a check placed here can
be invoked from either app's own test suite, and from this one.

That matters more than the layering does. The rule these check is
implemented twice on purpose, and the copy most often edited (E-Box
Designer's Python) sits in the repo that would otherwise never run the
comparison. A guard that only fires in the other app's suite is a guard
that fires long after the change that broke it.

The shape to follow: each module exposes a pure `check(...)` taking the
paths it needs and returning a report string, empty when the two agree.
No pytest, no repo location, no skipping -- the calling test does all of
that with `cct_common.parity`, so the same check can be invoked from
three suites without one of them dictating how the others report.
"""
