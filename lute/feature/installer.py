"""
On-demand installation of feature plugins from the Settings page.

Mirrors ``lute.parse.plugin_installer`` so feature plugins use the
familiar path: a pip spec (PyPI package name, or a local source-checkout
path) entered in the Settings "功能插件" panel.

Installing makes the entry point importable via ``importlib.metadata``,
but the running process only discovers entry points once at startup, so a
restart is required for a freshly-installed plugin's menu/routes to appear.
"""

import os
import subprocess
import sys

from .loader import _iter_feature_entry_points

PIP_TIMEOUT_SECONDS = 300


def installed_feature_packages():
    """
    Return a dict of entry_point_name -> pip package name (distribution).

    Covers same-name "覆盖更新": reinstalling the same package simply
    replaces it, so install always overwrites.  The package name lets a
    user uninstall a plugin whose distribution was installed from.
    """
    out = {}
    for ep in _iter_feature_entry_points():
        pkg = None
        try:
            pkg = ep.value.split(":")[0].split(".")[0]  # module, best guess
        except Exception:  # pylint: disable=broad-except
            pkg = None
        # Prefer the declared distribution name when available.
        try:
            if ep.dist is not None and ep.dist.metadata:
                dist_name = ep.dist.metadata.get("Name")
                if dist_name:
                    pkg = dist_name
        except Exception:  # pylint: disable=broad-except
            pass
        out[ep.name] = pkg or ep.name
    return out


def package_for_entry_point(name):
    """pip distribution name for an entry-point name, or None if unknown."""
    return installed_feature_packages().get(name)


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


def installed_feature_names():
    """Entry-point names currently discovered for 'lute.plugin.feature'."""
    return sorted(ep.name for ep in _iter_feature_entry_points())


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


def install_feature_plugin(spec):
    """
    Install a feature plugin from a pip ``spec``.

    ``spec`` may be a PyPI package name (e.g. 'lute3-storygen'), a local
    source directory path, a file:// URL, or a pip URL.

    Returns (ok, message).
    """
    spec = _normalize_spec(spec)
    if not spec:
        return False, "请输入 pip 包名或本地插件路径"

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


def uninstall_feature_plugin(name):
    """
    Uninstall a feature plugin by its entry-point name.

    Returns (ok, message).  A restart is required for the UI to update.
    """
    package = package_for_entry_point(name)
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