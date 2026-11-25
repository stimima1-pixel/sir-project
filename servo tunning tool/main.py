"""Run this file (PyCharm: right-click > Run 'main') to start the tuning tool.

On a Mac it first re-launches itself through dist/SDR Servo Tuning Tool.app's own interpreter (built on demand), so the
Dock knows the process as the tool - its own icon while it runs AND while it quits - instead of as Python.app
(sdr_tuner/dock_identity.py explains; set SDR_NO_RELAUNCH=1 to skip it)."""
import sys
from pathlib import Path

from sdr_tuner.dock_identity import relaunch_in_bundle_if_needed

if __name__ == "__main__":
    relaunch_in_bundle_if_needed(Path(__file__).resolve())
    from sdr_tuner.app import main
    sys.exit(main())
