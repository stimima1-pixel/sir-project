"""Feedback Settings check: before commanding a Work Zone Count target (Enable, Move To/By on the motion page, and
any G-code action that moves to a target - see gcode_runner.py), a Motor Encoder or Servo Encoder must be configured
on the drive, or the count is not known to track a real position and a target on it would not be meaningful.

Factored out of DriveTab (the only caller until GcodeRunner started reusing it directly) into its own small module,
so a future per-axis caller (the multi-axis G-code dispatch - see the README) can run this check without going
through one page's own button/label. `check()` takes any hashable "guard" object in place of a page's button, and
plain callables in place of a page's own status label - a page (DriveTab._with_feedback_check) or a non-page caller
(GcodeRunner._go) can each wire these to whatever makes sense for them."""
from .. import defs
from PySide6.QtCore import QObject

from .workers import run_async

FEEDBACK_KEYS = ((0x2000, 0x0A), (0x2000, 0x0B))    # Motor Encoder (Direct Drive), Servo Encoder (Load Side): 0 = "No encoder"
CHECKING_TEXT = "Checking Feedback Settings…"
_regs = None


def _feedback_regs():
    global _regs
    if _regs is None:
        _regs = {r.key: r for r in defs.load_registers(False) if r.key in FEEDBACK_KEYS}
    return _regs


def check(client, guard, on_progress, on_ok, on_warn, on_failed):
    """Does ONE on-demand read (only when called, never automatic telemetry) of the two Feedback Settings registers,
    then calls `on_ok()` if at least one is configured, `on_warn(text)` otherwise; `on_failed(exc)` if the read
    itself fails. `guard` stays visually enabled throughout the (usually brief) check - it used to be disabled while
    it ran, but a disabled QPushButton's background is a different, darker colour (theme.py's QPushButton:disabled),
    which a user found live ("some background is coming for a very short period of time" pressing MOVE BY) - a
    private, non-visual flag set on `guard` instead (no QSS property, so nothing repaints from it) blocks a second,
    overlapping check on the SAME guard; a DIFFERENT guard (a different button, or eventually a different axis) is
    unaffected, so two independent checks may run concurrently."""
    if getattr(guard, "_checking_feedback", False):
        on_warn("A Feedback Settings check is still running for this button - try again in a moment.")
        return                  # said, not silent: a caller waiting on one of the callbacks (gcode_runner, a whole
                                # program) used to wait forever - STOP then RUN before the old check finished
    guard._checking_feedback = True
    on_progress(CHECKING_TEXT, "warn")
    regs = _feedback_regs()

    def done(encoders):
        guard._checking_feedback = False
        if not any(encoders):        # both "No encoder" (0)
            on_warn("This needs a Motor Encoder or Servo Encoder configured (Datasheet → Feedback Settings) - this "
                   "drive currently has none, so Work Zone Count would not track a real position.")
            return
        on_ok()

    def failed(exc):
        guard._checking_feedback = False
        on_failed(exc)

    run_async(lambda emit, cancel: [client.read(regs[k]) for k in FEEDBACK_KEYS if k in regs],
              on_done=done, on_failed=failed, owner=guard if isinstance(guard, QObject) else None)   # the button pressed
