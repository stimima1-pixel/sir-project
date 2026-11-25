"""Builds the installable, self-contained app for the OS this runs on (user, 2026-09-26: "can we make this software
in .exe file so it can be installed on windows pc, for mac and linux as well with each os file extension"):

    macOS    dist/release/SDR Servo Tuning Tool-<version>-macOS-<arch>.dmg      (holds SDR Servo Tuning Tool.app)
    Windows  dist/release/SDR Servo Tuning Tool-<version>-Windows-x64.zip       (holds the folder with the .exe)
             + SDR Servo Tuning Tool-<version>-Setup.exe when Inno Setup's iscc is on the PATH
    Linux    dist/release/SDR Servo Tuning Tool-<version>-Linux-<arch>.AppImage when appimagetool is on the PATH,
             else ...-Linux-<arch>.tar.gz (the folder with the executable and a .desktop file)

PyInstaller does not cross-compile: run this ON the OS you are packaging for - this Mac for the .dmg, the Windows VM
for the .exe, a Linux box or VM for the AppImage - with that OS's own venv (`pip install -r requirements-dev.txt`).
The build is a one-folder app (Qt starts far faster than from a one-file archive) with everything the tool needs:
Python itself, PySide6, pyqtgraph, numpy, python-can with every interface module, pyserial, the register/command
catalogue, the bundled G-code and kinematics samples and the icon. Nothing from this project's venv is referenced
at run time (unlike tools/make_app_bundle.py's developer bundle, which wraps THIS machine's venv and is for the Dock
icon during development). `sdr_tuner.dock_identity` skips its venv relaunch inside a frozen build (`sys.frozen`).

Run:  .venv/bin/python tools/build_release.py          (add --no-installer to skip the dmg/zip/AppImage step)

macOS Gatekeeper: an app that is only ad-hoc signed (the default here, no certificate needed) opens on the Mac that
built it, but on any OTHER Mac macOS shows "Apple could not verify ... is free of malware" (on macOS 15 and later
with no Open button at all - the person must go to System Settings > Privacy & Security > Open Anyway). The only
way to make that dialog never appear is Apple's own path: sign with a **Developer ID Application** certificate
(Apple Developer Program membership) with the hardened runtime and a timestamp, then have Apple **notarise** the
result and staple the ticket to it:

    .venv/bin/python tools/build_release.py --identity "Developer ID Application: Remi <surname> (<TEAMID>)" --notarize remi

The developer is Remi: the certificate Apple issues is named after the enrolled person, so the identity reads exactly as
`security find-identity -v -p codesigning` prints it once installed. `remi` is a keychain profile made once with
`xcrun notarytool store-credentials remi` (Apple ID, app-specific password, Team ID). The script signs the .app, notarises and staples the .app, builds the .dmg from it,
then notarises and staples the .dmg too - so the app opens on every Mac, online or not. `security find-identity -v
-p codesigning` lists the certificates this Mac holds.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sdr_tuner import __version__     # noqa: E402

NAME = "SDR Servo Tuning Tool"
BUNDLE_ID = "com.stimita.sdr-servo-tuning-tool"
RELEASE = ROOT / "dist" / "release"
WORK = ROOT / "build" / "release"
ICON_PNG = ROOT / "sdr_tuner" / "data" / "app_icon.png"
ICON_ICNS = ROOT / "sdr_tuner" / "data" / "app_icon.icns"
ICON_ICO = WORK / "app_icon.ico"
DATA = ROOT / "sdr_tuner" / "data"


def os_tag():
    """(OS, architecture) of the build - the architecture of the INTERPRETER doing it, which is what PyInstaller
    packages, not the machine's: an x64 Python on Windows-on-ARM (this project's own Windows VM) builds an x64 .exe,
    and platform.machine() there says ARM64 (2026-09-27: the zip came out named arm64)."""
    machine = platform.machine().lower()                   # macOS / Linux: PyInstaller targets the running architecture
    if sys.platform == "win32":                             # Windows: the interpreter's own (win-amd64 / win-arm64)
        machine = sysconfig.get_platform().rsplit("-", 1)[-1].lower()
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return {"darwin": "macOS", "win32": "Windows", "linux": "Linux"}.get(sys.platform, sys.platform), arch


def icon_for(system):
    """The icon file PyInstaller takes on this OS: the .icns on macOS, an .ico (written from the PNG) on Windows."""
    if system == "macOS":
        return ICON_ICNS
    if system == "Windows":
        from PIL import Image
        WORK.mkdir(parents=True, exist_ok=True)
        Image.open(ICON_PNG).convert("RGBA").save(ICON_ICO, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        return ICON_ICO
    return None


def pyinstaller_args(system, work=WORK, dist=RELEASE):
    """The PyInstaller command line for this OS (a pure function: tests check it without building anything)."""
    sep = ";" if system == "Windows" else ":"
    args = ["--noconfirm", "--clean", "--onedir", "--windowed", "--name", NAME,
            "--workpath", str(work), "--distpath", str(dist), "--specpath", str(work),
            "--add-data", f"{DATA}{sep}sdr_tuner/data",
            "--collect-submodules", "can.interfaces",           # every python-can interface the Connection Type list offers
            "--collect-submodules", "sdr_tuner",
            "--hidden-import", "serial", "--hidden-import", "serial.tools.list_ports",
            "--collect-data", "pyqtgraph",                      # its own icons/colour maps
            "--exclude-module", "tkinter", "--exclude-module", "pytest"]
    icon = icon_for(system)
    if icon is not None:
        args += ["--icon", str(icon)]
    if system == "macOS":
        args += ["--osx-bundle-identifier", BUNDLE_ID]
    return args + [str(ROOT / "main.py")]


ENTITLEMENTS = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
    <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict></plist>
"""     # the two hardened-runtime exceptions a PyInstaller/Qt app needs (Python's extension modules and Qt's plugins)


def sign_command(app, identity, entitlements):
    """The codesign command line (a pure function: tests check it). "-" = ad hoc (no certificate); anything else is a
    Developer ID identity, signed with the hardened runtime, a secure timestamp and the entitlements - what
    notarisation requires."""
    cmd = ["codesign", "--force", "--deep", "--sign", identity]
    if identity != "-":
        cmd += ["--options", "runtime", "--timestamp", "--entitlements", str(entitlements)]
    return cmd + [str(app)]


def sign_app(app, identity):
    entitlements = WORK / "entitlements.plist"
    entitlements.write_text(ENTITLEMENTS)
    subprocess.run(sign_command(app, identity, entitlements), check=identity != "-")
    if identity != "-":
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)


def notarize(path, profile):
    """Submit to Apple's notary service (waits for the verdict) and staple the ticket to the file - an .app is sent
    as a zip (ditto keeps the bundle intact), then the .app itself is stapled; a .dmg is sent as is."""
    target = path
    if path.suffix == ".app":
        target = WORK / f"{path.stem}.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(path), str(target)], check=True)
    subprocess.run(["xcrun", "notarytool", "submit", str(target), "--keychain-profile", profile, "--wait"], check=True)
    subprocess.run(["xcrun", "stapler", "staple", str(path)], check=True)


def build(system, identity="-"):
    RELEASE.mkdir(parents=True, exist_ok=True)
    for stale in (RELEASE / NAME, RELEASE / f"{NAME}.app"):
        if stale.exists():
            shutil.rmtree(stale)
    cmd = [sys.executable, "-m", "PyInstaller", *pyinstaller_args(system)]
    print("$", " ".join(f'"{a}"' if " " in a else a for a in cmd[:4]), "...")
    subprocess.run(cmd, check=True, cwd=ROOT)
    if system == "Windows":
        shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "icuuc.dll",
                     RELEASE / NAME / "_internal" / "icuuc.dll")
    if system == "macOS":
        app = RELEASE / f"{NAME}.app"
        plist = app / "Contents" / "Info.plist"
        import plistlib
        info = plistlib.loads(plist.read_bytes())
        info.update({"CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                     "NSHighResolutionCapable": True, "NSHumanReadableCopyright": "STIMITA MOTION CONTROL",
                     "LSMinimumSystemVersion": "12.0"})
        plist.write_bytes(plistlib.dumps(info))
        if shutil.which("codesign"):
            sign_app(app, identity)
        return app
    return RELEASE / NAME


def package(system, arch, built):
    """The installable file for this OS, next to the built folder/app."""
    stem = f"{NAME}-{__version__}-{system}-{arch}"
    if system == "macOS":
        dmg = RELEASE / f"{stem}.dmg"
        dmg.unlink(missing_ok=True)
        staging = WORK / "dmg"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        shutil.copytree(built, staging / built.name, symlinks=True)
        (staging / "Applications").symlink_to("/Applications")           # drag the app onto it to install
        subprocess.run(["hdiutil", "create", "-volname", NAME, "-srcfolder", str(staging), "-ov", "-format", "UDZO",
                        str(dmg)], check=True, capture_output=True)
        return dmg
    if system == "Windows":
        archive = RELEASE / f"{stem}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in built.rglob("*"):
                zf.write(path, path.relative_to(built.parent))
        iscc = shutil.which("iscc") or shutil.which("ISCC")
        if iscc:
            script = WORK / "installer.iss"
            script.write_text(INNO_SETUP.format(name=NAME, version=__version__, folder=built, out=RELEASE,
                                                exe=f"{NAME}.exe", icon=ICON_ICO, bundle_id=BUNDLE_ID))
            subprocess.run([iscc, str(script)], check=True)
            return RELEASE / f"{NAME}-{__version__}-Setup.exe"
        print("Inno Setup (iscc) not found: no Setup.exe - the zip is the installable package (unzip, run the .exe)")
        return archive
    # Linux: an AppDir; appimagetool packs it if present, else a tarball with a .desktop file
    appdir = WORK / "AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(built, appdir / "usr" / "bin")
    (appdir / "AppRun").symlink_to(f"usr/bin/{NAME}")
    shutil.copy(ICON_PNG, appdir / "app_icon.png")
    (appdir / f"{NAME}.desktop").write_text(DESKTOP.format(name=NAME, exe=NAME))
    tool = shutil.which("appimagetool")
    if tool:
        out = RELEASE / f"{stem}.AppImage"
        subprocess.run([tool, str(appdir), str(out)], check=True, env={**os.environ, "ARCH": platform.machine()})
        return out
    print("appimagetool not found: a tar.gz instead (extract, run the executable; the .desktop file is inside)")
    archive = RELEASE / f"{stem}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(appdir, arcname=stem)
    return archive


INNO_SETUP = """[Setup]
AppName={name}
AppVersion={version}
AppId={bundle_id}
DefaultDirName={{autopf}}\\{name}
DefaultGroupName={name}
OutputDir={out}
OutputBaseFilename={name}-{version}-Setup
SetupIconFile={icon}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
[Files]
Source: "{folder}\\*"; DestDir: "{{app}}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{{group}}\\{name}"; Filename: "{{app}}\\{exe}"
Name: "{{autodesktop}}\\{name}"; Filename: "{{app}}\\{exe}"
[Run]
Filename: "{{app}}\\{exe}"; Description: "Start {name}"; Flags: nowait postinstall skipifsilent
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name={name}
Exec={exe}
Icon=app_icon
Categories=Development;Engineering;
Terminal=false
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-installer", action="store_true", help="build the app folder/bundle only")
    parser.add_argument("--identity", default="-", help='macOS: the "Developer ID Application: ..." identity to sign with '
                        "(default: ad hoc - opens only on the Mac that built it without a Gatekeeper dialog)")
    parser.add_argument("--notarize", metavar="PROFILE", help="macOS: notarise and staple with this notarytool keychain "
                        "profile (needs --identity)")
    options = parser.parse_args()
    system, arch = os_tag()
    if options.notarize and options.identity == "-":
        parser.error("--notarize needs a Developer ID --identity (Apple only notarises Developer ID-signed apps)")
    built = build(system, options.identity)
    print(f"built {built.relative_to(ROOT)}")
    if system == "macOS" and options.notarize:
        notarize(built, options.notarize)
        print("notarised and stapled the .app")
    if not options.no_installer:
        out = package(system, arch, built)
        if system == "macOS" and options.identity != "-":
            subprocess.run(["codesign", "--sign", options.identity, "--timestamp", str(out)], check=True)   # the .dmg itself too
        if system == "macOS" and options.notarize:
            notarize(out, options.notarize)
            print("notarised and stapled the .dmg")
        size = out.stat().st_size / 1e6 if out.is_file() else sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
        print(f"package {out.relative_to(ROOT)}  ({size:.0f} MB)")
    if system == "macOS" and options.identity == "-":
        print("NOTE: ad-hoc signed - other Macs will show 'Apple could not verify ...'; sign with a Developer ID "
              "(--identity) and notarise (--notarize) to ship without that dialog (see the docstring)")
