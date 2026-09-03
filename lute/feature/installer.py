"""
On-demand installation of plugins from the Settings page.

Handles both plugin kinds:

* feature plugins (``lute.plugin.feature``) — UI modules like the story
  generator, installed from a pip spec (PyPI name, local path, URL);
* parser plugins (``lute.plugin.parse``) — language parsers such as
  lute3-cantonese / lute3-thai, which Lute installs on demand when a
  predefined language is added.

Installing makes the entry point importable via ``importlib.metadata``,
but the running process only discovers entry points once at startup, so a
restart is required for a freshly-installed plugin to appear in the UI.
"""

import os
import subprocess
import sys

from .loader import _iter_entry_points

PIP_TIMEOUT_SECONDS = 300

# parser_type -> PyPI package name (mirrors lute.parse.plugin_installer).
PARSER_PACKAGES = {
    "lute_mandarin": "lute3-mandarin",
    "lute_thai": "lute3-thai",
    "lute_khmer": "lute3-khmer",
    "lute_cantonese": "lute3-cantonese",
}


def _package_name_for(ep):
    """Best-guess pip distribution name for an entry point."""
    try:
        if ep.dist is not None and ep.dist.metadata:
            dist_name = ep.dist.metadata.get("Name")
            if dist_name:
                return dist_name
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        return ep.value.split(":")[0].split(".")[0]
    except Exception:  # pylint: disable=broad-except
        return ep.name


def installed_plugin_packages():
    """
    Return ``{name: {"type": "feature"|"parser", "package": dist}}`` for
    every discovered plugin entry point.
    """
    out = {}
    for kind, group in (("feature", "lute.plugin.feature"), ("parser", "lute.plugin.parse")):
        for ep in _iter_entry_points(group):
            out[ep.name] = {"type": kind, "package": _package_name_for(ep)}
    return out


def package_for_entry_point(name):
    """pip distribution name for a plugin entry-point name, or None."""
    return installed_plugin_packages().get(name, {}).get("package")


def _local_plugins_dirs():
    "Candidate 'plugins' directories, best guess first (same as parser installer)."
    dirs = []
    env_dir = os.environ.get("LUTE_PLUGINS_DIR")
    if env_dir:
        dirs.append(env_dir)
    dirs.append(
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "plugins")
        )
    )
    dirs.append(os.path.abspath(os.path.join(os.getcwd(), "plugins")))
    return [d for d in dirs if os.path.isdir(d)]


def installed_plugin_names():
    """Entry-point names currently discovered for both plugin groups."""
    names = set()
    for group in ("lute.plugin.feature", "lute.plugin.parse"):
        for ep in _iter_entry_points(group):
            names.add(ep.name)
    return sorted(names)


def _normalize_spec(spec):
    """
    Tolerate the ways a user might paste a local plugin location:

    * file:///path/to/plugin        → /path/to/plugin
    * file:///path/to/pyproject.toml → /path/to           (pip needs the dir)
    * /path/to/pyproject.toml       → /path/to
    * /path/to/setup.py             → /path/to
    * /path/to/plugin               → unchanged (dir or built artifact)
    * lute3-storygen                → unchanged (PyPI name)
    """
    spec = (spec or "").strip()
    if not spec:
        return spec
    if spec.startswith("file://"):
        spec = spec[len("file://"):]
    # strip trailing slashes
    spec = spec.rstrip("/")
    base = os.path.basename(spec)
    if base in ("pyproject.toml", "setup.py", "setup.cfg"):
        spec = os.path.dirname(spec) or spec
    return spec


def install_plugin(spec):
    """
    Install a plugin from a pip ``spec`` (PyPI name, local dir, file:// URL,
    or pip URL).  Parser plugins (lute3-cantonese, lute3-thai, ...) are
    detected by name and installed through the parser installer so they get
    registered immediately; anything else is treated as a feature plugin.

    Returns (ok, message).
    """
    spec = _normalize_spec(spec)
    if not spec:
        return False, "请输入 pip 包名或本地插件路径"

    # Detect a parser plugin by its PyPI name or local source-dir name.
    parser_type = _parser_type_for_spec(spec)
    if parser_type:
        from lute.parse import plugin_installer as pi

        return pi.ensure_parser_available(parser_type)

    # If the spec is a local path that exists, hand pip the directory
    # (pip installs from source dirs directly).
    pip_arg = spec
    if not spec.startswith(("http://", "https://", "git+")):
        candidate = os.path.abspath(os.path.expanduser(spec))
        if os.path.isdir(candidate):
            pip_arg = candidate

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_arg, "--quiet"],
            capture_output=True,
            text=True,
            timeout=PIP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip install 超时（{PIP_TIMEOUT_SECONDS}s）"
    except OSError as exc:
        return False, f"无法运行 pip：{exc}"

    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        return False, f"pip install 失败：\n{output.strip()[-1500:]}"

    # The installed plugin is now importable, but won't be wired into the
    # running app until the user restarts Lute (entry points are scanned
    # once at startup).
    return True, f"安装成功：" + pip_arg


def _parser_type_for_spec(spec):
    """
    Return the parser_type (e.g. 'lute_cantonese') for a spec if it names a
    known parser plugin, else None.

    Accepts the PyPI name (lute3-cantonese) or the local source-dir name
    (lute-cantonese), as a path or bare name.
    """
    base = os.path.basename(spec.rstrip("/")) if spec else ""
    pypi = spec if "/" not in spec else base
    for parser_type, package in PARSER_PACKAGES.items():
        if pypi in (package, package.replace("lute3-", "lute-")):
            return parser_type
    return None


def install_feature_plugin(spec):
    """Backwards-compatible alias for :func:`install_plugin`."""
    return install_plugin(spec)


def uninstall_plugin(name, kind="feature"):
    """
    Uninstall a plugin by its entry-point name.

    ``kind`` is 'feature' or 'parser'; it selects how the entry point maps
    to a pip distribution.  Returns (ok, message).  A restart is required
    for the UI to update.
    """
    packages = installed_plugin_packages()
    info = packages.get(name)
    if info:
        package = info.get("package")
    else:
        # Fall back to the parser mapping by name.
        package = _pypi_name_for_parser(name)
    if not package:
        return False, f"未找到插件 '{name}' 对应的包"

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", package],
            capture_output=True,
            text=True,
            timeout=PIP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip uninstall 超时（{PIP_TIMEOUT_SECONDS}s）"
    except OSError as exc:
        return False, f"无法运行 pip：{exc}"

    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        return False, f"pip uninstall 失败：\n{output.strip()[-1500:]}"

    return True, f"已卸载 {package}，请重启 Lute 生效"


def _pypi_name_for_parser(name):
    """PyPI package name for a parser entry-point name, or None."""
    for parser_type, package in PARSER_PACKAGES.items():
        if name == parser_type or name == package.replace("lute3-", "lute-"):
            return package
    return None


def uninstall_feature_plugin(name):
    """Backwards-compatible alias for :func:`uninstall_plugin`."""
    return uninstall_plugin(name, kind="feature")