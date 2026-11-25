"""The text-cleaning helpers of tools/import_device_reference.py (the PDF itself is not needed)."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

sys.modules.setdefault("pymupdf", types.ModuleType("pymupdf"))            # only the PDF reading needs it, not these helpers
spec = importlib.util.spec_from_file_location("import_device_reference",
                                              Path(__file__).resolve().parent.parent / "tools" / "import_device_reference.py")
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)


def test_flat_undoes_the_word_layout():
    assert imp.flat("Speed:\nElectrical  Frequency") == "Speed: Electrical Frequency"
    assert imp.flat("Acceleration/De celeration Control") == "Acceleration/Deceleration Control"
    assert imp.flat("Feed- Forward (Hz)") == "Feed-Forward (Hz)"
    assert imp.flat("moment of inertia kg*m 2") == "moment of inertia kg*m^2"
    assert imp.flat(None) == ""


def test_option_lists_come_from_the_bullets_at_the_start():
    raw = "• 0: No fault\n• 1: Driver IC Fault\n• 3: =1+2\nSome text 7: not an option"
    assert imp.options_from(raw) == {0: "No fault", 1: "Driver IC Fault", 3: "=1+2"}
    assert imp.options_from("plain description\n• 1: after text") == {}                   # bullets must open the cell


def test_a_wrapped_option_label_is_joined():
    raw = "• 0: idle low\n• 1: idle high (the clock line\nrests high between frames)\nnext paragraph"
    assert imp.options_from(raw) == {0: "idle low", 1: "idle high (the clock line rests high between frames)"}


def test_a_non_zero_bullet_means_the_list_is_not_closed():
    raw = "• 0: Not used\n• Non-zero: Telemetry Mapping Code"
    assert imp.options_from(raw) == {}                                       # any number is valid: no drop-down


@pytest.mark.parametrize("units,expected", [("-90 to 90 deg", (-90.0, 90.0)), ("0-1", (0.0, 1.0)), ("0.5 to 2", (0.5, 2.0)),
                                            ("Hz", (None, None)), ("90 to 10", (None, None)), ("", (None, None))])
def test_ranges_from_the_units_column(units, expected):
    assert imp.range_from(units) == expected
