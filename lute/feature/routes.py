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