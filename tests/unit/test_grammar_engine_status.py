"""Tests for the grammar-engine status helpers and the install function."""

import importlib.util

import pytest

from lute.read.render import grammar_analysis


class StubLanguage:
    def __init__(self, parser_type=None, name=None):
        self.parser_type = parser_type
        self.name = name


def test_grammar_engine_for_maps_by_language_name():
    "Known languages map to their engine label and extra."
    assert grammar_analysis.grammar_engine_for(StubLanguage(name="English")) == (
        "English",
        "english",
    )
    assert grammar_analysis.grammar_engine_for(StubLanguage(name="Русский")) == (
        "Russian",
        "russian",
    )
    assert grammar_analysis.grammar_engine_for(StubLanguage(parser_type="lute_thai")) == (
        "Thai",
        "thai",
    )


def test_grammar_engine_for_unknown_language():
    "Languages without a dedicated engine map to (None, None)."
    assert grammar_analysis.grammar_engine_for(StubLanguage(name="Turkish")) == (
        None,
        None,
    )
    assert grammar_analysis.grammar_engine_for(None) == (None, None)


def test_status_shape_and_installed_flag():
    "Status reports the dependency state actually found on disk."
    status = grammar_analysis.grammar_engine_status(StubLanguage(name="English"))
    assert status["label"] == "English"
    assert status["extra"] == "english"
    assert status["installable"] is True
    expected_installed = importlib.util.find_spec("spacy") is not None and (
        importlib.util.find_spec("en_core_web_sm") is not None
    )
    assert status["installed"] == expected_installed


def test_status_uninstallable_engine_has_no_label_when_missing():
    "A language without an engine reports installable=False and no label."
    status = grammar_analysis.grammar_engine_status(StubLanguage(name="Turkish"))
    assert status["label"] is None
    assert status["installable"] is False
    assert status["missing"] == []


def test_install_unknown_extra_fails_without_pip():
    "An unknown extra is rejected before any pip call."
    ok, message = grammar_analysis.install_grammar_engine("klingon")
    assert ok is False
    assert "Unknown grammar engine" in message


def _fake_run(resultcode, monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, timeout):  # pylint: disable=unused-argument
        calls.append(cmd)
        proc = type("Proc", (), {"returncode": resultcode, "stdout": "", "stderr": ""})()
        return proc

    monkeypatch.setattr(grammar_analysis.subprocess, "run", fake_run)
    return calls


def test_install_success_runs_pip_with_specs(monkeypatch):
    "A known extra pip-installs its requirement list."
    calls = _fake_run(0, monkeypatch)
    ok, message = grammar_analysis.install_grammar_engine("russian")
    assert ok is True
    assert len(calls) == 1
    assert calls[0][1:3] == ["-m", "pip"]
    assert any("pymorphy3" in spec for spec in calls[0][3:])
    assert "restart" in message


def test_install_failure_reports_output(monkeypatch):
    "A pip failure surfaces the failure message."
    calls = []

    def fake_run(cmd, capture_output, text, timeout):  # pylint: disable=unused-argument
        calls.append(cmd)
        return type("Proc", (), {"returncode": 1, "stdout": "boom", "stderr": "bad spec"})()

    monkeypatch.setattr(grammar_analysis.subprocess, "run", fake_run)
    ok, message = grammar_analysis.install_grammar_engine("russian")
    assert ok is False
    assert "failed" in message
    assert "bad spec" in message
    assert len(calls) == 1


def test_install_specs_cover_every_engine():
    "Every engine with importable deps has a pip spec list, and vice versa."
    for _label, _detect, _deps, extra in grammar_analysis._ENGINE_REQUIREMENTS:
        assert extra in grammar_analysis._ENGINE_INSTALL_SPECS, extra
    for extra in grammar_analysis._ENGINE_INSTALL_SPECS:
        assert any(e == extra for _l, _d, _deps, e in grammar_analysis._ENGINE_REQUIREMENTS), extra
