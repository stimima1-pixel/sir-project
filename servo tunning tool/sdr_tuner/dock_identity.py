"""Which program the Dock thinks this tool is - and how to make it ours from a PyCharm or terminal run.

The Dock identifies a process by the bundle its EXECUTABLE lives in. A bare `.venv/bin/python main.py` really runs
/Library/Frameworks/Python.framework/.../Python.app/Contents/MacOS/Python (the venv's python is a stub that execs it),
so the Dock draws that process's tile removal - the quit animation - with Python.app's rocket, whatever icon the
running app had set (measured frame by frame, 2026-09-26; no in-process trick changes it). `dist/SDR Servo Tuning
Tool.app` carries its own COPY of that interpreter binary, so a process running through it belongs to our bundle,
rocket-free - proven by launching that binary bare, outside LaunchServices, exactly as PyCharm would (user, after a
recording of the rocket: "now you see rocket icon so can you resolve it"). main.py therefore re-launches itself
through the bundle's interpreter (execve: same PID, PyCharm's Run/Stop and the console keep working) when it finds
itself running under Python.app. No Qt here; nothing happens off macOS or when SDR_NO_RELAUNCH is set."""
import ctypes
import os
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_NAME = "SDR Servo Tuning Tool"
BUNDLE = ROOT / "dist" / f"{BUNDLE_NAME}.app"
INTERPRETER_NAME = f"{BUNDLE_NAME} Python"       # the interpreter copy inside the bundle's MacOS folder
NO_RELAUNCH_ENV = "SDR_NO_RELAUNCH"              # set it to run under the bare interpreter on purpose (tests, debugging)


def real_executable():
    """The Mach-O file this process really runs (a venv's python is a stub; this is what it exec'd), or None."""
    if sys.platform != "darwin":
        return None
    try:
        size = ctypes.c_uint32(4096)
        buf = ctypes.create_string_buffer(size.value)
        if ctypes.CDLL(None)._NSGetExecutablePath(buf, ctypes.byref(size)) != 0:
            return None
        return Path(os.fsdecode(buf.value)).resolve()
    except (OSError, AttributeError):
        return None


def bundle_interpreter(bundle=BUNDLE):
    return Path(bundle) / "Contents" / "MacOS" / INTERPRETER_NAME


def running_inside(bundle=BUNDLE, executable=None):
    executable = executable or real_executable()
    if executable is None:
        return False
    try:
        return Path(bundle).resolve() in executable.parents
    except OSError:
        return False


def site_packages():
    """The venv's site-packages, for PYTHONPATH: the bundle's interpreter has no venv of its own."""
    return sysconfig.get_paths()["purelib"]


def should_relaunch(bundle=BUNDLE, environ=None):
    environ = os.environ if environ is None else environ
    if getattr(sys, "frozen", False):            # a PyInstaller build (tools/build_release.py) IS its own app: the Dock
        return False                             # already knows it, and there is no venv to relaunch into
    return (sys.platform == "darwin" and not environ.get(NO_RELAUNCH_ENV) and not running_inside(bundle)
            and "pytest" not in sys.modules)


def source_stamp(source=None):
    """Identifies the framework interpreter a bundle was built from: its path, size and modification time. The copy
    itself cannot be compared byte for byte - the ad-hoc signature rewrites it."""
    source = source or real_executable()
    if source is None or not source.exists():
        return ""
    st = source.stat()
    return f"{source}\n{st.st_size}\n{int(st.st_mtime)}\n"


def stamp_path(bundle=BUNDLE):
    return Path(bundle) / "Contents" / "Resources" / "interpreter.stamp"


def bundle_is_current(bundle=BUNDLE):
    """The interpreter copy exists and was made from the framework interpreter running now, and the icon is not newer
    than the bundle's."""
    interp = bundle_interpreter(bundle)
    stamp = stamp_path(bundle)
    if not interp.exists() or not stamp.exists() or stamp.read_text() != source_stamp():
        return False
    icns = ROOT / "sdr_tuner" / "data" / "app_icon.icns"
    bundled = Path(bundle) / "Contents" / "Resources" / "app.icns"
    return bundled.exists() and (not icns.exists() or bundled.stat().st_mtime >= icns.stat().st_mtime)


def ensure_bundle(bundle=BUNDLE, build=None):
    """Builds the bundle when it is missing or stale (tools/make_app_bundle.build, unless another builder is given)."""
    if bundle_is_current(bundle):
        return bundle
    if build is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_app_bundle", ROOT / "tools" / "make_app_bundle.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        build = module.build
    return build(bundle)


def relaunch(script, argv, bundle=BUNDLE, execve=os.execve, environ=None):
    """execve into the bundle's interpreter running `script argv` - the same PID, stdio and environment, plus the venv's
    site-packages on PYTHONPATH. Returns only if execve does (it never should)."""
    interp = bundle_interpreter(bundle)
    env = dict(os.environ if environ is None else environ)
    extra = env.get("PYTHONPATH")
    env["PYTHONPATH"] = site_packages() + (os.pathsep + extra if extra else "")
    env[NO_RELAUNCH_ENV] = "1"                                           # the relaunched process never loops
    return execve(str(interp), [str(interp), str(script), *argv], env)


def relaunch_in_bundle_if_needed(script, argv=None):
    """What main.py calls first: re-exec through the bundle when running under Python.app; on any trouble, carry on."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        if not should_relaunch():
            return False
        ensure_bundle()
        relaunch(script, argv)
    except Exception as exc:                       # a missing framework, a failed build, a refused exec: run as we are
        print(f"(running under the bare interpreter - the Dock will use Python's icon at quit: {exc})", file=sys.stderr)
    return False
