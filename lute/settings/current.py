"""
Current user settings cached in process memory.

With multi-user mode, settings are per user, so the cache is
partitioned into buckets keyed by the request's user scope
(lute.multiuser.context).  current_settings() returns the current
scope's live dict -- reads and in-place writes (e.g. theme toggles)
affect only the current user's bucket.  In single-user mode there is
a single DEFAULT_SCOPE bucket, and behavior is unchanged.

Buckets are filled by refresh_global_settings() (boot, per-user
seeding, and after settings changes).
"""

from lute.multiuser.context import current_scope_key
from lute.models.setting import UserSetting

# scope key -> {setting key: value}
_settings_buckets = {}

# scope key -> {hotkey mapping: mapping name}
_hotkeys_buckets = {}


def _bucket(buckets, scope=None):
    "The given scope's dict, creating it if needed."
    key = scope or current_scope_key()
    b = buckets.get(key)
    if b is None:
        b = {}
        buckets[key] = b
    return b


def current_settings(scope=None):
    "The current scope's settings dict."
    return _bucket(_settings_buckets, scope)


def current_hotkeys(scope=None):
    "The current scope's hotkey mappings dict."
    return _bucket(_hotkeys_buckets, scope)


def refresh_global_settings(session, scope=None):
    "Refresh the given scope's settings dictionaries from the db."
    # Have to reload to not mess up any references
    # (e.g. during testing).
    key = scope or current_scope_key()
    current_settings(key).clear()
    current_hotkeys(key).clear()

    settings = session.query(UserSetting).all()
    sdict = current_settings(key)
    for s in settings:
        sdict[s.key] = s.value

    hotkeys = [
        s for s in settings if s.key.startswith("hotkey_") and (s.value or "") != ""
    ]
    hdict = current_hotkeys(key)
    for h in hotkeys:
        hdict[h.value] = h.key

    # Convert some string values into bools.
    boolkeys = [
        "open_popup_in_new_tab",
        "stop_audio_on_term_form_open",
        "show_highlights",
        "term_popup_promote_parent_translation",
        "term_popup_show_components",
        "use_ankiconnect",
        "show_streak_on_home",
        "tts_hover_pronunciation",
        "tts_click_pronunciation",
        "tts_show_control_panel",
        "tts_show_sentence_buttons",
    ]
    true_vals = {"1", "true", "True", "yes", "Yes", "on"}
    for k in boolkeys:
        if k in sdict:
            sdict[k] = str(sdict[k]) in true_vals
