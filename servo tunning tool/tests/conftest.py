import os
import sys
from pathlib import Path

# Qt must run without a screen in tests; must be set before PySide6 is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.setswitchinterval(0.001)
