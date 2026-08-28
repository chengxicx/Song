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
