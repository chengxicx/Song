"""
AppConfig proxy that resolves user-scoped paths per request.

Attached to the app as app.env_config.  Every existing
`current_app.env_config.X` call site keeps working: path attributes
resolve to the logged-in user's directory when multi-user mode is on,
and fall through to the base config otherwise (single-user mode,
pre-login requests, boot-time code).
"""

from lute.multiuser import context, paths, store


class UserScopedAppConfig:
    "See module docstring."

    _USER_SCOPED = frozenset(paths.USER_SCOPED_ATTRS)

    def __init__(self, base_config):
        object.__setattr__(self, "_base", base_config)

    @property
    def base_config(self):
        "The underlying base AppConfig."
        return object.__getattribute__(self, "_base")

    def __getattr__(self, name):
        base = object.__getattribute__(self, "_base")
        if name in self._USER_SCOPED and store.enabled():
            username = context.get_current_user()
            if username:
                return getattr(paths.user_config(base, username), name)
        return getattr(base, name)
