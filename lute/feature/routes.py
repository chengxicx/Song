"""
Routes for the feature-plugin system.

Two surfaces are served here:

* HTMX fragments pulled into existing pages (the Book-menu fragment).
* A dedicated standalone "Plugin" management page under Setting >
  Plugin (/__feature/panel), with install / uninstall and the list of
  discovered plugins.

The plugin management page uses English so it reads naturally next to
Lute's other English UI.
"""

from flask import Blueprint, jsonify, render_template, request

from .registry import get_registry
from . import installer

bp = Blueprint(
    "lute_feature",
    __name__,
    url_prefix="/_feature",
    template_folder="templates",
)


def _find_package_root(tmp):
    """
    Locate the directory that contains the package metadata (pyproject.toml
    or setup.py) inside an uploaded folder.  The browser preserves the
    top-level folder name, so metadata may sit in ``tmp`` itself or one
    level down.  Returns the absolute path, or None.
    """
    import os

    for marker in ("pyproject.toml", "setup.py"):
        if os.path.isfile(os.path.join(tmp, marker)):
            return tmp
    for entry in os.listdir(tmp):
        sub = os.path.join(tmp, entry)
        if os.path.isdir(sub):
            for marker in ("pyproject.toml", "setup.py"):
                if os.path.isfile(os.path.join(sub, marker)):
                    return sub
    return None


@bp.get("/menu/<parent>")
def menu_fragment(parent):
    """Render the menu items contributed for the given top-level menu.

    ``parent`` is the id of a top-level menu in base.html,
    e.g. 'book', 'term', 'settings'.
    """
    registry = get_registry()
    items = registry.menu_items_for(parent)
    return render_template(
        "feature/_menu_items.html", items=items, parent=parent
    )


@bp.get("/panel")
def panel():
    """Render the standalone Plugin management page (Setting > Plugin)."""
    registry = get_registry()
    tiles = registry.sorted_settings_tiles()
    packages = installer.installed_feature_packages()
    return render_template(
        "feature/panel.html",
        tiles=tiles,
        packages=packages,
        loaded=registry.loaded_plugins,
    )


@bp.get("/installed")
def installed_json():
    """JSON list of discovered feature entry points, status, and packages."""
    registry = get_registry()
    return jsonify(
        {
            "installed": installer.installed_feature_names(),
            "packages": installer.installed_feature_packages(),
            "loaded": registry.loaded_plugins,
        }
    )


@bp.post("/install")
def install():
    """Install or update a feature plugin from a pip spec.

    Reinstalling a plugin with the same name overwrites/updates it.
    """
    spec = (request.form.get("spec") or "").strip()
    if not spec:
        return (
            jsonify(
                {"ok": False, "message": "Please enter a pip spec (name, path, or URL)."}
            ),
            400,
        )
    ok, message = installer.install_feature_plugin(spec)
    return jsonify({"ok": ok, "message": message})


@bp.post("/uninstall")
def uninstall():
    """Uninstall a feature plugin by its entry-point name."""
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "message": "Missing plugin name."}), 400
    if name == "_demo":
        return (
            jsonify(
                {
                    "ok": False,
                    "message": "'_demo' is Lute's built-in marker and cannot be removed.",
                }
            ),
            400,
        )
    ok, message = installer.uninstall_feature_plugin(name)
    return jsonify({"ok": ok, "message": message})


@bp.post("/upload_install")
def upload_install():
    """Install a feature plugin by uploading its source folder.

    The browser sends every file of the chosen folder as multipart data;
    each file's name is its ``webkitRelativePath``. We write them to a
    temp dir on the server (guarding against path traversal) and then pip
    install that dir. This is how a local plugin folder gets onto a remote
    Lute host.
    """
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "message": "No files received."}), 400

    import os
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="lute_plugin_")
    try:
        for f in files:
            rel = (f.filename or "").replace("\\", "/").lstrip("/")
            if not rel or rel.startswith("..") or "/.." in rel or ".." == rel:
                continue
            dest = os.path.join(tmp, rel)
            if not os.path.realpath(dest).startswith(os.path.realpath(tmp)):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            f.save(dest)

        # The browser uploads the folder contents under its top-level name
        # (e.g. lute-storygen/pyproject.toml), so locate the actual package
        # root before installing.
        pkg_root = _find_package_root(tmp)
        if pkg_root is None:
            return jsonify(
                {
                    "ok": False,
                    "message": (
                        "No pyproject.toml or setup.py found in the uploaded folder."
                    ),
                }
            )
        ok, message = installer.install_feature_plugin(pkg_root)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return jsonify({"ok": ok, "message": message})


@bp.get("/locate")
def locate():
    """
    Find the absolute path of a plugin folder picked via the browser.

    Browsers only expose a relative path (``webkitRelativePath``), so we
    search common locations on the server for a directory with that name.
    """
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "path": None})
    from . import locator

    path = locator.find_plugin_dir(name)
    if path:
        return jsonify({"ok": True, "path": path})
    return jsonify({"ok": False, "path": None})
