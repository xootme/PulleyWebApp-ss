"""Efficient test runs for the CCT apps (cct_common 0.25.0): one record of
what each test runs, and the tools that read it.

- record   pytest plugin (`-p cct_common.testing.record`, CCT_TEST_MAP set):
           which repo functions each test runs, and how long it takes.
- affected the test files and harnesses a change reaches since each last
           passed (an app's `--affected` mode); the record's I/O.
- controls negative controls scoped by test: break one thing, run only the
           tests that ran it, cheapest first, stop at the first that fails.
- select   pytest plugin (`-p cct_common.testing.select`, CCT_SELECT set):
           only the listed tests, in the list's order.

The record lives outside the repo (CCT_TEST_RECORD_DIR, else
%LOCALAPPDATA%/cct/test-records/<app>) and is keyed by file hashes with LF
line endings, so the main folder, a clean nightly worktree and another host
share it. Workers and lanes come from CCT_TEST_CPUS (the test turn's), else
os.cpu_count() // 2. From Bevel Gear Designer's pilot (its ADR-031,
2026-10-21); CCT_Controller/docs/faster-tests-notes.md has the measurements."""
