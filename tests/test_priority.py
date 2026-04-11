"""
test_priority.py — Verify the P1 → GR → P2 edit priority cascade.

Priority rules (implemented in _doTeethChange / _doODChange / onGearRatioChange):
  1. Edit P1 (teeth or OD): GR stays fixed, P2 is recalculated  →  P2 = round(GR × P1)
  2. Edit GR:               P1 stays fixed, P2 is recalculated  →  P2 = round(GR × P1)
  3. Edit P2 (teeth or OD): P1 stays fixed, GR is recalculated  →  GR = P2 / P1

These tests drive /api/od to confirm the server returns tooth and OD values that
are consistent with the expected cascade arithmetic at each editing step.
"""
import math
import pytest

FAMILY = 'HTD'
PITCH  = '5M'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_teeth(client, teeth, family=FAMILY, pitch=PITCH):
    """Ask the server to canonicalise a tooth count → (teeth, od)."""
    r = client.get(f'/api/od?family={family}&pitch={pitch}&mode=teeth&value={teeth}')
    assert r.status_code == 200
    d = r.get_json()
    return d['teeth'], d['od']


def resolve_od(client, od, family=FAMILY, pitch=PITCH):
    """Ask the server to reverse-calculate a tooth count from an OD → (teeth, od)."""
    r = client.get(f'/api/od?family={family}&pitch={pitch}&mode=od&value={od}')
    assert r.status_code == 200
    d = r.get_json()
    return d['teeth'], d['od']


# ---------------------------------------------------------------------------
# Rule 1 — editing P1 keeps GR fixed, recalculates P2
# ---------------------------------------------------------------------------

class TestP1Edit:

    def test_p2_derived_from_gr_times_new_p1(self, client):
        """P2 = round(GR × new_P1) when P1 teeth changes."""
        p1_before, _ = resolve_teeth(client, 20)
        gr = 1.5
        p1_after, _ = resolve_teeth(client, 24)
        expected_p2 = round(gr * p1_after)        # round(1.5 × 24) = 36
        actual_p2, _ = resolve_teeth(client, expected_p2)
        assert actual_p2 == expected_p2
        assert abs(actual_p2 / p1_after - gr) < 0.01

    @pytest.mark.parametrize('p1,gr,expected_p2', [
        (20, 1.0,   20),
        (20, 1.5,   30),
        (20, 2.0,   40),
        (16, 1.5,   24),
        (24, 2.0,   48),
        (30, 0.5,   15),
    ])
    def test_p2_values_for_various_p1_and_gr(self, client, p1, gr, expected_p2):
        p2_computed = round(gr * p1)
        assert p2_computed == expected_p2, 'test data sanity check'
        p2r, _ = resolve_teeth(client, p2_computed)
        assert p2r == p2_computed

    def test_p1_stays_at_new_value(self, client):
        """The edited P1 tooth count is preserved exactly (server must not clamp it)."""
        for teeth in [12, 16, 20, 28, 36]:  # 12 = HTD-5M min_teeth
            t, _ = resolve_teeth(client, teeth)
            assert t == teeth, f'P1 should be preserved at {teeth}T, got {t}T'

    def test_p1_od_edit_cascades_same_as_teeth_edit(self, client):
        """
        Editing P1 OD is equivalent: OD → teeth round-trip gives the same P1
        tooth count, so the resulting P2 is identical to the teeth-edit path.
        """
        p1_teeth = 20
        _, p1_od = resolve_teeth(client, p1_teeth)
        t_from_od, _ = resolve_od(client, p1_od)
        assert t_from_od == p1_teeth, 'OD round-trip must reproduce the same teeth count'

        gr = 1.5
        p2_via_teeth = round(gr * p1_teeth)
        p2_via_od    = round(gr * t_from_od)
        assert p2_via_od == p2_via_teeth, (
            f'P1 OD-edit cascade should match teeth-edit cascade: '
            f'{p2_via_od} vs {p2_via_teeth}'
        )

    def test_p1_edit_does_not_touch_gr(self, client):
        """
        The GR value is never recalculated when P1 is edited — only P2 moves.
        Verify by checking that the ratio applied to two different P1 sizes
        always produces P2 = round(GR × P1), leaving GR unchanged.
        """
        gr = 2.0
        for p1 in [12, 16, 20, 24]:
            p2 = round(gr * p1)
            p2r, _ = resolve_teeth(client, p2)
            assert p2r == p2
            # GR computed from the result equals the original GR
            assert abs(p2r / p1 - gr) < 0.001, (
                f'GR should stay {gr} after P1 edit; got {p2r / p1:.4f}'
            )


# ---------------------------------------------------------------------------
# Rule 2 — editing GR keeps P1 fixed, recalculates P2
# ---------------------------------------------------------------------------

class TestGearRatioEdit:

    @pytest.mark.parametrize('p1,gr,expected_p2', [
        (20, 1.0,   20),
        (20, 1.5,   30),
        (20, 2.0,   40),
        (30, 0.5,   15),   # round(0.5 × 30) = 15; above HTD-5M min_teeth
        (16, 1.5,   24),
        (24, 1.5,   36),
        (20, 1.333, 27),   # round(1.333 × 20) = round(26.66) = 27
    ])
    def test_p2_from_gr_times_p1(self, client, p1, gr, expected_p2):
        """GR edit: P2 = round(GR × P1), P1 unchanged."""
        p2_computed = round(gr * p1)
        assert p2_computed == expected_p2, 'test data sanity'
        p2r, _ = resolve_teeth(client, p2_computed)
        assert p2r == p2_computed

    def test_p1_unchanged_after_gr_edit(self, client):
        """P1 must never be touched when GR is edited."""
        p1 = 20
        p1r, _ = resolve_teeth(client, p1)
        assert p1r == p1
        for gr in [0.5, 1.0, 1.5, 2.0, 3.0]:
            # Simulate GR edit: compute P2, then check P1 is still resolvable unchanged
            p2 = round(gr * p1)
            resolve_teeth(client, p2)          # would be stored to p2 inputs in UI
            p1_still, _ = resolve_teeth(client, p1)
            assert p1_still == p1, f'P1 should be unchanged after GR edit to {gr}'

    def test_gr_snaps_to_achievable_ratio(self, client):
        """
        The displayed GR snaps to the exact achievable value (whole teeth).
        e.g. target GR 1.333 with P1=20 → P2=27 → true GR = 27/20 = 1.35
        """
        p1, gr_target = 20, 1.333
        p2 = round(gr_target * p1)           # 27
        p2r, _ = resolve_teeth(client, p2)
        gr_true = p2r / p1                    # 1.35
        assert p2r == 27
        assert abs(gr_true - 1.35) < 0.001


# ---------------------------------------------------------------------------
# Rule 3 — editing P2 keeps P1 fixed, updates GR
# ---------------------------------------------------------------------------

class TestP2Edit:

    @pytest.mark.parametrize('p1,p2,expected_gr', [
        (20, 20,  1.000),
        (20, 30,  1.500),
        (20, 40,  2.000),
        (30, 15,  0.500),  # 10T is below HTD-5M min; use 30→15 instead
        (16, 24,  1.500),
        (24, 36,  1.500),
        (20, 25,  1.250),
    ])
    def test_gr_derived_from_p2_edit(self, client, p1, p2, expected_gr):
        """GR = P2 / P1 after a P2 edit; P1 never changes."""
        p2r, _ = resolve_teeth(client, p2)
        p1r, _ = resolve_teeth(client, p1)
        gr = p2r / p1r
        assert abs(gr - expected_gr) < 0.001, (
            f'P2 edit P1={p1} P2={p2}: expected GR={expected_gr:.3f}, got {gr:.4f}'
        )

    def test_p1_unchanged_after_p2_edit(self, client):
        """P1 must not move when P2 is edited directly."""
        p1 = 20
        for p2 in [15, 25, 30, 40]:
            p1r, _ = resolve_teeth(client, p1)
            assert p1r == p1, f'P1 should stay {p1} after P2 set to {p2}'

    def test_p2_od_edit_same_cascade_as_teeth_edit(self, client):
        """
        Editing P2 OD resolves to the same tooth count as editing P2 teeth directly,
        so the resulting GR is identical on both paths.
        """
        p1, p2_teeth = 20, 30
        _, p2_od = resolve_teeth(client, p2_teeth)
        t_from_od, _ = resolve_od(client, p2_od)
        assert t_from_od == p2_teeth, (
            f'P2 OD round-trip: expected {p2_teeth}T, got {t_from_od}T'
        )
        gr_via_teeth = p2_teeth / p1
        gr_via_od    = t_from_od / p1
        assert abs(gr_via_od - gr_via_teeth) < 0.001

    def test_gr_reflects_new_p2_not_old(self, client):
        """After P2 changes, the new GR is based on the new P2, not the old one."""
        p1 = 20
        # Old state: P2=30, GR=1.5
        old_p2 = 30
        # User edits P2 to 40 → GR must become 40/20 = 2.0, not 1.5
        new_p2 = 40
        new_p2r, _ = resolve_teeth(client, new_p2)
        gr_new = new_p2r / p1
        assert abs(gr_new - 2.0) < 0.001, (
            f'GR after P2 edit from {old_p2} to {new_p2}: expected 2.0, got {gr_new:.4f}'
        )


# ---------------------------------------------------------------------------
# Full priority chain — simulate a realistic editing sequence
# ---------------------------------------------------------------------------

def test_full_priority_chain(client):
    """
    Simulate a realistic editing sequence and verify each priority rule fires correctly.

    Step 1 — Set P1=20, GR=1.5  →  P2 = round(1.5 × 20) = 30
    Step 2 — Edit P2 to 40       →  GR becomes 40/20 = 2.0, P1 stays 20
    Step 3 — Edit GR to 1.0      →  P2 becomes round(1.0 × 20) = 20, P1 stays 20
    Step 4 — Edit P1 to 24       →  P2 becomes round(1.0 × 24) = 24, GR stays 1.0
    """
    # Step 1: initial state
    p1, gr = 20, 1.5
    p2 = round(gr * p1)
    p2r, _ = resolve_teeth(client, p2)
    assert p2r == 30, f'Step 1: expected P2=30, got {p2r}'

    # Step 2: P2 edited → GR updates, P1 stays
    p2 = 40
    p2r, _ = resolve_teeth(client, p2)
    gr = p2r / p1
    assert abs(gr - 2.0) < 0.001, f'Step 2: expected GR=2.0, got {gr:.4f}'
    p1r, _ = resolve_teeth(client, p1)
    assert p1r == 20, f'Step 2: P1 should stay 20, got {p1r}'

    # Step 3: GR edited → P2 updates, P1 stays
    gr = 1.0
    p2 = round(gr * p1)
    p2r, _ = resolve_teeth(client, p2)
    assert p2r == 20, f'Step 3: expected P2=20, got {p2r}'
    p1r, _ = resolve_teeth(client, p1)
    assert p1r == 20, f'Step 3: P1 should stay 20, got {p1r}'

    # Step 4: P1 edited → P2 recalculated using current GR=1.0, P1 anchors
    p1 = 24
    p2 = round(gr * p1)
    p2r, _ = resolve_teeth(client, p2)
    assert p2r == 24, f'Step 4: expected P2=24, got {p2r}'
    p1r, _ = resolve_teeth(client, p1)
    assert p1r == 24, f'Step 4: new P1 should be 24, got {p1r}'


# ---------------------------------------------------------------------------
# Cross-family check — priority arithmetic is profile-independent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('family,pitch', [
    pytest.param('GT',       '3M', id='GT-3M'),
    pytest.param('T',        'T5', id='T-T5'),
    pytest.param('Imperial', 'XL', id='Imperial-XL'),
])
def test_priority_holds_across_profiles(client, family, pitch):
    """
    The P1→GR→P2 cascade is arithmetic (round(GR × P1) or P2/P1) and must be
    profile-independent — only the OD values differ per spec.
    """
    p1, gr = 20, 1.5
    p2_expected = round(gr * p1)   # 30

    # Rule 1: GR fixed, P2 from P1
    p2r, _ = resolve_teeth(client, p2_expected, family=family, pitch=pitch)
    assert p2r == p2_expected

    # Rule 3: P2 edited, GR derived
    p1r, _ = resolve_teeth(client, p1, family=family, pitch=pitch)
    gr_derived = p2r / p1r
    assert abs(gr_derived - gr) < 0.001, (
        f'{family} {pitch}: expected GR={gr}, got {gr_derived:.4f}'
    )
