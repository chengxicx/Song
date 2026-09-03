"""
Discover and load installed feature plugins via Python entry_points.

Mirrors the behaviour of ``lute.parse.registry.init_parser_plugins``
so feature plugins use a familiar install/upgrade path:

    pip install lute3-<name>

Local source checkouts can also be picked up via the
``LUTE_PLUGINS_DIR`` environment variable (see
``lute.parse.plugin_installer`` for the directory convention).
"""

from importlib.metadata import entry_points
from sys import version_info
import logging

from .registry import get_registry


_log = logging.getLogger(__name__)


def _iter_feature_entry_points():
    """Yield entry points in the 'lute.plugin.feature' group.

    Compatible with Python 3.8+ (entry_points API differences).
    """
    vmaj = version_info.major
    vmin = version_info.minor

    if vmaj == 3 and vmin in (8, 9, 10, 11):
        eps = entry_points()
        custom_eps = eps.get("lute.plugin.feature") or []
    elif (vmaj == 3 and vmin >= 12) or (vmaj >= 4):
        eps = entry_points()
        custom_eps = list(eps.select(group="lute.plugin.feature"))
    else:
        _log.warning(
            "Unable to load feature plugins for python %s.%s; "
            "please upgrade to 3.8+",
            vmaj,
            vmin,
        )
        return []

    return list(custom_eps)


def load_feature_plugins(app):
    """Discover installed feature plugins and invoke ``register(app)``.

    Each plugin is responsible for:

      * calling ``app.register_blueprint(bp)`` for any routes it adds
      * calling ``lute.feature.get_registry().add_menu_item(...)``
        and/or ``add_settings_tile(...)`` for any UI it contributes

    A plugin that raises during ``register(app)`` is logged and
    skipped; it does not abort app startup.
    """
    registry = get_registry()
    registry.blueprints.clear()
    registry.menu_items.clear()
    registry.settings_tiles.clear()
    registry.loaded_plugins.clear()

    for ep in _iter_feature_entry_points():
        try:
            register_fn = ep.load()
            register_fn(app)
            registry.loaded_plugins.append(ep.name)
            _log.info("Loaded feature plugin '%s'", ep.name)
        except Exception as exc:  # pylint: disable=broad-except
            _log.warning(
                "Feature plugin '%s' failed to load: %s", ep.name, exc
            )

    for bp in registry.blueprints:
        try:
            app.register_blueprint(bp)
        except Exception as exc:  # pylint: disable=broad-except
            _log.warning(
                "Failed to register feature blueprint '%s': %s", bp.name, exc
            )

    return registry