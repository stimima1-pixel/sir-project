import sys, time, io, contextlib
sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from sdr_tuner.gui.main_window import MainWindow

app = QApplication([])
app.setQuitOnLastWindowClosed(False)   # an accidental close (or Cmd+Q) must not end this process -
                                        # keep the Qt loop (and this console) alive regardless
win = MainWindow()
win.show()
win.quick_tuner._confirm = lambda text: True
win.advanced_tuner._confirm = lambda text: True

def _close_event(event):
    event.ignore()
    win.showMinimized()
win.closeEvent = _close_event

CMD_FILE = "/tmp/sdr_gui_cmd.txt"
RESULT_FILE = "/tmp/sdr_gui_result.txt"
open(CMD_FILE, "w").close()
open(RESULT_FILE, "w").close()

_last_pos = [0]

def poll():
    try:
        with open(CMD_FILE) as f:
            f.seek(_last_pos[0])
            new = f.read()
            _last_pos[0] = f.tell()
    except FileNotFoundError:
        return
    if not new.strip():
        return
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(new, "<console>", "exec"), globals())
    except Exception as e:
        buf.write(f"ERROR: {e!r}\n")
    with open(RESULT_FILE, "a") as rf:
        rf.write(buf.getvalue())
        rf.write("---END---\n")

timer = QTimer()
timer.timeout.connect(poll)
timer.start(200)

win.connect_btn.click()

app.exec()
