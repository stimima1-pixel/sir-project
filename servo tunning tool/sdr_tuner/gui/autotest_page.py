"""Automatic Test (Servo Drive): RUN TESTS - one button, the checks and the measurements, a result table; BETA - one
confirmation, then round after round: measure, propose, apply, measure again, until the loop is as fast as the drive
allows (peaking or overshoot appears and the last clean gain is put back), the sampling can verify no more, the gain
cap is reached, or MAX_ROUNDS rounds are done. STOP (and the round STOP / Esc) ends either at once: the drive is
stopped and its telemetry put back by the engine's own finally blocks. The arithmetic and the drive procedure live in
sdr_tuner/autotest.py (no Qt); this page only drives it in a worker and shows what it reports."""
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from .. import autotest
from ..client import DriveError, DriveTimeout
from . import theme
from .tuner import card
from .widgets import make_button, set_prop
from .workers import run_async

STATUS_COLOUR = {"ok": theme.EMERALD, "warn": theme.AMBER, "fail": theme.RUBY, "skip": theme.DIM}
STATUS_TEXT = {"ok": "OK", "warn": "CHECK", "fail": "FAIL", "skip": "SKIPPED"}


class AutomaticTestPage(QScrollArea):
    message = Signal(str, str)

    def __init__(self, session):
        super().__init__()
        self.title = "Automatic Test"
        self.s = session
        self._cancel = None
        self._running = ""              # "" | "tests" | "beta"
        self.report = None
        self.applied = []               # (Proposal, read-back) of everything Beta wrote this session
        self.beta_state = {}
        self._ended = False             # an outcome is on the status line: refresh must not replace it with "Ready."
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(22, 14, 22, 18)
        col.setSpacing(12)

        note = QLabel("RUN TESTS reads the drive, checks it, and measures its control loops (a step test and a chirp on the current "
                      "loop, and on the speed loop when an encoder is configured) - the motor moves briefly at a small signal. "
                      "BETA takes the result, works out what to change to make the loops as fast as the drive allows, applies it "
                      "once you have agreed to the plan, measures again, and repeats until the loop starts to peak or overshoot "
                      "(then the last clean gain goes back), the sampling can verify no more, or the gain cap is reached. "
                      "What Beta writes lives in the drive's RAM until SAVE TO FLASH.")
        note.setObjectName("formNote")
        note.setWordWrap(True)
        col.addWidget(note)

        box, lay = card("What to run")
        self.autotune = QCheckBox("Run the drive's own automatic tuning first (moves the motor for about 30 s and rewrites the "
                                  "Datasheet and Control Laws in flash)")
        self.current = QCheckBox("Current loop: step test and chirp (a quarter of Maximum Continuous Current, at most 1 A)")
        self.speed = QCheckBox("Speed loop: step test and chirp - only when an encoder is configured")
        for w in (self.autotune, self.current, self.speed):
            w.setChecked(True)
            lay.addWidget(w)
        col.addWidget(box)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.run_btn = make_button(QPushButton("RUN TESTS"), "primary")
        self.beta_btn = make_button(QPushButton("BETA: TUNE"), "primary")
        self.stop_btn = make_button(QPushButton("STOP"), "danger")
        self.save_btn = QPushButton("SAVE TO FLASH")
        self.save_btn.setToolTip("Persist what Beta wrote (and everything else in the drive's RAM) across power cycles")
        for b in (self.run_btn, self.beta_btn, self.stop_btn, self.save_btn):
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("bigStatus")
        self.status.setWordWrap(True)
        col.addWidget(self.status)

        box, lay = card("Results")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Check", "Result", "Value", "Note"])
        head = self.table.horizontalHeader()
        for column, width in ((0, 200), (1, 90), (2, 260)):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in range(4):
            self.table.horizontalHeaderItem(column).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(260)
        lay.addWidget(self.table)
        col.addWidget(box)

        box, lay = card("Log")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        lay.addWidget(self.log)
        col.addWidget(box)
        col.addStretch(1)

        self.run_btn.clicked.connect(lambda _c=False: self.start_tests())
        self.beta_btn.clicked.connect(lambda _c=False: self.start_beta())
        self.stop_btn.clicked.connect(lambda _c=False: self.stop())
        self.save_btn.clicked.connect(lambda _c=False: self.save_to_flash())
        for signal in (session.connected, session.probed, session.disconnected):
            signal.connect(lambda *_a: self.refresh())
        session.disconnecting.connect(lambda *_a: self.stop())
        self.refresh()

    # ---- state ------------------------------------------------------------------------------
    @property
    def running(self):
        return bool(self._running)

    def options(self):
        return {"autotune": self.autotune.isChecked(), "current": self.current.isChecked(), "speed": self.speed.isChecked()}

    def refresh(self):
        connected = self.s.client is not None
        running = self.running
        self.run_btn.setEnabled(connected and not running)
        self.beta_btn.setEnabled(connected and not running)
        self.stop_btn.setEnabled(running)
        self.save_btn.setEnabled(connected and not running and bool(self.applied))
        for w in (self.autotune, self.current, self.speed):
            w.setEnabled(not running)
        if not connected and not running:
            self._set_status("Connect to a drive first.", "warn")
        elif not running and not self._ended:
            self._set_status("Ready.", "ok")

    def _set_status(self, text, level=""):
        self.status.setText(text)
        set_prop(self.status, "state", level)

    def _log(self, text, level=""):
        colour = {"ok": theme.EMERALD, "warn": theme.AMBER, "error": theme.RUBY}.get(level, theme.TEXT)
        self.log.append(f"<span style='color:{colour}'>[{time.strftime('%H:%M:%S')}]  {text}</span>")

    def _confirm(self, title, text):
        return QMessageBox.question(self, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _plan_text(self):
        opts = self.options()
        lines = []
        if opts["autotune"]:
            lines.append("•  The drive's own automatic tuning runs first: the motor moves for about 30 s, the Datasheet and Control "
                         "Laws are rewritten in flash.")
        if opts["current"]:
            lines.append("•  Current loop: a square-wave step test and a chirp at a quarter of Maximum Continuous Current (at most "
                         "1 A), about 10 s of motion.")
        if opts["speed"]:
            lines.append("•  Speed loop, only if an encoder is configured: the same at 10 Hz electrical, about 20 s of motion.")
        lines.append("•  For each measurement the telemetry is switched to the loop's reference and response and put back after.")
        lines.append("•  STOP (or Esc) ends it at once.")
        return "\n".join(lines)

    # ---- RUN TESTS ----------------------------------------------------------------------------
    def start_tests(self):
        client = self.s.client
        if client is None or self.running:
            return
        if not any(self.options().values()):
            self.message.emit("Nothing selected to run.", "warn")
            return
        if not self._confirm("Automatic Test", f"Run the automatic test on node {self.s.node_id}?\n\n{self._plan_text()}"):
            return
        self._begin("tests", "Running the tests…")
        options = self.options()
        self._cancel = run_async(lambda emit, cancel: autotest.run_tests(client, options, cancel, lambda text: emit(("status", text))),
                                 on_done=self._tests_done, on_failed=self._failed, on_progress=self._progress, owner=self)

    def _tests_done(self, report):
        self._show_report(report)
        self._end("Tests complete: " + self._verdict(report), "ok" if self._worst(report) != "fail" else "warn")
        self.message.emit("Automatic test complete.", "ok")

    # ---- BETA ------------------------------------------------------------------------------------
    def start_beta(self):
        client = self.s.client
        if client is None or self.running:
            return
        options = self.options()
        if not options["current"] and not options["speed"]:
            self.message.emit("Beta needs at least one loop measurement selected.", "warn")
            return
        plan = (f"Let Beta tune node {self.s.node_id}?\n\n{self._plan_text()}\n\n"
                f"Then, up to {autotest.MAX_ROUNDS} rounds, without asking again:\n"
                f"•  FOC T is set to L/R if it is not within {autotest.T_TOLERANCE:.0%} of it.\n"
                f"•  FOC Kp is raised by half per round while the current loop stays clean (no peak above {autotest.PEAK_LIMIT_DB:g} dB, "
                f"no step overshoot above {autotest.OVERSHOOT_LIMIT_PCT:g} %, and - where the Nyquist crossings are in range - a phase "
                f"margin of at least {autotest.PHASE_MARGIN_FLOOR_DEG:g}° and a gain margin of at least {autotest.GAIN_MARGIN_FLOOR_DB:g} dB); "
                f"when a limit appears the last clean gain goes back and Beta stops. It also stops when the bandwidth outruns what "
                f"the sampling can verify or the gain reaches its cap.\n"
                "•  With an encoder: a notch filter on a resonance peak of the speed loop, and ESC Kp raised the same way.\n"
                "•  Everything is written to the drive's RAM only - SAVE TO FLASH afterwards makes it stay.\n"
                "•  STOP (or Esc) ends it at once; what was already applied stays in RAM.")
        if not self._confirm("Beta", plan):
            return
        self.beta_state = {}
        self._begin("beta", "Beta: round 1 - measuring…")

        def job(emit, cancel):
            state, applied_all, report = {}, [], None
            opts = dict(options)
            for round_no in range(1, autotest.MAX_ROUNDS + 1):
                emit(("status", f"Beta: round {round_no} - measuring…"))
                report = autotest.run_tests(client, opts, cancel, lambda text, n=round_no: emit(("status", f"Beta round {n}: {text}")))
                opts["autotune"] = False                        # the drive's own procedure runs once, at most
                emit(("report", report))
                proposals = autotest.propose(report, state)
                emit(("proposals", round_no, list(proposals), state.get("done")))
                if not proposals:
                    break
                if cancel.is_set():
                    raise autotest.Cancelled()
                applied = autotest.apply(client, proposals)
                applied_all += applied
                emit(("applied", round_no, applied))
                if "done" in state:                              # a back-off: measure once more to show where it ended
                    emit(("status", "Beta: final measurement…"))
                    report = autotest.run_tests(client, opts, cancel, lambda text: emit(("status", f"Beta final: {text}")))
                    emit(("report", report))
                    break
            else:
                state.setdefault("done", f"the round limit ({autotest.MAX_ROUNDS}) was reached")
            return state.get("done", "finished"), applied_all, report

        self._cancel = run_async(job, on_done=self._beta_done, on_failed=self._failed, on_progress=self._progress, owner=self)

    def _beta_done(self, result):
        reason, applied, report = result
        self.applied += applied
        if report is not None and report is not self.report:
            self._show_report(report)
        changed = ", ".join(f"{p.name} {p.old:g} → {value:g}" for p, value in applied) or "nothing"
        self._end(f"Beta finished: {reason}. Changed: {changed}." + (" SAVE TO FLASH keeps it." if applied else ""), "ok")
        self.message.emit(f"Beta finished: {reason}.", "ok")

    # ---- shared -------------------------------------------------------------------------------
    def _begin(self, what, text):
        self._running, self._ended = what, False
        self.log.clear() if what == "tests" else None
        self._log(text)
        self._set_status(text, "warn")
        self.refresh()

    def _end(self, text, level):
        self._running, self._cancel, self._ended = "", None, True
        self._log(text, level)
        self._set_status(text, level)
        self.refresh()

    def _progress(self, payload):
        kind = payload[0]
        if kind == "status":
            self._set_status(payload[1], "warn")
        elif kind == "report":
            self._show_report(payload[1])
        elif kind == "proposals":
            _k, round_no, proposals, done = payload
            if proposals:
                for p in proposals:
                    self._log(f"Round {round_no}: {p.name} {p.old:g} → {p.new:g} - {p.reason}", "warn")
            else:
                self._log(f"Round {round_no}: nothing to change - {done or 'finished'}", "ok")
        elif kind == "applied":
            _k, round_no, applied = payload
            self._log(f"Round {round_no}: applied " + ", ".join(f"{p.name} = {value:g}" for p, value in applied), "ok")

    def _failed(self, exc):
        if isinstance(exc, autotest.Cancelled):
            self._end("Stopped. The drive is stopped and its telemetry is back as it was.", "warn")
            self.message.emit("Automatic test stopped.", "warn")
        elif isinstance(exc, (DriveError, DriveTimeout, ValueError)):
            self._end(f"Failed: {exc}", "error")
            self.message.emit(f"Automatic test failed: {exc}", "error")
        else:
            self._end(f"Failed: {exc!r}", "error")
            raise exc

    def stop(self):
        if self._cancel is not None:
            self._cancel.set()
            self._set_status("Stopping…", "warn")

    def emergency(self):
        """STOP / Esc, from anywhere in the window."""
        self.stop()

    # ---- the report --------------------------------------------------------------------------
    @staticmethod
    def _worst(report):
        order = {"ok": 0, "skip": 1, "warn": 2, "fail": 3}
        return max((c.status for c in report.checks), key=lambda s: order[s], default="ok")

    @staticmethod
    def _verdict(report):
        n = {s: sum(1 for c in report.checks if c.status == s) for s in ("ok", "warn", "fail", "skip")}
        parts = [f"{n['ok']} OK"]
        if n["warn"]:
            parts.append(f"{n['warn']} to check")
        if n["fail"]:
            parts.append(f"{n['fail']} failed")
        if n["skip"]:
            parts.append(f"{n['skip']} skipped")
        summary = ", ".join(parts)
        if report.current is not None and report.current.bandwidth_hz is not None:
            summary += f". Current loop -3 dB at {report.current.bandwidth_hz:.3g} Hz"
        if report.speed is not None and report.speed.bandwidth_hz is not None:
            summary += f", speed loop at {report.speed.bandwidth_hz:.3g} Hz"
        return summary + "."

    def _show_report(self, report):
        self.report = report
        self.table.setRowCount(len(report.checks))
        for row, check in enumerate(report.checks):
            for column, text in enumerate((check.name, STATUS_TEXT[check.status], check.value, check.note)):
                item = QTableWidgetItem(str(text))
                if column == 1:
                    item.setForeground(QColor(STATUS_COLOUR[check.status]))
                self.table.setItem(row, column, item)
        for m in (report.current, report.speed):
            if m is None:
                continue
            bw = f"{m.bandwidth_hz:.3g} Hz" if m.bandwidth_hz is not None else "not within range"
            rise = f"{m.rise_ms:.1f} ms rise, {m.overshoot_pct:.0f} % overshoot" if m.rise_ms is not None else "no clean step"
            peak = f"{m.peak_db:+.1f} dB peak" if m.peak_db is not None else "no peak figure"
            mg = m.margins
            margin = (f", phase margin {mg.phase_margin_deg:.0f}° at {mg.gain_crossover_hz:.3g} Hz" if mg is not None and mg.phase_margin_deg is not None
                      else ", crossover beyond the range")
            margin += f", gain margin {mg.gain_margin_db:.1f} dB" if mg is not None and mg.gain_margin_db is not None else ""
            self._log(f"{m.loop.capitalize()} loop at {m.amplitude:g}: -3 dB {bw}, {rise}, {peak}{margin}, coherent to "
                      f"{m.coherent_to_hz:.3g} Hz, sampled at {m.sample_hz:.0f} Hz" if m.coherent_to_hz is not None else
                      f"{m.loop.capitalize()} loop at {m.amplitude:g}: {rise}; the chirp was not coherent")

    # ---- flash -------------------------------------------------------------------------------
    def save_to_flash(self):
        client = self.s.client
        if client is None or self.running:
            return
        if not self._confirm("Save to Flash", f"Save the whole current configuration of node {self.s.node_id} to its flash?\n\n"
                             "Everything the drive holds right now - what Beta wrote, the node ID, the CAN bit rate, the telemetry "
                             "mapping and rates - becomes what it loads at power-up."):
            return
        self._set_status("Saving the configuration to flash…", "warn")

        def done(_result):
            self._set_status("Configuration saved to flash.", "ok")
            self._log("Saved to flash.", "ok")
            self.message.emit(f"Node {self.s.node_id}: configuration saved to flash.", "ok")

        def failed(exc):
            self._set_status(f"Save to flash FAILED - {exc}", "error")
            self.message.emit(f"Save to flash failed: {exc}", "error")

        run_async(lambda emit, cancel: client.save_to_flash(), on_done=done, on_failed=failed, owner=self)
