"""
test_dual_pulley_bore_spoke.py — regression tests for bugs fixed in 7f7ff33.

Two crashes that were live in production until 2026-05-22:

  1. svg_exporter: `ll = None` in spoke base-fillet arc path
     Trigger: T-pitch pulley (T10) at 32 teeth with spokes + base fillets.
     Error before fix: TypeError — cannot unpack non-iterable NoneType object.

  2. png_exporter: `BORE_SAMPLES` NameError when keyway bore + spokes active.
     Error before fix: NameError — name 'BORE_SAMPLES' is not defined.
     Root cause: BORE_SAMPLES was only assigned inside `if not bore_px:`, but
     keyway bores populate bore_px via a polygon path, so the assignment was
     skipped while the spoke loop below still referenced BORE_SAMPLES.
"""
import pytest

from exporters.svg_exporter import generate_svg
from exporters.png_exporter  import generate_png


# ── Shared spoke params ────────────────────────────────────────────────────────

_SPOKES = dict(
    spoke_count=4,
    spoke_width_mm=4.0,
    spoke_hub_od_mm=14.0,
    rim_depth_mm=2.0,
    fillet_tip_mm=1.5,
    fillet_base_mm=1.5,
)


# ── Bug 1: SVG — ll=None in T-pitch base-fillet arc ───────────────────────────

class TestSvgTpitchSpokeBaseFillet:
    """T-pitch pulleys at certain tooth counts produce ll=None in the spoke
    base-fillet geometry; the guard added in 7f7ff33 must prevent a crash."""

    def _svg(self, teeth, **kw):
        params = dict(family='T', pitch='T10', num_teeth=teeth, bore_mm=8.0)
        params.update(_SPOKES)
        params.update(kw)
        return generate_svg(**params)

    def test_t10_32t_spokes_base_fillet_does_not_crash(self):
        """Exact reproduction of the original crash report."""
        svg = self._svg(32)
        assert '<svg' in svg

    def test_t10_32t_returns_valid_xml(self):
        svg = self._svg(32)
        assert svg.strip().startswith('<?xml')
        assert '</svg>' in svg

    @pytest.mark.parametrize('teeth', [12, 16, 20, 24, 28, 32, 36, 40])
    def test_t10_range_of_tooth_counts(self, teeth):
        """Any T10 tooth count with spokes must not raise."""
        svg = self._svg(teeth)
        assert '<svg' in svg

    @pytest.mark.parametrize('pitch', ['T2.5', 'T5', 'T10', 'T20'])
    def test_all_t_pitches_with_spokes(self, pitch):
        svg = generate_svg(
            family='T', pitch=pitch, num_teeth=20, bore_mm=8.0, **_SPOKES
        )
        assert '<svg' in svg

    def test_large_base_fillet_does_not_crash(self):
        """Oversized fillet that forces the ll=None code path."""
        svg = self._svg(32, fillet_base_mm=8.0)
        assert '<svg' in svg

    def test_zero_base_fillet_is_unaffected(self):
        """Sanity: zero fillet has never crashed — must still work."""
        svg = self._svg(32, fillet_base_mm=0.0)
        assert '<svg' in svg


# ── Bug 2: PNG — BORE_SAMPLES NameError with keyway bore + spokes ─────────────

class TestPngKeywaySpokeBorerSamples:
    """Keyway bore sets bore_px via polygon path, skipping the block that
    previously defined BORE_SAMPLES. The spoke loop then raised NameError."""

    def _png(self, **kw):
        params = dict(
            family='HTD', pitch='5M', num_teeth=20,
            bore_mm=10.0, size_px=128,
        )
        params.update(_SPOKES)
        params.update(kw)
        return generate_png(**params)

    def test_keyway_bore_plus_spokes_does_not_crash(self):
        """Exact reproduction: keyway + spokes was the crash trigger."""
        png = self._png(keyway_w_mm=3.0, keyway_h_mm=1.5)
        assert png[:4] == b'\x89PNG'

    def test_dflat_bore_plus_spokes_does_not_crash(self):
        """D-flat bore also populates bore_px via polygon — same code path."""
        png = self._png(flat_depth_mm=1.0)
        assert png[:4] == b'\x89PNG'

    def test_plain_bore_plus_spokes_still_works(self):
        """Plain circular bore must still render correctly alongside spokes."""
        png = self._png()
        assert png[:4] == b'\x89PNG'

    @pytest.mark.parametrize('n_spokes', [2, 4, 6, 8])
    def test_keyway_bore_various_spoke_counts(self, n_spokes):
        png = self._png(keyway_w_mm=3.0, keyway_h_mm=1.5, spoke_count=n_spokes)
        assert png[:4] == b'\x89PNG'

    def test_keyway_bore_no_fillets_plus_spokes(self):
        png = self._png(keyway_w_mm=3.0, keyway_h_mm=1.5,
                        fillet_tip_mm=0.0, fillet_base_mm=0.0)
        assert png[:4] == b'\x89PNG'

    def test_dflat_bore_no_spokes_unaffected(self):
        """Ensure the BORE_SAMPLES move did not break the no-spokes path."""
        png = self._png(flat_depth_mm=1.0, spoke_count=0)
        assert png[:4] == b'\x89PNG'

    @pytest.mark.parametrize('family,pitch,teeth', [
        ('HTD', '3M', 20),
        ('HTD', '8M', 20),
        ('T',   'T5', 20),
        ('GT',  '3M', 20),
    ])
    def test_keyway_plus_spokes_across_families(self, family, pitch, teeth):
        png = generate_png(
            family=family, pitch=pitch, num_teeth=teeth,
            bore_mm=10.0, size_px=128,
            keyway_w_mm=3.0, keyway_h_mm=1.5,
            **_SPOKES,
        )
        assert png[:4] == b'\x89PNG', f'{family}-{pitch}: PNG header invalid'
