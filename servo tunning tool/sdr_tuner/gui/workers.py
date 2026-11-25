"""Run blocking drive I/O off the GUI thread; results come back on the GUI thread."""
import threading

import shiboken6
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

_alive = set()      # keeps relays referenced until their job has finished


class _Relay(QObject):
    """Lives in the GUI thread, so signals emitted by the worker are delivered there."""
    progress = Signal(object)
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, on_done, on_failed, on_progress, owner):
        super().__init__()
        self._on_done, self._on_failed, self._on_progress, self._owner = on_done, on_failed, on_progress, owner
        self.progress.connect(self._handle_progress)
        self.done.connect(self._handle_done)
        self.failed.connect(self._handle_failed)

    def _gone(self):
        """The owner (the page the callbacks write to) has been deleted - a closed window, a disconnected axis's
        pages - while the job was still running: its result has nowhere to go (2026-09-27: two full-suite teardowns
        saw a Drive Load / Read All land on a deleted QStackedWidget / QCheckBox)."""
        return self._owner is not None and not shiboken6.isValid(self._owner)

    @Slot(object)
    def _handle_progress(self, payload):
        if self._on_progress and not self._gone():
            self._on_progress(payload)

    @Slot(object)
    def _handle_done(self, result):
        _alive.discard(self)
        if self._on_done and not self._gone():
            self._on_done(result)

    @Slot(object)
    def _handle_failed(self, exc):
        _alive.discard(self)
        if self._gone():
            return
        if self._on_failed:
            self._on_failed(exc)
        else:
            raise exc


class _Job(QRunnable):
    def __init__(self, fn, relay, cancel):
        super().__init__()
        self._fn, self._relay, self._cancel = fn, relay, cancel

    def run(self):
        try:
            result = self._fn(self._relay.progress.emit, self._cancel)
        except BaseException as exc:    # noqa: BLE001 - forwarded to the GUI thread
            self._relay.failed.emit(exc)
        else:
            self._relay.done.emit(result)


def run_async(fn, on_done=None, on_failed=None, on_progress=None, owner=None):
    """Run fn(emit, cancel) in a worker thread. emit(payload) -> on_progress(payload) in the GUI thread;
    the return value -> on_done(result); an exception -> on_failed(exc). Returns a threading.Event that
    asks the job to stop early (fn has to check it). `owner`: the QObject (the page) the callbacks write to -
    once it has been deleted, whatever the job still delivers is dropped instead of reaching a dead widget."""
    cancel = threading.Event()
    relay = _Relay(on_done, on_failed, on_progress, owner)
    _alive.add(relay)
    QThreadPool.globalInstance().start(_Job(fn, relay, cancel))
    return cancel
