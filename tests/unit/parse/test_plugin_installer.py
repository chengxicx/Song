"""
Plugin auto-installer tests.

These tests never run pip for real: subprocess and registry
re-initialization are mocked.
"""

import os
from types import SimpleNamespace

import lute.parse.plugin_installer as pi


def test_plugin_package_mapping():
    "Known parser types map to their pip packages."
    assert pi.plugin_package_for("lute_cantonese") == "lute3-cantonese"
    assert pi.plugin_package_for("lute_mandarin") == "lute3-mandarin"
    assert pi.plugin_package_for("lute_korean") == "lute3-korean"
    assert pi.plugin_package_for("spacedel") is None
    assert pi.plugin_package_for(None) is None


def test_is_auto_installable(tmp_path, monkeypatch):
    "Auto-installable = whitelisted and not currently supported."
    monkeypatch.setattr(pi, "is_supported", lambda pt: pt == "lute_cantonese")
    assert pi.is_auto_installable("lute_thai") is True, "whitelisted, missing"
    assert pi.is_auto_installable("lute_cantonese") is False, "already supported"
    assert pi.is_auto_installable("spacedel") is False, "not whitelisted"
    assert pi.is_auto_installable(None) is False


def test_find_local_plugin_dir_from_env(tmp_path, monkeypatch):
    "LUTE_PLUGINS_DIR is searched for plugins/<name> with a pyproject.toml."
    plugins = tmp_path / "plugins"
    plugdir = plugins / "lute-cantonese"
    plugdir.mkdir(parents=True)
    (plugdir / "pyproject.toml").write_text("[project]\nname='lute3-cantonese'\n")
    monkeypatch.setenv("LUTE_PLUGINS_DIR", str(plugins))
    assert pi.find_local_plugin_dir("lute_cantonese") == str(plugdir)
    # No such dir for a made-up parser type.
    assert pi.find_local_plugin_dir("lute_bogus") is None


def test_find_local_plugin_dir_missing(tmp_path, monkeypatch):
    "None is returned when no local checkout exists."
    monkeypatch.setattr(pi, "_local_plugins_dirs", lambda: [])
    assert pi.find_local_plugin_dir("lute_cantonese") is None


def test_ensure_parser_available_already_ok():
    "Supported parsers are a no-op success."
    ok, message = pi.ensure_parser_available("spacedel")
    assert ok is True
    assert message == "already installed"


def test_ensure_parser_available_unknown_parser():
    "Parser types outside the whitelist are refused without running pip."
    called = {"pip": False}

    def _no_run(*args, **kwargs):  # pylint: disable=unused-argument
        called["pip"] = True
        raise AssertionError("pip should not be called")

    orig_run = pi.subprocess.run
    pi.subprocess.run = _no_run
    try:
        ok, message = pi.ensure_parser_available("lute_bogus")
        assert ok is False
        assert "No installable plugin" in message
        assert called["pip"] is False
    finally:
        pi.subprocess.run = orig_run


def test_ensure_parser_available_installs_from_local_dir(tmp_path, monkeypatch):
    "Happy path: local dir found, pip succeeds, registry is re-scanned."
    # Not installed before the install; the mocked init_parser_plugins
    # stands in for the registry re-scan that makes it available.
    state = {"supported": False}
    monkeypatch.setattr(pi, "is_supported", lambda pt: state["supported"])
    monkeypatch.setattr(pi, "find_local_plugin_dir", lambda pt: str(tmp_path))

    calls = {}

    def fake_run(cmd, **kwargs):  # pylint: disable=unused-argument
        calls["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pi.subprocess, "run", fake_run)
    monkeypatch.setattr(
        pi, "init_parser_plugins", lambda: state.__setitem__("supported", True)
    )

    ok, message = pi.ensure_parser_available("lute_cantonese")
    assert ok is True, message
    assert str(tmp_path) in calls["cmd"], "installs from local dir"
    assert "pip" in calls["cmd"] and "install" in calls["cmd"]
    assert state["supported"] is True, "registry re-initialized after install"
    assert "Installed" in message


def test_ensure_parser_available_pip_failure(tmp_path, monkeypatch):
    "A pip failure returns ok=False with the captured output."
    monkeypatch.setattr(pi, "is_supported", lambda pt: False)
    monkeypatch.setattr(pi, "find_local_plugin_dir", lambda pt: None)

    def fake_run(cmd, **kwargs):  # pylint: disable=unused-argument
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(pi.subprocess, "run", fake_run)

    ok, message = pi.ensure_parser_available("lute_thai")
    assert ok is False
    assert "boom" in message
    assert "lute3-thai" in message, "falls back to PyPI package name"


def test_ensure_existing_language_parsers(app_context, monkeypatch):
    "Missing whitelisted parsers for existing languages are auto-installed."
    from lute.db import db
    from lute.models.language import Language

    lang = Language()
    lang.name = "Korean Existing (test)"
    lang.parser_type = "lute_korean"
    db.session.add(lang)
    db.session.commit()

    calls = []
    monkeypatch.setattr(pi, "is_auto_installable", lambda pt: pt == "lute_korean")
    monkeypatch.setattr(
        pi,
        "ensure_parser_available",
        lambda pt: calls.append(pt) or (True, "mocked install"),
    )

    results = pi.ensure_existing_language_parsers(db.session)
    assert calls == ["lute_korean"]
    assert results == [("Korean Existing (test)", True, "mocked install")]


def test_ensure_existing_language_parsers_skips_ok(app_context, monkeypatch):
    "Languages with a supported/non-whitelisted parser are left untouched."
    from lute.db import db
    from lute.models.language import Language

    lang = Language()
    lang.name = "Plain (test)"
    lang.parser_type = "spacedel"
    db.session.add(lang)
    db.session.commit()

    calls = []
    monkeypatch.setattr(pi, "is_auto_installable", lambda pt: False)
    monkeypatch.setattr(
        pi, "ensure_parser_available", lambda pt: calls.append(pt) or (True, "")
    )

    results = pi.ensure_existing_language_parsers(db.session)
    assert results == []
    assert calls == []


def test_ensure_existing_language_parsers_heals_spacedel_by_name(
    app_context, monkeypatch
):
    "A language on spacedel whose name matches a plugin is installed + healed."
    from lute.db import db
    from lute.models.language import Language

    lang = Language()
    lang.name = "Korean"
    lang.parser_type = "spacedel"
    db.session.add(lang)
    db.session.commit()

    calls = []
    monkeypatch.setattr(pi, "is_auto_installable", lambda pt: False)
    monkeypatch.setattr(
        pi,
        "ensure_parser_available",
        lambda pt: calls.append(pt) or (True, "mocked install"),
    )

    results = pi.ensure_existing_language_parsers(db.session)
    assert calls == ["lute_korean"]
    assert results == [("Korean", True, "mocked install")]
    # parser_type was restored to point at the plugin and persisted.
    lang2 = db.session.query(Language).filter(Language.name == "Korean").first()
    assert lang2.parser_type == "lute_korean"


def test_ensure_existing_language_parsers_name_match_fails(app_context, monkeypatch):
    "A failed install still reports the failure and does not change parser."
    from lute.db import db
    from lute.models.language import Language

    lang = Language()
    lang.name = "Korean"
    lang.parser_type = "spacedel"
    db.session.add(lang)
    db.session.commit()

    monkeypatch.setattr(pi, "is_auto_installable", lambda pt: False)
    monkeypatch.setattr(
        pi, "ensure_parser_available", lambda pt: (False, "install exploded")
    )

    results = pi.ensure_existing_language_parsers(db.session)
    assert results == [("Korean", False, "install exploded")]
    plain = db.session.query(Language).filter(Language.name == "Korean").first()
    assert plain.parser_type == "spacedel"
