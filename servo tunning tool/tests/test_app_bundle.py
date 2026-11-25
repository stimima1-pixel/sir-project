"""tools/make_app_bundle.py: the macOS app bundle around the project's venv - the Dock then shows the tool's own icon
while it runs, quits and sits pinned (a bare python process quits under Python's rocket: measured, 2026-09-26). Plain
files, so this runs anywhere; tools/make_app_icon.py's .icns writer needs macOS's iconutil and is skipped elsewhere."""
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def test_the_bundle_names_the_tool_carries_its_icon_and_launches_the_projects_own_venv(tmp_path):
    import make_app_bundle as mab
    bundle = mab.build(tmp_path / "Test.app")
    contents = bundle / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleName"] == info["CFBundleDisplayName"] == mab.NAME and info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleIconFile"] == "app.icns" and (contents / "Resources" / "app.icns").read_bytes()[:4] == b"icns"
    assert info["CFBundleExecutable"] == mab.NAME and info["NSHighResolutionCapable"] is True
    launcher = contents / "MacOS" / mab.NAME
    assert launcher.stat().st_mode & stat.S_IXUSR
    text = launcher.read_text()
    assert text.startswith("#!/bin/bash") and "exec " in text                 # one process: the one the Dock launched
    from sdr_tuner import dock_identity
    interpreter = dock_identity.bundle_interpreter(bundle)
    assert str(interpreter) in text and "main.py" in text and '"$@"' in text   # ...running the bundle's OWN interpreter
    assert dock_identity.site_packages() in text and f"export {dock_identity.NO_RELAUNCH_ENV}=1" in text
    assert (contents / "PkgInfo").read_text() == "APPL????"
    if sys.platform == "darwin":                                                # the interpreter copy, signed
        assert interpreter.exists() and interpreter.stat().st_mode & stat.S_IXUSR
        assert dock_identity.stamp_path(bundle).read_text() == dock_identity.source_stamp()
        assert dock_identity.bundle_is_current(bundle)                          # so main.py will not rebuild it
        assert interpreter.read_bytes()[:4] in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe")   # a Mach-O (fat or thin)
        if shutil.which("codesign"):
            assert subprocess.run(["codesign", "--verify", str(bundle)], capture_output=True).returncode == 0


@pytest.mark.skipif(shutil.which("iconutil") is None, reason="macOS's iconutil packs the .icns")
def test_the_icon_tool_writes_a_real_icns(tmp_path):
    import make_app_icon as mai
    from PIL import Image
    icon = Image.open(mai.OUT).convert("RGBA")
    out = tmp_path / "app.icns"
    assert mai.write_icns(icon, out) and out.read_bytes()[:4] == b"icns" and out.stat().st_size > 100_000


def test_the_release_is_named_after_the_interpreters_architecture_on_windows(monkeypatch):
    """An x64 Python on Windows-on-ARM (this project's own Windows 11 VM) builds an x64 .exe, and platform.machine()
    there says ARM64 - the first Windows zip came out named arm64 (2026-09-27). On Windows the tag follows the
    interpreter (sysconfig's win-amd64 / win-arm64); on macOS and Linux PyInstaller targets the running architecture."""
    import platform
    import sysconfig
    import build_release as br                     # tools/ is on sys.path at the top of this file
    monkeypatch.setattr(br.sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(sysconfig, "get_platform", lambda: "win-amd64")
    assert br.os_tag() == ("Windows", "x64")
    monkeypatch.setattr(sysconfig, "get_platform", lambda: "win-arm64")
    assert br.os_tag() == ("Windows", "arm64")
    monkeypatch.setattr(br.sys, "platform", "darwin")
    monkeypatch.setattr(sysconfig, "get_platform", lambda: "macosx-11.0-universal2")   # a universal Python still builds
    monkeypatch.setattr(platform, "machine", lambda: "arm64")                          # for the architecture it runs as
    assert br.os_tag() == ("macOS", "arm64")


def test_a_developer_id_signing_command_uses_the_hardened_runtime_timestamp_and_entitlements():
    """Ad hoc signing (the default, no certificate) opens only on the building Mac; other Macs show "Apple could not
    verify" (user's screenshot, 2026-09-27). A Developer ID identity is signed the way notarisation requires."""
    import build_release as br
    ad_hoc = br.sign_command("/x/A.app", "-", "/x/ent.plist")
    assert ad_hoc == ["codesign", "--force", "--deep", "--sign", "-", "/x/A.app"]
    dev = br.sign_command("/x/A.app", "Developer ID Application: STIMITA (TEAM1234)", "/x/ent.plist")
    assert dev[:5] == ["codesign", "--force", "--deep", "--sign", "Developer ID Application: STIMITA (TEAM1234)"]
    assert "--options" in dev and dev[dev.index("--options") + 1] == "runtime" and "--timestamp" in dev
    assert dev[dev.index("--entitlements") + 1] == "/x/ent.plist" and dev[-1] == "/x/A.app"
    assert "disable-library-validation" in br.ENTITLEMENTS and "allow-unsigned-executable-memory" in br.ENTITLEMENTS
