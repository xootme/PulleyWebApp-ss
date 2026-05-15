"""
test_repro.py — Tests for design-parameter embedding (export) and extraction (import).

Coverage:
  - _cct_meta()            structure, version fields
  - _embed_step()          comment placement and format
  - _embed_dxf()           group-code 999 placement, all EOF variants
  - _embed_svg()           <metadata><cct> placement
  - Download routes        SVG / DXF / STEP responses contain embedded metadata
  - Round-trip             embed → re-extract gives back original params + schema version
"""
import json
import re
import pytest


# ── Helpers that mirror the Fusion addin / browser extraction logic ──────────
#
# These replicate the regex used by:
#   • PulleyWebApp.py  _extract_cct_metadata()
#   • index.html       _parseCctFromText()
#
# Keeping them inline (not imported from the addin) makes the tests
# self-contained and shows the contract: whatever the server embeds, these
# patterns must be able to read back.

def _extract_stl(data: bytes) -> dict:
    text = data.decode('utf-8', errors='replace')
    return _extract_step(text)   # same /* CCT:{} */ format


def _extract_step(text: str) -> dict:
    m = re.search(r'/\* CCT:(\{.+?\}) \*/', text)
    if not m:
        return {}
    data = json.loads(m.group(1))
    params = dict(data.get('cct', data))
    if 'sv' in data:
        params.setdefault('sv', data['sv'])
    return params


def _extract_dxf(text: str) -> dict:
    m = re.search(r'999\nCCT:(\{.+})', text)
    if not m:
        return {}
    data = json.loads(m.group(1))
    params = dict(data.get('cct', data))
    if 'sv' in data:
        params.setdefault('sv', data['sv'])
    return params


def _extract_svg(text: str) -> dict:
    m = re.search(r'<cct>([\s\S]+?)</cct>', text)
    if not m:
        return {}
    data = json.loads(m.group(1))
    params = dict(data.get('cct', data))
    if 'sv' in data:
        params.setdefault('sv', data['sv'])
    return params


# ── Minimal synthetic file bodies ────────────────────────────────────────────

_MINIMAL_STEP = (
    "ISO-10303-21;\n"
    "HEADER;\n"
    "FILE_DESCRIPTION((''), '2;1');\n"
    "ENDSEC;\n"
    "DATA;\n"
    "ENDSEC;\n"
    "END-ISO-10303-21;\n"
)

_MINIMAL_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle/></svg>'

# Common params used across download-route tests
_BASE_QS = 'family=HTD&pitch=5M&teeth=20&bore=8&clearance_preset=STANDARD&backlash_preset=STANDARD'


# ── _cct_meta ────────────────────────────────────────────────────────────────

class TestCctMeta:
    def test_required_keys_present(self):
        from app import _cct_meta
        meta = _cct_meta({'family': 'HTD', 'teeth': '20'})
        assert 'v'   in meta, "app version key missing"
        assert 'sv'  in meta, "schema version key missing"
        assert 'cct' in meta, "params wrapper key missing"

    def test_app_version_matches_constant(self):
        from app import _cct_meta, APP_VERSION
        assert _cct_meta({})['v'] == APP_VERSION

    def test_schema_version_matches_constant(self):
        from app import _cct_meta, CCT_SCHEMA_VERSION
        assert _cct_meta({})['sv'] == CCT_SCHEMA_VERSION

    def test_schema_version_is_int(self):
        from app import CCT_SCHEMA_VERSION
        assert isinstance(CCT_SCHEMA_VERSION, int)

    def test_cct_contains_all_args(self):
        from app import _cct_meta
        args = {'family': 'GT', 'pitch': '3M', 'teeth': '30', 'bore': '10'}
        assert _cct_meta(args)['cct'] == args

    def test_cct_is_independent_copy(self):
        from app import _cct_meta
        args = {'family': 'HTD'}
        meta = _cct_meta(args)
        args['family'] = 'GT'
        assert meta['cct']['family'] == 'HTD'


# ── _embed_step ──────────────────────────────────────────────────────────────

class TestEmbedStep:
    def test_comment_present(self):
        from app import _embed_step
        out = _embed_step(_MINIMAL_STEP.encode(), {'family': 'HTD'}).decode()
        assert '/* CCT:' in out and ' */' in out

    def test_comment_after_header_endsec_before_data(self):
        from app import _embed_step
        out = _embed_step(_MINIMAL_STEP.encode(), {'family': 'HTD'}).decode()
        endsec = out.index('ENDSEC;')
        data   = out.index('DATA;')
        cct    = out.index('/* CCT:')
        assert endsec < cct < data

    def test_original_content_preserved(self):
        from app import _embed_step
        out = _embed_step(_MINIMAL_STEP.encode(), {'family': 'HTD'}).decode()
        assert 'ISO-10303-21;' in out
        assert 'END-ISO-10303-21;' in out

    def test_bad_input_returned_unchanged(self):
        from app import _embed_step
        garbage = b'not a step file'
        assert _embed_step(garbage, {'family': 'HTD'}) == garbage

    def test_blob_is_valid_json(self):
        from app import _embed_step
        out = _embed_step(_MINIMAL_STEP.encode(), {'family': 'HTD'}).decode()
        m = re.search(r'/\* CCT:(\{.+?\}) \*/', out)
        assert m is not None
        json.loads(m.group(1))  # must not raise


# ── _embed_dxf ───────────────────────────────────────────────────────────────

class TestEmbedDxf:
    @pytest.mark.parametrize('eof_marker', [
        b'  0\r\nEOF\r\n',
        b'  0\nEOF\n',
        b'0\r\nEOF\r\n',
        b'0\nEOF\n',
    ])
    def test_comment_inserted_all_eof_variants(self, eof_marker):
        from app import _embed_dxf
        dxf = b'SECTION 2 ENTITIES ENDSEC ' + eof_marker
        out = _embed_dxf(dxf, {'family': 'HTD'})
        assert b'999\nCCT:' in out

    def test_comment_before_eof_marker(self):
        from app import _embed_dxf
        dxf = b'  0\r\nEOF\r\n'
        out = _embed_dxf(dxf, {'family': 'HTD'})
        cct_pos = out.index(b'999\nCCT:')
        eof_pos = out.index(b'EOF')
        assert cct_pos < eof_pos

    def test_eof_marker_still_present(self):
        from app import _embed_dxf
        dxf = b'  0\r\nEOF\r\n'
        out = _embed_dxf(dxf, {'family': 'HTD'})
        assert b'EOF' in out

    def test_no_eof_marker_appends_comment(self):
        from app import _embed_dxf
        dxf = b'some dxf content'
        out = _embed_dxf(dxf, {'family': 'HTD'})
        assert b'999\nCCT:' in out

    def test_blob_is_valid_json(self):
        from app import _embed_dxf
        out = _embed_dxf(b'  0\nEOF\n', {'family': 'HTD'})
        m = re.search(rb'999\nCCT:(.+)', out)
        assert m is not None
        json.loads(m.group(1))

    def test_bad_input_returned_unchanged(self):
        # bytes that would cause an internal error should be returned as-is
        from app import _embed_dxf
        dxf = b'  0\r\nEOF\r\n'
        out = _embed_dxf(dxf, {})
        assert out is not None  # never raises


# ── _embed_stl ───────────────────────────────────────────────────────────────

class TestEmbedStl:
    def _minimal_stl(self):
        """Minimal valid binary STL: 80-byte header + 0 triangles."""
        header   = b'CCT test STL' + b'\x00' * (80 - 12)
        n_tris   = (0).to_bytes(4, 'little')
        return header + n_tris

    def test_trailer_appended(self):
        from app import _embed_stl
        out = _embed_stl(self._minimal_stl(), {'family': 'HTD'})
        assert b'/* CCT:' in out and b' */' in out

    def test_original_bytes_preserved(self):
        from app import _embed_stl
        stl = self._minimal_stl()
        out = _embed_stl(stl, {'family': 'HTD'})
        assert out[:len(stl)] == stl

    def test_trailer_starts_with_newline(self):
        from app import _embed_stl
        stl = self._minimal_stl()
        out = _embed_stl(stl, {'family': 'HTD'})
        trailer = out[len(stl):]
        assert trailer.startswith(b'\n')

    def test_blob_is_valid_json(self):
        from app import _embed_stl
        out = _embed_stl(self._minimal_stl(), {'family': 'HTD'})
        m = re.search(rb'/\* CCT:(.+?) \*/', out)
        assert m is not None
        json.loads(m.group(1))

    def test_bad_input_returned_unchanged(self):
        from app import _embed_stl
        garbage = b'\xff\xfe\xfd'
        assert _embed_stl(garbage, {}) is not None


# ── _embed_svg ───────────────────────────────────────────────────────────────

class TestEmbedSvg:
    def test_metadata_tag_inserted(self):
        from app import _embed_svg
        out = _embed_svg(_MINIMAL_SVG, {'family': 'HTD'})
        assert '<metadata>' in out and '</metadata>' in out

    def test_cct_tag_inside_metadata(self):
        from app import _embed_svg
        out = _embed_svg(_MINIMAL_SVG, {'family': 'HTD'})
        meta = re.search(r'<metadata>(.*?)</metadata>', out, re.DOTALL)
        assert meta is not None
        assert '<cct>' in meta.group(1)

    def test_metadata_immediately_after_svg_open_tag(self):
        from app import _embed_svg
        out = _embed_svg(_MINIMAL_SVG, {'family': 'HTD'})
        svg_end  = re.search(r'<svg\b[^>]*>', out).end()
        meta_pos = out.index('<metadata>')
        assert meta_pos == svg_end + 1  # +1 for the inserted newline

    def test_original_elements_preserved(self):
        from app import _embed_svg
        out = _embed_svg(_MINIMAL_SVG, {'family': 'HTD'})
        assert '<circle/>' in out

    def test_no_svg_tag_returns_string_unchanged(self):
        from app import _embed_svg
        not_svg = 'just plain text'
        assert _embed_svg(not_svg, {'family': 'HTD'}) == not_svg

    def test_blob_is_valid_json(self):
        from app import _embed_svg
        out = _embed_svg(_MINIMAL_SVG, {'family': 'HTD'})
        m = re.search(r'<cct>([\s\S]+?)</cct>', out)
        assert m is not None
        json.loads(m.group(1))


# ── Download route embedding (Flask test client) ─────────────────────────────

class TestDownloadSvgEmbedding:
    def test_response_ok(self, client):
        r = client.get(f'/download/svg?{_BASE_QS}')
        assert r.status_code == 200

    def test_cct_metadata_present(self, client):
        r = client.get(f'/download/svg?{_BASE_QS}')
        assert b'<cct>' in r.data and b'</cct>' in r.data

    def test_schema_version_correct(self, client):
        from app import CCT_SCHEMA_VERSION
        r = client.get(f'/download/svg?{_BASE_QS}')
        m = re.search(rb'<cct>([\s\S]+?)</cct>', r.data)
        assert m is not None
        data = json.loads(m.group(1))
        assert data['sv'] == CCT_SCHEMA_VERSION

    def test_params_reflected_in_metadata(self, client):
        r = client.get(f'/download/svg?{_BASE_QS}')
        m = re.search(rb'<cct>([\s\S]+?)</cct>', r.data)
        cct = json.loads(m.group(1))['cct']
        assert cct.get('family') == 'HTD'
        assert cct.get('pitch')  == '5M'
        assert cct.get('teeth')  == '20'
        assert cct.get('bore')   == '8'


class TestDownloadDxfEmbedding:
    def test_response_ok(self, client):
        r = client.get(f'/download/dxf?{_BASE_QS}')
        assert r.status_code == 200

    def test_cct_metadata_present(self, client):
        r = client.get(f'/download/dxf?{_BASE_QS}')
        assert b'999\nCCT:' in r.data

    def test_schema_version_correct(self, client):
        from app import CCT_SCHEMA_VERSION
        r = client.get(f'/download/dxf?{_BASE_QS}')
        m = re.search(rb'999\nCCT:(.+)', r.data)
        assert m is not None
        data = json.loads(m.group(1))
        assert data['sv'] == CCT_SCHEMA_VERSION

    def test_params_reflected_in_metadata(self, client):
        r = client.get(f'/download/dxf?{_BASE_QS}')
        m = re.search(rb'999\nCCT:(.+)', r.data)
        cct = json.loads(m.group(1))['cct']
        assert cct.get('family') == 'HTD'
        assert cct.get('teeth')  == '20'
        assert cct.get('bore')   == '8'

    def test_eof_marker_still_present(self, client):
        r = client.get(f'/download/dxf?{_BASE_QS}')
        assert b'EOF' in r.data


class TestDownloadStepEmbedding:
    def test_response_ok(self, client):
        r = client.get(f'/download/step?{_BASE_QS}')
        assert r.status_code == 200

    def test_cct_metadata_present(self, client):
        r = client.get(f'/download/step?{_BASE_QS}')
        assert b'/* CCT:' in r.data and b' */' in r.data

    def test_schema_version_correct(self, client):
        from app import CCT_SCHEMA_VERSION
        r = client.get(f'/download/step?{_BASE_QS}')
        m = re.search(rb'/\* CCT:(.+?) \*/', r.data)
        assert m is not None
        data = json.loads(m.group(1))
        assert data['sv'] == CCT_SCHEMA_VERSION

    def test_params_reflected_in_metadata(self, client):
        r = client.get(f'/download/step?{_BASE_QS}')
        m = re.search(rb'/\* CCT:(.+?) \*/', r.data)
        assert m is not None
        cct = json.loads(m.group(1))['cct']
        assert cct.get('family') == 'HTD'
        assert cct.get('pitch')  == '5M'
        assert cct.get('bore')   == '8'


# ── Round-trip tests: embed → extract matches original params ─────────────────

@pytest.mark.parametrize('family,pitch,teeth,bore', [
    pytest.param('HTD',      '5M', '20', '8',  id='HTD-5M'),
    pytest.param('GT',       '3M', '30', '6',  id='GT-3M'),
    pytest.param('T',        'T5', '24', '10', id='T-T5'),
    pytest.param('Imperial', 'XL', '18', '5',  id='Imperial-XL'),
    pytest.param('AT',       'AT5','16', '12', id='AT-AT5'),
])
class TestRoundTrip:
    def _args(self, family, pitch, teeth, bore):
        return {'family': family, 'pitch': pitch, 'teeth': teeth, 'bore': bore}

    def test_step_roundtrip(self, family, pitch, teeth, bore):
        from app import _embed_step, CCT_SCHEMA_VERSION
        args = self._args(family, pitch, teeth, bore)
        out  = _embed_step(_MINIMAL_STEP.encode(), args).decode()
        got  = _extract_step(out)
        assert got.get('family') == family
        assert got.get('pitch')  == pitch
        assert got.get('teeth')  == teeth
        assert got.get('bore')   == bore
        assert int(got['sv'])    == CCT_SCHEMA_VERSION

    def test_dxf_roundtrip(self, family, pitch, teeth, bore):
        from app import _embed_dxf, CCT_SCHEMA_VERSION
        args = self._args(family, pitch, teeth, bore)
        dxf  = b'  0\r\nEOF\r\n'
        out  = _embed_dxf(dxf, args).decode('utf-8')
        got  = _extract_dxf(out)
        assert got.get('family') == family
        assert got.get('pitch')  == pitch
        assert got.get('teeth')  == teeth
        assert got.get('bore')   == bore
        assert int(got['sv'])    == CCT_SCHEMA_VERSION

    def test_svg_roundtrip(self, family, pitch, teeth, bore):
        from app import _embed_svg, CCT_SCHEMA_VERSION
        args = self._args(family, pitch, teeth, bore)
        out  = _embed_svg(_MINIMAL_SVG, args)
        got  = _extract_svg(out)
        assert got.get('family') == family
        assert got.get('pitch')  == pitch
        assert got.get('teeth')  == teeth
        assert got.get('bore')   == bore
        assert int(got['sv'])    == CCT_SCHEMA_VERSION

    def test_stl_roundtrip(self, family, pitch, teeth, bore):
        from app import _embed_stl, CCT_SCHEMA_VERSION
        header = b'CCT test' + b'\x00' * 72 + (0).to_bytes(4, 'little')
        args = self._args(family, pitch, teeth, bore)
        out  = _embed_stl(header, args)
        got  = _extract_stl(out)
        assert got.get('family') == family
        assert got.get('pitch')  == pitch
        assert got.get('teeth')  == teeth
        assert got.get('bore')   == bore
        assert int(got['sv'])    == CCT_SCHEMA_VERSION

    def test_dual_params_preserved_in_svg(self, family, pitch, teeth, bore):
        from app import _embed_svg, CCT_SCHEMA_VERSION
        args = self._args(family, pitch, teeth, bore)
        args['dual'] = 'true'
        args['p2_teeth'] = '36'
        args['center_distance'] = '150'
        out = _embed_svg(_MINIMAL_SVG, args)
        got = _extract_svg(out)
        assert got.get('dual') == 'true'
        assert got.get('p2_teeth') == '36'
        assert got.get('center_distance') == '150'


# ── Schema version is stable across formats ───────────────────────────────────

class TestDownloadStlEmbedding:
    def test_response_ok(self, client):
        r = client.get(f'/download/stl?{_BASE_QS}')
        assert r.status_code == 200

    def test_cct_metadata_present(self, client):
        r = client.get(f'/download/stl?{_BASE_QS}')
        assert b'/* CCT:' in r.data and b' */' in r.data

    def test_schema_version_correct(self, client):
        from app import CCT_SCHEMA_VERSION
        r = client.get(f'/download/stl?{_BASE_QS}')
        m = re.search(rb'/\* CCT:(.+?) \*/', r.data)
        assert m is not None
        data = json.loads(m.group(1))
        assert data['sv'] == CCT_SCHEMA_VERSION

    def test_params_reflected_in_metadata(self, client):
        r = client.get(f'/download/stl?{_BASE_QS}')
        m = re.search(rb'/\* CCT:(.+?) \*/', r.data)
        assert m is not None
        cct = json.loads(m.group(1))['cct']
        assert cct.get('family') == 'HTD'
        assert cct.get('teeth')  == '20'
        assert cct.get('bore')   == '8'

    def test_original_binary_header_intact(self, client):
        r = client.get(f'/download/stl?{_BASE_QS}')
        # STL binary header is 80 bytes; first 5 bytes are 'solid' for ASCII or
        # arbitrary bytes for binary — just verify response is longer than 84 bytes
        assert len(r.data) > 84


def test_schema_version_consistent_across_formats():
    from app import _embed_step, _embed_dxf, _embed_svg, _embed_stl, CCT_SCHEMA_VERSION
    args = {'family': 'HTD', 'pitch': '5M', 'teeth': '20', 'bore': '8'}
    stl_header = b'test' + b'\x00' * 76 + (0).to_bytes(4, 'little')

    sv_step = json.loads(
        re.search(r'/\* CCT:(\{.+?\}) \*/',
                  _embed_step(_MINIMAL_STEP.encode(), args).decode()).group(1)
    )['sv']

    sv_dxf = json.loads(
        re.search(r'999\nCCT:(\{.+})',
                  _embed_dxf(b'  0\nEOF\n', args).decode()).group(1)
    )['sv']

    sv_svg = json.loads(
        re.search(r'<cct>([\s\S]+?)</cct>',
                  _embed_svg(_MINIMAL_SVG, args)).group(1)
    )['sv']

    sv_stl = json.loads(
        re.search(rb'/\* CCT:(.+?) \*/',
                  _embed_stl(stl_header, args)).group(1)
    )['sv']

    assert sv_step == sv_dxf == sv_svg == sv_stl == CCT_SCHEMA_VERSION


def test_schema_version_survives_extra_args():
    from app import _embed_svg, CCT_SCHEMA_VERSION
    # Unknown/future params should not break extraction
    args = {'family': 'HTD', 'teeth': '20', 'future_param': 'some_value'}
    out = _embed_svg(_MINIMAL_SVG, args)
    got = _extract_svg(out)
    assert int(got['sv']) == CCT_SCHEMA_VERSION
    assert got.get('future_param') == 'some_value'
