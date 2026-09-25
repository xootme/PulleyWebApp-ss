"""The two deploy allow-lists: .dockerignore (what goes into the image) and
.gcloudignore (what `gcloud run deploy --source .` uploads to be built).
A file added to one but not the other either bloats every upload or is
missing from the build — the latter only shows up as a broken deploy."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _allowed(name):
    lines = (ROOT / name).read_text(encoding='utf-8').splitlines()
    return {l[1:].rstrip('/').removesuffix('/**') for l in lines if l.startswith('!')}


def test_upload_list_covers_the_image_list():
    missing = _allowed('.dockerignore') - _allowed('.gcloudignore')
    assert not missing, f'add to .gcloudignore: {sorted(missing)}'


def test_upload_list_has_the_build_files():
    assert {'Dockerfile', '.dockerignore'} <= _allowed('.gcloudignore')


def test_every_top_level_module_app_imports_is_shipped():
    # A new helper module next to app.py must be on both lists.
    import re
    src = (ROOT / 'app.py').read_text(encoding='utf-8')
    local = {p.stem for p in ROOT.glob('*.py')}
    imported = set(re.findall(r'^\s*(?:from|import)\s+(\w+)', src, re.M)) & local
    imported.discard('app')
    shipped = {n.removesuffix('.py') for n in _allowed('.dockerignore')}
    assert imported <= shipped, f'not shipped: {sorted(imported - shipped)}'
