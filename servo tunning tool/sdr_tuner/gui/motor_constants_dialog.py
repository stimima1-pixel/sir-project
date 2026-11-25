"""A standalone Ke/Kv/Kt motor-constant converter - no drive connection needed, matching the vendor's own IDE
tool of the same purpose (found there 2026-09-24, reverse-derived and verified against it - see
motor_constants.py). Pick which constant you have with the radio buttons, type it in, the other two update."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QDialog, QFormLayout, QGridLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout

from .. import motor_constants as mc
from .widgets import DoubleSpinBox, SpinBox, make_button, set_prop

FIELDS = (
    ("ke", "Back-EMF Constant (Ke)", "V/(rad/s), peak, line-to-neutral, electrical = flux linkage in Wb"),
    ("kv", "Velocity Constant (Kv)", "RPM/V, peak, line-to-line, mechanical"),
    ("kt", "Torque Constant (Kt)", "N·m/A"),
)


class MotorConstantsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Motor Constants Converter")
        self._source = "ke"

        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.poles = SpinBox()
        self.poles.setRange(2, 200)
        self.poles.setSingleStep(2)
        self.poles.setValue(8)
        form.addRow("Poles Number (Rotor Poles):", self.poles)
        outer.addLayout(form)

        hint = QLabel("Pick which constant you know, type it in - the other two are computed. Set Poles first.")
        set_prop(hint, "muted", "true")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.radios, self.spins = {}, {}
        group = QButtonGroup(self)
        for row, (key, title, units) in enumerate(FIELDS):
            radio = QRadioButton(title)
            radio.setChecked(key == self._source)
            group.addButton(radio)
            radio.toggled.connect(lambda checked, k=key: checked and self._select_source(k))
            spin = DoubleSpinBox()
            spin.setDecimals(8)          # Ke/Kt are commonly ~1e-2..1e-3 - matches the vendor's own tool's own
                                          # precision, which 6 decimals silently truncated (found live: typing the
                                          # real bench drive's own Ke=0.01366658 rounded to 0.013666 before being
                                          # used to compute Kt/Kv, drifting Kv to 80.686 instead of the correct 80.683)
            spin.setRange(0.0, 1_000_000.0)
            spin.valueChanged.connect(lambda _v, k=key: self._on_value_changed(k))
            grid.addWidget(radio, row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(QLabel(units), row, 2)
            self.radios[key], self.spins[key] = radio, spin
        outer.addLayout(grid)

        self.poles.valueChanged.connect(self._recompute)
        self._set_enabled_source(self._source)

        close_btn = make_button(QPushButton("Close"), "primary")
        close_btn.clicked.connect(self.accept)
        outer.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        self._recompute()

    def _select_source(self, key):
        self._source = key
        self._set_enabled_source(key)
        self._recompute()

    def _set_enabled_source(self, key):
        for k, spin in self.spins.items():
            spin.setReadOnly(k != key)

    def _on_value_changed(self, key):
        if key == self._source:
            self._recompute()

    def _recompute(self):
        source_value = self.spins[self._source].value()
        if source_value <= 0.0:      # nothing meaningful typed yet - leave the other two at 0, do not divide by it
            values = {"ke": 0.0, "kv": 0.0, "kt": 0.0, self._source: source_value}
        else:
            ke, kv, kt = mc.other_two(self._source, source_value, self.poles.value())
            values = {"ke": ke, "kv": kv, "kt": kt}
        for key, value in values.items():
            if key == self._source:
                continue
            spin = self.spins[key]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
