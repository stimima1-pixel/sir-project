"""Drive Setup and Motion > Peripherals: the drive's RC PWM / Electronic Gearing, GPIO, Thermistor and Inverter settings -
the parameters that fit no tuning wizard and were otherwise reachable only through Parameters Explorer."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from ..client import DriveError, DriveTimeout
from .params_tab import RISKY
from .tuner import FormPage, read_registers
from .widgets import DoubleSpinBox, make_button, set_prop
from .workers import run_async

GROUPS = [
    ("RC PWM or Electronic Gearing", [(0x3015, 0x01), (0x3015, 0x05), (0x3015, 0x0F), (0x3015, 0x13), (0x3015, 0x10),
                                      (0x3015, 0x11), (0x3015, 0x12), (0x3015, 0x14), (0x3015, 0x04), (0x3015, 0x0E),
                                      (0x3015, 0x20), (0x3015, 0x21)]),
    ("GPIO", [(0x3020, 0x10), (0x3020, 0x11), (0x3020, 0x12), (0x3020, 0x19), (0x3020, 0x20), (0x3020, 0x30)]),
    ("Digital Inputs & Safety (read-only)", [(0x5009, 0x04), (0x5009, 0x05), (0x5009, 0x06), (0x5009, 0x10), (0x5009, 0x20)]),
    ("Thermistor", [(0x3021, 0x10)]),
    ("Inverter", [(0x3002, 0x03), (0x3002, 0x0A), (0x3002, 0x0B), (0x3002, 0x10), (0x3002, 0x30), (0x3002, 0x32),
                 (0x3002, 0x31), (0x3002, 0x33), (0x3002, 0x20)]),
]
NOTE = ("Settings of the drive's optional peripherals: an RC PWM input or electronic gearing, the general-purpose GPIO pins "
       "(Emergency Stop and limit switches, a generic output and input), the motor thermistor, and the inverter's current "
       "sensing and PWM - plus, read-only, those same GPIO pins' and the Safe Torque Off/Interlock's own LIVE status (like "
       "Elmo Application Studio III's own Motion page shows at a glance - this tool never polls telemetry outside a "
       "broadcast, per drive_tab.py's own rule, so Read All is how to check it here instead). Read once when the drive "
       "connects; edited values are written only with Write Changed, after a confirmation.")


class PeripheralsPage(QWidget):
    """A flat set of parameter forms (`FormPage`, shared with the tuning wizards), with its own Read All / Write Changed /
    Revert - there is no wizard here, just the settings, like Parameters Explorer but grouped by peripheral."""
    message = Signal(str, str)      # (text, level) for the window's status bar

    def __init__(self, session):
        super().__init__()
        self.s = session
        self._loaded = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 0)
        header_layout.addWidget(self._commands_card())
        outer.addWidget(header)
        self.form = FormPage("Peripherals", GROUPS, NOTE)
        outer.addWidget(self.form, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(22, 10, 22, 14)
        bar.setSpacing(10)
        self.read_btn = make_button(QPushButton("READ ALL"), "primary")
        self.write_btn = QPushButton("WRITE CHANGED")
        self.revert_btn = QPushButton("REVERT")
        for w in (self.read_btn, self.write_btn, self.revert_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.read_btn.clicked.connect(lambda _c=False: self.read_all())
        self.write_btn.clicked.connect(lambda _c=False: self.write_changed())
        self.form.submitted.connect(self.write_changed)     # Enter in a box writes the changed parameters, like clicking Write Changed
        self.revert_btn.clicked.connect(lambda _c=False: self.revert())
        self.form.changed.connect(self._sync)
        session.probed.connect(self._probed)
        session.disconnected.connect(self._disconnected)
        self._sync()

    def _commands_card(self):
        """Three one-shot commands (not registers, and not continuous - the reference never asks for them to be
        repeated): Electromagnetic Brake, GPIO: Generic Output, GPIO: PWM output. Placed above the parameter
        form, always visible, since they're actions, not settings to read/write/revert."""
        box = QGroupBox("PERIPHERAL COMMANDS")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(10)

        grid.addWidget(self._row_label("Electromagnetic Brake"), 0, 0)
        self.brake_engage_btn = make_button(QPushButton("ENGAGE"), "primary")
        self.brake_disengage_btn = QPushButton("DISENGAGE")
        row = QHBoxLayout()
        row.addWidget(self.brake_engage_btn)
        row.addWidget(self.brake_disengage_btn)
        grid.addLayout(row, 0, 1)

        grid.addWidget(self._row_label("GPIO: Generic Output"), 1, 0)
        self.gpio_high_btn = make_button(QPushButton("SET HIGH"), "primary")
        self.gpio_low_btn = QPushButton("SET LOW")
        row2 = QHBoxLayout()
        row2.addWidget(self.gpio_high_btn)
        row2.addWidget(self.gpio_low_btn)
        grid.addLayout(row2, 1, 1)

        grid.addWidget(self._row_label("GPIO: PWM Output (Duty Cycle)"), 2, 0)
        self.gpio_pwm_value = DoubleSpinBox()
        self.gpio_pwm_value.setRange(0.0, 1.0)
        self.gpio_pwm_value.setDecimals(2)
        self.gpio_pwm_value.setSingleStep(0.05)
        self.gpio_pwm_value.setKeyboardTracking(False)
        self.gpio_pwm_send_btn = make_button(QPushButton("SEND"), "primary")
        row3 = QHBoxLayout()
        row3.addWidget(self.gpio_pwm_value)
        row3.addWidget(self.gpio_pwm_send_btn)
        grid.addLayout(row3, 2, 1)

        self.brake_engage_btn.clicked.connect(lambda: self._send_brake(True))
        self.brake_disengage_btn.clicked.connect(lambda: self._send_brake(False))
        self.gpio_high_btn.clicked.connect(lambda: self._send_gpio_output(True))
        self.gpio_low_btn.clicked.connect(lambda: self._send_gpio_output(False))
        self.gpio_pwm_send_btn.clicked.connect(self._send_gpio_pwm)
        return box

    @staticmethod
    def _row_label(text):
        label = QLabel(text)
        set_prop(label, "muted", "true")
        return label

    def _send_brake(self, engaged):
        client = self.s.client
        if client is None:
            self.message.emit("Connect to a drive first.", "warn")
            return
        try:
            client.set_electromagnetic_brake(engaged)
        except (DriveError, ValueError) as exc:
            self.message.emit(f"Electromagnetic Brake failed: {exc}", "error")
            return
        self.message.emit(f"Electromagnetic Brake {'engaged' if engaged else 'disengaged'}.", "ok")

    def _send_gpio_output(self, high):
        client = self.s.client
        if client is None:
            self.message.emit("Connect to a drive first.", "warn")
            return
        try:
            client.set_gpio_output(high)
        except (DriveError, ValueError) as exc:
            self.message.emit(f"GPIO: Generic Output failed: {exc}", "error")
            return
        self.message.emit(f"GPIO: Generic Output set {'high' if high else 'low'}.", "ok")

    def _send_gpio_pwm(self):
        client = self.s.client
        if client is None:
            self.message.emit("Connect to a drive first.", "warn")
            return
        duty = self.gpio_pwm_value.value()
        try:
            client.set_gpio_pwm(duty)
        except (DriveError, ValueError) as exc:
            self.message.emit(f"GPIO: PWM Output failed: {exc}", "error")
            return
        self.message.emit(f"GPIO: PWM Output duty cycle set to {duty:.2f}.", "ok")

    def _sync(self):
        connected = self.s.is_connected
        client = self.s.client
        self.read_btn.setEnabled(connected)
        self.write_btn.setEnabled(connected and self.form.is_dirty)
        self.revert_btn.setEnabled(self.form.is_dirty)
        for btn, key in ((self.brake_engage_btn, "electromagnetic_brake"), (self.brake_disengage_btn, "electromagnetic_brake"),
                         (self.gpio_high_btn, "gpio_generic_output"), (self.gpio_low_btn, "gpio_generic_output"),
                         (self.gpio_pwm_send_btn, "gpio_pwm_output")):
            btn.setEnabled(bool(connected and client and client.commands[key].defined))

    # ---- connection ---------------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded and self.s.is_connected:
            self.read_all()

    def _probed(self, ok, _message=""):
        self._loaded = False
        if ok and self.isVisible():
            self.read_all()
        self._sync()

    def _disconnected(self):
        self._loaded = False
        self._sync()

    # ---- reading and writing ----------------------------------------------------------------
    def read_all(self):
        client = self.s.client
        if client is None:
            self.message.emit("Connect to a drive first.", "warn")
            return
        regs = [row.reg for row in self.form.rows.values()]

        def job(emit, cancel):
            return read_registers(client, regs, cancel)

        def done(values):
            for key, value in values.items():
                self.form.show_drive_value(key, value)
            self._loaded = True
            missing = len(regs) - len(values)
            self.message.emit(f"Read {len(values)} parameter(s)" + (f" - {missing} could not be read." if missing else "."),
                              "warn" if missing else "info")
            self._sync()

        run_async(job, on_done=done, on_failed=lambda exc: self.message.emit(f"Could not read: {exc}", "error"), owner=self)

    def _confirm(self, text):
        return QMessageBox.question(self, "Write parameters", text) == QMessageBox.StandardButton.Yes

    def write_changed(self):
        client = self.s.client
        if client is None:
            self.message.emit("Connect to a drive first.", "warn")
            return
        items, errors = self.form.collect()
        if errors:
            self.message.emit(f"Cannot write: {errors[0]}" + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""), "error")
            return
        if not items:
            self.message.emit("Nothing to write - edit a value first.", "info")
            return
        power_cycle = [row.reg.name for row, _v in items if row.reg.power_cycle]
        notes = "\n\nThese take effect only after a power cycle of the drive:\n  " + "\n  ".join(power_cycle) if power_cycle else ""
        if any(row.reg.key in RISKY for row, _v in items):
            notes += "\n\n⚠  A wrong value can make the drive unreachable."
        if not self._confirm(f"Write {len(items)} changed parameter(s) to node {self.s.node_id}?{notes}"):
            return

        def job(emit, cancel):
            for row, value in items:
                if cancel.is_set():
                    break
                try:
                    emit((row, client.write(row.reg, value), None))
                except DriveTimeout as exc:
                    emit((row, None, exc))
                    raise DriveTimeout("the drive is not answering - stopped") from None
                except DriveError as exc:
                    emit((row, None, exc))
            return len(items)

        failed = []

        def progress(payload):
            row, stored, exc = payload
            if exc is not None:
                failed.append(row)
                self.form.refuse(row.reg.key, str(exc))
            else:
                self.form.show_drive_value(row.reg.key, stored)

        def done(n):
            self._sync()
            self.message.emit(f"Wrote {n - len(failed)} parameter(s)" + (f" - {len(failed)} failed." if failed else "."),
                              "warn" if failed else "ok")

        def failed_job(exc):
            self._sync()
            self.message.emit(f"Stopped: {exc}", "error")

        run_async(job, on_done=done, on_failed=failed_job, on_progress=progress, owner=self)

    def revert(self):
        self.form.revert()
        self._sync()
