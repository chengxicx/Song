"""
scripts/ensure_nginx_epub_alias.py -- the deploy-time nginx guard.

Two properties matter and both were broken by a placeholder constant:
the check must recognise an alias that already points at the data
directory (the app root differs per install), and the block it inserts
must carry the real absolute path rather than the literal
<prod-app-root> text -- nginx would reject that and every deploy would
report a missing alias.
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SCRIPT = os.path.join(_ROOT, "scripts", "ensure_nginx_epub_alias.py")


def _load():
    spec = importlib.util.spec_from_file_location("ensure_nginx_epub_alias", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recognises_an_alias_under_any_app_root():
    mod = _load()
    for alias in (
        "alias /opt/lute/lute_data/static/epub/;",
        "alias /srv/song/lute_data/static/epub;",
    ):
        source = "location /static/epub/ {\n    " + alias + "\n}"
        assert mod.ALIAS_RE.search(source), alias


def test_does_not_accept_a_code_tree_alias():
    mod = _load()
    assert not mod.ALIAS_RE.search("alias /opt/lute/lute/static/epub/;")


def test_inserted_block_uses_the_absolute_path():
    mod = _load()
    block = mod.render_block()
    assert "<prod-app-root>" not in block
    assert f"alias {mod.APP_ROOT}/lute_data/static/epub/;" in block
