# Changelog

What changes for people using the Timing Pulley Generator, newest first. The
version is the one the nightly release gives it (tools/release.py); until then
an entry sits under "Unreleased".

## Unreleased

- **Imperial belts (MXL, XL, L, H, XH, XXH) have the right thickness.** Belt
  files (SVG, DXF, STEP, STL) and the drive animation drew these belts too
  thick: they now use ISO 5296-1's overall belt height (MXL 1.14, XL 2.3,
  L 3.6, H 4.3, XH 11.2, XXH 15.7 mm; they were 1.65, 3.84, 6.48, 8.64, 17.53,
  25.41). **A belt file downloaded again comes out thinner than before.**
  Pulleys, belt lengths, tooth counts and centre distances are unchanged.
- **Agents: `belt_teeth`.** The v1 API takes a two-pulley drive's belt tooth
  count and returns the centre distance it gives (check's `drive.centre`).
