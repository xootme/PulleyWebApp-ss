"""test_assembly_step.py — the whole design as one assembly STEP (the owner,
2026-10-06): /download/assembly-step, exporters/assembly.py::assemble_step,
small_step's `assemble` (Documents\\CCT_Assembly_STEP_Handoff.md, item 2).

Without a binary: which parts go in (the Download window's ticks), a pulley
ticked alone being its own STEP, the shaft's sections as pieces while
small_step's extrude takes none, the agent API listing one STEP file.

With a small_step that can assemble (SMALL_STEP_BIN, or bin/small_step_linux on
Linux; there is no Windows build with it yet): the route's file is one
assembly, root -> the named assembly -> one occurrence per placement, every
product in the tree. With OCP too, it is read as a CAD program reads it
(XCAF): each occurrence's name and place to 1e-9 and its volume.
"""
import math
import re

import pytest

from exporters import assembly as asm
from exporters import colors

BIN = asm.ss_binary()
needs_assemble = pytest.mark.skipif(not asm.can_assemble(BIN),
                                    reason=f'no small_step with assemble ({BIN or "none found"})')

DRIVE = dict(family='HTD', pitch='5M', teeth='20', bore='8', belt_height='10', feature_build='1',
             dual='true', p2_teeth='30', p2_bore='8', center_distance='100', n_belt='0')
FLANGED = dict(DRIVE, flange_enabled='1', flange_3dprint='1', flange_top_separate='1',
               flange_angle='15', flange_rim_radius='3', flange_height='1.5',
               p2_flange_enabled='1', p2_flange_3dprint='0', p2_flange_plate_height='1',
               p2_flange_angle='15', p2_flange_rim_radius='3')
SPLINE = dict(family='HTD', pitch='5M', teeth='40', bore='8', belt_height='10', feature_build='1',
              bore_shape='spline', spline_type='involute', spline_m='1.5', spline_z='16', spline_pa='30',
              spline_root='flat', spline_ring_top='1', spline_ring_bottom='1', spline_washer='1',
              hub_od='40', hub_height='10')


def _names(m):
    return [p['name'] for p in m['parts']]


def _places(m):
    return {p['name']: [p['placement']] + p.get('instances', []) for p in m['parts']}


# ── which parts go in ────────────────────────────────────────────────────────

def test_the_parts_ticked_are_the_parts_in_it(client):
    full = asm.manifest(DRIVE)
    some = asm.manifest(DRIVE, only={'p2', 'belt'})
    assert _names(full) == ['HTD-5M-20T', 'HTD-5M-30T-P2', 'HTD-5M-belt']
    assert _names(some) == ['HTD-5M-30T-P2', 'HTD-5M-belt']
    assert some['name'] == full['name'] == 'HTD-5M-20T-30T'
    assert all(_places(some)[n] == _places(full)[n] for n in _names(some))   # placed as in the whole drive


def test_a_ring_goes_with_its_shaft(client):
    """The ring sits in the shaft's groove: it comes with the shaft, not the washer."""
    ring = asm.manifest(SPLINE)['parts'][-1]['name']
    assert ring.startswith('DIN 471')
    assert _names(asm.manifest(SPLINE, only={'sh1'})) == ['HTD-5M-40T-spline-shaft', ring]
    assert _names(asm.manifest(SPLINE, only={'wa1'})) == ['HTD-5M-40T-spline-washer']
    assert len(_places(asm.manifest(SPLINE, only={'sh1'}))[ring]) == 2              # both faces


def test_one_pulley_alone_is_its_own_step(client):
    assert asm.single_make(asm.manifest(DRIVE, only={'p1'}))['name'] == 'HTD-5M-20T'
    assert asm.single_make(asm.manifest(DRIVE, only={'p1', 'p2'})) is None
    assert asm.single_make(asm.manifest(SPLINE, only={'wa1'})) is None              # made by extrude
    made = []
    out = asm.assemble_step(asm.manifest(DRIVE, only={'p2'}), lambda mk: made.append(mk) or b'STEP', ss_bin='none')
    assert out == b'STEP' and made == [{'kind': 'pulley', 'pulley': 2, 'color': colors.PULLEY[2]}]   # no binary


def test_the_shaft_in_pieces_while_extrude_takes_no_sections(client):
    m = asm.manifest(SPLINE)
    shaft = next(p for p in m['parts'] if p['name'].endswith('shaft'))
    sections = shaft['extrude']['sections']
    low = asm.lower_sections(m)
    pieces = [p for p in low['parts'] if p['name'].startswith(shaft['name'] + '-')]
    assert [p['name'] for p in pieces] == [f"{shaft['name']}-{i}" for i in range(1, len(sections) + 1)]
    z = shaft['placement']['origin'][2]
    for p, s in zip(pieces, sections):
        assert p['extrude']['thickness'] == pytest.approx(s['z1'] - s['z0'])
        assert p['extrude']['loops'] == s['loops'] and p['extrude']['name'] == p['name']
        assert p['placement']['origin'][2] == pytest.approx(z + s['z0'])
        assert p['placement']['rotate_z_deg'] == shaft['placement']['rotate_z_deg']
        assert p['color'] == shaft['color'] == colors.SHAFT
    assert [p for p in low['parts'] if p not in pieces] == [p for p in m['parts'] if p is not shaft]


def test_every_part_has_its_colour(client):
    """The 3D view's colours (exporters/colors.py), one per part, in the manifest:
    small_step paints every occurrence of a part from it."""
    want = {'HTD-5M-20T': colors.PULLEY[1], 'HTD-5M-30T-P2': colors.PULLEY[2], 'HTD-5M-belt': colors.BELT}
    assert {p['name']: p['color'] for p in asm.manifest(DRIVE)['parts']} == want
    got = {p['name']: p['color'] for p in asm.manifest(SPLINE)['parts']}
    ring = next(n for n in got if n.startswith('DIN 471'))
    assert got == {'HTD-5M-40T': colors.PULLEY[1], 'HTD-5M-40T-spline-shaft': colors.SHAFT,
                   'HTD-5M-40T-spline-washer': colors.WASHER, ring: colors.RING}


def test_parts_is_a_route_key_not_the_design():
    from charging import ROUTE_ONLY_KEYS, design_matches
    assert 'parts' in ROUTE_ONLY_KEYS
    assert design_matches(dict(DRIVE, parts='p1,p2,belt'), dict(DRIVE))


def test_the_agent_lists_one_step_file(client):
    import agent
    files = agent.files(DRIVE, ['pulley1', 'pulley2', 'belt'], ['step', 'stl'])
    steps = [f for f in files if f[1] == 'step']
    assert len(steps) == 1
    part, fmt, path, params, name = steps[0]
    assert (part, path, name) == ('pulley1,pulley2,belt', '/download/assembly-step', 'HTD-5M-20T-30T.step')
    assert params['parts'] == 'p1,p2,belt'
    assert [f[0] for f in files if f[1] == 'stl'] == ['pulley1', 'pulley2', 'belt']
    files = agent.files(SPLINE, ['shaft1', 'washer1'], ['step'])
    assert [(f[0], f[3]['parts']) for f in files] == [('shaft1,washer1', 'sh1,wa1')]


# ── the assembly STEP itself ─────────────────────────────────────────────────

def _get(client, q):
    r = client.get('/download/assembly-step', query_string=q)
    assert r.status_code == 200, r.data[:300]
    return r


def _tree(data: bytes):
    """(product name per PRODUCT_DEFINITION id, [(parent, child, occurrence name)], root ids)."""
    text = data.decode('utf-8', errors='replace')         # small_step writes names as UTF-8
    prod = {m.group(1): m.group(2) for m in re.finditer(r"#(\d+)=PRODUCT\('([^']*)'", text)}
    form = {m.group(1): m.group(2) for m in re.finditer(r"#(\d+)=PRODUCT_DEFINITION_FORMATION\w*\('[^']*','[^']*',#(\d+)", text)}
    pdef = {m.group(1): prod[form[m.group(2)]]
            for m in re.finditer(r"#(\d+)=PRODUCT_DEFINITION\('[^']*','[^']*',#(\d+)", text)}
    uses = [(a, b, n) for n, a, b in
            re.findall(r"NEXT_ASSEMBLY_USAGE_OCCURRENCE\('([^']*)','[^']*','[^']*',#(\d+),#(\d+)", text)]
    roots = [d for d in pdef if d not in {b for _, b, _ in uses}]
    return pdef, uses, roots


def _rgb(hexcolor):
    h = hexcolor.lstrip('#')
    return tuple(round(int(h[i:i + 2], 16) / 255.0, 6) for i in (0, 2, 4))


def _check_colours(data, m):
    """One colour chain per distinct colour, a styled item per solid: every
    part's solids painted its manifest colour, each geometry once."""
    got = {tuple(round(float(x), 6) for x in c) for c in
           re.findall(r"COLOUR_RGB\('[^']*',([-0-9.Ee+]+),([-0-9.Ee+]+),([-0-9.Ee+]+)\)", data.decode('latin-1'))}
    assert got == {_rgb(p['color']) for p in m['parts']}
    assert data.count(b'STYLED_ITEM(') == data.count(b'MANIFOLD_SOLID_BREP')


def _check_tree(data, m):
    """root -> the named assembly -> one occurrence per placement of each part."""
    pdef, uses, roots = _tree(data)
    assert len(roots) == 1, f'loose products: {[pdef[r] for r in roots]}'
    top = [b for a, b, _ in uses if a == roots[0]]
    assert len(top) == 1 and pdef[top[0]] == m['name']
    occ = sorted(n for a, _, n in uses if a == top[0])
    assert occ == sorted(n for n, pls in _places(m).items() for _ in pls)


def _xcaf(data):
    """[(occurrence name, location 4x3, volume)] read by OCCT's XCAF, as CAD does."""
    import os
    import tempfile
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(data)
    try:
        doc = TDocStd_Document(TCollection_ExtendedString('XmlOcaf'))
        rd = STEPCAFControl_Reader()
        rd.SetNameMode(True)
        rd.ReadFile(f.name)
        rd.Transfer(doc)
    finally:
        os.unlink(f.name)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def kids(lab):
        seq = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetComponents_s(lab, seq, False)
        return [seq.Value(i) for i in range(1, seq.Length() + 1)]

    def name(lab):
        a = TDataStd_Name()
        return a.Get().ToExtString() if lab.FindAttribute(TDataStd_Name.GetID_s(), a) else ''

    free = TDF_LabelSequence()
    st.GetFreeShapes(free)
    assert free.Length() == 1
    out = []
    for occ in kids(free.Value(1)):                                  # root -> the named assembly
        from OCP.TDF import TDF_Label
        ref = TDF_Label()
        XCAFDoc_ShapeTool.GetReferredShape_s(occ, ref)
        for c in kids(ref):                                          # -> each occurrence
            t = XCAFDoc_ShapeTool.GetLocation_s(c).Transformation()
            g = GProp_GProps()
            BRepGProp.VolumeProperties_s(XCAFDoc_ShapeTool.GetShape_s(c), g)
            out.append((name(c), [[t.Value(i, j) for j in (1, 2, 3, 4)] for i in (1, 2, 3)], g.Mass()))
    return doc, out


def _check_places(data, m):
    """Each occurrence where its placement says (rotate about z, then move), to 1e-9
    (mm, and radians: the file writes a direction to 9 decimals)."""
    try:
        import OCP  # noqa: F401
    except ImportError:
        return                                                       # Linux without OCP: the tree is checked
    _doc, occ = _xcaf(data)
    want = sorted((n, pl['origin'], math.radians(pl.get('rotate_z_deg', 0.0)))
                  for n, pls in _places(m).items() for pl in pls)
    got = sorted((n, [row[3] for row in t], math.atan2(t[1][0], t[0][0])) for n, t, _ in occ)
    assert [g[0] for g in got] == [w[0] for w in want]
    for (_, o, a), (_, wo, wa) in zip(got, want):
        assert o == pytest.approx(wo, abs=1e-9) and a == pytest.approx(wa, abs=1e-9)
    assert all(v > 0 for _, _, v in occ)


@needs_assemble
def test_a_drive_is_one_assembly(client):
    r = _get(client, DRIVE)
    assert 'filename="HTD-5M-20T-30T.step"' in r.headers['Content-Disposition']
    m = asm.manifest(DRIVE)
    _check_tree(r.data, m)
    _check_places(r.data, m)
    _check_colours(r.data, m)


@needs_assemble
def test_the_parts_ticked_are_in_the_file(client):
    q = dict(DRIVE, parts='p2,belt')
    r = _get(client, q)
    _check_tree(r.data, asm.manifest(q, only={'p2', 'belt'}))


@needs_assemble
def test_one_pulley_ticked_is_a_plain_part(client):
    r = _get(client, dict(DRIVE, parts='p1'))
    assert 'filename="HTD-5M-20T.step"' in r.headers['Content-Disposition']
    assert b'NEXT_ASSEMBLY_USAGE_OCCURRENCE' not in r.data
    _check_colours(r.data, asm.manifest(DRIVE, only={'p1'}))       # painted by its own file


@needs_assemble
def test_a_splined_pulley_with_its_shaft_washers_and_rings(client):
    r = _get(client, SPLINE)
    m = asm.manifest(SPLINE)
    if not asm.can_sections(BIN):
        m = asm.lower_sections(m)
    _check_tree(r.data, m)
    _check_places(r.data, m)
    _check_colours(r.data, m)


@needs_assemble
@pytest.mark.xfail(strict=True, reason="small_step assemble (a99954e) keeps only a part file's first PRODUCT: "
                                       "a flanged pulley's top_flange/bottom_flange are left loose, unplaced")
def test_a_flanged_drive_keeps_its_flanges_in_place(client):
    r = _get(client, FLANGED)
    _check_tree(r.data, asm.manifest(FLANGED))


@needs_assemble
def test_the_ring_is_named_as_its_label(client):
    """"DIN 471 26 × 1.2", not "Ã\\x97": small_step a99954e double-encoded an
    extruded part's non-ASCII PRODUCT name; fixed by c03179c."""
    r = _get(client, dict(SPLINE, parts='sh1'))
    pdef, _, _ = _tree(r.data)
    assert any(n.startswith('DIN 471') and '×' in n for n in pdef.values()), sorted(pdef.values())
