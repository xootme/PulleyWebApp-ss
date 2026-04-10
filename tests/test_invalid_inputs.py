"""
test_invalid_inputs.py — adversarial / boundary-value tests.

Simulates a user entering bad data through the web UI.  The contract:
  • JSON API endpoints  (/api/spec, /api/od, /api/belt)
      → 400 + JSON {'error': '...'} on bad input, never 500
  • Download endpoints  (/download/svg, /download/belt-svg)
      → 400 + plain text on bad input, never 500
  • Preview endpoints   (/api/preview, /api/belt-preview)
      → always 200 + valid PNG (error image on failure), never 500

Any 500 response means the app crashed on user input and is a bug.
"""
import pytest


# ---------------------------------------------------------------------------
# Convenience: baseline valid query-strings
# ---------------------------------------------------------------------------
_SVG_GOOD = (
    'family=HTD&pitch=5M&teeth=20&bore=8&print_extra=0'
    '&clearance_preset=STANDARD&backlash_preset=STANDARD'
)
_PREVIEW_GOOD = _SVG_GOOD
_DUAL_EXTRA = (
    '&dual=true&center_distance=100'
    '&p2_teeth=30&p2_bore=8&p2_print_extra=0'
    '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
)


def _is_png(data):
    return data[:4] == b'\x89PNG'


# ===========================================================================
# 1. Unknown / mismatched profile
# ===========================================================================
class TestUnknownProfile:
    """Bad family or pitch combinations → 400 with error key."""

    @pytest.mark.parametrize('family,pitch', [
        ('BOGUS',    '5M'),       # unknown family
        ('HTD',      '999M'),     # unknown pitch
        ('',         ''),         # empty strings
        ('HTD',      'T5'),       # T-series pitch on HTD family
        ('T',        '5M'),       # metric pitch on T family
        ('Imperial', '3M'),       # metric pitch on Imperial family
        ('<evil>',   '5M'),       # injection attempt
    ])
    def test_api_spec(self, client, family, pitch):
        r = client.get(f'/api/spec?family={family}&pitch={pitch}')
        assert r.status_code == 400
        assert 'error' in r.get_json()

    @pytest.mark.parametrize('family,pitch', [
        ('BOGUS', '5M'),
        ('HTD',   '999M'),
        ('',      '5M'),
    ])
    def test_api_od(self, client, family, pitch):
        r = client.get(f'/api/od?family={family}&pitch={pitch}&mode=teeth&value=20')
        assert r.status_code == 400
        assert 'error' in r.get_json()

    @pytest.mark.parametrize('family,pitch', [
        ('BOGUS', '5M'),
        ('HTD',   '999M'),
    ])
    def test_api_belt(self, client, family, pitch):
        r = client.get(
            f'/api/belt?family={family}&pitch={pitch}'
            '&mode=from_center&teeth1=20&teeth2=30&center_distance=100'
        )
        assert r.status_code == 400
        assert 'error' in r.get_json()

    @pytest.mark.parametrize('family,pitch', [
        ('BOGUS', '5M'),
        ('HTD',   '999M'),
        ('',      '5M'),
    ])
    def test_download_svg(self, client, family, pitch):
        r = client.get(
            f'/download/svg?family={family}&pitch={pitch}'
            '&teeth=20&bore=8&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 400

    @pytest.mark.parametrize('family,pitch', [
        ('BOGUS', '5M'),
        ('HTD',   '999M'),
    ])
    def test_download_belt_svg_single(self, client, family, pitch):
        r = client.get(f'/download/belt-svg?family={family}&pitch={pitch}')
        assert r.status_code == 400

    @pytest.mark.parametrize('family,pitch', [
        ('BOGUS', '5M'),
        ('HTD',   '999M'),
    ])
    def test_download_belt_svg_dual(self, client, family, pitch):
        r = client.get(
            f'/download/belt-svg?family={family}&pitch={pitch}&dual=true'
            '&teeth=20&p2_teeth=30&bore=8&p2_bore=8&center_distance=100'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
            '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        )
        assert r.status_code == 400

    def test_belt_svg_non_belt_family_single(self, client):
        """Requesting a belt cross-section SVG for a family without belt specs."""
        # RPP has no belt cross-section — use a completely unknown family
        r = client.get('/download/belt-svg?family=BOGUS&pitch=5M')
        assert r.status_code == 400


# ===========================================================================
# 2. Non-numeric inputs where numbers are expected
# ===========================================================================
class TestNonNumericInputs:
    """Letters / symbols in numeric fields.

    Preview endpoints absorb exceptions and always return a PNG.
    Download + JSON endpoints must return 4xx, not 500.
    """

    @pytest.mark.parametrize('param,value', [
        ('teeth',   'abc'),
        ('teeth',   ''),
        ('teeth',   'NaN'),
        ('teeth',   'inf'),
        ('bore',    'xyz'),
        ('bore',    ''),
        ('print_extra', 'abc'),
    ])
    def test_preview_non_numeric_returns_png(self, client, param, value):
        r = client.get(f'/api/preview?family=HTD&pitch=5M&{param}={value}'
                       '&clearance_preset=STANDARD&backlash_preset=STANDARD')
        assert r.status_code == 200
        assert _is_png(r.data)

    @pytest.mark.parametrize('param,value', [
        ('teeth',   'abc'),
        ('bore',    'xyz'),
        ('print_extra', 'abc'),
    ])
    def test_download_svg_non_numeric(self, client, param, value):
        r = client.get(
            f'/download/svg?family=HTD&pitch=5M&{param}={value}'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('value', ['abc', '', 'twelve', '3.5.1'])
    def test_api_od_non_numeric_value(self, client, value):
        r = client.get(f'/api/od?family=HTD&pitch=5M&mode=teeth&value={value}')
        assert r.status_code != 500

    @pytest.mark.parametrize('value', ['abc', '', 'far'])
    def test_api_belt_non_numeric_center_distance(self, client, value):
        r = client.get(
            f'/api/belt?family=HTD&pitch=5M&mode=from_center'
            f'&teeth1=20&teeth2=30&center_distance={value}'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('param,value', [
        ('teeth1', 'abc'),
        ('teeth2', 'abc'),
        ('n_belt', 'abc'),
    ])
    def test_api_belt_non_numeric_teeth(self, client, param, value):
        r = client.get(
            f'/api/belt?family=HTD&pitch=5M&mode=from_center'
            f'&{param}={value}&center_distance=100'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('value', ['abc', '', 'far'])
    def test_preview_dual_non_numeric_center(self, client, value):
        r = client.get(
            f'/api/preview?{_PREVIEW_GOOD}'
            f'&dual=true&center_distance={value}'
            '&p2_teeth=30&p2_bore=8&p2_print_extra=0'
            '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)


# ===========================================================================
# 3. Out-of-range but parseable numbers
# ===========================================================================
class TestOutOfRangeNumbers:
    """Values that parse fine but are outside sensible ranges."""

    # Teeth below min — app clamps to min_teeth, should succeed
    @pytest.mark.parametrize('teeth', [0, -1, -100, 1, 2, 3])
    def test_preview_teeth_below_min_clamped(self, client, teeth):
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth={teeth}'
            '&bore=8&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)

    # Very large teeth — should render without crashing
    @pytest.mark.parametrize('teeth', [200, 500, 1000])
    def test_preview_teeth_very_large(self, client, teeth):
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth={teeth}'
            '&bore=8&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)

    @pytest.mark.parametrize('teeth', [200, 500, 1000])
    def test_download_svg_teeth_very_large(self, client, teeth):
        r = client.get(
            f'/download/svg?family=HTD&pitch=5M&teeth={teeth}'
            '&bore=8&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code != 500

    # Zero / negative bore
    @pytest.mark.parametrize('bore', [0, -1, -0.001, 0.0, 0.5, 0.9])
    def test_preview_bore_below_minimum(self, client, bore):
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth=20&bore={bore}'
            '&print_extra=0&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)

    # Bore much larger than OD
    def test_preview_bore_larger_than_od(self, client):
        r = client.get(
            '/api/preview?family=HTD&pitch=5M&teeth=20&bore=9999'
            '&print_extra=0&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)

    # Extreme print_extra — exporter clamps to [0, pitch], should not crash
    @pytest.mark.parametrize('pe', [-1.0, -100.0, 100.0, 9999.0])
    def test_preview_extreme_print_extra(self, client, pe):
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth=20&bore=8&print_extra={pe}'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)

    # Center distance at or below minimum possible
    @pytest.mark.parametrize('cd', [0.001, 0, -100, -9999])
    def test_preview_dual_tiny_center_distance(self, client, cd):
        r = client.get(
            f'/api/preview?{_PREVIEW_GOOD}{_DUAL_EXTRA}'.replace(
                'center_distance=100', f'center_distance={cd}'
            )
        )
        assert r.status_code == 200
        assert _is_png(r.data)

    # Very large center distance
    def test_preview_dual_huge_center_distance(self, client):
        r = client.get(
            f'/api/preview?{_PREVIEW_GOOD}'
            '&dual=true&center_distance=100000'
            '&p2_teeth=30&p2_bore=8&p2_print_extra=0'
            '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)

    # api/od with zero or negative OD
    @pytest.mark.parametrize('od', [0, -1, -0.001, 0.00001])
    def test_api_od_tiny_or_negative_od(self, client, od):
        r = client.get(f'/api/od?family=HTD&pitch=5M&mode=od&value={od}')
        assert r.status_code != 500


# ===========================================================================
# 4. Belt-specific edge cases
# ===========================================================================
class TestBeltEdgeCases:
    """Invalid inputs specific to belt calculation and cross-section endpoints."""

    def test_api_belt_n_belt_zero(self, client):
        r = client.get(
            '/api/belt?family=HTD&pitch=5M&mode=from_teeth'
            '&teeth1=20&teeth2=30&n_belt=0'
        )
        assert r.status_code == 400
        assert 'error' in r.get_json()

    def test_api_belt_n_belt_negative(self, client):
        r = client.get(
            '/api/belt?family=HTD&pitch=5M&mode=from_teeth'
            '&teeth1=20&teeth2=30&n_belt=-10'
        )
        assert r.status_code == 400
        assert 'error' in r.get_json()

    def test_api_belt_n_belt_too_small_to_span(self, client):
        """A belt of 2 teeth cannot wrap two pulleys of 10+ teeth each."""
        r = client.get(
            '/api/belt?family=HTD&pitch=5M&mode=from_teeth'
            '&teeth1=20&teeth2=30&n_belt=2'
        )
        assert r.status_code == 400
        assert 'error' in r.get_json()

    def test_belt_preview_non_belt_family_returns_200(self, client):
        """Non-belt family on belt-preview returns empty/blank 200, not error."""
        r = client.get('/api/belt-preview?family=BOGUS&pitch=5M')
        assert r.status_code == 200

    def test_belt_preview_valid_family_bad_pitch_returns_svg(self, client):
        """Belt-preview with valid family but bad pitch returns error SVG, not 500."""
        r = client.get('/api/belt-preview?family=HTD&pitch=999M')
        assert r.status_code == 200
        assert b'<svg' in r.data

    def test_belt_svg_bad_pitch_for_family(self, client):
        r = client.get('/download/belt-svg?family=HTD&pitch=999M')
        assert r.status_code == 400

    def test_belt_svg_dual_center_distance_zero(self, client):
        """Zero centre distance in dual belt SVG — app should clamp, not crash."""
        r = client.get(
            '/download/belt-svg?family=HTD&pitch=5M&dual=true'
            '&teeth=20&p2_teeth=30&bore=8&p2_bore=8&center_distance=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
            '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('teeth', [0, -1, 1])
    def test_belt_svg_dual_teeth_below_min(self, client, teeth):
        """Teeth values below min are clamped; should not crash."""
        r = client.get(
            f'/download/belt-svg?family=HTD&pitch=5M&dual=true'
            f'&teeth={teeth}&p2_teeth=30&bore=8&p2_bore=8&center_distance=100'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
            '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        )
        assert r.status_code != 500


# ===========================================================================
# 5. Injection / malicious strings
# ===========================================================================
class TestInjectionStrings:
    """Injection payloads in text parameters should never crash the server.
    The app treats unknown family/pitch as an unknown profile → 400.
    """

    @pytest.mark.parametrize('family', [
        '<script>alert(1)</script>',
        "'; DROP TABLE pulley_specs; --",
        '../../../etc/passwd',
        'A' * 2000,
        '\x00\x01\x02\x03',
        '{{7*7}}',          # template injection probe
        '%00%0d%0a',        # null-byte / CRLF
    ])
    def test_api_spec_injection_family(self, client, family):
        r = client.get(f'/api/spec?family={family}&pitch=5M')
        assert r.status_code != 500

    @pytest.mark.parametrize('pitch', [
        '<img src=x onerror=alert(1)>',
        "5M'; --",
        '5M\n8M',
        '../5M',
        '%2e%2e%2f5M',
    ])
    def test_api_spec_injection_pitch(self, client, pitch):
        r = client.get(f'/api/spec?family=HTD&pitch={pitch}')
        assert r.status_code != 500

    @pytest.mark.parametrize('family', [
        '<script>alert(1)</script>',
        "'; DROP TABLE--",
        'A' * 500,
    ])
    def test_download_svg_injection_family(self, client, family):
        r = client.get(
            f'/download/svg?family={family}&pitch=5M'
            '&teeth=20&bore=8&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('value', [
        '<script>alert(1)</script>',
        "'; --",
        '1 OR 1=1',
        '../../',
    ])
    def test_api_od_injection_value(self, client, value):
        r = client.get(f'/api/od?family=HTD&pitch=5M&mode=teeth&value={value}')
        assert r.status_code != 500

    @pytest.mark.parametrize('value', [
        '<script>',
        "1; DROP TABLE--",
        '100 OR 1=1',
    ])
    def test_api_belt_injection_center_distance(self, client, value):
        r = client.get(
            f'/api/belt?family=HTD&pitch=5M&mode=from_center'
            f'&teeth1=20&teeth2=30&center_distance={value}'
        )
        assert r.status_code != 500


# ===========================================================================
# 6. Unknown preset names
# ===========================================================================
class TestUnknownPresets:
    """Unrecognised clearance/backlash preset names — should not crash.
    The app falls through to 0.0 for unknown presets.
    """

    @pytest.mark.parametrize('preset', [
        'INVALID', 'custom', 'loose', '', 'NULL', 'undefined',
        '<script>', "STANDARD' OR '1'='1",
    ])
    def test_download_svg_unknown_clearance_preset(self, client, preset):
        r = client.get(
            f'/download/svg?family=HTD&pitch=5M&teeth=20&bore=8&print_extra=0'
            f'&clearance_preset={preset}&backlash_preset=STANDARD'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('preset', [
        'INVALID', 'none', 'ZERO', '', 'NULL',
    ])
    def test_download_svg_unknown_backlash_preset(self, client, preset):
        r = client.get(
            f'/download/svg?family=HTD&pitch=5M&teeth=20&bore=8&print_extra=0'
            '&clearance_preset=STANDARD'
            f'&backlash_preset={preset}'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('preset', ['INVALID', '', 'loose'])
    def test_preview_unknown_preset(self, client, preset):
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth=20&bore=8&print_extra=0'
            f'&clearance_preset={preset}&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)


# ===========================================================================
# 7. Minimum-teeth auto-correction  (regression for UI fix)
# ===========================================================================
class TestMinTeethCorrection:
    """
    When a user types a tooth count below the profile minimum the server must
    clamp to min_teeth and return valid geometry — never a 500 or corrupt output.

    This mirrors the client-side fix in onTeethChange() that snaps the input
    field to currentSpec.min_teeth before sending to the API.
    """

    # Profiles and their known min_teeth values
    _PROFILES = [
        ('HTD',      '3M',  10),
        ('HTD',      '5M',  12),
        ('HTD',      '8M',  15),
        ('HTD',      '14M', 17),
        ('GT',       '2M',  10),
        ('GT',       '3M',  10),
        ('T',        'T5',  10),
        ('Imperial', 'XL',  10),
    ]

    @pytest.mark.parametrize('family,pitch,min_t', _PROFILES,
                             ids=[f'{f}-{p}' for f, p, _ in _PROFILES])
    def test_api_od_below_min_clamped(self, client, family, pitch, min_t):
        """
        /api/od with teeth=1 (always below any profile min) must return the OD
        for min_teeth, not an error and not a zero/negative diameter.
        """
        r = client.get(
            f'/api/od?family={family}&pitch={pitch}&mode=teeth&value=1'
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data['od'] > 0, "OD must be positive when teeth clamped to min"
        assert data['teeth'] >= min_t, (
            f"Returned teeth {data['teeth']} is below profile min {min_t}"
        )

    @pytest.mark.parametrize('family,pitch,min_t', _PROFILES,
                             ids=[f'{f}-{p}' for f, p, _ in _PROFILES])
    def test_download_svg_below_min_succeeds(self, client, family, pitch, min_t):
        """
        /download/svg with teeth below minimum must produce a valid SVG,
        not a 400/500.  The server clamps to min_teeth.
        """
        below = max(0, min_t - 1)
        r = client.get(
            f'/download/svg?family={family}&pitch={pitch}&teeth={below}'
            '&bore=6&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert b'<svg' in r.data

    @pytest.mark.parametrize('family,pitch,min_t', _PROFILES,
                             ids=[f'{f}-{p}' for f, p, _ in _PROFILES])
    def test_download_dxf_below_min_succeeds(self, client, family, pitch, min_t):
        """
        /download/dxf with teeth below minimum must produce a valid DXF,
        not a 400/500.
        """
        below = max(0, min_t - 1)
        r = client.get(
            f'/download/dxf?family={family}&pitch={pitch}&teeth={below}'
            '&bore=6&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert len(r.data) > 0

    @pytest.mark.parametrize('teeth', [0, -1, -100, 1])
    def test_preview_various_below_min_never_500(self, client, teeth):
        """
        Preview with multiple below-minimum values must always return a PNG,
        never a 500.  Covers the common accidental inputs (0, negative, 1).
        """
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth={teeth}'
            '&bore=8&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert r.data[:4] == b'\x89PNG'

    def test_api_od_exactly_at_min_is_accepted(self, client):
        """teeth == min_teeth must pass through unchanged (no off-by-one clamping)."""
        from tests.conftest import get_spec
        spec = get_spec('HTD', '5M')
        min_t = spec['min_teeth']
        r = client.get(
            f'/api/od?family=HTD&pitch=5M&mode=teeth&value={min_t}'
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data['teeth'] == min_t

    def test_api_od_one_above_min_unchanged(self, client):
        """teeth == min_teeth + 1 must not be altered by any clamping logic."""
        from tests.conftest import get_spec
        spec  = get_spec('HTD', '5M')
        above = spec['min_teeth'] + 1
        r = client.get(
            f'/api/od?family=HTD&pitch=5M&mode=teeth&value={above}'
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data['teeth'] == above

    def test_p2_teeth_below_min_svg_succeeds(self, client):
        """
        Pulley 2 below-min teeth in a dual-mode belt SVG must not crash.
        Mirrors the P2 branch of the onTeethChange() fix.
        """
        r = client.get(
            '/download/belt-svg?family=HTD&pitch=5M&dual=true'
            '&teeth=20&p2_teeth=1'          # P2 teeth = 1, below HTD-5M min of 12
            '&bore=8&p2_bore=8&center_distance=120'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
            '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
            '&print_extra=0&p2_print_extra=0'
        )
        assert r.status_code != 500

    def test_p2_teeth_below_min_dxf_succeeds(self, client):
        """Pulley 2 DXF download with below-min teeth must not crash."""
        r = client.get(
            '/download/dxf?family=HTD&pitch=5M&pulley=2'
            '&p2_teeth=1&p2_bore=6&p2_print_extra=0'
            '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert len(r.data) > 0


# ===========================================================================
# 8. Bore diameter validation  (regression — negative bore was accepted)
# ===========================================================================
class TestBoreValidation:
    """
    Bore diameter must be clamped to a minimum of 0.1 mm server-side.
    Negative, zero, and near-zero values must never crash the server or
    produce corrupt geometry.
    """

    _BAD_BORES = [-10, -1, -0.001, 0, 0.0, 0.5, 0.9]
    _ENDPOINTS = [
        ('/api/preview',   '&clearance_preset=STANDARD&backlash_preset=STANDARD'),
        ('/download/svg',  '&clearance_preset=STANDARD&backlash_preset=STANDARD'),
        ('/download/dxf',  '&clearance_preset=STANDARD&backlash_preset=STANDARD'),
    ]

    @pytest.mark.parametrize('bore', _BAD_BORES)
    def test_preview_negative_bore_returns_png(self, client, bore):
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth=20&bore={bore}'
            '&print_extra=0&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert r.data[:4] == b'\x89PNG'

    @pytest.mark.parametrize('bore', _BAD_BORES)
    def test_download_svg_negative_bore_succeeds(self, client, bore):
        r = client.get(
            f'/download/svg?family=HTD&pitch=5M&teeth=20&bore={bore}'
            '&print_extra=0&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert b'<svg' in r.data

    @pytest.mark.parametrize('bore', _BAD_BORES)
    def test_download_dxf_negative_bore_succeeds(self, client, bore):
        r = client.get(
            f'/download/dxf?family=HTD&pitch=5M&teeth=20&bore={bore}'
            '&print_extra=0&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert len(r.data) > 0

    @pytest.mark.parametrize('bore', _BAD_BORES)
    def test_p2_negative_bore_preview_returns_png(self, client, bore):
        """Pulley 2 bore clamped in dual preview."""
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth=20&bore=8&print_extra=0'
            '&clearance_preset=STANDARD&backlash_preset=STANDARD'
            f'&dual=true&center_distance=120'
            f'&p2_teeth=30&p2_bore={bore}&p2_print_extra=0'
            '&p2_clearance_preset=STANDARD&p2_backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert r.data[:4] == b'\x89PNG'

    def test_bore_non_numeric_preview_returns_png(self, client):
        r = client.get(
            '/api/preview?family=HTD&pitch=5M&teeth=20&bore=abc'
            '&print_extra=0&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert r.data[:4] == b'\x89PNG'

    def test_bore_minimum_valid_value_accepted(self, client):
        """bore=1.0 is the clamped minimum — must produce valid output."""
        r = client.get(
            '/download/svg?family=HTD&pitch=5M&teeth=20&bore=1.0'
            '&print_extra=0&clearance_preset=STANDARD&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert b'<svg' in r.data


# ===========================================================================
# 9. Extreme custom preset values
# ===========================================================================
class TestExtremeCustomValues:
    """Custom clearance/backlash mm values at extremes."""

    @pytest.mark.parametrize('cl', [-9999, 9999, 0.00001, -0.00001])
    def test_download_svg_extreme_custom_clearance(self, client, cl):
        r = client.get(
            f'/download/svg?family=HTD&pitch=5M&teeth=20&bore=8&print_extra=0'
            f'&clearance_preset=CUSTOM&clearance_custom={cl}'
            '&backlash_preset=STANDARD'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('bl', [-9999, 9999, 0.00001])
    def test_download_svg_extreme_custom_backlash(self, client, bl):
        r = client.get(
            f'/download/svg?family=HTD&pitch=5M&teeth=20&bore=8&print_extra=0'
            '&clearance_preset=STANDARD'
            f'&backlash_preset=CUSTOM&backlash_custom={bl}'
        )
        assert r.status_code != 500

    @pytest.mark.parametrize('cl', [-9999, 9999])
    def test_preview_extreme_custom_clearance(self, client, cl):
        r = client.get(
            f'/api/preview?family=HTD&pitch=5M&teeth=20&bore=8&print_extra=0'
            f'&clearance_preset=CUSTOM&clearance_custom={cl}'
            '&backlash_preset=STANDARD'
        )
        assert r.status_code == 200
        assert _is_png(r.data)
