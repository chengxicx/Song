"""
In-memory registry of feature-plugin-supplied UI contributions.

The registry is a process-global singleton populated by feature
plugins during ``register(app)``.  Templates and routes read from it
to render plugin-contributed menu items and settings tiles.

Plugins must only use the public ``add_*`` methods; the dataclasses
are exposed for read-only access from template fragments.
"""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class MenuItem:
    """A single menu entry contributed by a feature plugin."""

    parent: str  # 'book' | 'term' | 'settings' | 'home'
    label: str  # user-visible text
    url: str  # route, e.g. '/storygen/'
    icon: str = ""  # optional CSS class for an icon
    order: int = 100  # smaller = earlier in the list


@dataclass
class SettingsTile:
    """A card/link on the Settings page."""

    label: str
    url: str
    description: str = ""
    order: int = 100


class FeatureRegistry:
    """Process-global store of feature-plugin UI contributions."""

    def __init__(self):
        self.blueprints: List[Any] = []
        self.menu_items: List[MenuItem] = []
        self.settings_tiles: List[SettingsTile] = []
        # Names (entry_point names) that have loaded successfully.
        self.loaded_plugins: List[str] = []

    def add_blueprint(self, blueprint):
        """Register a Flask blueprint to be attached to the app."""
        self.blueprints.append(blueprint)

    def add_menu_item(self, parent, label, url, icon="", order=100):
        """Register a menu item under an existing top-level menu."""
        self.menu_items.append(
            MenuItem(
                parent=parent, label=label, url=url, icon=icon, order=order
            )
        )

    def add_settings_tile(self, label, url, description="", order=100):
        """Register a card on the Settings page."""
        self.settings_tiles.append(
            SettingsTile(
                label=label, url=url, description=description, order=order
            )
        )

    def menu_items_for(self, parent):
        """Return menu items for ``parent``, sorted by ``order``."""
        items = [m for m in self.menu_items if m.parent == parent]
        items.sort(key=lambda m: (m.order, m.label))
        return items

    def sorted_settings_tiles(self):
        """Return all settings tiles, sorted by ``order``."""
        return sorted(self.settings_tiles, key=lambda t: (t.order, t.label))


_registry: Optional[FeatureRegistry] = None


def get_registry() -> FeatureRegistry:
    """Return the process-global feature registry, creating it on first use."""
    global _registry
    if _registry is None:
        _registry = FeatureRegistry()
    return _registry