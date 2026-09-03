"""
Feature plugin entry point for Lute.

Feature plugins are external Python packages that can contribute:
    * Flask blueprints (registered under their own url_prefix)
    * Menu items injected under an existing top-level menu
      (currently: 'book', 'term', 'settings')
    * Settings tiles shown on the Settings page

A feature plugin is a Python package that exposes a ``register(app)``
callable via the ``lute.plugin.feature`` entry_points group.

Example ``pyproject.toml`` of a feature plugin package::

    [project.entry-points."lute.plugin.feature"]
    myplugin = "myplugin.plugin:register"

The ``register(app)`` callable receives the Flask app after all core
blueprints have been registered, and is expected to:

    1. Register any blueprints on the app.
    2. Call ``lute.feature.get_registry().add_*`` to register menu
       items, etc.

Lute's core code only contains the entry-point loader, the in-memory
registry, and a tiny blueprint that serves menu/settings fragments
to the existing base.html via HTMX.  No feature plugin code lives in
Lute itself; everything else ships as separate packages.
"""

from .registry import FeatureRegistry, get_registry
from .loader import load_feature_plugins

__all__ = ["FeatureRegistry", "get_registry", "load_feature_plugins"]