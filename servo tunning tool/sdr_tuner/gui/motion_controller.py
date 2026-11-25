"""Motion > Motion Controller: write, run and save a short G-code PROGRAM (several lines) - the
Terminal page's own G-code console (log_tab.py) does one line at a time; this page runs a whole program, one line
after another, reusing the exact same translation and safety checks (gcode.py, gcode_runner.py) - never a separate,
parallel implementation. Comes with a few bundled sample programs (sdr_tuner/data/gcode_samples/*.nc) to load and
run as a starting point, and Open…/Save… for the user's own .nc files."""
import html
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QTextCursor
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTextEdit,
                               QVBoxLayout, QWidget)

from . import gcode, theme
from .gcode_runner import GcodeRunner, combine_action_results
from .gcode_sim import VirtualGcodeRunner
from .widgets import ComboBox, SpinBox, make_button, mono_font

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "gcode_samples"
FILE_FILTER = "G-code programs (*.nc);;All files (*)"
STATE_COLOUR = {"ok": theme.EMERALD, "warn": theme.AMBER, "error": theme.RUBY}
PLACEHOLDER = "One G-code command per line, e.g.:\n\nG1030 X500\nG04 P1\nG1000"
MAX_LOOPS = 9999    # the Loop spin box's own range; 0 has its own special-value text, "Forever"


def sample_paths():
    """The bundled sample programs, alphabetically by file name."""
    return sorted(SAMPLES_DIR.glob("*.nc"))


class _LineNumberGutter(QWidget):
    """The narrow strip a `GcodeEditor` paints its own line numbers into - a plain QWidget child, per Qt's own
    standard recipe for this (QPlainTextEdit has no built-in line-number support); all the actual drawing lives on
    the editor itself (`GcodeEditor.gutter_paint_event`), this just forwards `paintEvent` to it and reports how
    wide it currently needs to be."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.gutter_paint_event(event)


class GcodeEditor(QPlainTextEdit):
    """A QPlainTextEdit with a line-number gutter down its left edge, so a program's own lines match the numbers
    `_log_line()` already reports ("Line N: ..."), the same way any real code editor numbers its lines - the
    standard Qt recipe (`setViewportMargins` reserves the strip, a child widget paints into it, kept in sync via
    `blockCountChanged`/`updateRequest`), not a third-party widget, since PySide6 has no built-in one."""

    def __init__(self):
        super().__init__()
        self._gutter = _LineNumberGutter(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self._update_gutter_width()

    def gutter_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _new_block_count=0):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        r = self.contentsRect()
        self._gutter.setGeometry(QRect(r.left(), r.top(), self.gutter_width(), r.height()))

    def gutter_paint_event(self, event):
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(theme.INPUT))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        painter.setPen(QColor(theme.DIM))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._gutter.width() - 6, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, str(block_number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1


class MotionControllerPage(QWidget):
    """A multi-line G-code editor with RUN / PAUSE / RESUME / STOP, a Loop count, a library of bundled samples, and
    Open…/Save… for the user's own programs. Lines are executed strictly in order; execution stops at the first
    line that fails (a parse error or a refused/failed action) rather than skip it and carry on with what might now
    be a wrong assumption about where the drive is. G04 (Dwell) genuinely pauses here before the next line - the
    one thing the single-line console could not meaningfully do, since it never has a "next line" to pause before.
    PAUSE freezes at the next LINE BOUNDARY (never mid-command - a line already in flight always finishes); Loop
    repeats the whole program a chosen number of times, or forever (0).

    **SIMULATE runs the exact same loaded program with no drive - real or simulated CAN hardware - needed at
    all** (`gcode_sim.VirtualGcodeRunner`, driving the 3D Simulation page's own "Simulation" mode instead of any
    telemetry): reuses this page's own RUN/PAUSE/RESUME/STOP/Loop state machine (`self._simulating` is the only
    new flag - which runner `_advance()` calls into, and whether STOP touches real hardware) rather than a second
    one, since RUN and SIMULATE are simply two different ways to execute the SAME program, never both at once."""
    message = Signal(str, str)      # (text, level) for the window's status bar

    def __init__(self, session, drive_tab_for):
        super().__init__()
        self.s = session
        self.drive_tab_for = drive_tab_for      # node id -> that axis's own DriveTab (main_window.py) - real
                                                 # multi-axis: a program's own lines may address several axes
        self.runner = GcodeRunner(session, drive_tab_for)
        self.sim_runner = VirtualGcodeRunner(session)
        self._running = False
        self._simulating = False     # only meaningful while self._running - which runner is executing the program
        self._stopped = False
        self._paused = False
        self._succeeded = False      # earned only by reaching the last requested loop - not a failure/stop
        self._lines = []             # [(0-based source line number, raw line text)] of the program being run
        self._pos = 0
        self._loops_done = 0         # how many full passes of the program this run has completed (get_loops_counter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 14)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.run_btn = make_button(QPushButton("RUN"), "primary")
        self.run_btn.setToolTip("Run the program, one line at a time, top to bottom. Stops at the first line that "
                                "fails, instead of guessing and carrying on.")
        self.simulate_btn = QPushButton("SIMULATE")
        self.simulate_btn.setToolTip("Preview the program's own motion on the 3D Simulation page - no drive, real "
                                     "or simulated, needed at all. Always starts from the loaded model's own home "
                                     "pose. A speed command (G1030) spins that axis continuously in the preview; "
                                     "a torque command (G1040) shows no motion (there is no load/inertia to model "
                                     "offline); arcs (G02/G03) are not yet supported in this mode.")
        self.pause_btn = QPushButton("PAUSE")
        self.pause_btn.setToolTip("Freeze the program before its next line - a line already in flight always "
                                  "finishes first, nothing is cut off mid-command.")
        self.pause_btn.setEnabled(False)
        self.resume_btn = QPushButton("RESUME")
        self.resume_btn.setToolTip("Continue the program from where it was paused.")
        self.resume_btn.setEnabled(False)
        self.stop_btn = make_button(QPushButton("STOP"), "danger")
        self.stop_btn.setToolTip("Stop the program (does not send anything itself) and stop the drive, the same "
                                 "way the round STOP button does.")
        self.stop_btn.setEnabled(False)
        self.loop_count = SpinBox()
        self.loop_count.setRange(0, MAX_LOOPS)
        self.loop_count.setValue(1)
        self.loop_count.setSpecialValueText("Forever")
        self.loop_count.setToolTip("How many times to run the whole program - 1 (the default) runs it once; "
                                   "\"Forever\" (0) repeats it until STOP.")
        self.samples = ComboBox()
        self.samples.addItem("Samples…", None)
        for path in sample_paths():
            self.samples.addItem(path.stem.replace("_", " ").title(), str(path))
        self.samples.setToolTip("Load one of the bundled example programs (replaces what is in the editor). They "
                                "all address axis 0 (X / [0]) - change that to match your connected drive's own "
                                "node ID before running, or a line naming a different axis will be skipped (see ?).")
        self.open_btn = QPushButton("Open…")
        self.save_btn = QPushButton("Save…")
        self.output_toggle = QPushButton("Hide Output")
        self.output_toggle.setCheckable(True)
        self.output_toggle.setToolTip("Minimise the program output log below, to give the editor above more room")
        self.help_btn = QPushButton("?")
        self.help_btn.setFixedWidth(28)
        self.help_btn.setToolTip(gcode.HELP)
        # Keep keyboard focus in the editor: the app's Fusion style lets a push button take focus on a mouse click,
        # so after clicking RUN a stray Space/Return would press whichever button held focus (live, 2026-09-25: the
        # space in a typed line opened the Open… dialog) instead of typing into the program. Same policy the
        # ribbon's own buttons already use (ribbon.py); the Loop box and Samples combo keep focus - they are typed in.
        for button in (self.run_btn, self.simulate_btn, self.pause_btn, self.resume_btn, self.stop_btn,
                       self.open_btn, self.save_btn, self.output_toggle, self.help_btn):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bar.addWidget(self.run_btn)
        bar.addWidget(self.simulate_btn)
        bar.addWidget(self.pause_btn)
        bar.addWidget(self.resume_btn)
        bar.addWidget(self.stop_btn)
        bar.addSpacing(10)
        bar.addWidget(QLabel("Loop"))
        bar.addWidget(self.loop_count)
        bar.addSpacing(10)
        bar.addWidget(self.samples)
        bar.addWidget(self.open_btn)
        bar.addWidget(self.save_btn)
        bar.addWidget(self.output_toggle)
        bar.addStretch(1)
        bar.addWidget(self.help_btn)
        outer.addLayout(bar)

        self.editor = GcodeEditor()
        self.editor.setObjectName("gcodeEditor")
        self.editor.setFont(mono_font(13))
        self.editor.setPlaceholderText(PLACEHOLDER)
        outer.addWidget(self.editor, 1)

        self.output = QTextEdit()
        self.output.setObjectName("gcodeOutput")
        self.output.setReadOnly(True)
        self.output.setFont(mono_font(12))
        self.output.setFixedHeight(160)
        self.output.setPlaceholderText("Each line's result appears here as the program runs.")
        outer.addWidget(self.output)
        self.output_toggle.toggled.connect(self._toggle_output)

        self.run_btn.clicked.connect(self._run_program)
        self.simulate_btn.clicked.connect(self._simulate_program)
        self.pause_btn.clicked.connect(self._pause_program)
        self.resume_btn.clicked.connect(self._resume_program)
        self.stop_btn.clicked.connect(self._stop_program)
        self.samples.activated.connect(self._load_sample)
        self.open_btn.clicked.connect(self._open_dialog)
        self.save_btn.clicked.connect(self._save_dialog)
        self.help_btn.clicked.connect(lambda: self._log(gcode.HELP, "warn"))

    def _toggle_output(self, hidden):
        self.output.setVisible(not hidden)
        self.output_toggle.setText("Show Output" if hidden else "Hide Output")

    # ---- loading a program -------------------------------------------------------------------
    def _confirm_replace(self):
        if not self.editor.toPlainText().strip():
            return True
        return QMessageBox.question(self, "Replace program",
                                    "Replace the current program in the editor?") == QMessageBox.StandardButton.Yes

    def _load_sample(self, index):
        path = self.samples.itemData(index)
        self.samples.setCurrentIndex(0)
        if path is None or not self._confirm_replace():
            return
        self.editor.setPlainText(Path(path).read_text())
        self.message.emit(f"Loaded sample: {Path(path).stem}", "info")
        self._log(f"Loaded {Path(path).name} - it addresses axis 0 (X / [0]). Change that to match your connected "
                  "drive's own node ID before running, or lines naming a different axis will be skipped.", "warn")

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open G-code Program", "", FILE_FILTER)
        if not path or not self._confirm_replace():
            return
        try:
            text = Path(path).read_text()
        except OSError as exc:
            self.message.emit(f"Could not open {path}: {exc}", "error")
            return
        self.editor.setPlainText(text)
        self.message.emit(f"Opened {Path(path).name}", "info")

    def _save_dialog(self):
        """Defaults to the bundled samples folder: name the file and save there, and it shows up in the Samples
        list right away (`_refresh_samples`) - a simple way to "save it in software" as the user asked, rather than
        just anywhere on disk. Saving somewhere else instead (the dialog can still navigate anywhere) works exactly
        the same, it just will not appear as a Sample afterwards."""
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save G-code Program", str(SAMPLES_DIR), FILE_FILTER)
        if not path:
            return
        if not path.lower().endswith(".nc"):
            path += ".nc"
        try:
            Path(path).write_text(self.editor.toPlainText())
        except OSError as exc:
            self.message.emit(f"Could not save {path}: {exc}", "error")
            return
        self._refresh_samples()
        self.message.emit(f"Saved {Path(path).name}", "ok")

    def _refresh_samples(self):
        self.samples.blockSignals(True)
        self.samples.clear()
        self.samples.addItem("Samples…", None)
        for path in sample_paths():
            self.samples.addItem(path.stem.replace("_", " ").title(), str(path))
        self.samples.blockSignals(False)

    # ---- running --------------------------------------------------------------------------
    def _run_program(self):
        if self._running:
            return
        if self.s.client is None:
            self._log("Connect to a drive first.", "warn")
            return
        if not self._start(simulating=False):
            return
        self._advance_soon()

    def _simulate_program(self):
        """Same program, same state machine (RUN and SIMULATE are two ways to execute it, never both at once) -
        but no client is required, and the virtual runner always starts from the loaded model's own home pose
        (`sim_runner.reset()`), the user's own choice: a clean, repeatable preview every time, independent of
        whatever the 3D Simulation page's own LIVE mode or an earlier Simulate run left on screen."""
        if self._running:
            return
        if self.s.kinematics_model is None:
            self._log("Load a kinematics model first - see Item Configuration's Kinematics Model group.", "warn")
            return
        if not self._start(simulating=True):
            return
        self.sim_runner.reset()
        self._advance_soon()

    def _start(self, simulating):
        """Shared setup for RUN and SIMULATE: parses the editor's own lines and resets the run state. -> False (a
        message was already logged) if there is nothing to run."""
        lines = [(i, raw) for i, raw in enumerate(self.editor.toPlainText().splitlines()) if gcode.strip_comment(raw)]
        if not lines:
            self._log("Nothing to run - the program is empty.", "warn")
            return False
        self.output.clear()
        self._lines, self._pos = lines, 0
        self._running, self._simulating, self._stopped, self._paused = True, simulating, False, False
        self._in_flight = False      # a dispatched line has not finished yet - see _resume_program
        self._loops_done = 0
        self._succeeded = False      # earned only by _loop_completed() reaching its target - not by a failure/stop
        self._sync()
        return True

    def _pause_program(self):
        """Freezes before the NEXT line - a line already dispatched always finishes (its own `done()` below just
        notices `_paused` instead of advancing), never cut off mid-command."""
        if not self._running or self._paused:
            return
        self._paused = True
        self._sync()
        self._log("Pausing before the next line…", "warn")

    def _resume_program(self):
        if not self._running or not self._paused:
            return
        self._paused = False
        self._sync()
        self._log("Resumed.", "warn")
        if not self._in_flight:      # a line still running (PAUSE only freezes before the NEXT one) advances on its
            self._advance_soon()     # own when it finishes - resuming used to dispatch that very line a second time

    def _stop_program(self, *, stop_drives=True):     # keyword-only: the STOP button's clicked(bool) must not fill it
        """STOP: ends the program AND everything of its current line still in flight (`GcodeRunner.cancel` - a
        Feedback Settings check that finishes later sends nothing, no further axis/waypoint/action runs), then
        stops every connected axis like the round STOP button does - unless that button is what called this
        (`stop_drives=False`, main_window.py's emergency_stop, which stops the axes itself, once)."""
        if not self._running:
            return
        self._stopped, self._paused = True, False
        if self._simulating:
            self.sim_runner.cancel()       # no real hardware: ends the virtual line and freezes the virtual axes
        else:
            self.runner.cancel()
            if stop_drives:
                for node in self.s.all_clients():
                    drive = self.drive_tab_for(node)
                    if drive is not None:
                        drive.emergency_stop()
        self._log("Stopped.", "warn")
        self._finish()

    def _advance_soon(self):
        """Schedules `_advance()` on the event loop instead of calling it directly. Needed because a line whose
        dispatch resolves synchronously (no dwell, no Feedback Settings check - e.g. a plain speed command) would
        otherwise call back into `_advance()` from WITHIN the current call, line after line, all on one Python call
        stack - harmless for an ordinary 10-15 line program, but a real crash risk (Python's own recursion limit)
        for a long Loop, and a guaranteed one for "Forever" (0), which has no natural bound at all. A 0 ms
        `QTimer.singleShot` unwinds the stack between lines and lets STOP/PAUSE clicks be processed in between,
        at a delay far below anything a user could perceive."""
        QTimer.singleShot(0, self._advance)

    def _advance(self):
        if self._stopped:
            self._finish()
            return
        if self._pos >= len(self._lines):
            self._loop_completed()
            return
        line_no, raw = self._lines[self._pos]
        self._highlight(line_no)
        try:
            actions = gcode.parse_line(raw)     # usually one action - several, concatenated, for a real multi-axis
                                                 # line (DOG.NC's own gait) - each is run, in order, before this
                                                 # line is considered done and the next one starts
        except gcode.GcodeError as exc:
            self._log_line(line_no, raw, str(exc), "error")
            self._finish()
            return

        def all_done(results):
            self._in_flight = False
            text, state = combine_action_results(results)
            self._log_line(line_no, raw, text, state)
            if self._stopped:
                self._finish()
                return
            # a noop's own "warn" ("G93 ... not tracked") is harmless and never halts the program; a "warn" from
            # ANY OTHER action IS a refusal (no encoder configured, no telemetry yet, not connected...) and does -
            # decided per action (not from the line's own combined state above, which is only for the log) so one
            # harmless noop concatenated alongside a real move never masks - or is masked by - the other's own result
            halts = any(state == "error" or (state == "warn" and action["action"] != "noop")
                       for action, text, state in results)
            if halts:
                self._finish()
                return
            self._pos += 1
            if self._paused:
                self._log(f"Paused before line {self._pos + 1}.", "warn")
                return
            self._advance_soon()

        def progress(text, state):
            self._log_line(line_no, raw, text, state)

        self._in_flight = True
        if self._simulating:
            self.sim_runner.run_actions(actions, all_done, on_progress=progress)
        else:
            self.runner.run_actions(actions, self.run_btn, all_done, on_progress=progress)

    def _loop_completed(self):
        """The program reached its last line - repeat it if Loop asks for that (0 = forever, otherwise a count),
        otherwise finish, successfully. `get_loops_counter`-equivalent: `self._loops_done`, incremented on every
        full pass."""
        self._loops_done += 1
        target = self.loop_count.value()
        if target == 0 or self._loops_done < target:
            self._log(f"Loop {self._loops_done} finished - repeating.", "ok")
            self._pos = 0
            self._advance_soon()
            return
        self._succeeded = True
        self._finish()

    def _finish(self):
        if not self._running:
            return
        self._running, self._paused = False, False
        self._highlight(None)
        self._sync()
        if self._succeeded:      # NOT true after a parse error, a failed/refused line, or STOP - only a real finish
            loops = f" ({self._loops_done} loop(s))" if self._loops_done > 1 else ""
            label = "Simulation" if self._simulating else "Program"
            self._log(f"{label} finished.{loops}", "ok")
            self.message.emit(f"G-code {label.lower()} finished.{loops}", "ok")

    def _sync(self):
        self.run_btn.setEnabled(not self._running)
        self.simulate_btn.setEnabled(not self._running)
        self.pause_btn.setEnabled(self._running and not self._paused)
        self.resume_btn.setEnabled(self._running and self._paused)
        self.stop_btn.setEnabled(self._running)
        self.loop_count.setEnabled(not self._running)

    # ---- feedback -----------------------------------------------------------------------------
    def _highlight(self, line_no):
        """Marks the line currently executing in the editor - `theme.GOLD_LO`, the same accent already used
        elsewhere for "this row is active/edited" (params_tab.py's DIRTY_STYLE), not a new colour."""
        if line_no is None:
            self.editor.setExtraSelections([])
            return
        cursor = QTextCursor(self.editor.document().findBlockByNumber(line_no))
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(QColor(theme.GOLD_LO))
        self.editor.setExtraSelections([selection])

    def _log_line(self, line_no, raw, text, state):
        self._log(f"Line {line_no + 1}: {raw.strip()}  ->  {text}", state)

    def _log(self, text, state):
        colour = STATE_COLOUR.get(state, theme.MUTED)
        self.output.append(f'<span style="color:{colour}">{html.escape(text)}</span>')
        bar = self.output.verticalScrollBar()
        bar.setValue(bar.maximum())
