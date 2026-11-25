"""sdr_tuner/dock_identity.py: main.py re-launching itself through the app bundle's own interpreter, so the Dock knows a
PyCharm/terminal run as the tool (its icon through the quit animation, not Python's rocket - user, 2026-09-26, after a
recording of the rocket: "now you see rocket icon so can you resolve it"). The real exec is never done here."""
import os
import sys
from pathlib import Path

import pytest

from sdr_tuner import dock_identity as di


def test_running_inside_is_decided_by_where_the_real_executable_lives(tmp_path):
    bundle = tmp_path / "Tool.app"
    inside = bundle / "Contents" / "MacOS" / di.INTERPRETER_NAME
    outside = Path("/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python")
    assert di.running_inside(bundle, executable=inside)
    assert not di.running_inside(bundle, executable=outside)
    assert not di.running_inside(bundle, executable=None)


def test_should_relaunch_only_on_a_mac_outside_the_bundle_and_not_when_told_not_to(monkeypatch, tmp_path):
    bundle = tmp_path / "Tool.app"
    monkeypatch.setattr(di.sys, "platform", "darwin")
    monkeypatch.setattr(di, "real_executable", lambda: Path("/Library/Frameworks/Python.framework/X/Python"))
    monkeypatch.delitem(sys.modules, "pytest", raising=False)            # the guard for the test runner itself
    try:
        assert di.should_relaunch(bundle, environ={})
        assert not di.should_relaunch(bundle, environ={di.NO_RELAUNCH_ENV: "1"})
        monkeypatch.setattr(di, "real_executable", lambda: bundle / "Contents" / "MacOS" / di.INTERPRETER_NAME)
        assert not di.should_relaunch(bundle, environ={})                  # already the bundle's own process
        monkeypatch.setattr(di, "real_executable", lambda: Path("/Library/Frameworks/Python.framework/X/Python"))
        monkeypatch.setattr(di.sys, "platform", "linux")
        assert not di.should_relaunch(bundle, environ={})
    finally:
        sys.modules["pytest"] = pytest


def test_relaunch_execs_the_bundles_interpreter_on_the_script_with_the_venv_on_pythonpath(tmp_path):
    bundle = tmp_path / "Tool.app"
    calls = []
    di.relaunch(Path("/proj/main.py"), ["--sim"], bundle=bundle, execve=lambda *a: calls.append(a),
                environ={"PYTHONPATH": "/extra", "HOME": "/h"})
    (path, argv, env), = calls
    interp = str(di.bundle_interpreter(bundle))
    assert path == interp and argv == [interp, "/proj/main.py", "--sim"]
    assert env["PYTHONPATH"] == di.site_packages() + os.pathsep + "/extra" and env["HOME"] == "/h"
    assert env[di.NO_RELAUNCH_ENV] == "1"                                   # the relaunched process never loops


def test_ensure_bundle_builds_only_when_the_bundle_is_missing_or_stale(monkeypatch, tmp_path):
    bundle = tmp_path / "Tool.app"
    built = []
    monkeypatch.setattr(di, "bundle_is_current", lambda b: False)
    assert di.ensure_bundle(bundle, build=lambda b: built.append(b) or b) == bundle and built == [bundle]
    monkeypatch.setattr(di, "bundle_is_current", lambda b: True)
    assert di.ensure_bundle(bundle, build=lambda b: built.append(b) or b) == bundle and built == [bundle]


def test_relaunch_if_needed_never_raises_and_falls_back_to_the_bare_run(monkeypatch, capsys):
    monkeypatch.setattr(di, "should_relaunch", lambda: True)
    monkeypatch.setattr(di, "ensure_bundle", lambda: (_ for _ in ()).throw(OSError("no framework")))
    assert di.relaunch_in_bundle_if_needed(Path("/proj/main.py"), ["--sim"]) is False
    assert "bare interpreter" in capsys.readouterr().err
    monkeypatch.setattr(di, "should_relaunch", lambda: False)
    assert di.relaunch_in_bundle_if_needed(Path("/proj/main.py"), []) is False


def test_a_frozen_build_never_relaunches(monkeypatch):
    """A PyInstaller app (tools/build_release.py) is its own bundle: no venv to relaunch into, the Dock already right."""
    monkeypatch.setattr(di.sys, "platform", "darwin")
    monkeypatch.setattr(di, "real_executable", lambda: Path("/Applications/SDR Servo Tuning Tool.app/Contents/MacOS/x"))
    monkeypatch.setattr(di.sys, "frozen", True, raising=False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    try:
        assert not di.should_relaunch(Path("/nowhere/Tool.app"), environ={})
    finally:
        sys.modules["pytest"] = pytest
