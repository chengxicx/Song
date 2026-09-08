"""
On-demand installation of parser plugins.

Some language parsers ship as separate pip packages ("plugins",
e.g. lute3-cantonese).  When a user loads a predefined language whose
parser isn't installed yet, Lute can install the plugin at that
moment instead of requiring a manual install and restart.

Only parser types listed in PLUGIN_PACKAGES are ever installed,
and each is installed from the first available source: a local
plugin checkout (repo "plugins/" directory or LUTE_PLUGINS_DIR),
falling back to PyPI.
"""

import os
import subprocess
import sys

from lute.parse.registry import init_parser_plugins, is_supported

# parser_type -> plugin package name on PyPI.
PLUGIN_PACKAGES = {
    "lute_mandarin": "lute3-mandarin",
    "lute_thai": "lute3-thai",
    "lute_khmer": "lute3-khmer",
    "lute_cantonese": "lute3-cantonese",
    "lute_korean": "lute3-korean",
}

# Language names (lowercased) served by each auto-installable parser plugin.
# Mirrors the `languages()` classmethod each parser plugin declares, so that
# startup can heal a language that fell back to a generic parser (e.g.
# Korean saved as "spacedel") even though its parser_type no longer names the
# plugin directly.
PLUGIN_LANGUAGE_NAMES = {
    "lute_mandarin": {"mandarin", "mandarin chinese", "官话", "官話", "普通话", "普通話", "中文", "汉语", "漢語"},
    "lute_thai": {"thai", "ไทย", "泰语", "泰語"},
    "lute_khmer": {"khmer", "크메르어", "高棉语", "高棉語"},
    "lute_cantonese": {"cantonese", "cantonese chinese", "廣東話", "广东话", "粤语", "粵語"},
    "lute_korean": {"korean", "한국어", "한국말", "韩语", "韓語"},
}

PIP_TIMEOUT_SECONDS = 300


def plugin_package_for(parser_type):
    "PyPI package name for the given parser type, or None."
    return PLUGIN_PACKAGES.get(parser_type or "")


def is_auto_installable(parser_type):
    "True if the parser type has a known plugin and isn't installed yet."
    pt = parser_type or ""
    return pt in PLUGIN_PACKAGES and not is_supported(pt)


def _local_plugins_dirs():
    "Candidate 'plugins' directories, best guess first."
    dirs = []
    env_dir = os.environ.get("LUTE_PLUGINS_DIR")
    if env_dir:
        dirs.append(env_dir)
    # Editable install: lute/parse/plugin_installer.py -> repo root.
    dirs.append(
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "plugins")
        )
    )
    # Service started from the repo root.
    dirs.append(os.path.abspath(os.path.join(os.getcwd(), "plugins")))
    return [d for d in dirs if os.path.isdir(d)]


def find_local_plugin_dir(parser_type):
    "Find a source checkout of the plugin for parser_type, or None."
    package = plugin_package_for(parser_type)
    if not package:
        return None
    # Package lute3-cantonese lives in a "lute-cantonese" source dir.
    plugin_dir_name = package.replace("lute3-", "lute-")
    for base in _local_plugins_dirs():
        candidate = os.path.join(base, plugin_dir_name)
        if os.path.exists(os.path.join(candidate, "pyproject.toml")):
            return candidate
    return None


def ensure_parser_available(parser_type):
    """
    Ensure the parser for parser_type is usable, installing its
    plugin package if needed.

    Returns (ok, message).  ok is True if the parser is (now)
    registered and supported; message describes what happened.
    """
    pt = parser_type or ""
    if is_supported(pt):
        return True, "already installed"

    package = plugin_package_for(pt)
    if not package:
        return False, f"No installable plugin known for parser type '{pt}'"

    local_dir = find_local_plugin_dir(pt)
    spec = local_dir if local_dir else package
    source = "local plugins directory" if local_dir else "PyPI"

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", spec],
            capture_output=True,
            text=True,
            timeout=PIP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip install of {package} timed out after {PIP_TIMEOUT_SECONDS}s"
    except OSError as e:
        return False, f"Could not run pip: {e}"

    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        return False, f"pip install of {package} failed:\n{output.strip()[-2000:]}"

    # Newly installed entry points only appear after a re-scan.
    init_parser_plugins()
    if not is_supported(pt):
        return (
            False,
            f"Installed {package} but parser '{pt}' is still unavailable; "
            "a restart may be needed",
        )
    return True, f"Installed {package} from {source}"


def _plugin_for_language_name(lang_name):
    """
    Return the parser_type of the plugin that serves the given language
    name, or None if none match.
    """
    nl = (lang_name or "").strip().lower()
    if not nl:
        return None
    for pt, names in PLUGIN_LANGUAGE_NAMES.items():
        if nl in names:
            return pt
    return None


def ensure_existing_language_parsers(session):
    """
    Install missing whitelisted parser plugins for languages already in the DB.

    A language created before its parser was pluginized (e.g. Korean) never
    triggers the install-on-predefined-load path, and there is no manual
    install option, so those languages would otherwise be stuck with an
    unusable parser.  Called at startup so existing languages heal
    automatically.  Returns a list of (language_name, ok, message).

    Two situations are handled:

    * The language's parser_type already names a whitelisted plugin that
      isn't installed (normal auto-install path).  The plugin is installed.

    * The language fell back to a generic placeholder parser (empty or
      "spacedel") yet its name matches a known plugin (e.g. Korean saved as
      "spacedel").  The plugin is installed AND the language's parser_type is
      restored to point at it, so it becomes usable again without manual
      re-selection.
    """
    # Imported inside the function to avoid a circular import at module load.
    from lute.models.language import Language

    results = []
    touched_parser = False
    for lang in session.query(Language).all():
        pt = (lang.parser_type or "").strip()
        # Normal path: the language already names a whitelisted plugin.
        if pt and is_auto_installable(pt):
            results.append((lang.name, *ensure_parser_available(pt)))
            continue
        # Heal path: a generic/placeholder parser whose name maps to a plugin.
        matched = None
        if pt in ("", "spacedel"):
            matched = _plugin_for_language_name(lang.name)
        if matched:
            ok, message = ensure_parser_available(matched)
            results.append((lang.name, ok, message))
            if ok and lang.parser_type != matched:
                lang.parser_type = matched
                touched_parser = True
    if touched_parser:
        session.commit()
    return results
