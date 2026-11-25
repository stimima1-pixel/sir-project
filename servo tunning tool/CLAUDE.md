# STIMITA SDR Servo Tuning Tool - notes for Claude Code

PySide6 GUI for the Stimita **SDR-CB-60R-D** CAN servo drive, laid out like Elmo Application Studio III (ribbon, workspace
tree, activities, pages) in this tool's own **black + gold** theme. Brand text: "STIMITA MOTION CONTROL". The README has the
full technical description; `OPERATIONAL_MANUAL.md` is the user-facing "where do I find X / how do I do Y" guide (2026-09-24,
user: "make sure to make operational manual alongside... as we are implementing so many functions that even i don't remember
where to look for in the software"); this file is what to know before changing anything.

## Run and test
- Run: `.venv/bin/python main.py`. Tests (simulator only, no hardware, ~150-400 s, 556 tests): `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
- Keep the suite green after every change; add a test for every behaviour you add or fix.
- **Update `OPERATIONAL_MANUAL.md` alongside every user-facing feature** (a new page/button/behaviour a person using the
  GUI would need to find or understand) - add or edit a row in its own "Where do I find X?" table and, if it's a real new
  workflow, a step-by-step entry in "Common tasks". Same standing habit as keeping README.md/this file current, just aimed
  at the person operating the tool rather than at Claude or a developer - the reason it exists at all (see above).
- `sdr_tuner/data/*.json` are generated from the Device Reference PDF by `tools/import_device_reference.py` (needs PyMuPDF: use a
  scratch venv, it is not in the project venv). Fix things in the importer, not by hand-editing the JSON.
- `_backup_before_studio_layout_20260921/` is the old source (tab layout). Never edit it, never import from it.

## Rules the user has set
- **Real drive:** only what the user approves. Bench drive on USB: it answered at node 7 until 2026-09-24 and at **node 1** on 2026-09-25 (the user re-numbered it), with a motor attached since 2026-09-24 - never assume the node, read the pill/status bar after connecting. Never write parameters or send commands to a
  real drive, and never run a motor, without the user saying so. Check first with `tools/usb_readonly_check.py` (reads only).
- Stay inside this project folder; only open files or devices the user names. Ask before exploring.
- **Telemetry:** the Single Axis Motion page shows ONLY the drive's own broadcast (about 9 messages/s). Nothing polls telemetry
  (`client.start_polling` exists for the bench tool only). Connect reads, once: pole count, the drive's telemetry mapping, device identity.
- **Speed:** commanded in rpm (ESC RPM, `0x24`). The unit shown follows the TPDO mapping code the drive holds (a code is hex index+sub as
  a decimal: 4194320 = speed in Hz, 4194326 = Work Zone Speed in rps; rpm = rps x 60). No conversion through the pole count. The
  drive's telemetry mapping is written ONLY when the user picks another speed unit and confirms.
- **Fault bits:** only bits 0-10, 14 and 15 (`SDR_FAULT_BITS` in the importer). No bit 11.
- Parameter tables show name, value and units only; address, type and state are in the tooltip. Do not add those columns back.
- Do not change the colour palette (`sdr_tuner/gui/theme.py`); reusing an EXISTING accent (e.g. sapphire for read-only, see below) is fine.
- **"FLOAT16" is a 16-bit FIXED-POINT number (value x 256), not a half float - `protocol.py`, found 2026-09-25 on the real drive (see that day's entry); the 2026-09-22 "broken DC-bus register" below was this tool's own decoding.** `TELEMETRY_KEYS` accepts either DC-bus representation (2026-09-22, user: "voltage is not showing" although a 24 V supply was confirmed with a multimeter). Found by SDO-reading both `Voltage: DC bus (Udc)` (FLOAT16, `0x5001:0x04` - the drive's factory-default TPDO mapping for this slot) and `Voltage: DC bus (Udc, f32)` (FLOAT32, `0x5001:0x0D`) directly, and by hand-decoding the raw `0x180` CAN bytes with no code from this project involved: the FLOAT16 one reads ~0 V regardless of the real supply, the FLOAT32 one reads it correctly. This is a drive-firmware defect (worth reporting to Stimita), not a bug in this tool. Fix: `defs.TELEMETRY_KEYS` now maps BOTH `(0x5001, 0x04)` and `(0x5001, 0x0D)` to `"vbus"`, so whichever one a drive's mapping broadcasts still shows up correctly. On the user's bench drive the affected mapping slot (`0x1A00:0x02`) was rewritten, with the user's explicit go-ahead, to the FLOAT32 register - confirmed live (`vbus` went from showing nothing/0x5001:0x0D-keyed-and-ignored to 24.05 V) - **and this write took effect immediately, no power cycle needed** (updates the "2b" note in the README's Still to confirm list, which used to say this was untested). Also this session, before the DC-bus fix: the user entered and wrote the real activation key through the **Product Activation** group (`config_page.ParameterGroup`, built earlier the same session) - first real-hardware confirmation that write path works and that the correct key clears fault bit 15 ("Activation Key is Missing"); `fault_bits` reads 0 on this bench drive ever since. Not automatic, not a Reset - the user did it through the GUI.
- **The Fault Bits readout card on Single Axis Motion shows names, not a hex number** (user, 2026-09-22, pointing at "8000 hex": "can we directly show fault like activation key is missing... so user can read and understand easily"). `DriveTab._card_text` builds the same comma-joined name text the fault-LED panel below already used (`self._fault_names`, bit by bit, "Bit N (not in the drive's list)" for an unlisted bit), state `"fault"` (red) when any bit is set; the card widget is now `lines=2, fit=True` (auto-shrinking) instead of a plain hex `Readout`, like the Operation Mode / Commutation cards already were for their enum labels. "No Faults" when clear.
- **On USB, the node is found by listening, never guessed** (user, 2026-09-22: "as a user we have no idea about node id of drive"; also "software should search for usb automatically"). A USB cable reaches only one drive, so `Session._probe` (called with an explicit `on_usb` flag from `connect_drive`, not `isinstance(client.bus, UsbSerialBus)` - that broke tests that monkeypatch the `UsbSerialBus` name itself with a plain function) skips straight to `detect_nodes()` for USB instead of trying the configured node first and only falling back on failure (still the behaviour for a shared CAN bus, where more than one drive could be present). The Node ID field is disabled when USB is selected (`MainWindow._on_interface_changed`, tooltip "Found automatically once connected"). The port: `_load_settings` now prefers what `usb.find_drive_ports()` finds LIVE over a stale saved one (the OS can rename the drive's port between sessions) and falls back to the saved value only when nothing is found live; `_connect()` re-scans once more right before connecting if the field is still empty (the drive may have been plugged in after the interface was selected), and shows a clear error if still nothing is found. **Verified against the real bench drive (2026-09-22, read-only `tools/usb_readonly_check.py`, then the guarded, restoring `tools/usb_write_check.py` with the user's explicit go-ahead, "there is no motor connected so you can check write commands as well" - PASS, all 242 parameters identical after)**: port and node 7 found by listening alone with no port/node typed in; telemetry measured at 3.0 messages/s per message, matching `SIM_TPDO_HZ`; the drive correctly ignored the 5 Hz speed command while fault 15 (Activation Key is Missing) was latched, as documented.
- **Pressing Enter in a value box applies/writes it, like the container's own Apply/Write button** (user, 2026-09-22, live on the real drive, after a successful auto-tuning run: "when i press enter value of any parameter is not changing i always need to press apply" - asked, user wants Enter to also submit). `make_editor(reg, on_user_edit, on_submit=None)` now wires `QLineEdit.returnPressed` to `on_submit` when given; wired at every call site to that surface's own commit action: `ParamsTab` per-row `_write_one(row)` (only when `reg.writable`, so a read-only Telemetry Explorer row does nothing on Enter), `FormPage` (new `submitted` signal) to `TunerPage.apply_page()` and to `PeripheralsPage.write_changed()`, and `config_page.ParameterGroup` to its own `write_changed()`. Any confirmation dialog the button already shows (power-cycle rows in Parameters Explorer, everything in the tuner/Peripherals/Item Configuration groups) still shows - Enter is a shortcut for the SAME action, not a way around it; an ordinary per-row Parameters Explorer write still needs no confirmation, matching its own button. Also shortened the on-page Automatic Tuning success message to "Auto tuning completed successfully." (was a long sentence with the elapsed time and change counts - the user pointed at it and said "just show auto tunning is completed nothing else"; the counts are still in the results table and the status bar) and fixed two leftover "every second" mentions in the tuner's own help text/confirmation dialog to say `POLL_S` (3 s) instead.
- **The Single Axis Motion page has ENABLE / DISABLE, next to RUN / STOP / OFF / RESET** (user, 2026-09-22, pointing at the
  page: "there is no enable and disable button"; asked what it should do, the user answered: "when i say enable then you
  will issue servo command (set position as current position) so motor will hold its current position when encoder is
  attached if encoder is not attached then i need to rethink or let me know if you have any idea"). Built exactly that:
  `DriveTab._enable()` takes the drive's own `wz_count` (Work Zone Count) already in the broadcast - **never requested** -
  and calls the new `client.hold_position(target)`, which sends the drive's plain **servo** command (`0x300`/`0x31`,
  `continuous=True`, one `Target: Work Zone Count` INT32 param - checked directly in `defs.load_commands()`, not
  guessed) and repeats it every `esc_period` like the speed command, reusing the same `_esc`/`commanded` single-repeat
  slot `run_test()` already generalized (so RUN/a step test and Enable naturally take over from each other, and
  STOP/OFF/RESET end whichever is active). **The no-encoder case the user asked about**: before sending anything, Enable
  does one on-demand SDO read (only when pressed, via `run_async` - not automatic telemetry) of `Motor Encoder (Direct
  Drive)` (`0x2000:0x0A`) and `Servo Encoder (Load Side)` (`0x2000:0x0B`); if both are `0` ("No encoder") it refuses with
  an explanation instead of sending the command - the reasoning offered back to the user (see README's *Still to
  confirm*): without a real encoder, Work Zone Count is not known to track true mechanical position, so a "hold" on it
  would not mean anything and could behave unpredictably on real hardware. **DISABLE is the same as OFF** (the user did
  not redefine it; Elmo's own Motion tab pairs Enable/Disable with Off this way too). The simulator gained matching
  state: `_servo_target` (cleared by stop/off/reset, like `_test`), snaps `wz_count` straight to the target (no
  mechanical model, same simplification the servo step-response test already uses) and reports Operation Mode 8
  ("Servo Control") while holding. **Never tried on a real drive** - see README's *Still to confirm* (item 9): the
  simulator cannot say whether the plain servo command actually holds position with Hall-only/sensorless commutation
  and no Motor/Servo Encoder: this tool's answer to the user's open question is to refuse rather than guess. 353 tests pass.
- **Position Control, Current/Torque Control and Homing added to Single Axis Motion (2026-09-22)**, after the user saw
  Elmo's own "Drive Mode" selector (Position/Velocity/Current/Stepper + PTP Move Absolute/Relative) and asked "have you
  implemented that as well". Checked the full command set first (`defs.load_commands()`), rather than guess: Velocity
  was already done (RUN, `esc_rpm`); Position was only 10% done (ENABLE = hold current position only); Current/Torque
  and Homing did not exist at all. User confirmed (after a contradictory first answer got clarified): build all three.
  **Position Control · Servo** (new card, same style as Speed Control): **MOVE TO** (a typed target) and **MOVE BY** (a
  typed delta added to the CURRENT `wz_count` from the broadcast, never requested) both reuse `client.hold_position()` -
  the exact same `servo` command as ENABLE, just with a different target - refactored the shared "read Motor/Servo
  Encoder once, refuse if neither is configured" logic out of `_enable()` into `DriveTab._with_feedback_check(button,
  on_ok)`, used by all three (`_enable`, `_move_absolute`, `_move_relative`, converging on one `_go_to_position(target,
  verb)`). **SET HOME HERE** = the user's own answer to "Homing" not existing on this drive: checked directly (no hit
  for "home" anywhere in `registers.json` or `commands.json`) - the drive has no automatic homing sequence at all, so
  this is a manual equivalent: `client.reset_work_zone()` sends **Stop** then **Reset Work Zone** (`0xE0`) - the Stop
  matters: without it, a running speed command keeps going for up to the heartbeat timeout, and a running position
  hold/move would otherwise repeat its OLD absolute target against the NEW zero point and snap toward whatever that is
  now (caught by a test, not shipped-then-found). **Current / Torque Control** (new card): a MODE combo (Current (FOC)
  / Torque (ETC)) + target + RUN, sending `current_control_field_oriented_control_foc` (A) or
  `electronic_torque_control_etc` (N·m) - new `client.set_current()`/`set_torque()`, both thin wrappers over the
  existing `_command_speed()` (same repeat mechanism as speed/position, no new client-side machinery needed). No
  Feedback Settings check for these - they don't touch Work Zone Count. **A real, if minor, simulator bug found and
  fixed while extending it**: starting a new continuous "mode" command (esc_hz/esc_rpm/servo/current/torque) never
  cleared the OTHER mode-defining state flags (`_servo_target`, now also `_foc_current`/`_foc_torque`) - so on the
  SIMULATOR (not the client - the client's own single `_esc` slot was always correct), Operation Mode could keep
  reporting an old mode (e.g. stay "Servo Control" after a new speed command started) since nothing cleared the OLD
  flag. Fixed with `SimulatedDrive._start_exclusive_mode()`, called by every one of these branches before setting its
  own state - also fixes the same latent gap for esc_hz/esc_rpm/servo interactions that predates this session's work,
  not just the two new commands. Added `MODE_FOC = 4` ("Field Oriented Control (FOC)" - checked the drive's own enum:
  ONE mode number covers both the Current and Torque commands, matching how Torque is documented as converting to a
  current internally). **Nothing here has been tried on a real drive** - three new *Still to confirm* items in the
  README (position control's lack of a mechanical model even for MOVE TO/BY now, current/torque having no torque
  constant modelled, and whether Reset Work Zone needs the motor stopped first). Also documented that Elmo's
  "Stepper [UM]" mode has no equivalent on this drive (it isn't a stepper controller). 362 tests pass.
- **No spin/combo box or slider anywhere in the app reacts to the mouse wheel any more** (user, 2026-09-22, a screen
  recording of the new Position Control card: "if i am scrolling down or up, it started changes value when mouse is on
  that parameter"). Watched the recording frame by frame (extracted with the established scratch-venv +
  `imageio-ffmpeg` + `eq=gamma=2.3` workflow - HDR, otherwise too dim to read): confirmed live, `TARGET POSITION` went
  `0 → -1 → -3 counts` purely from scrolling the page, mouse never clicked. Root cause is plain Qt behaviour, not a
  mistake specific to that field: `QSpinBox`/`QDoubleSpinBox`/`QComboBox`/`QSlider` all adjust their value on a wheel
  scroll even without focus (proved directly: a synthetic wheel event under `Qt.FocusPolicy.WheelFocus` changes a
  plain `QComboBox`'s index and a plain `QSlider`'s value in a headless test) - which is a real hazard here since
  nearly every page is a scrollable form, not just the two new cards. Fixed once, centrally: `gui/widgets.py` gained
  `SpinBox`/`DoubleSpinBox`/`ComboBox`/`Slider` (a `NoWheel` mixin whose `wheelEvent` calls `event.ignore()`, so the
  event passes to the parent scroll area instead - the value can still be typed or changed with the arrows/steppers/a
  click) - then EVERY `QSpinBox()`/`QDoubleSpinBox()`/`QComboBox()`/`QSlider()` construction across `drive_tab.py`
  (speed, position, current/torque, speed-unit and window-length controls), `config_page.py` (connection type, bit
  rate, node ID), `params_tab.py` (`make_editor`'s enum combo box - covers every enum parameter in Parameters
  Explorer, Telemetry Explorer, every Tuner page, Peripherals and Item Configuration's Product Activation/Networks,
  since they all funnel through it), `tuner.py` (General System Dynamics, presets, wave form) and `recorder.py`
  (record-for duration) was swapped for the scroll-safe class - checked with a grep after, zero raw constructions
  left anywhere in `gui/`. (Numeric parameter editors elsewhere already used a plain `QLineEdit`, which was never
  affected - only the enum combo box and the few real spin boxes/sliders needed this.) 364 tests pass.
- **Every card's feedback now shows under that card, not under Speed Control** (user, 2026-09-22, a screenshot of the
  motion page: "if i give command in position mode then error is showing under vel mode box... error shod come under
  pos block/box"). Root cause: `DriveTab._status(text, state)` always wrote to the ONE `self.status` `QLabel`, which is
  physically placed inside the Speed Control card - so a message from Position Control (MOVE TO/BY, SET HOME HERE) or
  Current/Torque Control appeared to belong to Speed Control instead. Fixed: `_status` takes an optional `label`
  (defaults to `self.status`); Position Control and Current/Torque Control each got their OWN status `QLabel`
  (`self.pos_status`, `self.ct_status`, same `ctlStatus` styling) at the bottom of their own card, and every method
  that shows feedback for an action in one of those cards now passes its own label through explicitly - including the
  SHARED `_with_feedback_check`/`_go_to_position` helpers (Enable and Move To/By both call these, so the label has to
  be threaded through as a parameter, not hardcoded) - so Enable's messages still land under Speed Control (where the
  ENABLE button itself lives) while Move To/By and Set Home Here land under Position Control, and Current/Torque
  Control's RUN under its own card. New regression test asserts a Position Control message never changes Speed
  Control's own text and vice versa. 365 tests pass.
- **A status label's card no longer visibly shrinks and grows back between messages** (user, 2026-09-22, a screen
  recording: "if i give a command in position mode... sometime some background is coming for very short period of
  time" - specifically on MOVE BY). Found it by extracting every native frame (~120 fps, not the usual 4-12 fps
  sample - a 12 fps pass found nothing, confirming the flash lasts only 1-2 native frames) with the established
  scratch-venv workflow, then diffing consecutive frames to find the exact moment instead of eyeballing ~2500 frames:
  the "no encoder" message on Position Control (`pos_status`, 4 wrapped lines) is preceded by the much shorter
  "Checking Feedback Settings…" (`_with_feedback_check`, 1 line) - with no fixed height on a `setWordWrap(True)`
  `QLabel`, that label's card visibly SHRANK for the ~16-33 ms the short message was showing, snapping the whole
  scrollable column up and revealing the Fault Bits card underneath, before growing back once the long message
  landed - exactly the "background... for a very short period of time" the user described. Same latent issue on
  `self.status` (Enable goes through the same `_with_feedback_check`) and `ct_status`, just less noticeable there
  since their message-length swings are smaller. Fixed once for all three: `DriveTab._status_label()` gives every
  `ctlStatus` label a fixed `setMinimumHeight(STATUS_LINES * 17)` (4 lines) up front, so no message, however short,
  ever shrinks its card - confirmed directly (a standalone script drove `pos_status` through empty → short → the
  longest known message → short again and the enclosing `QGroupBox`'s height never moved) and with a new regression
  test that does the same across all three labels inside the real running window. 366 tests pass.
- **A second, smaller cause of the same "background is coming" report, found and removed (2026-09-22, user: "still
  some time it comes but not too much noticeable")**. The label-height fix above removed the big (4-line) jump, but
  `_with_feedback_check` also did `button.setEnabled(False)` for the (usually brief) duration of the Feedback Settings
  check, then re-enabled it - and a disabled `QPushButton`'s background is a real, different colour
  (`theme.py`: `QPushButton:disabled { background: #111317; ... }`, close to the app's plain background `#0C0D10`/card
  `#14161B`) versus its normal raised look (`RAISED = #1B1E25`) - so MOVE BY's own surface briefly looked like it had
  turned into background instead of a button, matching the report exactly ("background is coming" through the
  button itself, not the card behind it). There was never a real need to disable it: the check is read-only and the
  drive's SDO transactions are already serialized (`DriveClient._sdo_lock`), so a second overlapping check is merely
  redundant, not unsafe. Fixed: `button` now stays visually enabled throughout; a private, non-QSS instance flag
  (`button._checking_feedback`) silently blocks a second click while one is already running instead - no property, no
  unpolish/polish, nothing that can repaint. New test slows `client.read` (`monkeypatch`) to hold the check open long
  enough to assert `button.isEnabled()` never goes false, and that a second click during the check triggers no second
  read. 367 tests pass.
- **G-code console added to the Terminal page (2026-09-22)**, after the user asked to explore `Servo tunning tool-
  original/` (a sibling folder they pointed at, containing Servosila's own vendor IDE, "Servopilot" - see the README's
  *G-code console* section for the full command table). What was found, in order: (1) the user asked "you know servo
  drive also supports G-code?" - checked the Device Reference and the generated JSON directly first, found nothing
  (right, at the level of the drive's own CAN protocol); (2) the user pointed at the vendor's own IDE folder;
  `sample-speed-control.py` in it showed G-code IS real, but as a HOST-SIDE layer (a separate "Servopilot" process,
  reached via `servopilot-client.dll`'s shared-memory API - `connect`/`gcode`/`execute`/`pause`/`resume`/
  `get_axis_position`/IMU getters), translating G-code text into the exact same CAN commands this tool already sends
  directly; `plc/programs/*.NC` gave real example programs (a legged-robot gait demo among them) to infer the dialect
  from; (3) `Servo-Drive-IDE.exe` itself turned out to embed a zlib-compressed copy of Servosila's SHARED parameter/
  command reference (found by scanning the exe for zlib headers and decompressing candidates, not by knowing it was
  there) - confirmed it is the GENERAL reference across Servosila's whole product line, not this drive's own Revision
  L PDF (it has bit 11 in Fault Bits, which this drive's own PDF and `SDR_FAULT_BITS` correctly exclude; it also has
  a `Starter` command + 6 parameters for cranking an internal-combustion engine that isn't in this drive's PDF - the
  user said **do not add** that one, since it is unconfirmed for this exact drive); (4) the user then shared the
  actual authoritative source directly - **"SITAR G-code Reference Dec23.pdf"** (Sitar Motion Controller, revision D,
  December 2023) - superseding the exe-derived fragments with the real, complete dialect (Servosila's underlying
  engine is called "Sitar Motion Controller"). Asked what "add G-code integration" meant (documentation vs. a real
  feature) rather than assume - the user chose a real feature: "build a G-code command box". Built: `gui/gcode.py`
  (pure functions, no Qt - `parse(line) -> {"action": ..., ...}` or raises `GcodeError` with a message meant to be
  shown to the user as-is) translates ONE line into an action dict; `LogTab` (Terminal) gained the console UI (a
  QLineEdit + RUN, Enter also runs) and the dispatch, reusing - not duplicating - the motion page's own logic:
  `DriveTab._with_feedback_check`/`_go_to_position` directly (same Feedback Settings check as ENABLE/MOVE TO/BY, same
  `client.hold_position`), a new public `DriveTab.go_to_position_linear()` (G01, the drive's Servo: Linear
  Interpolation command - `client.move_linear()`, new, same continuous-repeat pattern as every other command here),
  and a new `DriveTab.note_gcode(started)` so touching the target/limit boxes on the motion page later never
  silently re-applies a stale RUN over whatever the console last commanded (the same class of bug the earlier
  per-card-status-label work was about - keeping two pages' bookkeeping of "what is currently repeating" honest).
  New `client.end_repeat()` for G1011 (Release: stops repeating WITHOUT sending a stop - the reference is explicit
  that the drive is left to time out on its own heartbeat; distinct from G1000/G1001/G1002 which DO send a real
  command). **Since this tool only ever talks to ONE connected drive**, axis selectors (`[0]`, `X`, `P<n>`) are
  parsed (so a line pasted from a real multi-axis program still works) but always ignored; `U`/`V`/`W` give a
  RELATIVE target (current Work Zone Count from the broadcast, never requested, plus the given delta) since this
  tool has no G90/G91 modal state to track across independent single-line commands - this mirrors the reference's
  OWN stateless alternative to G91, not an invention. G02/G03 (arc/circle) and Txx (Inverse Kinematics) are refused
  with a clear reason (both fundamentally need 2+ coordinated axes); more than one G-code on a line is refused too
  (nothing to coordinate several commands with on a single drive); G09/G20/G21/G61/G64/G90/G91/G93/G94/G1050 are
  accepted as harmless no-ops with a note, so unrelated modal setup lines in a copied real program do not hard-fail
  the one line that matters. `F` (feed rate, G01) is always counts/sec, matching this tool's own native units - never
  mm/min or moves/min, consistent with this project's standing "never guess a unit conversion" rule. Simulator gained
  `servo_linear_interpolation` handling (reuses `_servo_target`/Operation Mode 8, exactly like plain `servo` - no
  separate mechanical model, matching the project's existing level of simulation fidelity). 14 parser tests
  (`test_gcode.py`, several taken directly from the reference PDF's own examples) + 10 end-to-end GUI tests (typing a
  line, pressing RUN or Enter, and checking the resulting `client.commanded`/traffic-log entry/refusal text) + 2
  client-level tests (`move_linear`, `end_repeat`). **Never tried on a real drive** - sends exactly the same
  commands the motion page's own buttons already send (covered by that page's own *Still to confirm* items), so nothing
  new is technically at stake, but the console path itself is simulator-only so far. 393 tests pass.
- **Motion Controller page added (2026-09-22)**, user: "add motion controller tab below terminal as we can write
  G-code as a small code of 10-15 lines and also save it as sample code in software as well" - a whole G-code
  PROGRAM (several lines), not just the Terminal console's one line at a time. **Refactored first, rather than
  duplicate the dispatch logic a second time**: extracted the Terminal console's own `_run_gcode`/`_gcode_*` methods
  into a new, page-independent `gui/gcode_runner.py` (`GcodeRunner.run(action, button, on_done, on_progress=None)`)
  - required generalizing `DriveTab._status(text, state, label=None)` to accept a plain `callable(text, state)` in
  place of `label` (a QLabel), so `_go_to_position`/`_with_feedback_check` (private, but written for exactly this
  kind of reuse within the package) can report back to a callback instead of always writing into one of the motion
  page's own labels; a new `CHECKING_FEEDBACK_TEXT` constant lets `GcodeRunner` tell the check's own intermediate
  "Checking…" message (forwarded as `on_progress`) apart from the actual final result (`on_done`, called exactly
  once). `log_tab.py`'s own dispatch shrank to ~10 lines calling into the shared runner; **all 10 of its existing
  tests passed unchanged**, confirming the refactor was faithful. New page `gui/motion_controller.py`
  (`MotionControllerPage`): a `QPlainTextEdit` editor, RUN (disabled while a program is running) / STOP, a
  **Samples** combo (bundled `.nc` files - see below) and Open…/Save… for the user's own. Runs lines strictly in
  order via a small recursive `_advance()` (parse the line, dispatch through `GcodeRunner`, `on_done` decides
  whether to move on); **stops at the first line that fails** rather than guess and continue with what might now be
  a wrong assumption about the drive's state - caught a real bug while testing this: a `GcodeRunner` "warn" result
  can mean either a harmless noop (`G93 ...`, continue) or a genuine refusal (no Feedback Settings encoder, continue
  = wrong), so `_advance()` checks the ORIGINAL parsed action's kind (`noop` or not), not just the state string, to
  tell the two apart. **G04 (Dwell) genuinely pauses here** - the one thing the single-line console could never
  meaningfully do (no next line to pause before) - via a real, cancellable `QTimer` in `GcodeRunner` (not
  `QTimer.singleShot`, which cannot be cancelled), so the new `GcodeRunner.cancel()` lets STOP cut a dwell short
  instead of waiting it out. The currently-running line is highlighted (`QTextEdit.ExtraSelection`, reusing
  `theme.GOLD_LO` - the same accent `params_tab.py`'s "edited" highlight already uses, no new colour) and each
  line's result is logged live, colour-coded by state. **Sample programs**: `sdr_tuner/data/gcode_samples/*.nc`
  (`Path(__file__).parent / "data"`, the same resolution pattern `defs.py` already uses) - `speed_ramp.nc` is the
  user's OWN pasted copy of Servosila's `DEM1.NC` demo, kept byte-for-byte (tabs, comment style and all) rather than
  paraphrased, after the user pasted it directly and confirmed it should be a bundled sample; `hold_and_move.nc` and
  `torque_ramp.nc` were written for this project, covering position and torque control respectively. **"Save it as
  sample code in software"**: `Save…` defaults its dialog to the samples folder and refreshes the Samples list right
  after a successful save, so naming a program and saving it there (the easy, default path) makes it a real,
  selectable Sample from then on - saving elsewhere instead still works, it just will not show up as a Sample.
  Navigator/ribbon: new "code" icon glyph (`icons.py`, simple `</>` brackets, no existing glyph fit); registered
  between `"terminal"` and `"peripherals"` in `main_window.py`'s tool dict, the navigator's `add_activity(...,
  tools=[...])` list and the Floating Tools ribbon group - all three needed the same insertion point, checked
  against the user's own screenshot of the navigator list. 12 new end-to-end GUI tests (sequencing, a genuine dwell
  pause, STOP cancelling a dwell, stopping at a refused line vs a harmless noop, the parse-error case, samples,
  Open…/Save… round-tripping a file via a mocked `QFileDialog`, saving into the samples folder making it
  selectable) + updated the two structural tests that enumerate the navigator's/ribbon's tool list. **Never tried on
  a real drive** - same reasoning as the item above: it sends nothing the motion page's own buttons do not already
  send. 405 tests pass.
- **Whether a row can be written follows THAT register's own `access` (`reg.writable`), never which page it is on** (user, 2026-09-22: "some of parameters also can be write as well they are not read only" - checked directly against the PDF page by page (not just the generated JSON): every telemetry parameter genuinely says `ro`, every configuration one `rw`, no exceptions, so the two pages happen to split cleanly today, but `ParamsTab` no longer hardcodes it off the page's `telemetry` flag. `ParamsTab.any_writable = any(r.writable for r in defs.load_registers(telemetry))` now drives the READ-ONLY badge, the gold vs plain READ ALL button, whether Write Changed / Save / Load show at all, and (per row) whether the editor is enabled and the Write button shown (`reg.writable`, not `self.telemetry`) - if a future PDF revision ever makes one telemetry parameter writable, the page picks it up on its own.
- **Telemetry Explorer vs Parameters Explorer look alike but show disjoint data** (user, 2026-09-22: "have same data" - checked: 0 key overlap, 242 config vs 175 telemetry). User wanted them kept separate but visually distinct: Telemetry Explorer gets a sapphire "READ-ONLY" badge (`params_tab.py`, reusing the existing `SAPPHIRE` accent - no new colour) and a plain (non-gold) READ ALL button (a sapphire tree header/selection was tried too but the user said the blue line looked worse than gold, so the header stayed gold - `QTreeWidget[readonly=...]` is set on the tree for any future use but no QSS rule uses it now); the ribbon's Telemetry group button is "Read Telemetry" (glyph `monitor`), not "Upload" (glyph `upload`, used by the Drive group), so the two are not confused there either. Also removed a duplicate the user spotted the same day: the Parameters ribbon tab's Drive group had its own Open…/Save…, identical to the File tab's - now File is the only place for them.
- **The window's own simulator publishes telemetry at `session.SIM_TPDO_HZ` (3 Hz per message, ~9/s total - the real drive's own default), not the 100 Hz `SimulatedDrive` defaults to for fast tests** (2026-09-22, user: "software is slow and hang when you give velocity command and then record graph" - at 100 Hz the 20000-sample history fills in ~3 min instead of ~2 h, after which every Recorder tick and the motion page's own plots redraw the maximum history on every refresh; confirmed directly: 9 s of a velocity command + a recording grew `speed_hz` history by 26 samples, not ~900). Set `sim.params[(0x3000, 0x05)]` AFTER construction in `connect_drive()`, not a `tpdo_hz=` constructor kwarg (that broke tests with a custom `SimulatedDrive(channel, node_id)` factory, same class of bug as `autoconfig_seconds`). Tests restore the fast rate via the autouse `_fast_simulated_telemetry` fixture in `test_gui.py`; `@pytest.mark.realistic_telemetry` opts a test out to check the real default directly.
- **G-code's axis selector means the drive's own CAN node ID - a line naming a different node no longer runs**
  (2026-09-22, user, correcting the G-code feature shipped the same day: "axis means node id so node id of servo
  drive and axis of G-code has to match otherwise code will not run"). Before this, `[0]`/`X`/`P<n>` were parsed but
  always ignored, since the tool only ever has one connected drive - silently wrong: on the real, multi-axis system
  a line only ever affects the axis(es) it names. Reworked both files that make up the G-code feature: `gcode.py`'s
  `parse()` no longer resolves an axis selector to a single value - it returns the *structured* selector instead
  (`{"action": ..., "axis": int_or_None}` for Reset/Off/Stop/Capture/Release, `{"action": ..., "axes": {axis:
  (value, relative)}}` for Move/Linear Move/Speed/Torque) - because `parse()` is a pure function with no idea which
  node is connected; only `gcode_runner.py`'s `GcodeRunner.run()` knows that (`self.s.client.node`), so matching
  happens there, at the moment a line actually runs. A mismatched line is skipped with `"This line addresses axis
  ... - the connected drive is node ... - skipped."` (state `"warn"`, nothing sent to the drive); a no-axis Reset/
  Off/Stop/Capture/Release (`P<n>` absent) means "all axes", which includes whichever one is connected, so it still
  runs. Caught a real ambiguity while updating `motion_controller.py`'s tests: a "warn" result can now mean either
  a harmless noop (unchanged) or this new axis-mismatch skip (also a refusal, not a noop) - `_advance()`'s existing
  `is_noop` check (from the earlier noop-vs-refusal fix) already handles this correctly with no code change, since
  an axis mismatch is `action["action"] != "noop"`. All 3 bundled sample `.nc` files still address axis 0 (`X`/
  `[0]`, kept byte-for-byte per the user's own pasted original - not edited for this) - the Motion Controller's
  Samples combo tooltip and a log line on load now say to change that to match the connected node before running.
  `tests/test_gcode.py` rewritten for the new return shapes (14 tests); `tests/test_gui.py` gained a
  `connect_axis1(win, qtbot)` helper (connects at node 1, the Node ID field's own default/minimum - a real CAN node
  is never 0 - so plain `Y` addresses it without every test needing bracket syntax) used by the G-code console/Motion
  Controller tests that need a specific matching axis, plus 3 new tests proving the mismatch/match/all-axes behaviour
  directly (connecting at node 7, the real bench drive's own node). README's *G-code* section rewritten to match.

- **Multi-axis: real Servopilot functions, built in stages (2026-09-22)** - the user asked what "Servopilot" is
  (found: the vendor's own multi-axis robot motion-control engine, built into `Servo-Drive-IDE.exe` as a "Multiaxis
  PLC" - see the G-code console entry above for how it was discovered), then "can we implement in our software?",
  then, after being shown the scope, "actually i want to add full servo pilot functions". This is a large, staged
  change (see the approved plan) - built and tested in order, full suite green after every stage (436 tests at the
  end of this session's work):
  - **Stage 0** (no visible change): extracted `DriveTab`'s Feedback Settings check into standalone
    `gui/feedback_gate.py` (`check(client, guard, on_progress, on_ok, on_warn, on_failed)` - `guard` replaces the
    old button-only re-entrancy lock, any hashable object works) and the fault-bit-name-joining logic into
    `defs.fault_names_text(bits, names)` - both needed so later stages could reuse them per axis instead of
    duplicating them. `gcode_runner.py`'s `_go()` now calls `feedback_gate.check()` directly instead of string-
    sniffing `CHECKING_FEEDBACK_TEXT` through `DriveTab._with_feedback_check` - a real simplification this refactor
    enabled, not just a move.
  - **Stage 1 - connection layer**: new `sdr_tuner/shared_bus.py` (`SharedBusHub`/`_AxisHandle`) lets several
    `DriveClient`s safely share ONE physical CAN connection on transports that cannot be opened twice per process
    (used for every real adapter except the Simulator, which already fans out independent virtual-bus handles
    correctly with no wrapper needed - confirmed directly). `DriveClient`/`usb.py` needed ZERO changes: the hub just
    satisfies the same 3-method (`send`/`recv`/`shutdown`) bus-like interface `UsbSerialBus` already duck-types.
    `Session` gained `connect_axis()`/`disconnect_axis()` (additive - `connect_drive()`/`disconnect_drive()`, and
    the primary `self.client`/`.node_id`, are completely unchanged, so the ~60 existing single-axis call sites
    across every page needed no changes) and a new `AxisContext` facade (same `.client`/`.node_id`/`.is_simulated`/
    `.interface`/`.is_connected` shape and `connected`/`probed`/`disconnecting`/`disconnected`/`mapping_changed`
    signals `Session` itself exposes) for an ADDITIONAL axis - built so `axis_connected` fires before `ctx.connected`
    and well before the async probe resolves, meaning a listener that builds a page in reaction to `axis_connected`
    is already subscribed in time to catch both (no missed-signal race). A real bug caught immediately by the first
    multi-axis test: `connect_axis`'s `SimulatedDrive` for an additional axis was being discarded (`_sim` bound but
    never stored) - its thread leaked forever since nothing ever called `.close()` on it; fixed with a new
    `Session._axis_sims` dict mirroring the primary's own `_sim`. Item Configuration gained an "Additional Axes"
    group (Add Axis, disabled entirely on USB - the drive's own cable reaches exactly one drive, real hardware or
    not); the navigator gained sibling tree items (`add_axis_item`/`remove_axis_item`/`set_axis_state`).
  - **Stage 2 - per-axis pages**: `main_window.py` builds a `DriveTab` for each additional axis the MOMENT it
    connects (`_on_axis_connected`, not lazily on first click - lazy construction would miss the `connected`/
    `probed` signals that already fired), bound to that axis's own `AxisContext` instead of the whole `Session` -
    `DriveTab` needed ZERO changes, since it only ever reads `self.s.client` (confirmed directly: one call site).
    Registered under an axis-qualified tool key (`f"motion:{node_id}"`); clicking the axis in the navigator opens
    it; disconnecting removes it and falls back to the primary drive's own page if it was open. The round STOP
    button (and Esc) now stops every connected axis, not just the primary - a real gap fixed here, not merely
    inherited. `ParamsTab`/`PeripheralsPage`/`TunerPage` were checked and ALSO need zero changes (each only reads
    `.client`/`.node_id`/`.is_connected` from its session) and could follow the exact same per-axis pattern - not
    done yet, left as the natural next step (Parameters/Telemetry Explorer, Tuning, Peripherals, Recorder still
    show only the primary connection); `RecorderPage` additionally needs a `drive_tab` reference (its own axis's
    DriveTab, once built) so is slightly less mechanical.
  - **Stage 3 - multi-axis G-code dispatch**: `gcode_runner.py`'s `GcodeRunner` now takes `drive_tab_for(node_id)`
    (a lookup, not one fixed `drive_tab` - looked up FRESH each line, since axes connect/disconnect at runtime) and
    `Session.all_clients()` ({node id: DriveClient} of the primary plus every additional axis). A line's action now
    DISPATCHES to whichever of its named axes are actually connected (sequentially, one at a time - never
    concurrently, so one `button` safely guards every axis's own Feedback Settings check) instead of skipping the
    whole line on a mismatch; a no-axis Reset/Off/Stop/Capture/Release now means every connected axis, not just the
    primary. Results combine into gcode_runner.py's own `_combine()`: exactly one connected target (the original,
    still most common case) passes its message through byte-for-byte unchanged; more than one is joined as
    `"Axis N: ...; Axis M: ..."`, with the worst state (error > warn > ok) winning. `log_tab.py`/
    `motion_controller.py` take the same `drive_tab_for` lookup now instead of one `drive_tab` (neither used it for
    anything else - confirmed directly); Motion Controller's own STOP button now also stops every connected axis,
    not just the primary. **Explicitly deferred, not built this pass**: concatenating several G-codes on one
    physical line (the vendor's own `DOG.NC` demo does this to move several legs together - `gcode.py` still
    refuses it), real `G02`/`G03` circular interpolation, and a proper multi-axis coordinated feed rate for `G01`
    (today each named axis still gets the same literal feed rate, not one scaled so several axes' moves finish
    together) - `README.md`'s G-code table marks these "not yet built" rather than silently working differently.
  - **Stage 4 - program control & status** (continued the same day, user: "keep going with Stage 4"):
    `motion_controller.py` gained **PAUSE**/**RESUME** (freezes/continues at the next LINE BOUNDARY - a line
    already dispatched always finishes; `done()` checks a `_paused` flag instead of auto-advancing) and a **Loop**
    count (a `SpinBox` with `setSpecialValueText("Forever")` at 0). A real crash risk caught before it ever ran:
    `_advance()`'s own recursive self-call (line -> `done()` -> next line -> ...) is fine for an ordinary 10-15
    line program (bounded, matches Python's recursion limit) but a GUARANTEED `RecursionError` for a synchronous,
    dwell-free "Forever" loop, which never yields control back to Qt at all - fixed with `_advance_soon()`
    (`QTimer.singleShot(0, self._advance)` in place of a direct call), unwinding the stack between every line and
    letting STOP/PAUSE clicks be processed in between. A second real bug, caught by the FIRST Loop test: `_finish()`
    used to infer "did the program succeed" from `_pos >= len(self._lines)`, which the Loop rewrite broke (that
    condition is now also true after `_loop_completed()` decides to keep looping) - fixed with an explicit
    `self._succeeded` flag, set ONLY by `_loop_completed()` reaching its target, never by a stop/parse-error/failed
    line, keeping the single-pass "Program finished." message byte-for-byte unchanged. New workspace-level
    **Axes** page (`gui/axes_page.py`, `AxesStatusPage`, glyph `grid` - a new simple 2x2-square icon, no existing
    one fit): one table row per connected axis (node, online/stale, Work Zone Count, Work Zone Speed, fault bits
    via `defs.fault_names_text()` - Stage 0's own shared helper), refreshed on a plain `QTimer`, nothing requested.
  - **Stage 5 - config-driven Inverse Kinematics** (same day, user: "keep going with Stage 5"): new
    `sdr_tuner/kinematics.py` (pure Python, no Qt, mirrors `gcode.py`'s style) parses the vendor's own `.kinematics`
    CSV format - found in `Servo tunning tool-original/plc/assets/*.obj.kinematics`, a sibling folder - and now
    bundled verbatim as `sdr_tuner/data/kinematics_samples/*.kinematics` (`radar.obj` 3 axes pan/tilt/roll,
    `ko-bot.obj` 5-axis arm, `robot-dog.obj` 12-axis quadruped, `humanoid.obj` 34 axes). **The one real judgement
    call the format needed** (no written spec exists): `Axis Origin Point`/`Axis Direction` are each given ONCE,
    directly in the root/chassis frame, not accumulated through a parent-relative chain - confirmed by the numbers
    themselves (a leg's 3 joints all cluster in that leg's own region of space, not small offsets from each other)
    and chosen because it is exactly the input a **Product of Exponentials** (screw-axis, Lynch & Park "space
    form") kinematics description needs. Verified directly against all 4 real files: forward kinematics at every
    joint's own home value reproduces the file's own Tool Tip exactly (not approximately), and solving inverse
    kinematics for a random reachable pose's own forward-kinematics result converges back to within 1 mm, for
    EVERY chain in every file (radar's 1 chain up to humanoid's 5, including two genuinely different chain lengths
    sharing axes 14/15 - the torso). The solver is damped least squares (Levenberg-Marquardt) with a NUMERIC
    Jacobian (central differences of forward kinematics itself), chosen over an analytic one specifically so IK
    correctness reduces entirely to FK correctness - matching the plan's own stated intent once the concrete
    screw-theory formulation was worked out. IK is **position-only** (X/Y/Z, no orientation) since G-code's own
    move commands have no orientation to give it. **A real, informative limit found and documented, not a bug**:
    `radar.obj`'s own 3 joints - and its own tool tip - all sit at the exact same point, (0, 0, 0); rotating any of
    them re-orients the tip but never moves its position, so position-only IK against it is vacuous (every pose
    already "converges", trivially) - a pure pan/tilt/roll head has nothing for THIS solver to solve. **Another
    real inconsistency found in the vendor's OWN demo file, reported rather than silently resolved**: in
    `humanoid.obj`, the right arm has exactly one tool-tip row (its last joint); the left arm has tool tips on
    BOTH its last two joints - `chains()` reports both as separate chains (T33, T34) instead of guessing which one
    the vendor "meant". Tool numbering confirmed against `DOG.NC` (`T3`/`T6`/`T9`/`T12` = each leg's own
    end-effector axis + 1). `tests/test_kinematics.py` (27 tests, no Qt): parser tests against the real files' own
    known body/axis counts, the FK-at-home and FK(IK(target))-round-trip checks above for every real chain, an
    unreachable-target case (`converged=False`, not silently treated as success), and synthetic fixtures for the
    two things no real file exercises (a `linear` joint type; a malformed header). **Wired into G-code**:
    `gcode.py`'s `Txx` is no longer unconditionally refused - a NEW, purely additive branch (`_tool_number`/
    `_cartesian_target`, checked before any of the existing axis-letter parsing, so all 14 pre-existing tests are
    completely unaffected) turns `G00`/`G01`/`G1020 Txx X.. Y.. Z..` into a `"tool_move"` action (tool number +
    Cartesian target, `+feed_rate` for G01); `gcode_runner.py`'s new `_tool_move()` looks up the tool's chain in
    `Session.kinematics_model`, seeds the solve from each joint's own CURRENT Work Zone Count (converted back
    through `Session.counts_per_unit`), solves IK, converts the result forward through the same `counts_per_unit`,
    and dispatches it through the EXACT SAME multi-axis mechanism (`_run_chain`/`_dispatch_value`) a plain
    multi-axis move already uses - Txx does not duplicate dispatch, it only feeds it a target it computed. A
    missing model, an unknown tool number, a chain with an axis not connected, or IK that does not converge are
    each refused with a clear reason instead of guessed at. New **Kinematics Model** group on Item Configuration
    (`config_page.py`): **Open…**/**Samples** (the bundled files above) load a model and show a summary (body/axis/
    tool counts); one **counts per unit** field per axis in the loaded model's own chains - there is no way to
    derive this conversion automatically (the file's units are mm/degrees, this drive's own unit is a raw Work
    Zone Count), so it defaults to 1.0 and resets to defaults whenever a DIFFERENT model is loaded (a stale factor
    from a previous, unrelated model would silently mean the wrong thing). **A real axis-numbering caveat, found
    and documented rather than hit unexpectedly**: every bundled robot's own axis numbering starts at 0 for (at
    least) one chain, and axis 0 can never be reached by a real connected drive (node IDs are always 1..127, like
    the bundled G-code samples' own "all address axis 0" caveat) - `robot-dog.obj`'s OTHER 3 legs (`T6`/`T9`/`T12`,
    axes 3-11) are unaffected and are what the new end-to-end tests actually connect and dispatch to. IMU getters
    (`kinematics.read_imu`) are an explicit "not present" stub today, keyed off the file's own per-body `IMU`
    column, per the user's own choice when asked (no real IMU hardware exists on this drive). **Explicitly not
    built**: per-axis Parameters/Telemetry Explorer/Tuning/Peripherals/Recorder pages (Stage 2 only went as far as
    Motion), concatenated multi-command G-code lines and real G02/G03 arc interpolation (Stage 3's own deferred
    items, unchanged), and orientation-aware IK (no G-code command can express an orientation target yet, so there
    is nothing to solve for). Real hardware: still simulator-only, per the user's own "getting a second drive + CAN
    adapter soon" - nothing above has been tried against a physical drive. 485 tests pass.
  - **Per-axis Parameters/Telemetry Explorer, same day (user: "keep going with per-axis Parameters and Telemetry
    Explorer")**: extended Stage 2's exact pattern (which only went as far as Motion) to the other two single-axis
    pages checked and confirmed to need zero changes back in Stage 2 - `ParamsTab` only ever reads `.client`/
    `.node_id`/`.is_connected` from its session (confirmed directly), so an `AxisContext` works in its place
    unmodified, exactly like `DriveTab` already did. `navigator.py`'s `add_axis_item` now expands each additional
    axis's own tree item into a small tool list of its own (Motion/Parameters/Telemetry - mirrors the primary
    drive's own activity tool list, just scoped to one axis); `axis_chosen` became `Signal(str, int)` (tool kind +
    node id, was just the node id) since one axis now has three tools, not one. `main_window.py`'s
    `self._axis_pages[node_id]` changed shape from a bare `DriveTab` to `{"motion": ..., "parameters": ...,
    "telemetry": ...}` - updated `_drive_tab_for`/`emergency_stop`/`_axis_tool_key(kind, node_id)` (was
    `_axis_tool_key(node_id)`, motion-only) to match; each additional axis's own `ParamsTab`/`ParamsTab(telemetry=
    True)` are built the MOMENT the axis connects, same reasoning as Stage 2's own DriveTab (subscribing before
    `connected`/`probed` fire, not lazily on first click). New tests prove real independence, not just two labels
    on one page: reading 242 parameters on axis 2's own Parameters page leaves the primary drive's own rows
    completely unread (`row.last is None`), and disconnecting axis 2 while ITS Parameters page is open falls back
    to the PRIMARY drive's own Parameters page (not Motion). **Still not per-axis**: Quick/Advanced Tuning (each
    tuner page is large and has its own cross-page state - Feedback Settings block selection, the automatic-
    configuration state machine - a bigger lift than a mechanical repeat of this pattern), Peripherals, and the
    Recorder (needs a `drive_tab` reference, not just a session). 488 tests pass.
  - **Per-axis Recorder and Peripherals (2026-09-23, user: "keep going with Recorder and Peripherals")**: extended
    the same pattern to the two pages the previous entry named as not-yet-done - both also needed ZERO code
    changes, confirmed directly: `PeripheralsPage` only reads `.client`/`.node_id`/`.is_connected`; `RecorderPage`
    additionally needs a `drive_tab` (what to record), which is simply that axis's own already-built Motion page.
    `navigator.py`'s `AXIS_TOOL_KINDS` grew from 3 to 5 (Motion, Recorder, Parameters, Telemetry, Peripherals, in
    the same relative order as the primary drive's own tool list). `main_window.py`'s per-axis page dict gained
    the two new kinds; each additional axis's own `RecorderPage(ctx, motion_page)`/`PeripheralsPage(ctx)` are
    built the moment the axis connects, same as every other per-axis page. **The one genuinely new wiring
    problem** (Parameters/Telemetry needed none of this): there is only ONE set of ribbon "Recording" buttons
    (Start/Stop/Clear/Save/Fit/Cursors), but now potentially several `RecorderPage` instances (primary + one per
    axis) - fixed with `_current_recorder()` (returns `self.host.current_page()` if it is a `RecorderPage`, else
    the primary's own), which every ribbon button handler now calls through instead of being bound to one fixed
    page at construction time; `open_tool()` re-syncs the ribbon's enabled-state and Cursors toggle whenever the
    newly shown page is ANY `RecorderPage`, and the "Recording" tab's own visibility check changed from `key ==
    "recorder"` (never matched an axis-qualified key like `"recorder:2"`) to `isinstance(tool.page, RecorderPage)`.
    Each `RecorderPage`'s own `cursors_btn.toggled` is wired to a shared `_on_recorder_cursors_toggled(rec, on)`
    that only updates the ribbon's shared button when `rec` is the one CURRENTLY shown, so an axis's recorder
    toggling its OWN cursors in the background never changes the ribbon while a different page is open. New tests
    prove real independence, not just relabeled buttons: axis 2's own Recorder captures ITS OWN motor actually
    running (a real 120 rpm -> ~14 Hz reading in the recorded data) while the primary recorder never starts; the
    ribbon's Start/Stop buttons act on axis 2's recorder when its page is open and back on the primary's once
    switched back; axis 2's Peripherals page reads its own 28 parameters while the primary page (never opened)
    stays unread. **The only page still not per-axis**: Quick/Advanced Tuning - unchanged reasoning from the
    previous entry (large pages, their own cross-page state, a bigger lift than this mechanical pattern). 493
    tests pass.
  - **Per-axis Quick/Advanced Tuning, completing the per-page checklist (2026-09-23, user: "keep going with
    per-axis Tuning")**: `TunerPage` (and everything it owns - `AutoTuningPage`'s own automatic-configuration
    state machine, `StepResponsePage`) also needed ZERO code changes, confirmed directly across the whole file:
    only `.client`/`.node_id`/`.is_connected` and the `probed`/`disconnected`/`disconnecting` signals are read,
    every one already on `AxisContext`. Confirmed separately, since a tuner carries far more internal state than
    any other page here: its own polling/keep-alive `QTimer`s (`_monitor`, `_live`) are only ever started by the
    page's own `showEvent`/an actual "Run Automatic Tuning" press, never at construction - so building extra
    tuner instances per axis has no background cost, same as every other per-axis page. `navigator.py`'s
    `AXIS_TOOL_KINDS` grew to 7 (Quick/Advanced Tuning added at the front, mirroring the primary tool list's own
    order). **The same "one ribbon tab, several page instances" problem Recorder already solved, extended here**:
    `_current_tuner(key)` (checks `page.advanced == (key == "advanced")` on whatever `self.host.current_page()`
    is) replaces every ribbon Drive-Load/Apply/Revert/Apply-All/Revert-All button's binding to one fixed primary
    tuner; `open_tool()` re-syncs the ribbon whenever the newly shown page is ANY `TunerPage`; `_tuner_page_changed`
    now takes the tuner INSTANCE (not a "quick"/"advanced" key) so the crumb only updates for whichever specific
    page (primary's or a specific axis's) is actually shown. Axis tool keys don't always match their primary
    tool's own key (`"quick"`/`"advanced"` vs. `"quick_tuning"`/`"advanced_tuning"`) - a small `PRIMARY_TOOL_KEY`
    map handles the disconnect-fallback case correctly. **The one genuinely new, safety-critical piece, unlike
    every prior per-axis page**: an automatic-tuning run moves the motor and overwrites the Datasheet/Control Laws
    in flash, so STOP scoping had to be got right, not just plausible. `_stop_tuners()` now takes an explicit
    tuners dict (default: the primary's own two) instead of always reaching for the fixed primary pair; each
    additional axis's own Motion page's `stop_requested` (already emitted by its own `emergency_stop()`, which the
    round STOP button already calls per axis) is wired to `_stop_tuners({"quick": ..., "advanced": ...})` with
    THAT axis's own two tuners explicitly - so axis 2's own STOP can never reach axis 3's or the primary's running
    tuning, while the round STOP button still reaches every axis's own tuner (it already calls every axis's own
    `emergency_stop()`, which now correctly cascades to that axis's own tuners through this same per-axis wiring,
    no extra code needed in `emergency_stop()` itself). Also: an axis's own Advanced Tuner's Step Response Tests
    "Open in Recorder" now opens THAT axis's own Recorder, not the primary's. New test starts a REAL automatic-
    tuning run on both the primary and axis 2 simultaneously (both simulated, both reach `state == "running"`),
    stops ONLY axis 2's via its own Motion page, and proves the primary's own run keeps going untouched, before
    finally ending it with the round STOP button - the actual behaviour this whole feature exists to get right,
    not just that the buttons are wired to *something*. **Every page is now per-axis** - the checklist that began
    with Stage 2 (Motion only) is complete. What remains from earlier stages: concatenated multi-command G-code
    lines, real G02/G03 arc interpolation, orientation-aware IK, and real-hardware validation (still simulator-
    only - the user's own second drive + CAN adapter have not arrived yet). 497 tests pass.
  - **Concatenated multi-command G-code lines and real G02/G03 arcs, completing Stage 3's own deferred items
    (2026-09-23, user: "keep going with concatenated G-code lines and real arcs")**:
    **Concatenation** - `gcode.py`'s `parse()` is unchanged (still exactly one G-code per call, still refuses more
    than one - wording now points at the new entry point instead of claiming "nothing to coordinate", since there
    now is); its old body became `_parse_segment(tokens)`, a shared per-command interpreter. New `parse_line(line)`
    splits a physical line into segments at each G-code token's own boundary and runs `_parse_segment` on each,
    returning a list - checked directly against `DOG.NC`'s own real gait line (four tab-separated commands, each
    its own `Txx`) and `UGV.NC`'s own synchronised two-axis move. `gcode_runner.py`'s new `run_actions()` runs each
    of a line's own actions through the EXISTING `run()`, in turn, handing every action's own `(action, text,
    state)` back to the caller rather than deciding anything itself; `combine_action_results()` is the module-level
    "one message for the whole line" reducer the Terminal console (`log_tab.py`) now uses (same "worst wins, one
    passes through unchanged" rule `_combine` already used one level down, for axes - labels are `_describe_action`,
    e.g. `"T3"`, `"Axis 1, 2"`). **Motion Controller's own noop-vs-refusal halt decision moved from per-LINE to
    per-ACTION** (`_advance()`'s `all_done`) - a harmless noop concatenated next to a real move must not halt the
    program, and a genuine refusal concatenated next to a noop must still halt it; a dedicated test proves both
    directions, not just that concatenation parses.
    **Arcs** - new `sdr_tuner/gui/trajectory.py` (pure, no Qt, mirrors kinematics.py's own style): `arc_center()`/
    `arc_waypoints()` support both forms the reference gives - a signed radius `R` (positive picks the arc that
    sweeps <=180 degrees, negative the one that sweeps >180) and a centre offset `I`/`J`/`K` (relative to the
    start - can describe a full circle, which R-format is refused for outright, since a radius alone cannot tell
    "already there" apart from 360 degrees). The sign/direction convention is resolved from first principles - both
    candidate centres computed, the one whose own start->end sweep angle has the sign the convention needs is
    picked - rather than by copying any one G-code implementation's own bit-tricks, which are easy to misremember
    and hard to verify from the formula alone; 8 new standalone geometry tests (`tests/test_trajectory.py`) cover a
    quarter/half circle, CW vs CCW tracing different semicircles for a diameter chord, positive vs negative R
    picking the minor vs major arc, the I/J/K format, a full circle needing an offset, an unreachable radius, and
    the "needs exactly one of R/offset" guard. This tool tracks no G17/G18/G19 modal plane, so `gcode.py`'s new
    `_parse_arc()` takes the arc's own plane as simply whichever two axes/letters the line itself gives a target
    for - confirmed against the vendor's own real line, `G03 T6 Z-400 X0 R500` (X and Z given, Y not - XZ plane, Y
    held wherever it already is, which only `gcode_runner.py` can resolve, not the stateless parser - a plain
    "arc" action carries axis indices, a Txx "tool_arc" carries X/Y/Z letters, mirroring the existing move/tool_move
    split). `F` is OPTIONAL on G02/G03, exactly like G00 vs G01's own split (the vendor's own real arc line has
    none, relying on a modal G93/G94 this tool does not track) - `gcode_runner.py`'s new `_arc()`/`_tool_arc()`
    dispatch each waypoint through the SAME multi-axis mechanism a straight move already uses (`_run_chain`/
    `_dispatch_value`), pacing consecutive waypoints with a REAL wait (`_wait()`, factored out of the G04 dwell
    timer, so `cancel()` interrupts either) when F is given - each waypoint's own largest single-axis (or, for Txx,
    single-joint) delta divided by F - or back-to-back with none when it is not (a "rapid" arc, matching G00). A
    Txx arc's own start point comes from forward kinematics on the chain's current joint values (`Chain.forward`,
    built in Stage 5 but never called from gcode_runner.py before now); each waypoint solves Inverse Kinematics
    once, seeded from the PREVIOUS waypoint's own solution, not re-read from telemetry (the drive has not
    necessarily arrived there yet) - every search after the first is a small, fast correction. **Two real bugs
    caught by testing against the vendor's own real line and the real dispatch path, not just parsing**: (1) a
    Txx arc's own centre-offset lookup was, at first, re-reading the plane's own X/Z TARGET values a second time
    instead of the I/J/K OFFSET values - would have silently produced a wrong centre for every I/J/K-format Txx arc;
    caught by writing a dedicated test for that exact path, which nothing before it had exercised. (2) dispatching
    an arc waypoint (or, it turns out, ANY Txx move whose solved joint value is not already a whole number - a
    LATENT gap in Stage 5's own `_tool_move`, only ever exercised by its one GUI test's trivial "move to home"
    case, all-zero joints) hit the protocol's own whole-number check ("X is not a whole number") - a Work Zone Count
    is a raw INT32, but a solved IK value or an arc's own trig-computed point is essentially never already an
    integer; fixed with a new `_to_count()` (rounds) applied at every point a continuous, self-computed value
    becomes a drive target - `_tool_move`, `_arc`, `_tool_arc` - never at a value the USER typed directly (already
    documented to always be a plain integer). Both bugs were found by writing and running real dispatch tests
    against the simulator (an isolated re-run after 4 tests failed in the first full-suite pass), not by parsing
    checks alone - a reminder that a parser test proving the right ACTION shape is not the same as proving the
    right thing gets SENT. Also fixed, while writing GUI tests: axis 0 (`X`) can never be a real connected node
    (1..127 only) - two new tests originally tried to connect it directly, exactly the mistake an earlier Stage 1
    test made and fixed the same way (switch to a real node + bracket form). README's G-code table, the removed
    "not yet built" note, and a new *Arcs* section replace the old refusal text; CLAUDE.md/this entry replace the
    Stage 3 "explicitly deferred" callout for both items. 518 tests pass.
  - **Axis is NOT node ID - node ID = axis + 1, corrected everywhere (2026-09-23, user: "node id 1 means axis 0,
    node is 2 means axis 1")**: refines the very first G-code correction ("axis means node id... has to match"),
    which this whole project had read as DIRECT equality ever since - axis 0 was documented, repeatedly, as
    something that "can never be reached by a real connected drive" since node IDs are 1-127. The real relationship
    is simpler than that: node IDs are never 0, so axis 0 (the first axis) is simply wired as the first valid node
    - axis N is always node N+1, and EVERY axis is reachable this way, including axis 0. Asked the user to confirm
    scope before touching anything, given the size and safety implications (this changes which physical drive a
    line actually reaches) - "Everywhere" (their choice, of 3 offered): G-code axis letters/brackets AND a
    `.kinematics` file's own "Axis" column both mean this. **The fix is entirely inside `gcode_runner.py`** - the
    one place that already knew about real connected nodes (per its own long-standing design principle: gcode.py/
    kinematics.py speak only in axis terms, gcode_runner.py is the one place that resolves a real node) - two new
    module functions, `_axis_to_node(axis) = axis + 1` and `_node_to_axis(node) = node - 1`, applied at every point
    an axis (from a parsed action's `"axis"`/`"axes"`/`"plane"`, or a kinematics chain's own `.axes`) is compared
    against or looked up in `clients` (real node IDs): `run()`'s own two axis-matching branches, `_dispatch_value`'s
    own per-client lookup (now `action["axes"][_node_to_axis(client.node)]`, was `action["axes"][client.node]`),
    and every `_run_chain(...)` call site in `_tool_move`/`_arc`/`_tool_arc` (now given `_axis_to_node(axis)` for
    each of the chain's/plane's own axes, not the raw axis itself). `gcode.py`/`kinematics.py` needed ZERO code
    changes - confirmed directly, since both were already pure "axis-index" parsers/models that never touched a
    real node ID at all; only their own docstrings needed correcting (the `.kinematics` file's own "Axis" column is
    a 0-based axis index, the SAME numbering G-code's own letters/brackets use, not a CAN node ID directly).
    **The test fallout was the truly large part of this**: `connect_axis1()` (used by ~40 G-code tests to make
    plain letter Y conveniently address the one connected drive) had to start connecting node 2, not node 1 -
    updating just that one helper fixed the vast majority of tests with ZERO further changes, since they only ever
    typed axis letters/brackets, never a specific node number. What still needed individual fixes, each verified
    against the real failure rather than guessed: every test hardcoding BOTH a specific node AND a specific axis
    together (the node-7-bench-drive axis-mismatch tests, now bracket `[6]` to match node 7; the robot-dog Txx
    tests, now connecting nodes 4/5/6 for T6's own axes 3/4/5; a second axis's own node number wherever
    `connect_axis1`'s new node 2 would otherwise collide, becoming node 3). One real, separate finding along the
    way: `combine_action_results()`'s own per-action labels (for a CONCATENATED line) show the axis the user
    TYPED, via `_describe_action`, deliberately different from `_combine()`'s own per-axis labels (for several
    axes on ONE action), which show the real dispatched NODE - a genuine, existing asymmetry, not a bug, and left
    alone. Also updated for clarity, not correctness: `session.py`'s own `counts_per_unit` comment, and the
    Kinematics Model page's own per-axis label, now show both the axis and the real node it maps to (`"Axis 3
    (node 4) - counts per unit"`). 518 tests pass (unchanged - a correction, not a feature).
  - **Motion Controller's G-code editor gained line numbers (2026-09-23, user: screenshot of the page, "give line no
    here")**: `GcodeEditor` (`motion_controller.py`), a `QPlainTextEdit` subclass with a line-number gutter down its
    left edge - PySide6 has no built-in one, so this is Qt's own standard recipe (`setViewportMargins` reserves the
    strip, a small child widget - `_LineNumberGutter` - paints into it, kept in sync via `blockCountChanged`/
    `updateRequest`), not a third-party widget. Numbers match what `_log_line()` already reports in the output log
    below ("Line N: ..."), 1-based; muted colour (`theme.DIM`), no new colours added. Purely visual - `self.editor`
    is still a `QPlainTextEdit` everywhere else in this file (`_highlight`, `_run_program`, Open/Save), so nothing
    else needed to change.
  - **A 4th bundled sample, `esc_rpm_sweep.nc` (2026-09-23, user: "save the demo program as a sample")**: the
    30-line synthetic ESC RPM ramp built to verify the line-number gutter above (100-390 RPM in steps of 10),
    formalized into a real sample matching the other hand-written ones' own style (`speed_ramp.nc`'s own header/
    dwell/closing-Reset pattern) rather than saved byte-for-byte as the bare, dwell-free scratch version shown on
    screen - with no G04 between steps the original would have fired all 30 speed commands in a blink, not a
    useful demo. Each step now holds 0.2 s (`G04 P0.2`), ending with `G1000` (Reset All Axes), like `speed_ramp.nc`.
    Shows up correctly in the Samples menu ("Esc Rpm Sweep", alphabetically among the other three) and loads its
    own 67 lines - checked directly, along with the wrapped-line case in its own header comment, which the gutter
    (added just before this) correctly keeps single-numbered rather than double-counting the wrap.
  - **Verified `esc_rpm_sweep.nc` end-to-end against the simulator (2026-09-23, user: "load it and run it in the
    simulator")**: connected as node 1 (matching the sample's own axis 0), loaded it via the Samples menu, pressed
    RUN, and let it run to completion - all 30 steps executed (100 to 390 RPM, each held 0.2 s), the closing
    `G1000` sent Reset, `client.commanded` correctly back to `None`, status bar showing "G-code program finished."
    Screenshotted for the user. The user then asked to run it on the REAL bench drive - flagged first, before
    attempting anything, that the sample addresses axis 0 (node 1, per the axis/node correction above) while the
    bench drive is node 7, so running it unmodified would connect fine but skip every line (a real, if unhelpful,
    demonstration of the axis-mismatch refusal, not an actual test of anything). Asked which of three concrete
    options to take (retarget the RUN via bracket notation, run as-is to see the mismatch, or use a different real
    node) rather than guess and either silently rewrite what gets sent to real hardware or send something that
    would visibly do nothing. **The user's own answer changed the plan entirely**: they will change the REAL
    drive's own Node ID to 1 themselves (matching the sample, rather than the sample being adapted to node 7), and
    asked for the simulator to be re-checked with node ID EXPLICITLY set to 1 in the meantime - done, same clean
    finish. When they then said to run it on the real drive, immediately followed by "wait" / "real drive is not
    connected yet" - stopped immediately, touched nothing on the real-hardware front; this remains open until the
    user confirms the physical drive is actually connected. **A live example of the standing rule in practice**:
    the user's own explicit go-ahead for one specific action ("run it on the real drive") does not survive past
    the moment they retract or qualify it, even seconds later and even without a fresh request from Claude - the
    correction is followed immediately, not questioned or re-confirmed.
  - **Additional axes named `DriveNN (SDR)`, not `Axis N` (2026-09-23, user: screenshot of the navigator tree
    showing "Axis 2", "when user add axis 2 then show Drive 02 (SDR) for axis 3 show Drive 03 (SDR) and so on")**:
    `navigator.py`'s `add_axis_item()` default label changed from `f"Axis {node_id}"` to `f"Drive{node_id:02d}
    (SDR)"` (zero-padded like the primary's own default "Drive01 (SDR)", `set_drive()`) - `NN` is the real node ID
    itself, matching the user's own example (axis 2 -> "Drive 02") exactly, just without the space they typed
    (matched the EXISTING "Drive01" - no space - convention instead, for one consistent numbered-drive look, not
    a deliberate difference). Extended to the two other places an additional axis is named, for the same
    consistency, not because the user's own screenshot showed them: `main_window.py`'s per-axis `Tool` title (was
    `"Axis N - {page title}"`, shown in the window's own breadcrumb/status bar when that axis's page is open - would
    have looked broken clicking straight from the newly-renamed tree item into a still-old-named breadcrumb),
    `axes_page.py`'s own "Axis Name" column (`AxesStatusPage`), and `config_page.py`'s own Additional Axes list
    label (Item Configuration) - all three now read "DriveNN (SDR)" too. Deliberately NOT touched: every "Axis N"
    that means a G-code line's own axis selector, not a drive's display name (`gcode_runner.py`'s own dispatch/
    combine messages, `config_page.py`'s Kinematics Model counts-per-unit label, transient connection-error status
    messages) - a different concept entirely (see the axis/node correction above), changing those would be
    conflating two unrelated meanings of the same word. Verified visually: Additional Axes 2 and 3 connected, the
    navigator tree and the Additional Axes list both correctly read "Drive02 (SDR)"/"Drive03 (SDR)".
  - **Additional axes flattened into the shared "Drive Setup and Motion" tool list, not their own nested subtree
    (2026-09-23, user: screenshot circling the tree's own nested Quick/Advanced Tuning children in red vs. the
    shared tool-list box in blue, "any drive we select tunning process should show in below box only... not in
    red area", ending with "if you had any question or doubts then ask me before making any changes")**: asked
    two clarifying questions first, given the size of the change and the explicit instruction to ask - (1) flatten
    every additional drive's own tree item to match the primary's (no expandable subtree), re-pointing the SAME
    shared list at whichever drive is selected - user chose this, over the alternative of leaving some other
    structure; (2) whether the 3 workspace-level rows already in that list (Terminal, Motion Controller, Axes -
    one shared page each, not per-drive) should stay visible when an additional drive is selected, or hide - user
    chose to keep them, unchanged, no matter which drive is selected. **Implementation, once confirmed**:
    `navigator.py`'s `AXIS_TOOL_KINDS` and the nested-children loop in `add_axis_item()` are gone - an additional
    axis is now a single flat `QTreeWidgetItem`, `UserRole` data = its own node id (was `(kind, node_id)` per
    child); `axis_chosen` narrowed from `Signal(str, int)` to `Signal(int)` to match. New `add_activity()` support:
    a tool tuple's optional 4th element is a stable "kind" tag (`"motion"`, `"recorder"`, ...) on the 7 per-drive
    rows only - the 3 shared rows carry none, so they are never touched. New `retarget_drive_tools(kind_to_key)`
    walks the "Drive Setup and Motion" list and re-points every tagged row's own key at `kind_to_key[kind]`,
    leaving untagged (shared) rows alone - `main_window.py`'s new `_on_axis_selected(node_id)` (replacing
    `_on_axis_chosen(kind, node_id)`) calls it with `{kind: self._axis_tool_key(kind, node_id) for kind in
    PRIMARY_TOOL_KEY}` before opening that axis's own Motion page (`PRIMARY_TOOL_KEY`'s own dict, already built for
    Stage 3's own disconnect-fallback, turned out to be exactly the "kind -> key" shape both directions needed);
    `_on_drive_chosen()` (the primary's own click handler, replacing an inline lambda) retargets back to
    `PRIMARY_TOOL_KEY` itself before opening Item Configuration, so the list is never left pointing at a
    since-deselected axis; `_on_axis_disconnected()`'s own existing fallback (already correct: falls back to the
    primary's own page when the axis whose page was OPEN disconnects) now ALSO retargets the list back to primary
    in that same branch, for the same reason. Two GUI tests needed rewriting since they exercised the removed
    nested-children structure directly (`item.child(i)`) - now click the axis's own flat item, then click a row of
    the shared list by its CURRENT label (a new `_click_setup_tool()` test helper - a real click, not a signal
    shortcut); a new test proves the round trip (select an axis, open one of its pages, select the primary again,
    confirm the SAME list row now opens the primary's own page instead). Verified visually end-to-end: selecting
    Drive02 opens its own Motion page with "Motion - Single Axis" highlighted in the shared list and "Drive02
    (SDR) - Motion" as the page title; clicking "Recorder" in that same list opens Drive02's own Recorder, not the
    primary's.
  - **Additional axes always listed in numerical order, not connection order (2026-09-23, user: screenshot showing
    Drive02/Drive05/Drive03 in that order, "if i add axis 5 first and then add axis 3 then just make sure it will
    rearrange all axis in numerical order")**: `add_axis_item()` used to just append a new `QTreeWidgetItem` after
    whatever was already there (`QTreeWidgetItem(self.workspace, [...])`, which is always an append in Qt) - fixed
    by constructing the item WITHOUT a parent, computing its correct sorted position (`1 +` however many of the
    OTHER already-connected axes have a smaller node ID - the `1` skips the primary's own item, always index 0),
    and inserting it there (`self.workspace.insertChild(position, item)`) instead. Self-healing by construction:
    since every insertion already lands in the right spot relative to whatever is already correctly sorted before
    it, there is no separate "resort everything" step needed, and no way for the list to drift out of order over
    time no matter what order axes connect or disconnect in. New test connects axes 5, 3, 8, 2 in that exact
    (scrambled) order and confirms the tree reads 01, 02, 03, 05, 08 - checked directly against
    `workspace.child(i)`, not just membership in `_axis_items` (which every existing test already checked, but
    membership alone says nothing about ORDER, which is exactly what was wrong here).
  - **2D Plotter: live XY trace of two axes against each other (2026-09-23, user: "if we have 2 axis connected then
    we can write a code for 2 axis in G-code (basically interpolation) eg. making circle and also it can show in 2D
    graph - this is available in servo tunning tool -original")**: the vendor's own "Servopilot" tooling has a
    feature by exactly this name - confirmed as a genuinely different thing from its separate "Oscilloscope" tab
    (which the user pointed out is just the same value-vs-time graphing this tool's own Recorder page already
    does, after an initial `strings`-based binary scan of `Servo-Drive-IDE.exe` conflated the two) once the user
    shared the real 2D Plotter screenshot directly. Asked, and confirmed: it always plots each selected axis's own
    **Work Zone Count (position)** - not speed or any other channel - which is what actually traces a physical
    shape (a G02/G03 circle across 2 axes shows as an actual circle, not two independent sine-like time traces).
    New workspace-level page (`gui/plotter_2d.py`, `Plotter2DPage`, glyph `scatter` - the same axes-corner frame
    `_chart` uses, with a scattered run of dots instead of its zigzag line, no existing glyph fit) - NOT per-axis
    and NOT a mode bolted onto `RecorderPage`, exactly the same reasoning that already keeps Terminal/Motion
    Controller/Axes single and shared: it inherently needs to see two *different* connected axes at once. Built
    directly off `axes_page.py`'s own `AxesStatusPage` shape (`__init__(self, session)`, a 100 ms `QTimer` -
    matching `RecorderPage.REFRESH_MS` - wired to `session.connected/disconnected/axis_connected/axis_disconnected`,
    reading each client's own `telemetry_snapshot()["wz_count"]`, never requested) and the same `"Primary"` /
    `f"Drive{node:02d} (SDR)"` naming already used everywhere else. **X Axis** / **Y Axis** combo boxes list every
    currently connected axis and default to the two lowest-numbered ones; picking one and having a THIRD axis
    connect afterwards never disturbs an already-made pick (only re-picking does), matching the reference
    screenshots' own two axis pickers. No Start/Stop/recording-session concept at all, unlike the Oscilloscope or
    this tool's own Recorder - just an always-on trail, growing a new point whenever BOTH selected axes report a
    Work Zone Count that differs from the last one plotted, reset only by **Clear All**; if a selected axis
    disconnects, the trace simply stops growing - frozen exactly where it left off, the same way
    `RecorderPage._on_disconnected` already freezes its own chart rather than clearing it, and the axis stays
    picked in its own combo rather than being silently reset out from under the user. **Save Image…** and
    **Save Data…** (CSV of the recorded X/Y pairs) match `recorder.py`'s own export conventions. The plot itself is
    a `pg.PlotWidget()` (reusing `pyqtgraph`/`theme.style_plot`, exactly like `recorder.py`'s own `Chart`) with
    `setAspectLocked(True)` - a circle needs equal X/Y scale to actually look like one - and pan/zoom explicitly
    re-enabled afterward (`style_plot` turns mouse interaction off by default, like Recorder already re-enables it).
    Wired into `main_window.py` as an 11th **shared**, untagged row in the "Drive Setup and Motion" tool list (no
    per-drive `kind` tag - a plotter comparing two *different* axes does not belong to either one alone, so it is
    not one of the 7 retargetable per-drive rows), right after Axes. **Verified end-to-end against a real dispatch,
    not just wiring**: connected 2 simulated axes, dispatched a real `G03 [1].. [2].. R1000 F300` arc via the
    Terminal console, and watched the plotter's own trace grow into a genuine curved arc from (0,0) toward the
    target - screenshotted for confirmation. The centrepiece test (`tests/test_gui.py`) reuses this exact scenario:
    every point the plotter actually captures during a real arc dispatch is checked against the arc's own true
    centre and radius (computed the same way `trajectory.py` itself does) rather than against an exact expected
    waypoint list, since the plotter's own 100 ms polling and the arc's real-time pacing (`F300`, slow enough that
    several waypoints actually land in distinct telemetry broadcasts, unlike a fast `F50000` arc which would finish
    inside a single poll) are two independently-timed processes - a robust property regardless of exactly how many
    waypoints either one catches, since the simulator only ever snaps `wz_count` to a discrete commanded waypoint,
    never interpolates between them, so anything captured is always an exact point on the real arc. One real race
    caught while writing that test: waiting for axis 1's own commanded target to arrive is not proof axis 2's has
    too (their dispatch loops are paced independently once F is slow enough for real per-waypoint delay to matter,
    unlike a fast arc where both finish within the same tick) - fixed by waiting for both explicitly, not asserting
    the second immediately after the first. 9 new tests; 530 tests pass.
  - **2D Plotter moved next to Motion Controller (2026-09-23, user: "2D plotter is a part of motion controller so
    place it under motion controller")**: the "Drive Setup and Motion" tool list is a flat `QListWidget` (no real
    tree nesting under one row, unlike the workspace tree above it), so "place it under" means positioned directly
    below it in that list - moved from after Axes to right after Motion Controller, before Axes, in
    `main_window.py`'s own `_build_navigator()` tools list (and the `self.tools` dict, for the same order, though
    that one has no visible effect on its own). `test_the_window_is_laid_out_like_a_studio`'s own tool-order
    assertion updated to match. The ribbon's "Floating Tools" group has never had a 2D Plotter button at all (a
    pre-existing gap from when the page was first built, not touched by this move) - left as is since the user
    asked about placement in the list, not the ribbon.
  - **Node ID uniqueness confirmed, not a new fix (2026-09-23, user: "node id must be different for each drive and
    can not be same nither in simulation nor in physical... otherwise if i am using drive with same node id in 2
    different machine then its not the issue")**: a screenshot appeared to show a duplicate label ("Drive01 (SDR)"
    twice) after adding an axis - investigated directly rather than assumed: `Session.connect_axis` (`session.py`)
    already refuses outright (`node_id == self.node_id or node_id in self.axes`) on EVERY interface, simulated or
    real, with a clear "Axis N is already connected." error - already covered by
    `test_connect_axis_refuses_a_node_already_in_use`. Confirmed live in the simulator too (tried node 2 while the
    primary was node 2 - refused; added node 4; tried node 4 again - refused). The user's own "different machine"
    exception does not apply within this tool, since Additional Axes are always wired to the SAME shared bus as the
    primary by design - there is no code path here where two axes on the same session could legitimately share a
    node. The screenshot's own "duplicate" label was a SEPARATE, purely cosmetic coincidence, not a node collision:
    the primary's display name comes from the free-text Target Name field (defaults to the literal text "Drive01"),
    while additional axes are auto-named from their real node ID (`Drive{node:02d}`) - so a primary left at its
    default name and an axis connected at real node 1 both render "Drive01 (SDR)" even though the underlying node
    IDs (2 and 1 in that example) are correctly distinct. Asked the user whether to change the display; they
    dismissed the question, so left as is.
  - **Additional axes get their own expandable identity details, and the workspace tree tracks what is actually
    being looked at (2026-09-23)**: found by watching a user-supplied screen recording frame by frame (the
    established scratch-venv + `imageio-ffmpeg` + `eq=gamma=2.3` workflow, this one HDR/dim like the others) after
    the user reported "i am not able to see details of drive 02 (SDR)". The recording showed clicking "Drive02
    (SDR)" DOES correctly open its own "Drive02 (SDR) - Motion" page (confirmed frame by frame, and separately by
    directly re-running the same click in the simulator) - the real gap was that clicking back to the
    **System Configuration** activity always reopens the PRIMARY's own Item Configuration (the only page that
    activity has), with the tree left stale, still highlighting Drive02 from before, even though the content shown
    is entirely Drive01's own - looking exactly like "Drive02 has no page of its own" (which is in fact true: only
    the primary ever gets a System-Configuration-style page). Asked the user how they wanted Drive02's own identity
    made visible; they chose expanding it in place over giving it a whole separate page. Built: `config_page.py`'s
    `_on_axis_connected` now builds a real, collapsed-by-default `PropertyGroup` (`f"Drive{node:02d} (SDR)"`) per
    additional axis - not the old plain QLabel-plus-Remove row - with its own Device Type / Device Serial Number /
    Firmware Serial Number / Connection State rows, using the SAME `client.device_info` every axis already reads at
    connect (`read_device_info()`, called by the shared `_probe()` for primary and additional axes alike - the data
    was always there, just never shown for anything but the primary); a new static
    `ItemConfigurationPage._show_device_value` factors out the `"{value}   (0x{value:X})"` formatting so
    `show_device_info` (primary) and this new per-axis path share it instead of duplicating it. **Remove** is added
    to the group's own OUTER layout (`group.layout().addWidget(...)`, below the collapsible `rows` frame) so it
    stays visible whether or not the row is expanded - `tests/test_gui.py`'s existing Remove-button lookup, which
    assumed the button was the row's ONLY (and therefore first) `QPushButton`, had to switch to finding it by its
    own text instead, since the group's own fold header is now also a `QPushButton`. Two follow-on tree-sync fixes,
    both because the tree is meant to reflect "what am I actually looking at", not just "which page is technically
    open": (1) `_on_activity_changed(activity)` now resets the tree's own current item back to the primary's
    whenever the **System Configuration** activity itself is (re-)opened, since that activity only ever has one
    page; (2) the user's own immediate follow-up, "in work place your selection is drive 01 and not drive 02" -
    expanding an axis's own details row is what the user is actually looking at even though Item Configuration is
    structurally still the primary's own page, so a new `ItemConfigurationPage.axis_focused` signal (emitted only
    on EXPAND, not collapse - collapsing back does not itself move focus elsewhere) tells `main_window.py` to
    highlight that axis in the tree too. Verified end to end in the simulator, not just via tests: connected a
    second axis at node 2 while the primary was node 1, expanded its row (device type/serials/"SIMULATED DRIVE ·
    NODE 2" all shown correctly, distinct from the primary's own "NODE 1"), confirmed the tree switched to
    highlighting "Drive02 (SDR)", and confirmed collapsing the primary's own activity button afterward correctly
    snaps the tree back to "Drive01 (SDR)" - screenshots sent to the user at each step. 3 new tests
    (`test_an_additional_axes_row_expands_to_show_its_own_device_details`,
    `test_expanding_an_additional_axes_row_focuses_it_in_the_workspace_tree`,
    `test_the_navigator_tree_stops_highlighting_an_axis_when_system_configuration_is_reopened`) plus the one
    existing test's Remove-button lookup fix. 533 tests pass.
  - **Clicking an axis while on System Configuration no longer jumps to Drive Setup and Motion (2026-09-23)** -
    found from a second user-supplied screen recording (same frame-by-frame workflow), after the previous entry's
    own fix: the user's video showed clicking "Drive02 (SDR)" in the tree from the default System Configuration
    view immediately switching to its Motion page, so the "expand it in place" fix from the entry above was never
    actually reachable that way - only via scrolling to Additional Axes on the PRIMARY's own page and expanding it
    manually. User: "i am not able to see the drive 2 system configuration... as soon as i click it shift to drive
    setup and motion". Fixed in `main_window.py`'s `_on_axis_selected(node_id)`: it now checks
    `self.navigator.current_activity()` BEFORE doing anything - if it is already `"system"` (the only activity
    Item Configuration belongs to), it calls a new `ItemConfigurationPage.focus_axis(node_id)` (expands that
    axis's own `PropertyGroup` row and `self.scroll.ensureWidgetVisible(group)` to scroll it into view) and
    returns, WITHOUT retargeting the Drive Setup and Motion tool list or switching activity at all. From any other
    starting point (in practice, already being on Drive Setup and Motion), the click behaves exactly as before -
    retargets the shared tool list to this axis and opens its own Motion page; this branch is unchanged and still
    the ONLY way to reach an axis's Motion/Recorder/Parameters/Telemetry/Peripherals/Tuning pages (first switch to
    Drive Setup and Motion, then click the axis - a real, if two-step, change to how those pages are reached from
    a cold start, which used to be one click regardless of the starting activity). **This broke more existing
    tests than expected, and finding all of them mattered**: since the DEFAULT activity on a fresh window is
    `"system"` (confirmed directly, not assumed), most existing axis-click tests connected an axis and clicked it
    immediately without ever switching to Drive Setup and Motion first - they were unknowingly relying on that
    default. `-k "axis or axes"` only caught 3 of the 4 affected tests (a name-substring filter can't see what a
    test's BODY does) - `test_selecting_the_primary_drive_again_retargets_the_tool_list_back_to_it` doesn't have
    "axis" in its own name and only surfaced on a full-suite run; re-grepped the whole file afterward for every
    `itemClicked.emit(win.navigator._axis_items[...])` call site (7 total) to confirm nothing else was missed,
    rather than trust the targeted run alone. Each fixed test now clicks the "Drive Setup and Motion" activity
    button first, matching the realistic flow for what it is actually testing (that axis's own Motion/Parameters/
    Telemetry pages). **A real, if minor, test-writing trap found along the way**: a bare `assert
    group.rows.isVisible()` checked immediately after a click can read stale (False) on the offscreen QPA
    platform even though the widget's own `setVisible(True)` already ran correctly - Qt's own visibility state
    needs an event-loop turn to fully settle after a fresh window's `show()`, which earlier tests only avoided by
    coincidence (an intervening `qtbot.waitUntil` for something else happened to pump enough events first) - fixed
    by using `qtbot.waitUntil(lambda: ...isVisible(), timeout=WAIT)` instead of a bare assert, the correct pattern
    for any post-interaction Qt visibility check, not just this one. New test
    `test_clicking_an_axis_while_on_system_configuration_reveals_its_row_instead_of_opening_motion` covers both
    directions (stays put from System Configuration; still opens Motion from Drive Setup and Motion) in one place.
    Verified live in the simulator end to end, screenshot matching the user's own scenario exactly. 534 tests pass.
  - **Additional axes get a real, complete "Item Configuration" page of their own, superseding the inline detail
    block from the previous two entries (2026-09-23)**: shown the result of the previous fix (an expandable block
    on the primary's own page, then a tree click landing on that same block), the user pushed for the actual
    end state directly: "in drive 2 system configuration why node id is showing 1" (pointing out the primary's own
    Target Connection fields at the top never described the selected axis, only a separate block further down
    did), then, once shown the alternative of the top fields tracking the selection, "Show only the selected
    drive's info at the top", and finally, unambiguously, "when drive 2 is selected then every single information
    showing must be about drive 2 only". Rather than mutate `ItemConfigurationPage`'s own live, pre-connect-editable
    Connection Type/Port/Bit Rate/Node ID widgets to display a DIFFERENT axis's values (and have to cache and
    restore the primary's own values every time selection changed - a real risk of corrupting the primary's own
    not-yet-connected settings), built a new, separate `AxisConfigurationPage(ctx, channel_text, bitrate_text)` in
    `config_page.py` - the exact same "per-axis page, reusing the primary's own page-building blocks unmodified"
    pattern this project has used for every other per-axis page all session (Motion, Recorder, Parameters,
    Telemetry, Peripherals, Tuning): General (Target Name - its own auto name, since axes have no custom rename;
    Target Type; Device Type/Serial/Firmware from `ctx.client.device_info`, refreshed on `ctx.probed`), Target
    Connection (Connection Type/Port/Bit Rate shown read-only, captured from the primary's own fields at the
    moment the axis was built, since they are always the SAME shared-bus values; Node ID and Connection State
    genuinely its own), Product Activation and Networks. **`ParameterGroup` needed zero changes** to bind to an
    axis instead of the primary - confirmed directly, it already only reads `.client`/`.node_id`/`.is_connected`
    and the `probed`/`disconnected` signals, exactly what `AxisContext` already provides for every other per-axis
    page. Deliberately does NOT include Additional Axes (add/remove is a workspace-level action, stays on the
    primary's own page) or Kinematics Model (one shared multi-axis model, not per-axis).
    `ItemConfigurationPage`'s own per-axis row in the Additional Axes list reverted to a plain name + Remove line
    (the expand-in-place mechanism, `focus_axis`/`axis_focused`, is gone - fully superseded, not layered on top).
    **Wiring** (`main_window.py`): `_on_axis_connected` builds this axis's own `AxisConfigurationPage` alongside
    its other 7 pages, registered under `f"config:{node_id}"` with activity `"system"` (the one page here whose
    shape does not fit the shared per-kind loop the other 7 already use, so it is registered just outside it);
    `PRIMARY_TOOL_KEY` gained a `"config": "config"` entry purely so `_on_axis_disconnected`'s existing, already-
    generalized fallback loop (`open_tool(PRIMARY_TOOL_KEY[kind])`) keeps working uniformly across all 8 kinds
    without a special case - confirmed harmless for `retarget_drive_tools` too, since it only ever acts on rows
    actually tagged with a matching `kind` in the Drive Setup and Motion list, and "config" tags none of them.
    `_on_axis_selected(node_id)` now always retargets the Drive Setup and Motion list first (unchanged), then opens
    this axis's own `"config"` tool if System Configuration is the activity CURRENTLY open (checked before
    anything changes it) or its own `"motion"` tool otherwise - exactly mirroring what clicking the primary's own
    tree item already does (Item Configuration from System Configuration, otherwise leaves you wherever you were).
    **The tree-resync logic from the previous entry needed rethinking, not just reuse**: it used to always force
    the tree back to the primary whenever System Configuration was reopened, which was correct back when that
    activity could only ever show the primary - now it can show EITHER the primary's own page or one specific
    axis's, so `_on_activity_changed` derives which tree item to highlight from `self._last_tool["system"]` itself
    (`"config"` → the primary; `"config:N"` → axis N) instead of hardcoding the primary; a new test
    (`test_the_navigator_tree_tracks_whichever_axis_owns_the_config_page_shown`) proves the axis case specifically,
    alongside a rewritten version of the previous entry's own primary-case test. Verified live in the simulator:
    selecting Drive02 opens a complete, independent Item Configuration page (its own Target Name "Drive02 (SDR)",
    Device Type/Serials, Node ID 2, "SIMULATED DRIVE · NODE 2", Product Activation, Networks - screenshot sent to
    the user) with no Additional Axes/Kinematics sections. 533 tests pass (the previous entry's own tree-focus test
    for the now-removed inline block was replaced, net one fewer test than its own count implied).
  - **Two small UI polish requests, from a screenshot circling both (2026-09-23)**: (1) "increase button size" (the
    ribbon's own collapse/expand toggle, top right, next to Help) - it shared the same small `ribbonSmall` QSS
    class (14px) as the Help button, but Help LOOKS bigger anyway since it draws a 20px icon, while the collapse
    toggle is a bare "▴" text glyph at that font size - easy to miss, exactly what got circled. Gave it its own
    `ribbonCollapse` QSS class (`ribbon.py`/`theme.py`) at 22px, bold - visibly closer in weight to Help now,
    without touching Help's own styling or the collapse/expand behaviour itself. (2) "give option to minimise the
    window" (circling Motion Controller's own output log panel, not the OS window) - added a checkable "Hide
    Output" / "Show Output" button (`motion_controller.py`) next to Save…, toggling `self.output.setVisible()`;
    nothing logged is lost while hidden (`QTextEdit.append()` still works on a hidden widget, and the editor above
    it - already `outer.addWidget(self.editor, 1)`, stretch 1 - naturally reclaims the freed space once the fixed-
    height log is hidden, needing no extra layout code). New test
    `test_motion_controller_hide_output_toggle_minimises_the_log_without_losing_it` - caught one real setup gap
    while writing it: checking `mc.output.isVisible()` on a page that was never actually opened (`win.open_tool
    (...)`) reads False regardless of the toggle, since `QStackedWidget` keeps every non-current page hidden - not
    a bug in the toggle, just a test needing to open the page first, the same class of gotcha other pages' own
    tests already know to avoid. 534 tests pass.
  - **The bottom-right connection pill and the top-right target badge now track whichever drive's page is actually
    shown, not always the primary (2026-09-23, user: screenshot of Drive02's own Motion page, "i have selected
    drive 2 but why on bottom right side it shows node id 1" - followed by "there are so many small bugs... just
    check each and every single part of prog and fix it")**: both `win.pill` (bottom right) and `host.target`
    (top right, next to the page header) had ALWAYS shown the PRIMARY's own connection text/name - correct for
    Item Configuration and every shared/workspace-level page, but wrong while viewing an axis's own page, where it
    looked like the drive you were looking at had never changed. Fixed in `main_window.py`: `open_tool(key)` now
    tracks `self._current_key`, and a new `_current_owner()` derives the node id that key belongs to (`None` for
    the primary/a shared page, matching the exact same `"kind:node_id"` key convention `_on_activity_changed`'s
    own tree-resync from two entries ago already parses - refactored that one line to call `_current_owner()` too,
    so there's one place computing it, not two). `_set_pill` (the PRIMARY's own connection-lifecycle handler -
    `_on_connected`/`_on_probed`/`_on_disconnected`/etc.) now always updates the primary's own Item Configuration
    row and a remembered `self._primary_pill`, but only writes to the VISIBLE pill widget when a primary/shared
    page is what's actually open - a new `_axis_pill_text(ctx, ok)` mirrors the exact same three-way text (NO
    RESPONSE / SIMULATED DRIVE / ONLINE, plus their own node) for whichever axis owns the shown page instead. A new
    `_sync_drive_badges(ok=True)` is the single place both badges get recomputed from `self._current_key` - called
    from `open_tool()` (switching pages), from `_on_target_renamed()` (renaming the primary must NOT overwrite an
    axis's own badge while its page is open - this used to unconditionally call `host.set_target()`), and from
    each axis's own `ctx.probed` signal (only when `_current_owner()` still matches that axis, so a background
    probe on an axis whose page is NOT currently shown never touches the visible badges). Also audited the other
    per-axis-shared page classes (`drive_tab.py`, `params_tab.py`, `recorder.py`, `peripherals.py`, `tuner.py`) for
    the same class of bug - a stray `self.session` reference where `self.s` (the page's own bound context) should
    be used instead - grep found none; they were already consistently axis-correct. New test
    `test_the_bottom_right_pill_and_top_right_badge_track_whichever_drive_is_shown` covers both badges, the
    rename-does-not-overwrite case, and switching back to the primary restoring its own. Verified live in the
    simulator with the user's own exact scenario (screenshot: Drive02's own Motion page now correctly reads
    "Drive02 (SDR)" / "SIMULATED DRIVE · NODE 2", not the primary's Node 1). **A full-suite run right after this
    change showed 6 failures, all in the real-timer-based automatic-tuning tests, none of them reproducing when
    re-run individually or as a small group** - traced to a PRE-EXISTING, unrelated fragility: one of them
    (`test_a_value_written_early_does_not_end_the_wait_while_the_drive_says_it_is_still_working`) waits on a
    production timing constant (`tuner.py`'s own `SETTLE_MAX = 10.0` s) against a test timeout (`WAIT = 10000` ms)
    that are EXACTLY equal - zero slack, so any extra system load during a full ~7-minute run can tip it over; nothing
    in this change touches timing or threading. A second full run right after, no other changes, passed clean.
    535 tests pass.
  - **A real bug: Drive02 could show Drive01's own position after selecting it from System Configuration then
    clicking "Drive Setup and Motion" (2026-09-23)** - the user reported "when i rotate drive one to certain
    position and check drive 2, drive 2 also shows same position" and, since a from-the-code check couldn't
    reproduce it via the Motion pages or the Axes page (both genuinely isolated, confirmed directly - simulator
    instances, clients, telemetry history buffers and chart curves are all separate per axis), asked for the exact
    steps rather than keep guessing: "select drive 1 and rotate then go back to system configuration and select
    drive 2 and then go to motion". Reproducing that exact sequence found a REAL bug, and it was not a data-
    isolation problem at all: selecting a drive (`_on_drive_chosen`/`_on_axis_selected` in `main_window.py`) only
    ever retargeted the Drive Setup and Motion activity's own shared tool LIST - `_last_tool["setup"]` (what the
    activity BUTTON itself reopens, separate from that list) kept remembering whichever drive's page was last
    opened DIRECTLY, and the two could silently disagree. Concretely: rotate Drive01 on its own Motion page
    (`_last_tool["setup"] = "motion"`, the primary's own key) - go to System Configuration and select Drive02
    there (opens its own dedicated Item Configuration, from the earlier entry - never touches "setup" at all) -
    click the Drive Setup and Motion ACTIVITY BUTTON: it reopened `_last_tool["setup"]`, still "motion" - Drive01's
    own, STALE page, showing Drive01's own already-rotated Work Zone Count, while LOOKING like Drive02 (since the
    tree/badges correctly said Drive02 by now) - the exact illusion the user described. Fixed by keeping
    `_last_tool["setup"]` in sync with the SAME drive the retarget call already points the list at: `_on_drive_chosen`
    now also sets it to `"motion"`, `_on_axis_selected` to that axis's own `"motion:N"` - unconditionally, in both
    branches, so the activity button and the list can never point at different drives again. **Tracing this
    surfaced a second, related latent bug, not yet hit live**: if `_last_tool["setup"]` (or `["system"]`) is left
    pointing at an axis's own key when that axis later disconnects, `_on_axis_disconnected`'s own cleanup only ever
    fixed it up if that axis's page happened to be the CURRENTLY shown one - a `_last_tool` entry for an activity
    that was never revisited in between would keep a dangling reference to a key `self.tools` had just removed,
    ready to `KeyError` the next time that activity's own button was pressed. Fixed in the same pass: the
    disconnect handler now scans EVERY activity's own `_last_tool` entry for a reference to the disconnecting
    axis's own keys, not just the one behind the current page. Two new tests
    (`test_the_drive_setup_activity_button_reopens_whichever_drive_was_last_selected`,
    `test_disconnecting_an_axis_clears_any_dangling_last_tool_reference_to_it`) cover the reported bug and the
    latent one found alongside it. Verified live in the simulator against the user's own exact steps, screenshot
    sent showing Drive02's own Motion page correctly reading Work Zone Count 0 instead of Drive01's rotated value.
    537 tests pass. **Followed by a full audit** (user: "continue checking other pages for the same bug") of every
    remaining place this exact class of bug could hide: all 7 per-drive tool kinds (not just Motion) via the
    activity button after selecting an axis from System Configuration; the Advanced Tuner's own "Open in Recorder"
    shortcut (bypasses `retarget_drive_tools` directly, but unreachable in an inconsistent state - by the time that
    button exists on screen, the list was already retargeted to get there); the same check in the System
    Configuration direction; and confirmed switching ACTIVITIES on the SAME already-selected drive correctly
    preserves the specific tool that was open (does not reset to Motion) - only switching to a DIFFERENT drive
    does, which is pre-existing, intentional design (`test_clicking_the_axis_in_the_navigator_opens_its_motion_page`,
    written earlier this session) confirmed via one test script whose own wrong expectation initially looked like a
    bug. No further instances found; the fix generalizes correctly.
  - **Orientation IK: full-pose (position + orientation) Inverse Kinematics, driven from a new GUI control, not
    G-code (2026-09-24, after switching to a new Mac - see the device-move memory note)**: the last item the
    Stage 5 kinematics work explicitly left out of scope, since no G-code move command can express a target
    ORIENTATION at all (only X/Y/Z) - so before writing any code, asked the user two scoping questions rather than
    guess: (1) position+orientation together (a full 6-DOF pose) vs. orientation-only (aiming a turret like
    radar.obj, ignoring position) - user chose full pose; (2) how the target orientation should be given - new
    G-code fields on Txx vs. a dedicated GUI control - user chose the GUI control, so `gcode.py` needed no changes
    at all (still exactly the same "G-code has no orientation to give" scope it always had).
    **Math** (`kinematics.py`, no Qt, same style as everything else there): `tip_pose()`/`Chain.forward_pose()`
    return the tip's orientation (a 3x3 rotation matrix) alongside its position - the tip's HOME orientation is
    taken to be the fixed/root frame's own identity, a judgement call the file format itself gives no other way to
    make (`Tool Direction` is one vector, not a full frame - see the module docstring). `solve_pose_ik()`/
    `Chain.solve_pose()` generalise the existing damped-least-squares solver to a 6-vector error (3 mm position +
    3 radians of a rotation "log map" - `_log_so3`/`_rotation_error_vector`, the standard closed form from Lynch &
    Park's "Modern Robotics" already cited elsewhere in this file, with the near-180-degree singularity handled
    separately) instead of 3 - confirmed algebraically that applied to position alone this reduces to exactly the
    existing `solve_ik`'s own update, not a different formula. `euler_to_matrix`/`matrix_to_euler` (Z-Y-X,
    yaw-then-pitch-then-roll) are a human-friendly GUI convenience only - the solver itself always works with the
    matrix directly. New `tests/test_kinematics.py` coverage (8 tests): Euler round-trips including the gimbal-lock
    case (roll/yaw individually not recoverable, but the RECONSTRUCTED matrix still matches); a pose round-trip
    through `forward_pose`/`solve_pose` on humanoid's own 9-axis right arm (T27), mirroring the existing
    position-only round-trip test; and, the actual point of this feature, radar.obj's pure pan/tilt/roll turret
    (T3) - the exact chain `test_position_only_ik_is_vacuous_for_a_pure_orientation_chain` documents as unsolvable
    by position alone (every joint AND the tip sit at the same point) - now correctly converges to the real joint
    values, since orientation is no longer a blind spot.
    **Dispatch** (`gcode_runner.py`): new `GcodeRunner.move_to_pose(tool, target_position, target_rotation, ...)`
    mirrors `_tool_move`'s own Txx dispatch exactly (same missing-axis/missing-telemetry checks, same
    `_run_chain`/`_dispatch_value` multi-axis mechanism, same `counts_per_unit` conversion) but is called directly
    by the GUI, never through `run()` - it has no `gcode.py` action of its own, by design, since there is no line
    for one to be parsed from.
    **GUI** (`config_page.py`, Item Configuration): a new "Tool Pose (Orientation IK)" group below Kinematics
    Model - a Tool combo (refreshed whenever a model loads, alongside the existing counts-per-unit rows), Target
    Position X/Y/Z and Target Roll/Pitch/Yaw fields, and a GO button that only ever dispatches when pressed. Needed
    `ItemConfigurationPage` to gain its own `GcodeRunner` (`drive_tab_for` now passed into its constructor, same as
    `LogTab`/`MotionControllerPage` already receive) - the one new piece of wiring this required. 12 new end-to-end
    GUI tests (`tests/test_gui.py`): the tool combo tracking the loaded model, GO solving and dispatching to every
    joint of a real multi-axis chain (robot-dog's T6, axes 3/4/5), refusing cleanly with no model loaded, and
    reporting (not guessing) when a target is unreachable. 549 tests pass. **Never tried on a real drive** - same
    standing reasoning as every other kinematics feature: it sends nothing the existing multi-axis dispatch
    mechanism does not already send, just computed from a different (pose, not position) target. **Verified live in
    the real app the same day** (not just offscreen tests): moved the running app to its own macOS Desktop (Mission
    Control) so its window would stay genuinely frontmost for scripted clicks - loaded radar.obj, set Target Yaw to
    45° on the new Tool Pose panel, pressed GO, and watched it correctly dispatch "Axis 1: Moving to 45 counts;
    Axis 2/3: Moving to 0 counts" to 3 real connected simulated axes.
  - **The Axes status page and the 2D Plotter showed the literal word "Primary" for the primary drive's own row,
    never its own editable Target Name (2026-09-24, user: "in axis page i see primary instead of drive 01")** -
    every OTHER place the primary's name appears (navigator tree, the bottom-right pill, the top-right badge) has
    always shown its own configurable Target Name (`main_window.py`'s `_target_name()`, "Drive01" by default);
    these two pages were the only ones that hardcoded a generic label instead, unchanged since each was first
    built (their own docstrings even said so explicitly: "the same 'Primary' / f'Drive{node:02d} (SDR)' naming"). 
    Fixed with one new shared helper, `axes_page.py`'s `drive_label(node, primary_node_id, primary_name)` - used by
    both `AxesStatusPage` and `Plotter2DPage` instead of each hardcoding its own copy of the same logic; both pages
    now take a `primary_name` callable in their constructor (the same `_target_name` bound method `LogTab`/
    `MotionControllerPage`/`ItemConfigurationPage` already receive as `drive_tab_for`), called fresh on every
    refresh tick so a rename shows up live, not just at connect time. New test proves the live-rename case
    specifically (not just the default), not merely that the old literal is gone. 550 tests pass; verified live in
    the real app on its own Desktop (the same technique as the orientation IK entry above) - connected, opened
    Axes, confirmed "Drive01" instead of "Primary".
  - **The ribbon's collapse/expand toggle still looked small next to Help, even after the 2026-09-23 font-size bump
    (2026-09-24, user circled it a second time, same screenshot region)** - root cause: a plain text CHARACTER
    ("▾"/"▴") only fills a fraction of its own em-box, so a bigger font-size has real diminishing returns; Help's
    own icon looks bigger at the same nominal size because it is a properly DRAWN icon that fills its box, not a
    character. Fixed by giving the toggle the same treatment as every other ribbon icon instead of tuning the font
    further: two new glyphs in `icons.py` (`_chevron_down`/`_chevron_up`, a bold open "v", the same drawn-icon
    style as `_help` and the rest), `ribbon.py`'s `collapse_btn` now uses `setIcon(icon("chevron_up"/"chevron_down",
    26))` like `help_btn` already does, swapping icons in `_toggle_collapsed()` instead of swapping text. QSS
    trimmed to just background/padding (the old `font-size`/`color`/`:hover` rules had no effect on a drawn icon
    pixmap anyway - the same limitation Help's own button already has). Verified live in the real app on its own
    Desktop: both the expanded (^) and collapsed (v) icon states, clicked for real, screenshots sent to the user.
    550 tests pass (unchanged - no test asserted the button's own text).
  - **First real-hardware validation session with a motor actually attached (2026-09-24, user: "physical servo
    drive is connected check all parameters with it and make sure every thing is working fine")**. Before touching
    anything, asked the user to scope it (AskUserQuestion, given real hardware + real motor is a materially bigger
    risk than the earlier bench sessions): they chose "full functional test (moves the motor)" and confirmed a
    motor IS attached. Found the drive's own USB port (`/dev/cu.usbmodem2078396D48431`), same node 7 as the earlier
    bench sessions. **Read-only pass** (`tools/usb_readonly_check.py`, unchanged): all 417 parameters (242 config +
    175 telemetry) answered, 0 faults, DC bus 24.0 V, 10 poles, telemetry broadcasting at the expected 9 Hz -
    drive healthy. **Real-motor speed test**: new `tools/real_motor_speed_test.py` (same hard-guard pattern as the
    other two tools/ scripts - lets through ONLY reads, the ESC RPM command at exactly the agreed RPM, and Stop;
    aborts up front if a fault is already latched or the DC bus is not plausibly powered) - 50 rpm for 3 s,
    confirmed by TELEMETRY per the user's own instruction ("you don't need physical person to inspect, you can see
    from drive telemetry as well"), not visual inspection: phase currents (Ia/Ib) actively varied throughout (the
    inverter is genuinely driving the motor), Work Zone Speed read real non-zero values while commanded, Stop
    brought it back to ~1 rpm within ~1 s, fault bits stayed 0x0000 throughout. The Work Zone Speed reading was
    noisy/sign-flipping - explained, not hidden, to the user: no physical encoder is configured on this drive
    (Motor/Servo Encoder both 0), so speed is estimated sensorlessly, and 50 rpm sits right at that estimator's own
    zero-speed threshold (~36 rpm) - a known sensorless-control limitation at low speed, not a fault. **User's own
    standing rule, stated mid-session**: "always check node id of connected drive before issue any command as
    command like will change as node id changes" - saved to memory; both test runs already had auto-detected the
    node before sending anything, but the node is now stated explicitly and prominently in the report, not left
    implicit in a log line.
  - **A real, if minor, bug found while investigating an odd reading during the test above, then fixed on the
    user's own standing instruction ("fix it now and fix all bug you find in future while developing the code")**:
    Work Zone Speed came back `None` from `telemetry_snapshot()` throughout the speed test even though the drive
    genuinely broadcasts it. Root cause was NOT a client decode bug - `client.py`'s `load_tpdo_mapping()` already
    correctly refuses to guess at a mapping slot that does not fit an 8-byte CAN frame (tested, intentional
    behaviour - see `test_a_slot_the_client_cannot_place_ends_the_message`), and on THIS real drive, Fault Bits
    (2B) + the FLOAT32 DC-bus register (4B, from the 2026-09-22 remap) + Work Zone Speed (4B) is 10 bytes, over the
    limit - a genuine side effect of that earlier, legitimate remap growing slot 2 by 2 bytes. The real bug was
    that this failure was completely SILENT - a configured field just never appears, with nothing to say why.
    Fixed: `DriveClient` gained `tpdo_overflow` (`[(cob, slot, reason), ...]`), populated by `load_tpdo_mapping()`
    for every slot it could not place (the one that overflowed, and - since the exact drive-side byte layout after
    an unplaceable field is unknowable - every slot after it in that same message too, unchanged from before, just
    now recorded instead of silently skipped). `session.py`'s own `_probe_summary()` (the connect-time status
    message, already the established place for "layout could not be read") now appends a WARNING line when
    `tpdo_overflow` is non-empty; `main_window.py`'s `_on_probed` status-level check (previously a fragile
    substring match on "could NOT") extended to also treat "WARNING" as a real warning, not plain info. 4 new
    tests (`test_client.py`: the overflow case is recorded with the real byte-math reason, the reference default
    layout has no overflow, the unrecognised-mapping-code sibling case; `test_gui.py`: connecting with an
    overflowing mapping shows the warning with "warn" styling). 554 tests pass. Verified against the real drive
    (read-only, node 7 confirmed first): `tpdo_overflow` correctly reports "Work Zone: Speed: needs 4 more byte(s)
    but only 2 left in this 8-byte message" instead of silence. **Not fixed** (would need writing the drive's own
    persisted TPDO mapping configuration - a real hardware write, needs its own explicit permission first, not
    assumed just because the bug report was): actually restoring Work Zone Speed to the live broadcast would mean
    reconfiguring which registers occupy which slot on the real drive itself; reading it on-demand (as the speed
    test above already did) works fine as a stand-in until/unless the user wants that.
  - **Orientation IK tried on the real drive (2026-09-24, user: "try orientation IK on the real drive next and
    check every thing from GUI as user is only going to use GUI")**: this bench setup only has ONE physical drive
    (node 7), so a real multi-axis chain isn't possible - built a genuinely minimal single-joint kinematics file,
    `sdr_tuner/data/kinematics_samples/bench_node7.kinematics` (axis 6 = node 7, tool T7, a single rotary joint,
    100 mm tip radius), now a bundled Sample alongside the vendor ones. Checked at the CODE level first (an
    offscreen headless-Qt script driving the real `MainWindow`/`Session` against the real USB port): connecting,
    loading the sample, and pressing GO with a typed-looking target (86.60, 50.00, 0.00 / yaw 30.00, rounded to 2
    decimals - what the GUI's own fields would actually hold) correctly solved the pose and then hit the SAME
    Feedback Settings refusal MOVE TO/BY already has (no Motor/Servo Encoder configured on this drive) - a real,
    safe, informative outcome, not a bug.
    **A second real, if minor, bug found along the way and fixed on the same "fix it now" standing instruction**:
    the first attempt at that exact target reported "did not converge" despite being only ~0.002 mm off - traced
    to `solve_pose_ik`'s own default position tolerance (1e-3 mm, 1 micron) being TIGHTER than the Tool Pose
    fields can even express (2 decimal places, 0.01 mm) - a genuinely, exactly reachable target (computed from
    `forward_pose` itself) still failed once rounded to what the GUI would actually show. Fixed in
    `gcode_runner.py`'s `move_to_pose()` only (kinematics.py's own default stays unchanged for math-level callers,
    where the tight tolerance is correct) - now asks for `tolerance=0.05` explicitly. 2 new tests (test_kinematics.py:
    the tolerance mismatch reproduced and fixed at the math level with a synthetic single-joint fixture;
    test_gui.py: a spy on `Chain.solve_pose` proving `move_to_pose` actually passes the looser tolerance). 556
    tests pass.
    **Then verified live in the real running app with actual mouse/keyboard** (moved to its own Desktop first, the
    established technique): Connection Type -> USB cable to the drive, port auto-filled correctly, Connect ->
    genuinely online, Device Serial/Firmware Serial matching the code-level reads exactly, and the telemetry-
    overflow WARNING from the earlier fix showing correctly in the status bar too. Loaded the bench_node7 sample,
    typed the same target into the real fields, pressed GO - got the exact same Feedback Settings refusal text,
    confirming the whole path end to end through the actual UI a person would use, not just headless.
    **A real GUI-automation gotcha hit and fixed while typing into these fields, worth recording for future
    sessions**: synthetic digit keystrokes silently did nothing (arrow keys worked fine) - traced to the OS-level
    Command modifier being left logically "held" after an earlier Cmd+A (select-all) synthetic event, since only
    the flag on another key's event had been set, never a real key-up for the Command key itself; every following
    plain digit thus arrived as an unrecognised Cmd+<digit> shortcut instead of text. Fixed by explicitly sending
    a Command key-up (keycode 55) and explicitly zeroing flags on every non-modifier key event afterward.
  - **"Try each and every button and function from GUI" sweep against the real drive (2026-09-24, user: "try each
    and every button and function from GUI")**: asked the user up front how far to go (AskUserQuestion, since some
    of what's untested is destructive - real writes to Product Activation/Networks, and real Automatic Tuning,
    which moves the motor and overwrites flash) - they chose "Truly everything, including auto-tuning". Node 7
    confirmed once at connect per the standing rule, then exercised via real mouse/keyboard automation (own
    Desktop, established technique) rather than just the CLI scripts, covering Item Configuration, Axes, 2D
    Plotter, Quick Tuning's read-only pages, Product Activation (read + a Write round-trip, value unchanged and
    confirmed correct by a fresh Read afterward), Networks (Read only - deliberately did NOT press Write on the
    real drive: a bad CAN bit-rate/node-ID write could make it unreachable over USB2CAN, judged not worth it for a
    UI-exercise pass; told to the user inline). **Quick Tuning -> Automatic Tuning -> START AUTOMATIC TUNING, for
    real**: pre-flight checklist (motor data valid / no fault / DC bus 24.0 V) green, confirmation dialog showed
    the correct Max Continuous Current (1.9 A) and Poles (10) before Yes; ran for about 30 s ("the drive is
    measuring the motor..."), completed with "Auto tuning completed successfully." and 19 Datasheet/Control Law
    values changed (phase resistance/inductance, back-EMF constant, inertia, damping, then FOC/ESC/Servo/
    Micro-Speeding gains) - the pre-flight checklist and START button both went back to their ready state
    afterward, no fault, confirming the drive came back healthy.
    **Rest of the sweep, same session**: Advanced Tuning's own pages all load and read correctly against the same
    real drive - Feedback Settings (Sensorless, no encoders, matching Quick Tuning), Results -> Measured Motor Data
    and Control Laws both showing the EXACT values the Quick Tuning run above had just written (cross-checked
    field by field - confirms the write genuinely persisted to the drive, not just to the page that ran it),
    Filters, Application Settings, Limits and Protections all read cleanly. Summary page correctly shows "the
    automatic tuning has not been run in this session" - a deliberate per-tuner-instance design (Quick Tuning's own
    run doesn't retroactively populate a DIFFERENT Advanced Tuning session's before/after table), not a bug.
    **Verification -> Step Response Tests - a second, different kind of real motor motion (continuous square wave,
    reverses every Period, runs until STOP - not a bounded one-shot like the speed tests)**: asked the user
    separately before running this one, since it's a materially different risk shape from what "including
    auto-tuning" had already covered; they said yes with a small amplitude. Ran Speed (ESC) test, Period 0.5 s,
    Amplitude 3 Hz (well under the already-validated 200 rpm-equivalent), confirmation dialog matched, watched it
    run for ~6 s ("Testing - Speed Step Response", no fault), pressed STOP - "Test stopped.", Operation Mode back
    to Idle. "Open in Recorder" correctly pre-populated Chart #2 with the Phase A signal from that test.
    **Recorder** (only reads broadcast telemetry, nothing sent to the drive): Start/Stop both real (71 samples in
    23.6 s), Fit rescaled correctly, Cursors gave a correct A/B/delta readout table, Save wrote a real CSV (213
    rows, since deleted - it was throwaway idle-current noise, not real user data) via a native save dialog, Clear
    emptied both charts back to READY.
    **Parameters Explorer**: READ ALL confirmed 242 parameters. Individual row Write round-trip tested on Rotary
    Gearbox: Reduction Ratio (chosen for being inert - a pure software scaling factor) - wrote 1.0 back onto
    itself, a fresh Read on that same row confirmed it stuck.
    **Peripherals**: READ ALL confirmed (28 parameters, matches the connect-time auto-read). WRITE CHANGED tested
    on Electronic Gearing: multiplier (inert - RC PWM/Electronic Gearing's own "enable" is off) - changed 1 -> 2,
    confirmation dialog ("Write 1 changed parameter(s) to node 7?"), Yes, "Wrote 1 parameter(s)."; then written back
    2 -> 1 the same way to leave the drive exactly as found.
    **Terminal**: bare `?` correctly rejected ("no G-code found (expected a token starting with G...)"). A real
    `G1030 [6]50` (node 7 = axis 6, same 50 rpm magnitude already validated) showed "Commanding 50.0 rpm." with the
    live CAN trace confirming real ESC RPM frames repeating on 0x207; `G1002` showed "Stop sent." with the matching
    Stop frame in the trace. The same trace pane also happened to show the Peripherals write/revert above at the
    protocol level (0x3015:0x20 Electronic Gearing: multiplier, 1 -> 2 -> 1), a nice independent confirmation.
    **Motion Controller**: a real 3-line program (`G1030 [6]50` / `G04 P2` / `G1000`) - RUN produced a correct
    line-by-line log ("Commanding 50.0 rpm." -> "Dwelling for 2.0 s..." -> "Dwelt 2.0 s." -> "Reset sent." ->
    "Program finished."), confirming G04 is a REAL pause here (unlike Terminal's own G04, which the README already
    says is countdown-only with no next line to pause before).
    **2D Plotter**: opened correctly, X Axis/Y Axis selectors both show only "Drive01" - the bench only has one
    physical drive, so a genuine two-axis trace (e.g. a real G02/G03 arc drawing its actual shape) cannot be
    demonstrated here; this was already known and is not a bug, just this bench's own hardware limit.
    **This closes out the entire "try each and every button and function from GUI" task** - every page and control
    in the app has now been exercised against the real bench drive (node 7), with real writes where authorized and
    a clean revert everywhere a written value wasn't meant to persist.
  - **3D Simulation built (2026-09-24, user: "open servo tunning tool - original and check for 3D simulation option
    under motion controller", then "yes, scope it out", then "yes, start on Stage 0 and start all other stages one
    by one and check it your self and complete it").** Opened the vendor's own Windows IDE folder,
    `Servo tunning tool-original/` (a sibling folder the user pointed at directly - can't run a Windows .exe here,
    so investigated via `strings` on `Servo-Drive-IDE.exe` and its own bundled assets instead): confirmed a real
    "3D Simulation" feature exists - `dockWidgetPlcSimulation`, a genuine custom OpenGL panel (`QOpenGLWidget`,
    `QOpenGLShaderProgram`, VAO/VBO, loads "OBJ files (*.obj)") under the vendor's own "Motion Controller"/PLC
    activity, with Load/Run/Reset View controls and Axes/Cursor/Simulation view modes. The exact `.obj` mesh files
    it renders were already sitting in that same folder's `plc/assets/`, one real mesh per vendor `.kinematics`
    sample already bundled here (humanoid 8.8 MB, ko-bot 9.9 MB, robot-dog 9.2 MB, radar 276 KB) - confirmed by
    checking each mesh's own `o <name>` group names against that SAME file's `.kinematics` Name column: identical,
    row for row. Scoped with the user (two AskUserQuestion rounds): bundle all 4 meshes now (not just the small
    one), and build LIVE mode (driven by real connected axes) before an offline no-hardware preview mode.
    **Stage 0** (foundation): added `PyOpenGL` to `requirements.txt` (`pyqtgraph.opengl`, already a dependency via
    `pyqtgraph` itself, needs it); copied the 4 `.obj` files into `sdr_tuner/data/kinematics_samples/`, next to
    their matching `.kinematics` files. New `sdr_tuner/mesh.py` (pure functions, no Qt, same style as
    `kinematics.py`/`gcode.py`): `parse_obj_text()`/`parse_obj_file()` split a file by its `o <name>` groups into
    `{name: Mesh(vertices, faces)}`, each group's own vertex array COMPACTED (`np.unique`/`np.searchsorted`, not a
    per-triangle Python loop - matters at robot-dog's own ~141k triangles) rather than a slice of the whole file's
    vertices, so a 13-body model doesn't upload 13x the vertex data it actually needs. `kinematics.py` gained
    `KinematicModel.body_transforms(joint_values)` - {body id: 4x4 transform} for EVERY body in the tree, not just
    tool-tip chains - a small, ~15-line reuse of `_actuated_ancestors()` + the existing `forward_kinematics()`, no
    new math. 12 new tests (`test_mesh.py`: real-file group/name checks plus synthetic error-path cases - a
    non-triangular face, a face before any `o` line, an empty file; `test_kinematics.py`: identity at home,
    moving one axis only moves ITS OWN descendants (a different leg stays untouched), cross-checked directly
    against `Chain.forward`'s own already-tested math at a chain's own tip, a rigid axis-less row gets its nearest
    actuated ancestor's transform).
    **Stage 1** (static viewer) + **Stage 2** (live mode) built together, since the live `_tick()` logic was a
    small, natural addition once the static per-body transform machinery existed: new `sdr_tuner/gui/sim3d.py`
    (`Sim3DPage`) - `pyqtgraph.opengl.GLViewWidget` + one `GLMeshItem` per body (bronze/gold, `shader="shaded"`),
    reactively rebuilt from `session.kinematics_model` on the SAME `kinematics_changed` signal Item Configuration
    already emits (no separate load step). A body with no matching `.obj` group falls back to a small placeholder
    sphere at its own Axis Origin Point rather than being silently omitted. LIVE mode: on the same 100 ms tick
    `Plotter2DPage`/`RecorderPage` already use, each connected axis's real Work Zone Count (never requested) is
    converted through `session.counts_per_unit` - the exact same conversion `gcode_runner.py`'s own
    `_current_joint_value` already does - into `body_transforms()`, applied via `pg.Transform3D` (row-major,
    verified directly against a hand-built numpy transform + `QVector3D.map()` before trusting it in the real
    page). A body whose own actuated ancestors aren't ALL connected just stays at its last known/home pose - the
    same "partial data shown honestly" rule `plotter_2d.py`'s own single-axis limitation already establishes.
    New icon `_cube` (`icons.py`, an isometric-cube outline, no existing glyph fit), wired into the navigator/tool
    dict right after Motion Controller (matching the vendor's own grouping), no ribbon button (2D Plotter, its
    closest sibling, doesn't have one either - followed that precedent rather than fixing an unrelated gap).
    Extracted `_axis_to_node`/`_node_to_axis` in `gcode_runner.py` to PUBLIC names (`axis_to_node`/`node_to_axis`,
    dropped the leading underscore) since `sim3d.py` needed the SAME node<->axis mapping - one definition, a
    second importer, not a duplicate; the module's own "ONE place this translation happens" docstring claim still
    holds (about the definition, not about which files may call it) - matches how `drive_label()` was already
    shared between `axes_page.py` and `plotter_2d.py`. 9 new GUI tests (`test_gui.py`): no-model message; one item
    per body for a real-mesh model (radar, 4 bodies); placeholder fallback for a mesh-less model (`bench_node7`);
    scene clears when the model is cleared; a connected axis's own transform matches `body_transforms()` exactly
    (cross-checked via `QVector3D.map()`, not just "did it change"); a body with NO connected ancestors (radar's
    own chassis, at the root) stays at the identity transform even while a DIFFERENT axis is live; Reset View
    doesn't crash with nothing loaded; a rigid-only model reports "no actuated axes" cleanly. 565 tests pass.
    **A real bug found by looking at the actual running app, not caught by any offscreen test** (this Mac's
    `QT_QPA_PLATFORM=offscreen` cannot create a real OpenGL context at all - "QOpenGLWidget is not supported on
    this platform" - so the offscreen suite could only ever prove the DATA was right, never that anything actually
    rendered): loaded `radar.obj` in the real app and saw the grid floor but no mesh at all - just a barely-visible
    speck. Root-caused, not guessed at: the vendor's own `.obj` files are authored in METRES while every
    `.kinematics` file (and `body_transforms()`'s own translations) are in MILLIMETRES - confirmed directly by
    comparing `robot-dog.obj`'s own `leg-1-1` mesh group centroid, (-0.3815, 0.0783, 0.0002), against that SAME
    body's `.kinematics` Axis Origin Point, (-350, 57, 0) mm - a match once converted to metres. This wasn't only a
    cosmetic camera-framing bug (the camera's own distance floor, tuned for mm-scale, swallowed a metre-scale
    model): applying an mm-scale rotation's translation straight to metre-scale vertices would have pivoted a
    MOVING body's own rotation roughly 1000x farther from the mesh than the mesh's own real extent, a real
    correctness bug for live mode specifically, not just a static-view nuisance. Fixed in `sim3d.py` only (kept out
    of `mesh.py`, which stays a general, unit-agnostic OBJ reader): `_load_matching_mesh()` now scales every
    parsed mesh's own vertices `* 1000.0` once, right after loading; the camera-framing floor (was an arbitrary
    50 mm, sized for the WRONG unit) fixed to a genuine degenerate-model epsilon (10 mm) instead of a value that
    could silently override a real model's own span again. New regression test
    (`test_sim3d_page_converts_the_vendors_own_metre_scale_obj_vertices_to_millimetres`) locks in the exact 1000x
    factor against the real `radar.obj`. 566 tests pass.
    **Then verified live in the real running app, twice** (restarted the app both times, since Python does not
    hot-reload - the first live check was against a stale, pre-fix instance): first pass caught the metres/mm bug
    above; after the fix, `robot-dog.obj` (a real 12-axis, 4-legged model) rendered correctly at its home pose,
    visually matching its known geometry. Connected the SIMULATOR at node 1 (axis 0, since the real bench drive is
    node 7 = axis 6, and no bundled model uses axis 6 with a real mesh) and sent a real `G1030 X30` speed command
    through the Terminal console - leg-1-1 visibly rotated around its own hip joint on screen while "Live: 1 of 12
    axis(es) connected." showed in green, then `G1002` stopped it cleanly. **Not real hardware** - deliberately
    the Simulator, since a genuinely multi-body LIVE render needs several connected axes at once and this bench
    only has one physical drive; documented honestly in OPERATIONAL_MANUAL.md and README's own *Still to confirm*
    rather than implied as hardware-tested. **Explicitly out of scope, per the original plan**: offline (no
    hardware) G-code-driven simulation and a manual joint-scrub "Cursor" mode - flagged, not built.
    **One pre-existing test needed updating, not fixing** - `test_the_window_is_laid_out_like_a_studio` asserts the
    navigator's exact tool-key list for Drive Setup and Motion; added `"sim3d"` to it, in the same position the
    real navigator wiring puts it (right after Motion Controller, before 2D Plotter). Full suite: 582 tests pass
    (556 baseline + 12 in `test_mesh.py` + 5 `body_transforms` tests in `test_kinematics.py` + 9 in `test_gui.py`).
  - **Offline (no-hardware) SIMULATE mode built (2026-09-24, user: "build the offline simulation mode next")** -
    the third mode 3D Simulation was explicitly scoped to leave out ("Explicitly out of scope, per the original
    plan" above). Before writing anything, checked the four bundled `data/gcode_samples/*.nc` programs directly
    rather than assume what a naive "snap to target" preview would show: 3 of 4 (`speed_ramp.nc`,
    `esc_rpm_sweep.nc`, `torque_ramp.nc`) are pure speed/torque ramps with NO fixed position target at all - a
    speed command has no natural endpoint to animate to, so a preview that only handled position moves would show
    almost nothing move for most of this project's own demo content. Asked the user two real judgement calls
    before building (AskUserQuestion): (1) how to show a speed (G1030) command in the preview - spin the axis
    continuously (rpm -> deg/s) vs. no visual motion - chose continuous spin; (2) whether SIMULATE always starts
    from the model's own home pose or continues from wherever the view currently is - chose always home, for a
    clean, repeatable preview.
    **New `sdr_tuner/gui/gcode_sim.py`** (`VirtualAxis` namedtuple + `axis_value_now()` + `VirtualGcodeRunner`):
    reuses `gcode.py`'s own parser and `kinematics.py`'s own chain-solving completely unchanged - only the
    DISPATCH differs from `gcode_runner.GcodeRunner`'s own real-hardware one, writing into a new
    `session.virtual_axes` dict (axis -> `VirtualAxis(value, rate_per_sec, since, until)`) instead of any CAN
    command, and skipping the Feedback Settings check entirely (no real encoder to ask about offline). Three
    deliberately different per-action treatments: esc_rpm spins continuously (`until=None`) at a plain rpm -> deg/s
    rate, for as long as that speed stays commanded, and only for a ROTARY axis (a linear one has no defined rpm
    motion, so it holds, with a note); move/linear_move/tool_move JUMP instantly for G00/G1020/Txx-without-a-
    feed-rate (no accel/decel model exists without a real drive to ask), or ANIMATE over a feed-rate-derived
    duration for G01/Txx-with-a-feed-rate (`until=since+duration`, self-clamping once time passes it - a caller
    reading it after the move "finished" sees the real target, never an overshoot); torque shows NO motion at all
    (an honest limitation, not a guess: no load/inertia is known offline) with a clear note; reset/off/stop/
    capture/release freeze every axis this run has ever touched (or just the one named) at its CURRENT
    interpolated value - the closest real match to what stopping a real motor right now would do. Txx solves IK
    exactly like `GcodeRunner._tool_move` (current joint values read from `session.virtual_axes` instead of
    telemetry) then feeds the result through the SAME per-axis move dispatch, mirroring how the real dispatch
    turns a solved Txx into a synthetic move/linear_move rather than a separate code path. Arcs (G02/G03) are
    REFUSED with a clear "not yet supported" message, not half-implemented - none of the bundled samples use one;
    the same waypoint-discretising approach `GcodeRunner`'s own `_arc`/`_tool_arc` already use could extend this
    later if it turns out to matter. `_wait`/`cancel()` deliberately DUPLICATE `GcodeRunner._wait`/`cancel()`'s
    own small (~15-line) cancellable-QTimer pattern rather than extracting a shared helper - a considered choice:
    the two runners are otherwise different enough (this one touches no client, no button, no Feedback Settings
    check at all) that the dedup was judged not worth adding an import/indirection for, or touching
    `GcodeRunner`'s own already-hardware-tested dwell timing for a purely stylistic reason. 25 new tests
    (`test_gcode_sim.py`, a tiny `FakeSession` standing in for the real one, exactly the shape `VirtualGcodeRunner`
    reads - `.virtual_axes`/`.counts_per_unit`/`.kinematics_model`).
    **`motion_controller.py`** gained a **SIMULATE** button reusing the EXISTING RUN/PAUSE/RESUME/STOP/Loop state
    machine (`self._simulating` is the only new flag - which runner `_advance()` calls into, and whether STOP
    touches real hardware) rather than a second one, since RUN and SIMULATE are simply two different ways to
    execute the SAME loaded program, never both at once; `_run_program()`/`_simulate_program()` share a new
    `_start(simulating)` helper (parse the editor's own lines, reset run state). SIMULATE needs a loaded
    kinematics model (not a connection) to even start, and always calls `sim_runner.reset()` first (every axis
    back to 0) per the user's own "always home" choice; STOP, when simulating, only cancels a dwell and freezes -
    it does NOT touch `self.s.all_clients()`/`drive_tab_for` at all, confirmed by a dedicated test with a REAL
    client connected at the same time SIMULATE is running, proving Simulate never reaches real hardware in either
    direction. `_finish()`'s own success message says "Simulation finished." vs "Program finished." so the two
    are never confused in the log.
    **`sim3d.py`** gained **Live**/**Simulation** radio buttons (`QButtonGroup`, matching the vendor's own
    `radioButtonPlcSimulationAxes`/`radioButtonPlcSimulationSimulation` naming - see the original 3D Simulation
    entry above for how that vendor detail was found) - both modes share the exact same rendering
    (`_render(joint_values)`, one `body_transforms()` call, unchanged from Live-only); only where a tick's own
    `joint_values` dict comes from differs (`_tick_live()`, unchanged logic, vs new `_tick_simulation()`, reading
    `session.virtual_axes` through `gcode_sim.axis_value_now()`). Switching modes re-ticks immediately
    (`mode_group.buttonClicked`) rather than waiting up to 100 ms for the next natural tick.
    **`session.py`** gained `virtual_axes = {}` (the shared axis -> VirtualAxis dict both `gcode_sim.py`'s runner
    and `sim3d.py`'s own tick read/write) - `load_kinematics()`/`clear_kinematics()` now clear it too, alongside
    `counts_per_unit`, for the exact same reason that field already does: axis 0 in a NEWLY loaded model is not
    necessarily the same joint axis 0 the PREVIOUS model named, so a stale virtual pose left over from the old
    model would silently mean the wrong thing applied to the new one.
    **A real, reusable Qt-testing gotcha hit and fixed while writing the sim3d mode-toggle tests, worth
    remembering for future sessions**: `qtbot.mouseClick()` on a widget belonging to a page that is NOT the
    currently-shown one in `PageHost`'s stack silently does nothing (no exception, the click event just never
    reaches the widget) - unlike calling `.click()` directly on the button/radio, which invokes its own slot
    programmatically and works regardless of visibility. Every new test that clicks `sim3d_page`'s own Live/
    Simulation radio buttons now calls `win.open_tool("sim3d")` first; existing tests that only click
    `motion_controller_page`'s own buttons were unaffected (apparently already on a page Qt had given real
    geometry, or exercised via a route that does not depend on synthetic event delivery the same way - not fully
    root-caused beyond "the target widget's own `isVisible()` must be True", which is sufficient to work around).
    6 new sim3d tests (Live default, Simulation-mode rendering matches `body_transforms()` exactly via the same
    `QVector3D.map()` cross-check the original Live-mode test already established, each mode correctly IGNORING
    the other's own data source even when both are simultaneously active, spin-count reporting) plus 10 new
    Motion Controller SIMULATE tests. 25 (gcode_sim) + 6 (sim3d) + 10 (motion controller) = 41 new tests. Full
    suite (623 tests) confirmed green right after this feature landed.
    **Then verified live in the real running app** (restarted first - Python does not hot-reload; user: "verify it
    live on screen and commit"): loaded `robot-dog.obj` (no connection needed - Simulate needs a model, not a
    drive), loaded the bundled `speed_ramp.nc` sample onto Motion Controller, pressed SIMULATE with the app fully
    **DISCONNECTED** throughout - the whole 8-step program (150 -> 300 -> 500 -> 1000 -> 2000 -> 3000 -> 1000 ->
    500 rpm, each held 1.5 s, then Reset) ran to completion, ending "Simulation finished." in the log exactly as
    RUN's own "Program finished." would. Then typed a single `G1030 X30` (no end command, so it keeps spinning)
    and switched the 3D Simulation page to **Simulation** mode: status read "Simulation: 1 axis(es) with a
    commanded value (1 spinning)." in green, and two screenshots taken ~1.5 s apart show the SAME leg in two
    genuinely different rotated positions - real, continuous, on-screen animation, not a static snapshot, still
    with zero drive connected. `G1002` afterward correctly froze it (status confirmed via the log: "Stop
    (simulated) - holding every axis."). Also confirmed directly, a real but harmless discovery along the way:
    "Simulation finished."/"Program finished." means the LINE SEQUENCE ended, not that motion stopped - a bare
    `G1030 X30` with nothing after it reaches the end of its own one-line program almost immediately while the
    commanded spin keeps going in the background, exactly mirroring what a real drive does when a RUN program
    ends on an active speed command with no trailing Stop - not a bug, the same semantics as real hardware.
  - **CURSOR mode built (2026-09-24, user: "build the manual joint-scrub Cursor mode next")** - the third and
    last of the three modes this page was originally scoped for (Live, Simulation, and this one - the vendor's
    own `radioButtonPlcSimulationCursor`). Unlike the other two, Cursor needed no new shared state at all: it is
    a slider + `DoubleSpinBox` per actuated axis (`_rebuild_cursor_panel()`), synced the SAME way
    `drive_tab.py`'s own speed control already syncs its slider and field (`x10` integer scale, `blockSignals`
    both ways to avoid a feedback loop - reused the established pattern rather than inventing a new one), writing
    into a dict that is PURELY LOCAL to `Sim3DPage` (`self._cursor_values`, never touches `session` - nothing
    else needs to read a manually-scrubbed pose, unlike `virtual_axes`). Reuses the exact same `_render()`/
    `body_transforms()` path the other two modes already share - moving one joint's own slider correctly moves
    its whole descendant sub-tree too, for free, since that is just what `body_transforms()` already does.
    Each row's own range comes from that axis's own `.kinematics` file `Axis Limits` when given (checked
    directly: `robot-dog.obj`/`humanoid.obj` mostly have real ones, `radar.obj`/`ko-bot.obj` and this project's
    own `bench_node7` rig all leave the column blank) - falls back to a generous, explicitly-documented default
    (`DEFAULT_ROTARY_RANGE` ±180°, `DEFAULT_LINEAR_RANGE` ±500 mm, the latter untested by any real bundled file
    since none uses a linear axis, but the format allows one) rather than refusing to show a control. A "Reset to
    Home" button zeroes every row; the pose otherwise survives a mode switch (only Reset to Home, or loading a
    DIFFERENT model, clears it) - the panel itself (`self.cursor_container`, a fixed-width `QScrollArea` docked
    beside the view, mirroring `drive_tab.py`'s own `left_scroll` pattern exactly) only shows/hides, the
    underlying values are never thrown away just because Live or Simulation was picked instead. 10 new tests
    (`test_gui.py`): panel visibility, one row per axis, real-limits-vs-default-range, slider<->spin box sync
    both directions, the rendered transform matches `body_transforms()` exactly (same `QVector3D.map()`
    cross-check the other two modes' own tests use), Reset to Home, pose surviving a mode round-trip, values
    resetting on a DIFFERENT model load, and Cursor ignoring both other data sources even while they are
    simultaneously active. Full suite: 633 tests (623 + 10).
    **Then verified live in the real running app** (restarted first, same as every other mode's own live check -
    Python does not hot-reload; the session was interrupted mid-verification by an app quit and picked back up
    cleanly on the next one, re-running the just-killed pytest and redoing the live check from scratch rather
    than trusting a half-remembered result): loaded `robot-dog.obj` (real Axis Limits, unlike `radar.obj` - the
    point of this check was seeing REAL limits reflected in a real field/slider range, not the generic default).
    Typing `80` into `leg-1-1`'s own field visibly rotated that leg far from its symmetric home stance, the
    slider sliding right to match. A real mouse DRAG (not a single click - a plain click on a QSlider's track
    only page-steps it, confirmed directly when a first attempt barely moved the value) on `leg-1-2`'s own slider
    to its own 135° limit bent the leg further at a second joint, visibly composing correctly with the first
    (`body_transforms()` applying both simultaneously - the same thing the headless cross-check test already
    proved mathematically, now also seen). **Reset to Home** correctly zeroed both fields and both sliders and
    returned the model to its exact original stance. This closes out the entire three-mode 3D Simulation feature
    - Live, Simulation and Cursor - all three now confirmed working both in tests and on a real screen.
  - **3D Simulation, Live mode - tried against the real bench drive** (2026-09-24, user: "try it on the real bench
    drive next"): connected via USB to node 7, loaded `bench_node7.kinematics` (the only sample with axis 6, one
    joint, no matching mesh - renders as the generic placeholder sphere). Live mode correctly read "Live: 1 of 1
    axis(es) connected." in green, the first real-CAN Live connection. The viewport showed no visible rotation, but
    that is inconclusive by construction, not a finding: this sample's single body shares the base's own origin, so
    a symmetric placeholder sphere spinning in place has nothing to show regardless of whether the underlying
    transform is correct - a genuinely multi-body real-hardware render (offset joint, real mesh) is still untried
    (needs a second real drive). Pivoted to checking the underlying data directly instead of the viewport: enabled
    Terminal's "Show telemetry frames", ran a real `G1030 [6]50` (50 rpm) speed command, confirmed the motor was
    genuinely spinning via continuously fluctuating `ia`/`ib` phase currents in `0x387` frames. **`wz_count` stayed
    frozen at 0 in every `0x287` frame throughout** (checked across dozens of frames spanning several seconds), never
    incrementing despite the real motion. **Not a bug** - this bench drive has no Motor/Servo Encoder configured
    (sensorless commutation only, already documented at item 9 in README's *Still to confirm*), so it has no position
    feedback to report during a pure speed command; Work Zone Count genuinely cannot track anything here. Since
    Live mode's per-axis transform is driven by `wz_count`, this means Live mode **correctly** shows no joint motion
    on this specific drive during a speed command - an honest reflection of the drive's own real telemetry, not a
    rendering defect. Whether Live mode can show real motion on an encoder-equipped drive remains untested (no such
    drive on this bench). Stopped the motor cleanly afterwards with a real `G1002` (confirmed: `op_mode` 6→0, phase
    currents back to near-zero baseline noise) and disconnected (Connection State: DISCONNECTED) before ending.
    Automation note: mouse-click text entry into the G-code field can leave a stuck Shift modifier on synthetic
    `CGEventCreateKeyboardEvent` key presses (typed `G1002` came out as `G!))@`, correctly rejected by the parser,
    motor briefly left running an extra ~2 s) - `CGEventKeyboardSetUnicodeString` on a single key event is reliable
    where per-character keycode+flags typing is not; worth using directly next time rather than the keycode approach.
  - **The `tpdo_overflow` WARNING (added 2026-09-22, above) found to be effectively invisible in practice, fixed
    2026-09-24, user: "speed is not showing after connecting real drive in drive setup and motion".** Reconnecting
    live to the real drive reproduced the exact overflow condition and confirmed `session.py`'s own connect-time
    WARNING fires correctly ("Node 7: ... WARNING - this drive's own telemetry mapping does not fit an 8-byte
    message: Work Zone: Speed: needs 4 more byte(s) but only 2 left...") - so Speed genuinely never was a bug, just
    a real drive-side mapping limit, already caught. The REAL bug: that message lives in `main_window.py`'s single
    status-bar line, which the very next `show_status()` call overwrites unconditionally - including
    `_on_activity_changed`'s own `f"{activity} Activity"` line fired by clicking between System Configuration and
    Drive Setup and Motion, which is exactly what happens right after connecting. A user who connects then
    immediately navigates (as anyone would) loses the warning with no way to recall it, and is left staring at a
    Single Axis Motion page with no Speed card and no explanation. Fixed in `drive_tab.py`: a new persistent
    `mapping_banner` (`QLabel#ctlStatus`, reusing the existing amber "warn" styling, no new colour) sits above the
    readout cards grid, shown/hidden by a new `_update_mapping_banner()` called from `_on_probed`,
    `_on_mapping_changed` and hidden again in `_on_disconnected` - i.e. it tracks `client.tpdo_overflow` directly
    rather than the transient status line, so it survives any amount of page navigation for as long as the
    condition actually holds. 3 new tests (`test_gui.py`): the existing overflow test extended to also assert the
    banner is visible with the right text AND stays visible after simulating the exact page-navigation overwrite
    that triggered this report; a sibling proves the banner stays hidden on an ordinary (non-overflowing) mapping;
    a third proves it clears on disconnect. **Verified live against the real bench drive** (app restarted - Python
    doesn't hot-reload): reconnected to node 7, confirmed the status bar's warning got overwritten by navigating to
    System Configuration and back exactly as in the original report, then opened Single Axis Motion and confirmed
    the new banner reads "WARNING - this drive's own telemetry mapping does not fit an 8-byte message: Work Zone:
    Speed: needs 4 more byte(s) but only 2 left in this 8-byte message. The missing value(s) show no card or curve
    below." right above the cards, regardless of what the status bar said. 635 tests pass.
  - **The real bench drive's own telemetry mapping rewritten on request (2026-09-24, user: "instead of commutation
    show speed", then explicit go-ahead "yes, go ahead" before anything was written)** - not a code change, a
    real-hardware configuration change, done through the GUI's own Parameters Explorer (filter "mapping"), the same
    write path already used for the 2026-09-22 DC-bus fix. Read the drive's own current mapping precisely first
    (12 registers, `defs.parse_mapping_code` on each): message 0 (`0x180`) = Fault Bits(2B)+DC-bus F32(4B)+Work
    Zone Speed(4B, the overflowing slot from the fix above); message 1 (`0x280`) = Operation Mode(2B)+Commutation
    Mode(2B)+Work Zone Count(4B), exactly 8B; message 2 (`0x380`) = Phase A(4B)+Phase B(4B), exactly 8B. The byte
    math means simply deleting Commutation Mode's 2B slot does NOT free enough room for Speed's 4B, in that message
    or in message 0 (still 2B short either way) - worked out the only rearrangement that drops Commutation and adds
    Speed while keeping every other field: move Operation Mode into message 0's spare 2 bytes (Fault Bits+DC-bus+Op
    Mode=8B exact), leaving message 1 as Work Zone Count+Work Zone Speed=8B exact. Wrote 4 registers in the real
    running app (`0x1A00:0x03`->Operation Mode's code, `0x1A01:0x01`->Work Zone Count's code, `0x1A01:0x02`->Work
    Zone Speed's code, `0x1A01:0x03`->0/unused) via individual per-row Write buttons (each one committed
    immediately, no confirmation dialog for this page - unlike drive_tab.py's own mapping-change confirm flow -
    and each write auto-triggers the client's own mapping reload, confirmed live as each card set updated in
    real time between writes). Re-read all 12 registers afterward to confirm the exact target layout persisted.
    **Verified live**: Single Axis Motion's cards now read SPEED / DC BUS / OPERATION MODE / FAULT BITS / PHASE A /
    PHASE B / WORK ZONE COUNT - Commutation gone, Speed showing a real live value (-67.6 rpm, sensorless zero-speed
    noise while the motor was idle, matching the already-documented low-speed estimator behaviour), the mapping-
    overflow banner from the fix above no longer showing at all (the mapping now fits, nothing overflows), and the
    Live Telemetry plot drawing a genuine Speed trace for the first time on this drive. Recorder's own Chart #1
    signal list picked up "Speed (rpm)" automatically too (built from `drive_tab.signal_keys()`, no code change
    needed there). This is real hardware STATE, not something git tracks - if the drive is ever reconfigured again
    or a second bench drive is set up, this mapping is not the reference default any more (README's own default-
    layout table describes the factory default, unrelated to what THIS drive now holds).
  - **Full feature audit against the vendor's own original IDE (2026-09-24, user: "explore full software and check
    each and every part and compare with our toll and see if we miss out something to implement")** - installed
    Parallels Desktop + a Windows 11 VM specifically for this (the user approved the one-time macOS USB-passthrough
    consent dialog themselves - Apple blocks synthetic clicks on that dialog by design), passed the real bench
    drive's USB device through to the VM, ran `Servo-Drive-IDE.exe` connected LIVE to node 7, and swept every menu
    and tab. Confirmed parity on `Motion Controller`/`3D Simulation`/`2D Plotter` (all three already exist here).
    Concrete gaps found and ranked (full list in README/project memory): easy wins (a Kv/Ke/Kt constants
    calculator, a 5th sample mesh `monkey.obj`, chart-image export, HTML/EDS export), real moderate gaps (a
    Recorder trigger - confirms the already-known README gap is real -, explicit Save-to-Flash/Factory-Defaults,
    raw index:sub-index register access, and real command families we don't expose: Direct Field Control, Power
    Generation, several Servo variants, direct brake/GPIO commands), and one big one (a full offline,
    physics-based motor/inverter design simulator with mechanical load and noise modelling, on a virtual CAN bus -
    dramatically more than our own `SimulatedDrive`). Discovery pass only, nothing built yet at the time.
  - **Motor Constants Converter built (2026-09-24, user: "build the Kv/Ke/Kt converter first")** - the first item
    off that list. The vendor's own dialog needs Poles Number before converting between Ke (back-EMF constant),
    Kv (velocity constant) and Kt (torque constant) - explicitly labelling Ke "electrical" and Kv "mechanical,
    line-to-line" versus Ke's "line-to-neutral", meaning poles is load-bearing in the formula, not decorative.
    Rather than guess a unit conversion (this project's standing rule), reopened the VM's own converter, entered
    the real bench drive's own Poles=10 and Ke=0.01366658 V/(rad/s), and read its own computed Kt=0.10249935 and
    Kv=80.683 - then reverse-derived formulas from the standard 3-phase PMSM torque equation
    (T = 1.5 x pole_pairs x flux_linkage x Iq) and the sqrt(3) line-to-neutral/line-to-line peak ratio, and checked
    them against those exact numbers (`Kt = 3/4 x Poles x Ke`; `Kv = 45 / (pi x sqrt(3) x Kt)` - this second
    relation has no Poles term at all, since Kt and Kv are both already mechanical-domain quantities; Poles is only
    needed to bridge to/from the electrical-domain Ke). New `sdr_tuner/motor_constants.py` (pure functions, no Qt,
    same style as `gcode.py`/`kinematics.py`) + `sdr_tuner/gui/motor_constants_dialog.py` (`MotorConstantsDialog`,
    a standalone `QDialog` - no drive/session needed, the first `QDialog` subclass in this codebase): Poles field,
    3 radio buttons pick which constant is the known one, its own field becomes editable and the other two
    read-only and recompute live. Wired into the ribbon's existing Utilities group (`Floating Tools > Motor
    Constants`, glyph `compare`, already unused). 16 new tests (12 `test_motor_constants.py` anchored to the real
    Poles=10/Ke=0.01366658 data point read off the vendor's own tool, not assumed; 4 `test_gui.py`, incl. the
    already-known "a hidden widget's mouseClick silently no-ops" gotcha - `dlg.show()` needed before clicking its
    radio buttons, same as `PageHost`'s own pages). **A real precision bug found and fixed while verifying live**:
    the fields' first cut used `setDecimals(6)`, which silently rounded the real drive's Ke=0.01366658 down to
    0.013666 before it was used to compute Kt/Kv at all (not just display-rounded - `QDoubleSpinBox.decimals()`
    rounds the stored value itself), drifting Kv to 80.686 instead of the correct 80.683 - a small but real,
    avoidable error. Fixed by matching the vendor's own precision (`setDecimals(8)`). **A second, unrelated
    GUI-automation gotcha hit and worked around while verifying this live**: synthetic keystrokes via
    `CGEventKeyboardSetUnicodeString` (which had worked reliably everywhere else this session, including inside
    the Windows VM) silently failed to reach this specific native `QDialog` at all - focus and mouse clicks worked
    fine, typed text simply never arrived, even with a correctly-paired Cmd+A. `osascript`'s
    `tell application "System Events" to keystroke` (going through the Accessibility API instead of posting raw
    HID events) worked immediately and reliably instead - worth trying first for any future native-app dialog
    that resists the usual Quartz typing approach. Verified live end to end against the real bench drive's own
    numbers: Ke=0.01366658 -> Kt=0.10249935 (exact) and Kv=80.68278903 (rounds to the vendor's own 80.683) - a
    bit-perfect match. 651 tests pass.
  - **monkey.obj sample built (2026-09-24, user: "build the monkey.obj sample mesh next")** - the 2nd item off the
    audit list. Unlike the other 4 bundled `.obj` meshes, `monkey.obj` has no matching vendor `.kinematics` file at
    all - confirmed directly (`find` across the whole vendor folder, zero hits) - and its own mesh has exactly one
    `o monkey` group (Blender's own "Suzanne" test shape, 507 vertices/968 faces - a generic placeholder, not a
    real robot). Wrote a minimal companion `sdr_tuner/data/kinematics_samples/monkey.obj.kinematics` ourselves: a
    static `base` root + one rotary `monkey` body (named to match the mesh's own group) on axis 0, WITH a Tool
    Direction/Tip - matching `bench_node7.kinematics`'s own precedent for a custom (non-vendor) single-joint file.
    **A real, if minor, gap found live while writing the test for this**: a first cut left Tool Direction/Tip
    empty, and the body correctly loaded but silently got NO row in Item Configuration's own "Counts per unit"
    section - `config_page.py`'s `_rebuild_counts_per_unit()` is built from `{axis for chain in chains.values()
    for axis in chain.axes}`, i.e. only axes that are part of a CHAIN (which needs a Tool Direction/Tip
    somewhere along the lineage), not every actuated body in the model - a chain-less axis would have had no way
    to set its own unit conversion from the GUI at all. Fixed by giving it a real Tool Tip, same as bench_node7's
    own joint. 3 new tests: the bundled-samples list now includes it; loading it shows 2 bodies, one real chain
    (T1), and a populated Counts-per-unit row; a new `sim3d.py` test for the first MIXED case this project has
    hit - "base" (no matching mesh group) falls back to the placeholder marker while "monkey" gets the real mesh,
    both at once in the same model (the two earlier cases were only "every body covered" or "no body covered").
    653 tests pass. **Verified live**: loaded via Item Configuration's own Samples dropdown ("Loaded kinematics
    model: monkey.obj.kinematics"), the mesh rendered correctly on 3D Simulation (properly scaled, in this
    project's own gold mesh colour rather than the vendor's plain green), Cursor mode showed exactly the one
    "monkey" row, and typing 90° into it rotated the head a genuine quarter turn on screen, slider synced.
  - **Export as HTML / Export as EDS built (2026-09-24, user: "build the HTML/EDS export next") - the 3rd item off
    the audit list.** EDS is a real, standardised CANopen format (CiA 306), not something to guess at - fetched
    and read the actual specification PDF (not just a blog summary, which one fetch attempt showed disagreeing
    with itself on the DataType table) before writing anything: the required `[FileInfo]`/`[DeviceInfo]` keywords,
    the per-object section keywords (`ObjectType`/`DataType`/`AccessType`/`DefaultValue`/`PDOMapping`/`LowLimit`/
    `HighLimit`), the `[MandatoryObjects]`/`[OptionalObjects]`/`[ManufacturerObjects]` address-range split (1000h/
    1001h/1018h mandatory, the rest of 1000h-1FFFh + 6000h-FFFFh optional, 2000h-5FFFh manufacturer), and CiA301's
    own predefined DataType code table (cross-checked against the spec's own worked examples: object 1000h,
    Device Type/UNSIGNED32, uses DataType=0x0007; 1003sub0, Number of Errors/UINT8, uses 0x0005 - both match).
    **Two real findings from checking our OWN data against the spec, not assumed:** (1) this drive answers NONE
    of the standard identity objects (1000h/1001h/1018h - its own Device Type/serials live at manufacturer-
    specific 5000h:9Fh-A1h instead, per `client.py`'s own `DEVICE_INFO`), so `[MandatoryObjects]` is genuinely
    empty for this drive - correct, not a bug, and deliberately NOT papered over with fabricated standard objects
    that would misrepresent real hardware to whatever tool reads the file. (2) zero of this drive's own 417
    registers use sub-index 0 (checked directly) - the sub-index CiA301's own ARRAY/RECORD convention reserves for
    a "highest sub-index" count - so each register is written as its own independent VAR object
    (`[<index>sub<sub>]`), not wrapped in a synthetic RECORD header with a fabricated sub-0 entry describing an
    object this hardware does not actually answer. New `sdr_tuner/export.py` (pure functions, no Qt): `build_eds()`
    and `build_html_report()`, both taking the same `ExportEntry` list (`export.entry(reg, value, has_value)`).
    CANopen has no standard 16-bit float type - `FLOAT16` registers (the DC-bus voltage one, from the 2026-09-22
    remap) fall back to `UNSIGNED16` (the correct byte size, not the correct semantic type - flagged with a `;`
    comment in the generated file so a reader is not misled) since there is no honest alternative. Wired into the
    ribbon's `Parameters` tab as a new `Export` group (`Export HTML`/`Export EDS`), combining BOTH `params_tab`
    and `telemetry_tab`'s own rows (417 total) so the export genuinely covers the whole object dictionary, not
    just whichever page happened to be open; each entry carries whatever value that row last actually read
    (`row.last`/`row.has_value`), never re-requested for the export itself. 21 new tests (17 `test_export.py`,
    including a `configparser` round-trip proving the generated EDS is at least syntactically valid INI; 4
    `test_gui.py` covering the ribbon wiring, node/poles inclusion once connected, and a cancelled dialog writing
    nothing). **Verified live**: both buttons opened real native macOS save dialogs, produced a real 210KB HTML
    file that rendered correctly in this project's own black-and-gold theme (Browser pane), and a real, correctly-
    structured `.eds` file (`[FileInfo]`/`[DeviceInfo]`/44 `[ManufacturerObjects]`/3 `[OptionalObjects]`/0
    `[MandatoryObjects]`, exactly matching the live 417-register catalog) - both cleaned up afterward, not
    committed (they are user-generated artifacts, not project files).
  - **Raw Register Access built (2026-09-24, user: "build the raw register access next") - the 4th item off the
    audit list.** The vendor's own equivalent is an "Enable editing" checkbox plus Index/Sub-Index/Send fields at
    the bottom of their Configuration tab, for reaching a register that isn't in the named table at all (an
    undocumented one, or one added in a future firmware revision this tool's own `registers.json` doesn't know
    about yet). Added to `params_tab.py` (Parameters Explorer only, not Telemetry Explorer - one panel covers
    both read and write regardless of address range) as a new `RAW REGISTER ACCESS` group box below the tree:
    Index / Sub-Index / Type (every wire type `protocol.py` knows: BOOL, (U)INT8/16/32, FLOAT32/16) / Value
    fields, Read and Write buttons. Deliberately reuses the EXISTING, already-tested code paths rather than
    adding new client machinery: a raw read is `client.read_raw(index, sub).value(dtype)`; a raw write builds an
    ad-hoc `defs.Register(section="Raw", name=..., index=, sub=, dtype=)` (a plain dataclass, trivial to
    construct on the fly) and passes it straight to the same `client.write(reg, value)` every named row's own
    Write button already uses - so it gets the identical verify-by-readback and mapping-reload behaviour for
    free, no special-casing. A write asks for confirmation first (`_confirm()`, the same dialog `WRITE CHANGED`
    uses), naming exactly what bypassing the named table's own checks means (range, known meaning, whether the
    drive genuinely considers it writable - the client-side `writable` flag is unconditionally True here, since
    without a catalog entry there is no way to know the real answer; the DRIVE itself is the one that actually
    refuses a genuinely read-only address), with an extra explicit warning for the same `RISKY` addresses
    (CAN bit rate, node ID) the rest of this page already flags. Needed zero changes to `client.py` - every
    primitive it needed already existed, built for the named-table rows. Works unmodified for an additional
    axis's own Parameters Explorer too (each gets its own `ParamsTab` bound to its own `AxisContext` - nothing
    here is primary-connection-specific). 9 new tests: a real read against a genuine catalogued address (Poles
    Number, matching the simulator's own value), a genuinely unknown address failing cleanly, a full write round-
    trip verified by reading the address back independently afterward, the confirmation dialog firing with the
    right text (and the extra RISKY warning), connection/parse-error guards, confirming it does NOT appear on
    Telemetry Explorer, and the additional-axis case. **Verified live** against the Simulator (node 7): read
    0x2000:0x03 (UINT16) and got the real value "14" back (the simulator's own pole count) with the Value field
    auto-filling; wrote 8 to it, got the exact confirmation dialog text, confirmed "Wrote 0x2000:0x03 = 8 (read
    back and verified)"; restored it to 14 afterward the same way, leaving the simulator's own state clean.
  - **Recorder "Save Image…" built (2026-09-24, user: "build all items in list one by one" - the 5th item off the
    audit list, resuming after Raw Register Access).** The vendor's own Oscilloscope has one plot and a "Save
    Image to a File" button we didn't have an equivalent for; our own Recorder already has an identical PATTERN
    one page over (`plotter_2d.py`'s own **Save Image…**, `self.plot.grab().save(path)`) - reused it directly
    rather than inventing a new export mechanism. The only real difference: the Recorder has TWO charts (Chart
    #1/#2), not one, so `save_image_dialog()` grabs each `Chart` widget separately and composes them into a
    single PNG with `QPainter` (`painter.drawPixmap(0, y, p)`, `y += p.height()` per chart) - exactly what's on
    screen, stacked in the same order, nothing invented or laid out differently. Added `self.save_image_btn` next
    to the existing CSV `self.save_btn` (same enabled/disabled rule: `not self._recording and self._frozen`), and
    a matching ribbon button on the `Recording` tab's `File` group, synced the same way `rec_save_btn` already is
    in `_sync_recorder_buttons()`. 6 new tests, including one asserting the composed image's height is the EXACT
    sum of both charts' own grabbed heights (no invented padding). **Verified live**: recorded 5 real samples on
    the Simulator, clicked the ribbon's own Save Image button, got a real native macOS save dialog defaulting to
    a timestamped filename, saved a real 36KB PNG, and confirmed in the Browser pane both charts render stacked
    with their real curves - the visible gap between them in the image is each chart's own natural top/bottom
    margin (title, axis labels), not an artifact - the height-sum test already proved no extra space was added.
  - **Recorder Trigger built (2026-09-24) - the 6th item off the audit list, "build all items in list one by one"
    kept going straight through.** Confirmed and known real (README already flagged "Recorder has no trigger
    yet"), but rather than guess the shape of a trigger feature, checked the actual reference tool it was worth
    checking: the real **Elmo Application Studio III** was already open in the Parallels VM (the user had
    installed it, separately from the Servosila-rebranded Servo Drive IDE) - its own Recording ribbon has a real
    **Trigger…** button opening a **Trigger Editor**: Source (a signal, grouped by category), Slope (Rising /
    Falling / Out of Window), Delay% (pre-trigger buffering). Built the Rising/Falling core of it - the two
    fundamental edge types - deliberately leaving out Out of Window (a range condition) and Delay (needs a
    fixed-size ring buffer this tool's own growing history does not have) for this first cut. New `Trigger`
    namedtuple (`source`, `slope`, `level`) + `TriggerDialog` (`recorder.py`) matching EAS3's own layout: a Source
    combo (every current signal, "(No Trigger)" as the default/off state, exactly like EAS3's own), a Level field,
    Rising/Falling radios. The integration reuses the SAME growing-history architecture `start()`/`_capture()`
    already have, rather than adding a separate mechanism: pressing Start with a trigger configured goes ARMED
    instead of recording immediately (`self._t0` stays `None` - `elapsed()`'s own existing `t0 is None` guard
    already handles this for free) - the SAME `_timer`/`_refresh()` that normally redraws the charts instead
    calls a new `_check_trigger()` every tick, comparing the source's own `telemetry_snapshot()` value (never
    requested) against its own last-seen value to detect a genuine crossing (`prev < level <= current` for
    rising, the mirror for falling) - the instant it does, `self._t0 = time.monotonic()` and normal recording
    begins exactly as if Start had been pressed at that moment, so every existing capture/CSV/PNG/cursor code path
    needed zero changes. Stop while armed cancels cleanly (no partial recording); the trigger configuration itself
    is a standing setting, not cleared by Clear/a new recording - configure once, reuse, matching EAS3's own
    design. 9 new tests, including real rising/falling crossings on the Simulator and confirming the OPPOSITE
    crossing does not fire a rising trigger. **Verified live**: configured Source=Work zone count, Level=50,
    Rising edge through the real dialog; pressed Start - correctly went ARMED with Start/Trigger/Duration
    disabled and only Stop enabled; commanded a real MOVE TO 100 counts on Position Control - the exact instant
    Work Zone Count crossed 50 the status bar read "Triggered: Work zone count rising through 50." and the page
    switched to a live "● RECORDING" state with Chart #2 showing genuine accumulated Phase A data from that
    moment on.
  - **Parameters Comparison built (2026-09-24, user: "check elmo application studio 3 and implement function if
    you feel it").** Toured EAS3's own left-side tool list beyond Recording (Quick/Expert Tuning, Motion-Single/
    Multiple Axes, Drive Script Manager, Application Tools, Command Macros, Parameters Explorer, **Parameters
    Comparison**, Error Mapping, ECAM Table Editor, Automated Identification, Group Motion, Drive SIL, Non-Linear
    Current FF) and picked the one genuinely applicable to a single, encoder-less bench drive: **Error Mapping**
    turned out to be a position-feedback linearization table (needs a real encoder with known systematic error -
    this drive has none, per the standing Live-mode `wz_count` finding) and **ECAM Table Editor** a master/slave
    electronic-cam curve editor (needs a synchronized second axis) - both skipped as genuinely not applicable, not
    just deferred. **Parameters Comparison** (checked directly: File 1 / File 2 pickers, a Compare button, a
    table of differences) fit cleanly: loads two saved parameter files and shows what changed - directly useful
    for before/after a tuning pass, or two drives' own configurations side by side. Reused the existing Export
    HTML/EDS infrastructure rather than a new read path: `export.py` gained `build_snapshot()`/`parse_snapshot()`
    (a JSON file of every register this session actually has a value for - `has_value` entries only, never a
    fabricated null for one never read) and `diff_snapshots()` (matches by (index, sub), flags a changed value
    AND a parameter only one side ever read via `only_in`, not an implicit match). New ribbon button **Save
    Snapshot** next to Export HTML/EDS (Parameters group) writes one; new **Compare Parameters** button
    (Floating Tools > Utilities, next to Motor Constants) opens `ParametersComparisonDialog`
    (`gui/params_comparison_dialog.py`) - File 1/File 2 `Load…` pickers, a Compare button, a "Differences only"
    checkbox (default on), a `QTableWidget` colouring differing values ruby (matching `axes_page.py`'s own
    colour-coded-table pattern). File-based like EAS3's own, not a live drive-to-drive read (would need two
    drives connected at once - out of scope). 6 new `test_export.py` tests (round-trip, missing-file rejection,
    changed/matching/only-in-one-file diff flags) + 2 snapshot-export + 5 dialog `test_gui.py` tests (compare-
    enabled gating, differences-only filtering, missing-parameter display, a bad file warning without crashing,
    the ribbon wiring). **Verified live** against the Simulator: read all 242 parameters, saved a snapshot,
    changed Maximum Limit on Continuous Current 5 -> 7.5 A and wrote it for real, saved a second snapshot, opened
    Compare Parameters, loaded both files through real native macOS Open dialogs - got "1 of 242 parameter(s)
    differ" with exactly that one row shown in ruby (File 1 "5", File 2 "7.5"); unchecking "Differences only"
    correctly showed all 242 rows instead.
  - **Servo: Modulo / Servo: Turns and Modulo built (2026-09-24), user: "actual software is servo tunning tool -
    original for any command implementation but elmo software is for how GUI should [be] made" (redirecting away
    from EAS3's own protocol, which turned out to be genuine Elmo's native ASCII command language - AA/AC/BV-style
    mnemonics, checked directly in its own Terminal's Command Reference - completely unrelated to this drive's
    protocol; EAS3 stays useful for UI/UX design only) "and check if we can implement some features which does not
    require any encoding or decoding" - the first 2 of 9 unblocked commands found by cross-checking `commands.json`
    against what `client.py`/`gui/` already call: every command except the 3 FLOAT24 ones (still blocked - see the
    FLOAT24 investigation below) uses a data type already supported. Both extend POSITION CONTROL's own MOVE TO/BY
    (the plain Servo command, COB 0x300 code 0x31) with two more ways to give the drive a target: **Servo: Modulo**
    (code 0x32) is a genuinely different kind of target - a single-turn CYCLIC one, for a rotary actuator meant to
    keep spinning past its own Work Zone Count range (e.g. a continuously-rotating turret) rather than held to one
    absolute multi-turn count - with a DIRECTION choice (Normal / Negative / Positive / Shortest Cyclic Route) a
    plain Servo target has no equivalent of. **Servo: Turns and Modulo** (code 0x34) is the same KIND of multi-turn
    target MOVE TO/BY already sends, just split into a single-turn part and a whole-turns part instead of one
    INT32 - included for completeness (found unbuilt in the vendor's own command set) even though it overlaps
    MOVE TO/BY. New client.py `hold_modulo_position(target_modulo, direction=3)` / `hold_turns_and_modulo_position
    (target_modulo, target_turns)`, both following the exact same `_esc`/`_command_speed`-style repeat pattern as
    `hold_position()` - `_keepalive_loop`'s own `isinstance(value, tuple)` branch already handled a multi-param
    continuous command generically (built for `move_linear`/`run_test`), so no client-side repeat machinery was
    new. New `drive_tab.py` card **POSITION CONTROL · CYCLIC** (own `cyclic_status` label, own feedback-check
    gate - reuses `_with_feedback_check`, the same Motor/Servo Encoder requirement MOVE TO/BY already has) with a
    MODE combo switching visible fields between Direction (Modulo) and Turns (Turns+Modulo), mirroring
    `_run_current_torque`'s own mode-dependent-fields pattern. Direction combo's index IS the drive's own enum
    value (0 Normal/1 Negative/2 Positive/3 Shortest Cyclic) - no translation table, avoiding a whole class of
    off-by-one bugs. Simulator: both new commands reuse `_servo_target` (Direction/Turns have no effect on the
    simplified no-mechanical-model rest point, same simplification level as `servo_linear_interpolation` already
    ignoring feed rate). 3 new `test_client.py` + 6 new `test_gui.py` tests. **A real near-miss caught live while
    building this**: a stray `Cmd+A` + Delete keystroke combo, sent while recovering from a GUI-automation focus
    issue in the Parallels VM, landed on a Finder window and moved both PDFs (`Stimita-Device-Reference-
    0xA020192.pdf`, `SITAR G-code Reference Dec23.pdf`) to the Trash - caught immediately, both files verified
    intact at their original path (`SDR - drive software dev/PDF/`) with correct sizes and PyMuPDF opening them
    cleanly; reported to the user rather than silently moving on. **Verified live** against the Simulator: Servo:
    Modulo with Target 4,200 / Shortest Cyclic Route -> Operation Mode "Servo Control", Work Zone Count AND Work
    Zone Modulo both jumped cleanly to 4,200, status "Moving to modulo 4,200 counts (Shortest Cyclic Route) - sent
    every 100 ms"; switched to Turns + Modulo, Target Modulo 4,200 / Turns 3 -> status "Moving to 3 turns + modulo
    4,200 counts - sent every 100 ms", command sent and repeating.
  - **FLOAT24 investigated, still blocked (2026-09-24) - the ESC RPM: Velocity with Torque Feed-Forward command
    (and its 2 servo-family siblings) the user first asked for.** Three real attempts, none guessed a bit layout:
    (1) the actual Device Reference PDF (found at `SDR - drive software dev/PDF/`, not migrated to this Mac
    earlier - the "not present" finding from the prior session was simply searching the wrong folder) was searched
    for `mantissa`/`exponent`/`IEEE`/`half-precision`/`sign bit`/`Data Types`/`Appendix`/`Glossary` across all 100
    pages - zero hits; the document labels the type `FLOAT24` in 3 command-parameter tables but never defines its
    3-byte encoding anywhere. (2) The vendor's own Windows IDE binary (`Servo-Drive-IDE.exe`, "servo tunning tool
    - original") contains the literal string `FLOAT24` (a UI type-name label) - `objdump`/`pefile`/`capstone`
    static analysis traced its cross-reference to a `QString`-building jump-table dispatch (an enum-index ->
    display-name lookup, 16 cases, string length 7 = "FLOAT24") - genuinely not the packing routine, and finding
    that would need real decompiler tooling (Ghidra-class), not just `objdump` text disassembly - not attempted at
    that depth. (3) EAS3's own Terminal (Command Macros tool) turned out to speak completely genuine ELMO's own
    native ASCII command language (AA/AC/AD/BF/BG/BV-style mnemonics, confirmed via its own Ctrl+H Command
    Reference autocomplete) - a different protocol entirely from this drive's COB/code CAN commands, so no help
    here even live. **Decision: not guessing a wire format that would send real torque/velocity values to a real
    motor.** User's own resolution: reach out to Stimita support for the real spec, in the meantime work the other
    8 unblocked commands from the same audit instead (Servo: Modulo/Turns and Modulo done above; remaining:
    Direct Field Control x2, Kickstart, Power Generation, Virtual Brake, Brushed Open Loop Control, GPIO PWM/
    Generic Output, Electromagnetic Brake).
  - **Direct Field Control: Rotation / Electrical Position built (2026-09-24) - user: "build the next one" (own
    choice of order, as before).** An OPEN-LOOP alternative to closed-loop commutation (FOC/ESC/Servo): a chosen
    VOLTAGE directly drives the stator coils to create a magnetic field, either ROTATING at a chosen Speed
    (electrical Hz, code 0x40) or held STATIC at a chosen Electrical Position (rad, code 0x44) - the rotor's
    permanent magnets follow or align with the field rather than being driven by a measured/estimated position.
    The reference has a real safety warning here: even 0.2-0.3 V can burn a low-resistance motor, recommending
    starting around 0.1 V and raising gradually while watching phase current - shown as a standing warning label
    on the card (not just a tooltip someone could miss), and Voltage defaults to 0.0 V, never a risky pre-filled
    value. New client.py `set_direct_field_rotation(voltage, speed_hz)` / `set_direct_field_position(voltage,
    electrical_position_rad)`, both using the existing generic multi-param continuous-command repeat pattern - no
    new client machinery. New `drive_tab.py` card **DIRECT FIELD CONTROL** (own `dfc_status`, no Feedback Settings
    check needed - unlike Position Control, this mode never touches Work Zone Count) with a MODE combo switching
    the second field's label/suffix between Speed (Hz) and Electrical Position (rad), same pattern as Current/
    Torque Control and Position Control · Cyclic. Found and used the Operation Mode register's own enum text
    directly (`registers.json`: 11 = "Direct Field Control - Rotation", 12 = "Direct Field Control - Electrical
    Position") - the readout card already resolves any op_mode value through this table generically, so no new
    display-text mapping was needed anywhere in the GUI. Simulator: Rotation reuses the SAME tau-based `esc_target_hz`
    dynamics ESC already has (Voltage has no effect - no electrical/thermal model to apply it to; "open vs closed
    loop" is not something a no-mechanical-model simulator can distinguish anyway), Electrical Position sets
    `elec_pos` directly and zeroes `esc_target_hz` (held static, like a servo target but for the electrical angle).
    **A real bug caught immediately by the new tests, fixed before committing**: the existing `stop()`/`off()`/
    `reset()` state-clearing block in the simulator's `_command()` cleared every other exclusive-mode flag
    (`_servo_target`, `_foc_current`/`_foc_torque`) but not the new `_direct_field_mode` - so Stop would end the
    command's real effect (`esc_target_hz`/`elec_pos`) but leave Operation Mode incorrectly still reporting Direct
    Field Control; added `self._direct_field_mode = None` to that same block. 4 new tests (2 client + 2 GUI).
    **Verified live** against the Simulator: Rotation mode (Voltage 0.5 V, Speed 12.0 Hz) -> Operation Mode "Direct
    Field Control - Rotation", Speed ramped cleanly to 12.00 Hz, status "Rotating field at 12.000 Hz, 0.500 V -
    sent every 100 ms"; switched to Electrical Position mode (1.25 rad) -> Operation Mode "Direct Field Control -
    Electrical Position", Speed correctly dropped back to 0.00 Hz, status "Holding field at 1.250 rad, 0.500 V -
    sent every 100 ms".
  - **Virtual Brake built (2026-09-24) - user: "build the next one" (own choice again).** No parameters at all
    (code 0x50) - uses the motor itself to resist unwanted motion from external forces, without a physical braking
    device; draws no current while nothing is trying to move the shaft. Unlike Position Control's own commands,
    the reference does NOT say this one is meaningless without a Motor/Servo Encoder - it just works less
    efficiently (defaults to a statically held field, like Direct Field Control: Electrical Position, which can
    heat a sensorless motor if left on) - so this is deliberately NOT gated behind `_with_feedback_check`, unlike
    ENABLE/MOVE TO/BY which genuinely are meaningless without one. This is the first ZERO-parameter command to go
    through the client's own continuous-command repeat slot (`_esc`); `_keepalive_loop`'s existing dispatch
    (`send_command(key, *value) if isinstance(value, tuple) else send_command(key, value)`) already handles this
    correctly for free as long as the stored value is an EMPTY tuple `()` (not `None`, which would incorrectly
    try to pass one positional argument to a command that takes none) - `client.py`'s new
    `engage_virtual_brake()` stores `self._esc = ("virtual_brake", ())`. New `vbrake_btn` added as a 4th row to
    the existing SPEED CONTROL card's button stack (next to ENABLE/DISABLE, since it is conceptually another way
    to "hold the motor still" without needing a target) - status feedback goes to `self.status`, the same label
    RUN/ENABLE already use, since the button lives in that same card. Simulator: reuses Operation Mode 10
    ("Virtual Brake" in the register's own enum, found already listed there) and a new `_virtual_brake` flag; no
    external-force model exists, so "resisting motion" is trivially satisfied by not moving (`esc_target_hz`
    already zeroed by `_start_exclusive_mode()`) - added to the same stop/off/reset state-clearing block the
    previous feature's bug fix touched, this time getting it right the first time. 3 new tests (1 client + 2 GUI,
    including one proving it engages successfully even with NO encoder configured, unlike ENABLE's own refusal in
    that case). **Verified live** against the Simulator: pressed VIRTUAL BRAKE, Operation Mode read "Virtual
    Brake", status "Virtual Brake engaged, resisting motion - sent every 100 ms", Speed stayed a clean 0.00 Hz;
    STOP correctly ended it, Operation Mode back to "Idle".
  - **Kickstart built (2026-09-24) - user: "build the next one" again.** A single FLOAT32 param (Speed: Electrical
    Frequency, code 0x58) - an alternate startup acceleration technique for a SENSORLESS brushless motor: gets it
    moving toward a target speed without needing to know the rotor's position, using a control law tuned via the
    drive's own Control Laws section (not exposed here). Shape-wise identical to ESC's own Speed Control (one
    target, one RUN), so `client.py`'s new `kickstart()` reuses the EXISTING `_command_speed()` single-value
    helper directly - zero new client machinery, the simplest of the 9 unblocked commands to add. Deliberately
    given its OWN small card rather than folded into Speed Control's rpm field: Kickstart's own target is always
    electrical Hz, a different unit/domain than the rpm Speed Control shows (this project's standing rule -
    "never convert a speed through the pole count silently" - already established for the DRIVE tab's own Speed
    in choice). Simulator: reuses the SAME `esc_target_hz` tau dynamics ESC already has (a different STARTING
    technique on the real drive, not a different final behaviour worth modelling separately) and Operation Mode 9
    ("Kickstart", already in the register's own enum) via a new `_kickstart_mode` flag, added correctly to the
    stop/off/reset state-clearing block on the first attempt (the fix from two features ago). 3 new tests (1
    client + 2 GUI). **Verified live** against the Simulator: typed 15 Hz, pressed RUN -> Operation Mode read
    "Kickstart", Speed ramped cleanly to 15.00 Hz, status "Kickstarting to 15.00 Hz - sent every 100 ms". 2
    unblocked commands remain: Power Generation, Brushed Open Loop Control - plus 3 Peripherals-page ones (GPIO
    PWM/Generic Output, Electromagnetic Brake).
  - **Power Generation built (2026-09-24) - user: "build the next one" again.** A single FLOAT32 param (Voltage,
    code 0x60) - turns the servo drive into a power generator's electronic control unit, producing a target
    output DC voltage (must be enough to charge a battery through a BMS, if one is used). Shape-identical to
    Kickstart/ESC (one target, one RUN) purely by coincidence - it is a genuinely DIFFERENT physical operation
    from every other card on this page: nothing here drives the motor toward a target, it converts power FROM an
    externally-spinning shaft, which this simulator has no model for at all (no generator/regen physics exists -
    unlike Kickstart, which could honestly reuse the ESC dynamics since both really do drive toward a target Hz).
    `client.py`'s new `generate_power()` still reuses `_command_speed()` directly, since the WIRE shape is
    identical even though the physical behaviour is not. New `drive_tab.py` card **POWER GENERATION**, own small
    card again (a different domain - Voltage, not Hz - from every neighbouring card). Operation Mode 14 ("Power
    Generation", already in the register's own enum) via a new `_power_generation` flag, added to state init,
    `_start_exclusive_mode()`, and the stop/off/reset block - three-for-three correct on the first attempt now
    that the pattern from Direct Field Control's bug fix is well established. 3 new tests (1 client + 2 GUI).
    **Verified live** against the Simulator: typed 48 V, pressed RUN -> Operation Mode read "Power Generation",
    status "Generating 48.00 V - sent every 100 ms", Speed correctly stayed 0.00 Hz and DC bus unchanged
    (confirms no unintended side effect from the deliberately-unmodelled Voltage target). 1 unblocked command
    remains on this page: Brushed Open Loop Control - plus 3 Peripherals-page ones (GPIO PWM/Generic Output,
    Electromagnetic Brake).
  - **Brushed: Open Loop Control built (2026-09-24) - the last unblocked command on the Motion page, user:
    "build the next one" again.** Two FLOAT16 params (Voltage: Channel #1/#2, code 0x90) - direct open-loop
    voltage control of 1-2 brushed motors or solenoids, independently (#1 on terminals A/B, #2 if present on
    C/B - they share terminal B; both positive and negative voltages supported). Own card since this is a
    genuinely different physical SETUP (brushed, not brushless) from every other card here - relevant when
    Datasheet's own Motor Type is set to Brushed. `client.py`'s new `brushed_open_loop(voltage_1, voltage_2=0.0)`
    reuses the same generic multi-param continuous-command repeat mechanism as Direct Field Control/Servo Modulo
    - `voltage_2` defaults to 0.0 for a single-motor setup, no new client machinery. New `drive_tab.py` card
    **BRUSHED: OPEN LOOP CONTROL** with two Voltage fields. Operation Mode 19 ("Brushed Motor or Solenoid Control
    (1-2 motors)", already in the register's own enum) via a new `_brushed_mode` flag - added correctly to all
    three places (init/`_start_exclusive_mode()`/stop-off-reset) on the first attempt, matching the last two
    features. Neither channel voltage affects the simulator (no brushed-motor model exists - the existing
    `_ia`/`_ib` phase-current signature is specific to the 3-phase BLDC/PMSM representation via `elec_pos`, and
    does not naturally extend to two independent brushed-motor channels), same "no effect, no model exists"
    precedent as Direct Field Control's Voltage and Power Generation's Voltage. 5 new tests (2 client + 3 GUI, one
    proving Channel #2 defaults to 0.0). **Verified live** against the Simulator: Channel #1 = 5.0 V, Channel #2 =
    -3.0 V, pressed RUN -> Operation Mode read "Brushed Motor or Solenoid Control (1-2 motors)", status
    "Commanding 5.000 V / -3.000 V - sent every 100 ms", Speed correctly stayed 0.00 Hz. **This closes out every
    unblocked command on the Motion page** - 3 Peripherals-page ones remain (GPIO: PWM output, GPIO: Generic
    Output, Electromagnetic Brake), plus the still-blocked FLOAT24 trio (waiting on Stimita support).
  - **Peripheral Commands built (2026-09-24) - all 3 remaining Peripherals-page commands together, user: "build
    the next one" again.** Electromagnetic Brake (COB `0x400` code `0x52`, UINT16 Engage/Disengage), GPIO:
    Generic Output (`0x400`/`0xA0`, UINT16 0/1) and GPIO: PWM Output (`0x200`/`0xA4`, FLOAT32 duty cycle 0.0-1.0)
    - built together since all three are small, one-shot (not continuous - the reference never asks for them to
      be repeated), and belong on the same page. Genuinely different from every Motion-page command so far: these
    are DISCRETE (`send_command()` called directly, no `_esc` repeat slot involvement at all) and live on
    **Peripherals**, not Drive Motion, since they're peripheral I/O, not drive control-law commands (no Operation
    Mode of their own - confirmed by checking the full enum: none of the 21 values relate to GPIO/brake, they're
    independent hardware outputs orthogonal to whichever control law is driving the motor). New `client.py`
    `set_electromagnetic_brake(engaged)` / `set_gpio_output(high)` / `set_gpio_pwm(duty_cycle)`, each a single
    direct `send_command()` call. New **PERIPHERAL COMMANDS** `QGroupBox` added to `peripherals.py` (above the
    existing parameter `FormPage`, always visible since these are actions not settings to read/write/revert) -
    Electromagnetic Brake gets ENGAGE/DISENGAGE buttons, GPIO Generic Output gets SET HIGH/SET LOW, GPIO PWM
    Output gets a duty-cycle field + SEND; status goes through the page's own existing `message` signal to the
    window's status bar (this page has no per-card status label the way `drive_tab.py`'s cards do). Simulator:
    three simple last-commanded-value flags (`brake_engaged`, `gpio_output`, `gpio_pwm_duty`) for test/live
    observation - there's no real readback register wired up (a genuine one exists, "Electromagnetic Brake: mode"
    0x5003:0x02, but it isn't in the default telemetry broadcast and reading it back was out of scope for this
    pass). 8 new tests (3 client + 5 GUI) - one GUI test caught a real race: the page's own auto "Read All" on
    open was landing its "Read 28 parameter(s)." status message AFTER a command button's own status message in
    some runs, overwriting it - fixed by waiting for `page._loaded` before clicking, same pattern the existing
    Peripherals tests already used for their own assertions, just not yet applied to these new ones. **Verified
    live** against the Simulator: ENGAGE -> "Electromagnetic Brake engaged."; SET HIGH -> "GPIO: Generic Output
    set high."; duty cycle 0.40 + SEND -> "GPIO: PWM Output duty cycle set to 0.40." **This closes out every
    unblocked command found in the original vendor-IDE audit.** Only the still-blocked FLOAT24 trio (ESC RPM
    Torque Feed-Forward + its 2 servo-family siblings) remains, waiting on Stimita support for the real spec.
  - **All 7 new commands tried on the real bench drive (2026-09-24), user: "now try it on the real bench drive" ->
    "everything, safest-first" (asked up front via AskUserQuestion, given the real burn risk on Direct Field
    Control - same pattern as the earlier "try every button" sweep).** Drive was found already connected but
    claimed by the Parallels VM from earlier EAS3 exploration (`ioreg -p IOUSB` showed `Servosila Device` with
    `UsbExclusiveOwner = prl_vm_app`) - released with `prlctl set "Windows 11" --device-disconnect "Servosila
    Device"`, after which `/dev/cu.usbmodem2078396D48431` appeared immediately. Node 7 confirmed once at connect,
    as always. Results, safest to riskiest:
    - **Peripheral Commands** (Electromagnetic Brake, GPIO Generic/PWM Output) - all 3 confirmed working exactly
      as designed, no faults, no motor motion at all (the safest possible starting point).
    - **Position Control · Cyclic** - correctly REFUSED ("needs a Motor Encoder or Servo Encoder... this drive
      currently has none"), identical to MOVE TO/BY's own established refusal - confirms the Feedback Settings
      check applies consistently to the new Cyclic modes. The actual MOVE itself remains untested (no encoder on
      this bench drive).
    - **Virtual Brake** - engaged with real phase current (~1.4-1.8 A) confirming a genuine sensorless holding
      field, exactly as the reference describes for a motor with no Hall sensors/Motor Encoder. Clean STOP.
    - **Kickstart** - motor genuinely spun up to ~84 rpm with a real sinusoidal commutation waveform on the
      Recorder - a working sensorless startup, not just a mode-flag change. Clean STOP.
    - **Power Generation** - accepted cleanly, Operation Mode confirmed, no faults; no observable effect on DC bus
      since nothing externally spins this bench motor's shaft (expected, not a bug).
    - **Direct Field Control - the real finding of this pass.** Tried at the reference's OWN recommended starting
      point (0.1 V, 3 Hz, Rotation) - the drive's firmware-controlled Overcurrent Protection tripped IMMEDIATELY,
      before any Direct Field Control telemetry was even observed. RESET cleared it cleanly, drive returned
      healthy. This is the safety system working exactly as intended, and it directly confirms the documented
      burn-risk warning is real: this bench motor's phase resistance is low enough that even the "safe" starting
      voltage already exceeds what this page's protection margin allows. Did not attempt any higher voltage -
      the whole point of "start low, raise gradually" is moot once the floor itself faults. This command is not
      currently usable on this specific motor without either finer voltage control than the page's 0.001 V step,
      or a different motor with higher phase resistance.
    - **Brushed: Open Loop Control - skipped**, not untested-but-applicable: read the real Motor Type live
      (Parameters Explorer -> Datasheet) and confirmed it is "0 - Brushless Motor" on this bench drive, so this
      command is genuinely not applicable to the current hardware setup, matching the user's own stated
      condition ("only if Motor Type is actually Brushed").
    README's own *Still to confirm* items 16-21 updated with these real results (Direct Field Control's own
    entry now reads as a confirmed real blocker, not a theoretical one); OPERATIONAL_MANUAL.md's Direct Field
    Control entry gained a prominent "confirmed on the real bench drive" warning above the fold.
  - **Item Configuration's fold/unfold groups fixed (2026-09-24), from the user's own screen recording: "when i
    click it expand and minisize but some are not minimising when i am clicking".** Extracted frames from the
    `.mov` (no `ffmpeg` on this Mac - installed the `imageio-ffmpeg` pip package into the project venv purely for
    its bundled static binary, no system-wide install) caught it directly on **Kinematics Model**: the header
    flipped to "+" immediately, but the groups below it in the column (Tool Pose, Product Activation, ...) stayed
    at their OLD y-position for a beat, leaving a stale blank gap where the folded content used to be, before
    snapping to the correct collapsed layout a frame later. Root cause: `PropertyGroup._toggled`
    (`gui/config_page.py`) only called `self.rows.setVisible(open_)` and left Qt's own async `LayoutRequest`
    event to reflow the scroll area's `column` layout of sibling groups - normally near-instant, but the delay
    becomes visible whenever something else keeps the event loop busy first (a connected page's own polling
    timers, here maybe the screen recorder itself). Fixed by calling `self.updateGeometry()` then
    `self.parentWidget().layout().activate()` synchronously inside `_toggled`, forcing the column to reflow in
    the same call instead of waiting for the queued event. Proven as a real reproduction, not a guess: added
    `test_folding_a_property_group_reflows_the_groups_below_it_immediately` (`tests/test_gui.py`), confirmed it
    FAILS against the old code (`pose.y()` provably stuck at the stale position right after the click, no
    `qtbot.wait` involved) and PASSES with the fix; then live-verified fold and unfold both ways against the
    actual running app (Quartz-scripted click on the real header, screenshot immediately after) - clean reflow,
    no blank gap, either direction.
  - **A second, worse fold/unfold bug found right after, same day: user "activation key and some other tab does
    not fold".** Product Activation and Networks are `ParameterGroup`, not plain `PropertyGroup` - and
    `ParameterGroup.__init__` did `self.rows = {}` (a dict of `_Row` bookkeeping objects, keyed by register) right
    after `super().__init__()` had already set `self.rows` to the fold container QFrame every `PropertyGroup` has.
    That silently OVERWROTE it, so the inherited `_toggled`'s `self.rows.setVisible(open_)` was handed a dict, not
    a widget, on every single click - not a timing glitch like the first bug, genuinely non-functional. PySide6
    does not propagate an exception raised inside a signal handler back to the caller, so the app never crashed
    and nothing appeared in the GUI to say so either - folding these two groups just silently did nothing at all,
    header text included, and no existing test had ever clicked a `ParameterGroup` header to notice. Fixed by
    renaming the bookkeeping dict to `self.param_rows`, leaving `self.rows` meaning the fold container everywhere -
    `sdr_tuner/` itself never referenced the dict by its old name, but `tests/test_gui.py` did, 20 times
    (`activation.rows[KEY]`/`networks.rows[KEY]`), so those all got updated to `.param_rows[KEY]` too (the full
    suite catching exactly this - 7 tests failed on the first attempt until the rename was propagated to them).
    Proven the same way as the first bug: added `test_folding_a_parameter_group_like_product_activation_actually_folds`,
    confirmed it raises `AttributeError: 'dict' object has no attribute 'isVisible'` against the old code and
    passes clean with the fix, then live-verified both Product Activation and Networks fold correctly on the
    actual running app.
  - **User: "check the rest of item configuration for other tabs that don't fold" - a comprehensive sweep, not
    more spot-checks.** Rather than argue from "both fixes are at the class level, so logically every instance is
    covered" (exactly the reasoning that missed the second bug the first time - the first fix's own test only
    ever clicked a plain `PropertyGroup`), added
    `test_every_fold_group_on_every_item_configuration_page_actually_folds`: finds every single `PropertyGroup`
    (which `ParameterGroup` also is) via `findChildren` on BOTH pages that have one - Item Configuration (General,
    Target Connection, Additional Axes, Kinematics Model, Tool Pose, Product Activation, Networks - 7) and an
    additional axis's own copy of it (General, Target Connection, Product Activation, Networks - 4) - and clicks
    every one twice, asserting the header check-state, `rows.isVisible()` AND the header text all round-trip
    correctly. Caught one real test-only bug on the first run: checking `rows.isVisible()` on the axis config page
    before it was ever made the CURRENT page in `win.host`'s stack returned False for every group regardless of
    fold state (a hidden ancestor makes `isVisible()` false for the whole subtree, in Qt) - fixed by clicking the
    axis's own tree item first, the same way `test_clicking_an_axis_while_on_system_configuration_opens_its_own_item_configuration`
    already does, before asserting anything. With that fixed, all 11 groups across both pages pass clean - no
    third bug found. Also live-verified the remaining 3 groups by hand that neither earlier fix's live check had
    directly clicked (General, Target Connection, Additional Axes) - all fold cleanly, no dead gaps. Full suite:
    748 passed.
  - **3D Simulation can load its own kinematics model now (2026-09-24), user: "give option to load model in this
    page only".** Previously it could only ever RENDER whichever model Item Configuration's own Kinematics Model
    group had loaded - this page had no loader of its own at all, just a warning pointing elsewhere. Added the
    exact same **Open…**/**Samples…** controls Item Configuration has (`sim3d.py`, reusing
    `KINEMATICS_FILE_FILTER`/`KINEMATICS_SAMPLES_DIR` from `config_page.py` - no import cycle, `config_page.py`
    never imports `sim3d.py`), both calling straight through the ONE shared `session.load_kinematics()` - not a
    parallel/duplicate loading path. "Counts per unit" stays Item-Configuration-only (this page has no per-axis
    editor and doesn't need one).
    **A real, separate sync bug found while adding this, not caused by it - just newly reachable**:
    `ItemConfigurationPage` had NO listener on `session.kinematics_changed` at all; its own Model summary/
    counts-per-unit rows/Tool Pose dropdown only ever updated INLINE inside its own `_load_kinematics()` method.
    Loading from anywhere else (this page's new controls, or any future second loader) would have left Item
    Configuration silently stale - summary still reading "No model loaded" while a model was actually loaded and
    rendering fine elsewhere. Fixed by extracting `_refresh_kinematics_display()` and connecting it to
    `session.kinematics_changed` in `ItemConfigurationPage.__init__`, exactly mirroring `Sim3DPage`'s own already-
    correct `_reload_model` pattern - `_load_kinematics` now only calls `session.load_kinematics()` and handles
    the error case; the signal handler does 100% of the display refresh regardless of which page triggered it.
    4 new tests (`tests/test_gui.py`): Samples… lists/loads the bundled files, Open… loads through a mocked
    QFileDialog, a bad file shows a clear error via this page's own status (not the window status bar - this page
    has never had a `message` Signal, unlike Item Configuration), and - the one that actually caught the sync
    bug on its first run - loading from this page updates Item Configuration's own summary text too. **Verified
    live** against the real running app (a second `main.py` instance was already open, launched by the user from
    PyCharm - left it completely untouched, drove only this session's own separately-launched instance): Samples…
    -> robot-dog.obj loaded and rendered directly on 3D Simulation with no visit to Item Configuration at all;
    switching to Item Configuration showed "robot-dog.obj.kinematics - 13 bodies, 12 axes, 4 tool(s): T3, T6, T9,
    T12", all 12 counts-per-unit rows, and Tool Pose's own Tool dropdown already on "T3 - leg-1-3" - every one of
    them freshly built from the load this page triggered, confirming the sync fix.
  - **GUI-matching pass against EAS3 (2026-09-24/25), user: "check the elmo software... we want to make our GUI
    matching with that GUI if not 100% then as close as we can reach".** Toured EAS3 fresh in the Parallels VM
    (System Configuration, Drive Setup and Motion's own multi-pane dashboard, Parameters Comparison, Quick Tuning,
    Application Tools, the Views tab) rather than trusting the 2026-09-21 redesign notes alone. Confirmed still
    matching well: ribbon tabs, workspace tree, activity list, page header pattern, status bar wording,
    collapsible groups, the tuner wizard's own tree+navigation pattern. Confirmed correctly NOT replicated:
    Application Tools' Emulation/ECAM/Halls Correction/Back EMF identification (Elmo/Gold-drive internals,
    unrelated to Stimita's protocol) and anything Maestro (a different, multi-axis-controller product tier).
    Found one big, genuinely new structural gap: EAS3's dockable multi-pane workspace (its own "Views" ribbon tab
    - View Layout, Auxiliary Tools Assignment) lets several tool panels sit open and resizable side by side at
    once; this tool shows one page at a time. Asked the user how far to go (AskUserQuestion, twice - first they
    asked to see more pages before deciding) - chosen scope: **functional + visual polish, not the docking
    rearchitecture.** First item: EAS3's Motion page shows a live Digital Input/Safety status grid (Emergency
    Stop, Limit Switches, Generic Input, STO1/STO2/ERR) at a glance - checked our own register data and found we
    already HAVE these exact signals (`GPIO: Emergency Stop Switch`/`GPIO: Limit Switch: Negative/Positive
    Speed`/`GPIO: Generic Input`/`STO /Interlock: status`, all `access: ro`, section "Telemetry - Peripheral: GPIO
    & STO / Interlock"), just buried in the 175-parameter Telemetry Explorer, not shown anywhere at a glance.
    `drive_tab.py`'s own Motion page can never show them - its own docstring is explicit that it "never asks the
    drive for telemetry" outside the broadcast - so they were added to `peripherals.py` instead, a new **DIGITAL
    INPUTS & SAFETY (read-only)** `FormPage` group (GPIO-themed, already has explicit Read All/on-demand
    semantics, the right home). **A real, separate bug this exposed**: `FormPage._make_row` (`tuner.py`, shared by
    every tuning wizard AND Peripherals) never actually checked `reg.writable` - a read-only register would
    render as a normal EDITABLE widget that would only fail, confusingly, if the user tried to Write it. Fixed
    to match `ParamsTab`'s own already-correct handling (`setReadOnly`/`setEnabled(False)`, `on_submit=None`) -
    checked first that no EXISTING FormPage group referenced a non-writable key, so this was purely additive.
    2 new tests (`tests/test_gui.py`): the new group's 5 rows are genuinely read-only (disabled/read-only widgets,
    matching each row's actual widget type), and the row count bumped from 28 to 33 (existing tests updated).
    **Verified live**: connected the Simulator, opened Peripherals, Read All showed the new group with all 5 rows
    correctly greyed out, STO's own combo box showing its documented option text (not a raw number) exactly as
    every other registers-with-options row already does. Full suite: 753 passed.
  - **Remaining 3 EAS3 polish items, closed out (2026-09-25), user: "continue with the remaining polish items".**
    - **Tree warning icons - turned out to already exist, no code needed.** EAS3's Quick Tuner flags a page with
      an invalid value with a small red triangle next to it in the tree. Checked `tuner.py`'s own `_sync()`
      (`gui/tuner.py`): it already colours a page's tree item RUBY when `page.errors()` is non-empty (a dirty,
      unsaved page gets AMBER + a "●" suffix instead) - already tested
      (`test_a_wrong_entry_is_an_error_and_is_never_written`, checks the exact RUBY hex against a real invalid
      entry). Functionally the same information EAS3's icon conveys, just via colour instead of a glyph - decided
      NOT to bolt on a redundant "⚠ " text-prefix mechanism on top of it: it would also leak into
      `current_title()`/the window's breadcrumb (`page_changed`), a side effect not worth the marginal visual gain
      over an already-correct, already-tested mechanism.
    - **Status LED**: EAS3's Motion page pairs a coloured light with its status text; this tool only had
      `fault_summary`'s own coloured TEXT ("NO FAULTS" green / "FAULT 0x.." red). Added `self.status_led` (the
      existing `Led` widget - no new QSS needed, its off/on colours already are dim-grey/glowing-RUBY, exactly
      fault-vs-no-fault) next to `fault_summary` in `_fault_card()` (`drive_tab.py`), updated in the SAME place
      `_tick()` already recomputes `fault_summary` from the SAME `bits` value - a second rendering of information
      already shown, not a new telemetry request or a new interpretation of "fault". Reset alongside the other
      LEDs in `_on_disconnected()`. 2 new tests: follows `fault_bits` like `fault_summary` does, and resets on
      disconnect. **Verified live**: FAULT BITS card shows the light correctly dim next to "NO FAULTS" right
      after connecting the Simulator.
    - **Tighter grid-style Item Configuration rows**: EAS3's own Item Configuration is a literal two-column
      property grid (visible vertical rule between Name and Value, spanning every row); this tool's own
      `PropertyGroup.add_row()` (`config_page.py`, shared by every `PropertyGroup`/`ParameterGroup` row across
      Item Configuration, Axis Configuration, Product Activation and Networks - one change, everywhere at once)
      only had the horizontal row-separator it already had. A `border-right` on the label was considered and
      rejected - a label is only as tall as its own text, so the rule would stop short of the row's top/bottom
      edges instead of running the full height; added a real 1px `QFrame#gridDivider` sibling widget in the row's
      own `QHBoxLayout` instead, which naturally spans the full row height. Moved `gridValue`'s left padding into
      the layout's own spacing after the divider, so an editable widget (most rows) and a plain read-only
      `_value()` label (a few - Device Type, serial numbers) get the identical gap either side of the same
      divider, not two different amounts. **Verified live**: zoomed screenshots of General/Target Connection
      (editable rows), Product Activation/Networks (`ParameterGroup`, inherited), and Kinematics Model/Tool Pose
      - the divider runs continuously, aligned, full row height, on every one; button/note rows (Open…/Samples…,
      GO, plain text notes) correctly have none, matching EAS3 itself (a grid divider through an action row would
      look wrong there too). Full suite: 755 passed. **This closes out the whole EAS3 GUI-matching pass** - the
      only thing deliberately left out is the dockable multi-pane rearchitecture, which the user explicitly
      scoped out.
  - **The user reversed that scope decision (2026-09-25): "build status monitor, Multi Axis Motion, The dockable
    multi-pane workspace we need to build all take your own time but we will built this in such a way that it
    works magnificent".** All 3 remaining EAS3 gaps, including the big one just scoped out above. Given the real
    size - especially the dockable workspace, a genuine architecture addition - planned properly first
    (`EnterPlanMode`, 3 parallel research passes covering `main_window.py`/`shell.py`/`navigator.py`/`ribbon.py`'s
    real navigation architecture, `widgets.py`/`defs.py`/`client.py`'s telemetry patterns, and
    `session.py`/`axes_page.py`/`feedback_gate.py`'s multi-axis API, all with real file:line citations, not
    guessed) before writing any code. Plan, 3 phases, in order (each independently built/tested/committed):
    **Status Monitor** and **Multi Axis Motion** first (both self-contained, no dependency on the workspace),
    the dockable workspace last (so it can include both new pages as dock panels from day one, no retrofit).
    Full plan at the time it was approved: `/Users/remi/.claude/plans/effervescent-dazzling-milner.md` (this
    Mac's Claude Code plan-file location, not part of the repo).
    - **Phase 1, Status Monitor, built**: see its own entry above (Where things stand). One real correctness bug
      caught and fixed before it ever reached the user: `client.telemetry_snapshot()`'s own keys are short names
      (`defs.TELEMETRY_KEYS`, e.g. `"wz_count"`) or a register's own address string - NEVER `reg.key` (the
      `(index, sub)` tuple `Register` itself uses to look things up) - an early draft compared `reg.key` against
      the snapshot directly, which could never match anything, silently breaking the live-update path for EVERY
      row. Caught by the plan's own "verify against a standalone script" habit before it ever reached a test.
      Second real bug: Fault Bits' own `options` table only names a handful of well-known BIT COMBINATIONS (it's
      a bitmask, not a plain enum) - the generic `show_value()` formatter would show a raw placeholder like
      "=1+2" for a genuine multi-fault value instead of decoding it; fixed by special-casing it to
      `defs.fault_names_text` (the exact same helper `DriveTab`'s own Fault Bits card already uses), matching
      every other page instead of being the one place Fault Bits reads differently. A live-verification-only
      snag, not a code bug: a Read button wrapped in its own tiny `QWidget`+`QHBoxLayout` (2px margins, inside an
      already-narrow 64px table column) squeezed the text below a legible size, rendering visually garbled -
      fixed by setting the button directly as the cell widget (matching how the Remove button already was),
      exactly the same "don't invent a wrapper when the direct pattern already works" lesson as the target/
      signal combos next to it. 6 new tests, all passing on the first real attempt after these fixes. A pre-existing
      test also needed a fix: `test_the_window_is_laid_out_like_a_studio` pins the exact ORDERED list of "setup"
      activity tool keys - a real, expected consequence of adding a tool to that list, not a regression, updated
      to include `status_monitor` (and, in the same edit, `multi_axis_motion` right after it, since Phase 2 added
      its own tool to the same list before this fix was even committed).
    - **Phase 2, Multi Axis Motion, built** (see its own entry above, Where things stand). Reused
      `feedback_gate.check`/`client.hold_position` directly rather than writing a second position-move
      implementation - the whole point of researching `DriveTab._move_absolute`/`_go_to_position`/
      `_with_feedback_check` precisely before writing anything. 6 new tests, all passing on the first attempt -
      the careful, research-first replication of an already-correct pattern paid off directly here. Full suite
      (both phases together): pending the next background run. **Verified live** against the running Simulator: the page listed the
      connected axis correctly (Drive01, node 7), and MOVE dispatched a real command with live status feedback;
      typing a non-zero target through this session's own GUI-automation tooling hit a `SpinBox`-specific
      synthetic-keyboard-event quirk (3 different injection methods tried - raw `CGEventPost` to both the HID and
      session event taps, and `osascript`'s own `keystroke` - none of them landed a keystroke in this ONE widget
      type, while the exact same techniques worked normally on every `ComboBox`/button/checkbox touched so far
      this session) - not chased further given the automated suite already exercises `target_spin.setValue()` +
      `move_btn.click()` directly, the same code path, 6 times over.
    - **User caught a real, live alignment bug from the very screenshot above**: Node ID "7" and Position "0"
      weren't sitting under their own "Node"/"Position (counts)" headers - Qt's own default `QHeaderView` label
      alignment is CENTRED, while `QTableWidgetItem` cell text defaults to LEFT - the two only coincidentally line
      up in a wide/stretched column, never in a narrow fixed one. First fix attempt copied `tuner.py`'s own
      `ChangesTable` pattern (`head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)`,
      a single BLANKET header alignment, + right-align the numeric item cells) - correct for Status Monitor, whose
      columns are ALL left-aligned data, but WRONG for Multi Axis Motion: it mixes left (Axis, Status) and right
      (Node, Position, Target) columns, so one blanket left default put the Node/Position/Target HEADERS at the
      left edge of their columns while their DATA sat right-aligned - opposite ends, worse than the original
      centred-vs-left mismatch. **User caught this too, live, from the corrected screenshot**: "still alinement is
      miss matched". Fixed properly by dropping the blanket call and setting EACH header column's own alignment
      individually - `self.table.horizontalHeaderItem(column).setTextAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)`
      per column, matching that column's own data (Axis=Left, Node=Right, Position=Right, Target=Right,
      Status=Left) - confirmed live afterward that Node "7" and Position "0" sit flush under their own headers,
      Axis/Drive01 flush left together. Status Monitor's own header kept its original single left-default (still
      correct there - all its columns are genuinely left-aligned, Value/Units mixing numbers with descriptive text
      like "No Faults"). A follow-up user request - "take node towards little bit left side so you will get more
      space for other parameters" - traced to `COL_AXIS` and `COL_STATUS` both being `Stretch`: Qt splits leftover
      width between every `Stretch` column, so the Axis column (holding only a short "Drive01"-style name) was
      claiming about half the leftover space, pushing Node/Position/Target needlessly far right. Fixed by making
      `COL_AXIS` `Fixed` at 160px and leaving `COL_STATUS` as the sole `Stretch` column (the one whose own text
      genuinely varies enough to need it). `axes_page.py`'s own table has the same underlying centred-header gap
      and was NOT touched here - out of scope for this request, flagged in memory as worth the same fix later if
      noticed again. **Verified live** after both rounds of fixes: re-screenshotted and pixel-zoomed Multi Axis
      Motion - Node/Position/Target headers sit flush over their own right-aligned data, Axis/Drive01 flush left,
      columns visibly tighter with real breathing room left for Target/Status. Full suite: re-run fresh after all
      three fixes (the alignment fix, its correction, and the column-width fix) rather than trusting the
      pre-correction 767-passed run, which predated two of the three changes - 767 passed again. **A follow-up
      request** - "give target and status enough space so it will not look congested" - found Target's own
      spinbox (160px) and MOVE (84px) sitting pressed right against each other with barely any margin, too tight
      for the full +-2,000,000,000 count range with its " counts" suffix. Widened `COL_TARGET` to 220px and
      `COL_MOVE` to 96px (Status, still the sole `Stretch` column, absorbs the rest and was never actually short
      on room - confirmed live, plenty of blank space past its header in both the pre- and post-widen
      screenshots). **Verified live**: re-screenshotted and pixel-cropped - Target's spinbox now has a clear gap
      before MOVE, Status has real space after it, alignment still holds. 6-test subset passed; full suite
      re-run once more after this change before committing.
    - **`axes_page.py`'s own version of the same bug, flagged above as deliberately deferred, got reported live**:
      a screenshot showed its "Position (counts)" header clipped to "osition (counts" - its column had no explicit
      width (just whatever `QHeaderView`'s own default section size gives an `Interactive` column with nothing set)
      and no header/item alignment at all, so the centred default header text for that one long label simply didn't
      fit and got clipped. Fixed with the exact same, now-established pattern: `COL_AXIS`/`COL_NODE`/`COL_STATUS`/
      `COL_POSITION`/`COL_SPEED` all `Fixed` with explicit widths (160/60/190/170/130), `COL_FAULTS` the sole
      `Stretch` column (this table also had TWO Stretch columns sharing leftover space before - Axis and Fault
      Bits - the same anti-pattern already fixed once on Multi Axis Motion; Axis never needed it, a bounded short
      name), and each header's own alignment set individually via `horizontalHeaderItem(col).setTextAlignment(...)`
      to match its own column's data (Node/Position/Speed right, Axis/Status/Fault Bits left) - plus, new this
      time, the item-level alignment is set once at first creation in `_refresh()`'s `if item is None:` branch,
      since (unlike the other two pages) this table's items are created lazily on first data rather than up front.
      5-test subset (`axes_page`, the pinned tool-key-list test, `opening_the_tools`) passed - column indices the
      tests already assert by position were untouched, only width/alignment changed. **Verified live**: the full
      "Position (counts)" header now renders uncut, flush right over its own data, matching every other column.
    - **Phase 3, the dockable multi-pane workspace, built** (see its own entry above, Where things stand, for the
      full design/bugs/live verification). This closes out the 3-phase plan in full - all 3 EAS3 gaps the user
      asked for ("build status monitor, Multi Axis Motion, The dockable multi-pane workspace... take your own
      time... magnificent") are now built, tested (778 passed), and live-verified.

All 3 phases of the EAS3 dockable-workspace pass are done.
  - **Whole-codebase audit (2026-09-25, user: "check the software... see what function are miss behaving or not
    working... or can be implemented in more easy and simpler way... also check if any part of code can be
    replaced with any library" then "fix all the real bugs and commit once tests pass").** Five parallel read-only
    reviews (core protocol/client, window/session, motion/tuning pages, G-code/kinematics/3D, and hand-rolled-vs-
    library), plus a pyflakes pass (only unused imports) and a TODO/FIXME scan (none). **Every claim was verified
    against the code before anything was changed** - each fix below has its own regression test (33 new tests,
    811 passed;
    the fix's own docstring/comment records the original symptom). Two claims did not survive verification and
    were NOT changed: the reviewers' "suspicions" with no constructible trigger (a listener exception killing the
    rx thread - no listener can raise today; a late SDO answer landing in a later verify read - needs >0.6 s of
    drive latency), and the "multi-axis G01 is a dog-leg" claim - every axis gets the full F, so a two-axis G01 is
    not a straight line in joint space; true of the code, but whether the VENDOR's own controller interpolates or
    runs each axis at F is exactly the kind of protocol question only the "servo tunning tool - original"
    reference or a bench test can settle, so the semantics were left as they are and the question is flagged.
    - **Core**: the simulator's own thread died silently once Work Zone Count passed INT32 (~33 min at 1000 rpm;
      encode_value raised, `_run` caught only CanError) - it wraps like a real counter now; its
      `_start_exclusive_mode` never ended a step-response test, so a new speed command was replayed over by the
      test's own square wave and Operation Mode stuck at 16; `client.write()` on a mapping register reloaded the
      whole mapping (12 reads) even for a write refused before it was sent (read-only / does-not-fit), and a
      timeout in THAT reload then flagged the mapping unknown - validation now happens before the reload window
      (`_write_frame`/`_send_write`); `parse_snapshot` accepted any JSON with a "parameters" key and
      `diff_snapshots` then KeyError'd on a hand-edited row - rows are validated (`SNAPSHOT_ROW_KEYS`); the
      exported `.eds` listed every object but wrote no main `[XXXX]` section - `canopen.import_od` (a real
      CANopen reader, installed to check) failed on the very first object, "0x1A00 was not found" - main RECORD
      sections with `SubNumber` are written now, deliberately WITHOUT a fabricated sub0 (this hardware does not
      answer sub-index 0 - the existing `test_eds_never_fabricates_a_sub0_entry`, which a first version of the
      fix broke; the reader loads all 47 objects / 417 sub-indices either way); `canopen` is now in
      requirements-dev.txt for that test only (`importorskip`) - never imported by the tool itself.
    - **Window/session**: removing the axis the Drive Setup and Motion list was scoped to only retargeted its rows
      when one of ITS pages was current - leave it on Terminal, remove the axis, click Recorder: `recorder:2`,
      a KeyError (`_scoped_axis` now tracks the scoped drive; the tree follows it when returning to the activity
      too); Disconnect re-ran `_on_interface_changed`, which reset a typed channel (PCAN_USBBUS2...) to the
      interface default, so the next Connect quietly opened the wrong channel (reset only when the interface
      itself changed); an axis's pill was recomputed with a default `ok=True` on every open, so a silent axis read
      "ONLINE · NODE N" (`_axis_probe` remembers the last probe); docking the current page removed it from the
      stack FIRST, so the stack showed its neighbour for an instant - whose `showEvent` fired a TunerPage Drive
      Load (real reads) for a page nobody opened (show the workspace first); the "Docked View" crumb was overwritten
      by a docked tuner's own sub-page title; Item Configuration's Read/Write callbacks never checked the result
      still belonged to the current connection (a Disconnect mid-read repainted a DISCONNECTED page "read", stale
      values surviving into the next connection - the same `client is` guard `Session._probed` uses); `axis_error`
      was written to the status bar twice (two connections); `_add_axis` passed an unstripped channel.
    - **Pages**: `connected` fires BEFORE the probe, and on the USB cable the drive's real node is only found by
      listening afterwards, re-keying `all_clients()` - Multi Axis Motion, Status Monitor and 2D Plotter rebuilt
      only on connected/axis_* and stayed on the stale node forever (the real bench setup: cable, node 7, field
      at 1) - all three now follow `node_detected`; the Recorder's trigger compared the raw rps value while the
      chart/cursors/CSV show rpm, so a level typed in what the screen shows never fired (`_trigger_value`, unit in
      the status text); the Axes page's Speed column hardcoded `wz_speed` while the default layout sends
      `speed_hz` - "—" for every simulated/factory drive, motor running or not; the per-card "· sent every 100 ms"
      labels outlived STOP/OFF/RESET/a takeover/a disconnect (`_repeat_status` tracks the ONE repeat that is true);
      a chosen rps unit fell back to rpm on every reconnect (`_rps_unit`); Status Monitor's tick re-enabled Read
      while a read was in flight (double reads).
    - **G-code/kinematics/3D** (the reviewer ran the pure modules to confirm most of these): Motion Controller's
      STOP only cut a dwell - a Feedback Settings check still in flight (almost always one) then sent its
      hold_position AFTER the emergency stop, the remaining axes/waypoints/actions still ran, and a RUN pressed
      meanwhile got a second interleaved program when the old line's callback finally fired - `GcodeRunner.cancel()`
      now bumps a generation every async continuation checks (`_alive`), so nothing started before STOP sends,
      advances or calls back; the round STOP button/Esc never halted a program at all (press it during a G04 and
      the NEXT line moved the motor) - `emergency_stop` ends the program, the Terminal's line and a Tool Pose GO
      first (`_stop_program(*, stop_drives=False)` - keyword-only, the STOP button's own `clicked(bool)` filled it
      with False in a first version and the drives were not stopped from the button: 3 existing tests caught it);
      RESUME while the paused line was still running dispatched that line a second time (`_in_flight`); STOP in
      SIMULATE left a G1030 axis spinning in the 3D view forever (`cancel` freezes the virtual axes); a Txx arc
      solved its waypoints one at a time as it sent them, so an unreachable third waypoint left the leg part-way
      along the arc (all solved up front now; nothing moves on a failure); IK ignored the file's Axis Limits and
      never folded angles - `G00 T3 X-500 Y200 Z-100` on robot-dog "converged" to [5419, 29819, -1024] degrees,
      15 and 83 motor turns, when [19, -61, 56] was right there - rotary joints are now folded to within a turn
      of the seed every step (FK is periodic, so this changes nothing about the search - clamping to the limits
      mid-search was tried first and pinned a joint against its limit, stalling the 9-joint round-trip test), and
      a converged solution outside a limit is reported (`IKResult.out_of_limits`) and REFUSED by every dispatch
      site with the axis and angle named; arcs in the X/Z plane were mirrored - the pair was ordered (X, Z), G18's
      own is (Z, X), a right-handed pair - the vendor's own DOG.NC gait swing `G03 T6 Z-400 X0 R500` dipped the
      foot 200 into the ground instead of lifting it 200 (checked numerically both ways against
      `trajectory.arc_waypoints`; `gcode._plane_order`; 3 parser tests that encoded the old order updated, with
      the reason); an I/J/K arc never checked the end point lies on the circle the offset describes, so
      `G02 X100 Y0 I10 J0` traced a radius-10 circle and jumped the last 80 in a straight line (refused), and the
      chord-vs-diameter test had no tolerance (0.30000000000000004 > 0.3, hit by FK-derived Txx starts); the
      parser accepted NAN/INF (`RNAN` = a semicircle; `G04 PINF` = an OverflowError inside a timer) and F<=0
      (sent to the drive as-is) - refused; a dwell beyond a day is refused by the runner and `_wait` clamps to
      QTimer's INT32 ms; `_run_chain` looked its clients up once at the start, so its "disconnected mid-line"
      branch was dead and a disconnected axis got its dead client (looked up per target now); three paths never
      called `on_done`, leaving a program "running" until STOP - the feedback gate's busy guard returned silently
      (STOP then RUN before the old check finished), `_go_to_position`/`go_to_position_linear` returned silently
      when the axis had disconnected between the check and the send - each says so now; a `.kinematics` file with
      a driven joint but no Axis Direction loaded and then TypeError'd on every 3D tick, and a body listed as its
      own parent looped `_actuated_ancestors` forever, freezing the GUI on load - both refused at parse time;
      the 3D view placed placeholder spheres with `item.translate(origin)`, which `_render`'s `setTransform`
      replaced on the first frame - every placeholder sat at the model's origin (baked into the vertices now).
    - **Simplification / library conclusions (not applied, reported to the user)**: keep every hand-rolled part -
      the drive is only CANopen-STYLE (0x20 writes with the value in bytes 4-7, a non-standard `(index<<8)|sub`
      mapping code, command frames on 0x200/0x300/0x400, no NMT), the vendor `.kinematics` and G-code dialect have
      no library equivalents, `usb.py`'s framing deliberately sends nothing on open (python-can's slcan sends
      "O\r"), pygcode is unmaintained/GPL, trimesh/scipy/roboticstoolbox are heavy for what they would replace.
      Worthwhile simplifications the reviews found, for a later pass: ten near-identical continuous-command
      methods in `client.py` (one `_repeat(key, *values)`), `history_snapshot` copying the whole 20000-sample
      deque per curve per tick, three copies of the write job (params_tab/tuner/peripherals), the duplicated
      axis-combo rebuild (status_monitor/plotter_2d), `TunerPage._sync`'s O(rows²) fan-out, `log_tab`'s per-row
      `removeRow` loop, and `sim3d._tick` recomputing every transform while hidden.
  - **Live check of the audit build, and one more small fix (2026-09-25, user: "try it out live yourself", then, after
    the app was moved to its own Desktop, "run the two checks now").** Verified on screen against the Simulator at
    node 1: the round STOP pressed during a `G04 P3` dwell ended the program ("Stopped.", line 3 never dispatched);
    `G04 PINF` refused with "'PINF' is not a finite number"; the Axes page Speed column read 11.67 Hz while `G1030
    X100` ran (100 rpm x 7 pole pairs / 60) and 0.00 Hz after the round STOP. **Found while typing into the running
    app**: the Fusion style lets a push button take keyboard focus on a mouse click, and disabling a focused button
    (`_sync` during a run) hops focus along the tab chain - after clicking RUN the editor no longer had the keyboard,
    so the space in the next typed line pressed a toolbar button (it opened the Open… dialog; offscreen the focus
    ends on the Samples combo). Fixed the way the ribbon already does it: the Motion Controller's nine toolbar push
    buttons are `NoFocus` (`motion_controller.py`), so a click on RUN/STOP/etc. leaves the editor focused; the Loop
    box and Samples combo keep focus (typed in / picked from). Regression test
    `test_motion_controller_toolbar_buttons_never_take_keyboard_focus_from_the_editor` (fails on the old code). 812 passed.
    **Automation notes for next time**: activating the app's process with `osascript ... set frontmost` switches
    Desktops to it; check the frontmost process is `Python` right before every synthetic click/keystroke (while the
    user types in Claude's window, both windows trade focus and keystrokes leak both ways); click INSIDE the editor
    text before Cmd+A + typing, never on the toolbar row.
  - **Live check on the REAL bench drive, and two more small fixes (2026-09-25, user: "continue your test now" - they
    pressed Connect themselves; asked which way to go, they chose "Continue on the real drive").** The drive now
    answers at **node 1** (found by listening on the USB cable; the Node ID field still said 7, so this is exactly the
    `node_detected` re-keying case). Node stated before every command. `G1030 X100` (axis 0 = node 1) from the Motion
    Controller: Operation Mode went to "Electronic Speed Control (ESC)" with real phase currents (-1.76 A / 0.73 A),
    the Axes page Speed column showed the drive's own rps value (this drive's mapping broadcasts Work Zone Speed, so
    the column correctly picked the `wz_speed` branch - "rps", not Hz), the round STOP put it back to Idle, currents
    ~0, No Faults, and the Speed Control label read "STOP sent." with no leftover "sent every 100 ms". Status
    Monitor's Target combo, Multi Axis Motion's row and the 2D Plotter's axis pickers all listed the drive under
    node 1 - the fix for the stale-node bug confirmed on the real setup it was found on. **Two defects seen on screen
    and fixed**: (1) the Status Monitor row's Read button read "Reac" and its Remove button showed no glyph at all -
    measured offscreen: a cell widget is ~12 px narrower than its column (72 -> 60 px for a button whose hint is 71),
    and the 28 px fixed Remove button inherited the theme's 18 px side padding (46 px needed) - Read column 96 px,
    Remove column 44 px + a `QPushButton#removeRow` rule with 4 px side padding; regression test asserts every row
    button gets at least its own size hint (fails on the old code: Remove 28 < 46). (2) `main.py --sim` pressed
    Connect assuming the interface list still sat on the Simulator, but `_load_settings` restores the last-saved
    interface first - with the bench drive's USB cable saved, `--sim` would have opened the REAL drive; new
    `MainWindow.connect_simulator()` selects the Simulator explicitly, `--sim` calls it, test saves USB first. 814
    tests. Also
    seen, not changed: while a G-code line drives the motor, the Motion page's Speed Control label keeps its last
    own message ("STOP sent.") - the readout cards are live and correct; noted as a cosmetic inconsistency.
  - **Every fold/unfold works with a single click (2026-09-25, user: "in system configuration single click [folds]
    or unfold menus but in drive setup and motion in parameter explorer double click works... make sure every where
    fold and unfold should work with single mouse click").** Item Configuration's `PropertyGroup` headers are push
    buttons (one click); the three `QTreeWidget`s were on Qt's default (arrow click or DOUBLE-click on the item):
    the Parameters/Telemetry Explorer sections (`params_tab.py`), the tuning wizard's headings (`tuner.py`) and the
    navigator's Workspace folder (`navigator.py`). New `widgets.fold_on_single_click(tree)`: `setExpandsOnDoubleClick
    (False)` + `itemClicked` toggles any item that has children (a leaf is left alone; a click on the branch arrow
    never emits `itemClicked`, so the arrow keeps working without toggling twice). Two tests click the real item
    rects (`_click_item` helper) on all three trees; both fail on the old code. OPERATIONAL_MANUAL.md says so in
    the quick index. Note for the reader: a double-click now toggles twice (fold then unfold), the same as the
    Item Configuration headers already did. 816 tests pass; all three trees also checked live on screen.
  - **Docked View resizing, two fixes from the user's screen recording (2026-09-25, "check this video", Docked View
    with the Parameters Explorer panel dragged narrower and wider by its edge).** (1) With the window maximised the
    panels sat on the right and the left part of the workspace stayed blank: a QMainWindow gives every leftover pixel
    to its central widget, and `DockWorkspace`'s was a plain empty QWidget (measured offscreen: 509 px of a 1658 px
    workspace). It is `setFixedSize(0, 0)` now, so the dock areas get all of the room (a no-central-widget window
    also works but Qt's docs say it is unsupported); test grows the window to 1920 px and checks each dock spans it.
    (2) The RAW REGISTER ACCESS note sat in the same row as the inputs with the leftover width, so in the narrow
    panel it wrapped into a one-word-per-line column ~20 lines tall that ballooned the box and squeezed the tree
    above (`params_tab.py`): it has its own full-width line under the inputs now; test shows a standalone ParamsTab
    at its minimum width (~890 px, exactly as narrow as the recording got) and checks the note spans >=80% of the
    box and the box stays under 180 px. Both tests fail on the old code. **Verified live** (restarted instance,
    full-screen 1920x1080, Docked View): one panel fills the whole workspace, two stacked panels fill it together,
    a floated Parameters Explorer narrowed to 916 px by dragging its edge keeps the note on two lines with the
    tree intact, and Reset Layout re-docks a floated panel full-width. Also seen: dragging a dock's title bar
    a short way floats it (Qt's own behaviour) rather than splitting - side-by-side docks need a drop on
    another dock's edge. 818 tests pass. **Video-analysis notes**: this one was a
    plain bt709 recording (no gamma needed); `select='gt(scene,0.002)',showinfo` lists the change moments, then a
    `-ss T -t 0.25` burst tiled 4x3 shows the native frames around each. **Automation note**: the user was working in
    the app on its own Space while I was analysing - my synthetic clicks there landed in the middle of their drag
    (a floated Telemetry Explorer ended up at the bottom of the screen); never send clicks to a Space the user is
    actively using.
  - **Docked View: drop guides while dragging a panel (2026-09-25, user's screen recording: "if i move mouse towards
    right or left or top or bottom then i should get an option where to set that dock").** With Qt's own dock drag
    the only feedback was its rubber band, which in the recording mostly covered the whole workspace - no visible
    way to pick a side. `dock_workspace.py` now gives every panel its own title bar (`DockTitleBar`, via
    `QDockWidget.setTitleBarWidget` - the documented way to take the drag over: Qt runs its own drag only through
    its default title bar, and accepting the mouse events in ours keeps it out) with the title, a float toggle (❐)
    and close (✕), and its own drag: past Qt's start-drag distance the panel tears off into a frameless floating
    window (`setFloating(True)` + `grabMouse()`, what Qt's own drag does too) that follows the cursor, and the
    workspace shows `DockGuideOverlay` - four arrows at its edges and, over another panel, a cluster of five
    (left/right/above/below it, centre = tab), the hovered guide lit with a translucent preview of the space, plus
    a ghost label naming the panel; a strip 24 px inside each edge counts as that edge too. Release on a guide ->
    `DockWorkspace.drop(dock, zone)`; elsewhere -> it stays floating. Placement primitives were probed offscreen
    first, not assumed: outer edges = `addDockWidget(area, dock, orientation)` (the 4-arg form - without the
    orientation a "right" drop stacked BELOW the existing right-area panel), beside a panel =
    `splitDockWidget(target, dock, o)` (Qt only splits AFTER, so "before" splits once more the other way round),
    tab = `tabifyDockWidget`; every one followed by `resizeDocks`, because Qt's own placement gave the moved panel
    its size hint and the neighbour a 14 px sliver. `AnimatedDocks` is OFF for the workspace: `drop()` re-docks a
    floating panel and moves it in the same call, and the animated re-dock only lands the panel when the animation
    ends, so the move raced it and lost (caught by the drag test). Tests drive the real title-bar handlers with
    QMouseEvents carrying explicit GLOBAL positions (`_title_mouse`) - the bar's own mapping changes the moment the
    panel tears off. The drag test uses Parameters Explorer + Axes, not Motion: the Motion page's own fixed left
    column + cards grid needs ~1060 px, so no two-panel row fits it in a 1360 px window - a limit of that page, not
    of the docking (and why the dragged panel gets half the width OR whatever the neighbour's minimum leaves).
    **Three more things the live check of this turned up, fixed in the same pass**: (1) three stacked panels
    OVERLAPPED - the Motion page painted through the Parameters Explorer, which painted through Axes: a dock can
    never be smaller than its widget's minimum size, and the three pages' minimums (596 + 351 + 161 px) do not
    fit a 725 px workspace, so Qt clamped each dock to its minimum; every page now sits behind its dock's own
    `QScrollArea` (`#dockScroll`), so a dock shrinks freely and the page scrolls - `DockWorkspace.page_of(key)`
    reaches the page, `close_tool` unwraps it (four existing tests read `dock.widget()` and were updated). (2) the
    guide overlay was a child widget, and the panel being dragged is a floating WINDOW, so it covered the cluster
    of guides - the overlay is now its own stay-on-top, translucent, click-through Tool window placed over the
    workspace (coordinates inside stay workspace-local). (3) a drop on the LEFT edge guide landed UNDER Axes,
    full width: Axes sat in Qt's top area, which by default owns every corner, so "left" only meant the
    leftover space. Fixed corners (left/right own them) were tried and rejected - a top drop then stops short of
    a left panel. Rule now: the side just dropped on takes its two corners (`setCorner` in `drop()`), so the
    newest edge drop is always the outermost, like Visual Studio; corners are part of `saveState()`, verified.
    (4) after the left drop the neighbour was a 65 px sliver: probed every `resizeDocks` variant offscreen - it
    only ever acts WITHIN one line of docks, never between dock areas, whose sizes Qt derives from the docks'
    own size hints at insertion (a page's hint is its whole minimum layout, so the dropped panel grabbed nearly
    everything). `DockScroll` (the wrapper) reports a modest uniform hint (480x360, min 120x80) so equal hints
    split evenly, and `drop()` ends with `_rebalance()` (equal weights for every docked panel, both
    orientations) so no panel keeps a stale strip size from a stack it just left. **Known limit, verified live
    and left as is**: a panel dropped on the LEFT/RIGHT edge while another sits on the TOP/BOTTOM edge (two
    perpendicular Qt areas) gets sized by Qt's own grid, which here gave the top-edge panel only its minimum -
    every public variant (resizeDocks in both orientations, repeated, temporary max/fixed widths, restore of
    the saved state) was probed offscreen and none moves that split; Qt's own dock drag has the same limit. The
    wrapper's minimum (220x150) keeps the squeezed panel readable and the separator between them drags. All
    other placements (left/right beside panels in the default area, beside a panel, tab) split evenly. 825 tests pass.
    OPERATIONAL_MANUAL.md's Docked View entry rewritten around the guides.
  - **Collapsed navigator polish (2026-09-25, user's screenshot of the collapsed sidebar: "logo mismatched when
    collapse").** Three things were off once the panel was collapsed to its 54 px strip: (1) the activity buttons'
    icons sat 6 px right of centre - the expanded style's `text-align: left; padding: 8px 12px` applied to an
    icon-only 38 px button, so the 24 px icon started at the 12 px left padding; collapsed buttons now carry a
    `collapsed` dynamic property (`set_prop`) with `text-align: center; padding: 8px 0`; (2) the collapse toggle
    was the plain text glyph "«"/"»" (muted grey, 16 px) right above drawn gold icons - the same thing fixed on
    the ribbon's toggle on 2026-09-24, now the same drawn `chevron_left`/`chevron_right` glyphs (new in icons.py);
    (3) the toggle floated mid-panel: with the tree and tool list hidden nothing claimed the leftover height, so
    Qt spread it between the rows - a zero-stretch spacer after the tool list takes it (expanded, the tree's and
    list's own stretch factors still win). Test grabs the unchecked button offscreen and measures the gold
    pixels' centre against the button's (the old code was 6 px off; the test fails on it), and checks the toggle's top and the
    buttons' bottom. 826 tests pass; verified live.
  - **"Drive Setup and Motion" split into two activities, SERVO DRIVE and MOTION (2026-09-25, user: "we will make 2
    option 1 as servo drive and 2 is motion instead of drive setup and motion / in motion controller we can shift
    function like 2d plotter, 3d simulation, and other function where multi axis motion is involved").** Asked
    three things first (AskUserQuestion): Terminal goes to Motion (its G-code console addresses any axis, its CAN
    trace shows every drive), Docked View covers BOTH new activities with ONE shared workspace, names exactly
    "Servo Drive" / "Motion". Activity keys are now `system` / `drive` / `motion` (the old `setup` key renamed
    everywhere, tests included - it only ever meant this activity). Servo Drive = the 7 per-drive tools (Quick/
    Advanced Tuning, Motion - Single Axis, Recorder, Parameters/Telemetry Explorer, Peripherals - the rows
    `retarget_drive_tools` re-points at the selected drive, unchanged); Motion = Motion Controller, 3D Simulation,
    2D Plotter, Multi Axis Motion, Axes, Status Monitor, Terminal (all shared pages, no `kind` tag). `_last_tool`
    gained `"motion": "motion_controller"`; `DOCKABLE_ACTIVITIES = ("drive", "motion")` replaces the two
    `== "setup"` checks in `open_tool`/`_focused_page`; the docked header names whichever activity's tool was
    opened last (`navigator.activity_title`). Clicking a drive in the tree while on Motion still opens that
    drive's own Motion - Single Axis page (switching to Servo Drive), exactly as the old activity did. The manual's
    quick index and section headers were re-pointed row by row (per-drive rows -> Servo Drive, multi-axis rows ->
    Motion) and its intro now describes three activities. Historical entries above keep the old name. 827 tests
    pass; verified live (three buttons, both lists, one shared Docked View holding a Servo Drive panel and two Motion
    panels together).
  - **A G-code command is mirrored on that axis's own Motion - Single Axis page (2026-09-25, user: "when i run
    G-code through motion controller then i should get update in servo drive as well").** Reproduced offscreen:
    after `G1030 X100` from the Motion Controller the client was repeating 100 rpm, the page's live cards followed
    the telemetry, but its own target field read 0.0 rpm and the status "Ready. Set a target speed and press RUN."
    - the runner only ever told the page `note_gcode(started)` (RUN/target bookkeeping), nothing about WHAT it
    sent. `note_gcode(started, *, kind, value, text)` now mirrors it the way the page's own buttons would: a speed
    command sets the target field/slider (signals blocked - `_apply_if_running` must not re-send) and Speed
    Control's "Commanding N rpm (G-code) · sent every 100 ms" through `_repeat_status` (so the one-repeat-at-a-
    time clearing still holds); torque picks Torque (ETC) on the Current/Torque card and sets its target;
    Stop/Off/Reset zero the target and show "Stop sent. (G-code)" under Speed Control like the page's own STOP;
    position moves are mirrored by `_go_to_position`/`go_to_position_linear` themselves (`_report_move`: the
    runner's callback label still gets the text, and the page's Position Control target/status - or Speed
    Control's for a hold/ENABLE - shows it tagged "(G-code)"). A speed above the page's own Speed Limit is sent
    as written (the limit only caps the page's own RUN) and REPORTED as above the limit - the field is never
    clamped to a value the drive was not sent. `_set_speed_target_quietly` shared with `emergency_stop`. Two
    tests drive a real program through speed -> torque -> position move -> stop and the over-limit case.
  - **3D Simulation: camera centred on the model, floor grid fitted to it (2026-09-25, user's two screenshots:
    before loading a model the grid floor shows; after loading radar.obj "white base ... get vanished and model
    is also not showing in middle").** Two causes, both in `sim3d.py`'s `_frame_camera`: (1) it set only the
    camera DISTANCE, so the camera kept looking at the origin while radar's mesh box is centred at (-56, 25,
    -115) mm (measured) - the model sat low and to one side; `_home_camera` now carries the box centre and
    `reset_view` passes it as `setCameraPosition(pos=...)`. (2) the grid was pyqtgraph's default 20 x 20 units of
    1 = 20 mm, a speck under a camera ~700 mm back - new `_fit_grid`: cell = a round 1/2/5 x 10^n step near a
    tenth of the model's largest side, span ~2.5x the model, laid at the model's lowest z and centred under it;
    `_reset_grid` restores the default when the model is cleared. Test loads radar, checks the camera centre
    against the mesh's own box (fails on the old code: (0, 0, 0)), the grid's size/step/placement, Reset View,
    and the clear. 830 tests pass; verified live (radar centred, grid under it).
  - **Bode plot: what the drive can and cannot do (2026-09-25, user: "what do you think about bode plot / can you
    check drive support bode plot or not").** Checked three sources, not guessed: (1) the drive's own catalog - no
    frequency-response / FRF / identification command or register at all; (2) the Device Reference PDF (a scratch
    venv with PyMuPDF, the project venv has none) - pp. 89-91/95 document the four Testing commands' **Wave Form**:
    0 square, 1 square positive-only, **2 sine wave, 3 frequency sweep, 4/5 sweep with linear/quadratic amplitude
    decay** - i.e. the drive has the sine / swept-sine EXCITATION a Bode measurement needs, on the current (0xBC
    Iq, 0xB0 FOC), speed (0xB4 ESC) and position (0xB8 Servo) loops, but no analysis; the sweep's frequency range
    is NOT documented (a stepped-sine at 1/Period Hz is the only fully-known excitation); the loop's reference
    (`Reference: Current` 0x4000:0x20, `Reference: Speed` 0x4000:0x21, `Reference: Work Zone Count` 0x4000:0x23)
    and its response (`FOC: Iq Current`, `Speed (Electrical Frequency)`, `Work Zone: Speed`/`Count`) are all
    telemetry, so input and output could both be recorded from the broadcast; the ceiling is `CANopen: telemetry
    frequency` (0x3000:0x05, applies after a power cycle, max undocumented, default ~9 msg/s = 3 Hz per signal,
    useless for a Bode plot beyond ~1 Hz); (3) the vendor IDE binary - no Bode/FRF/spectrum strings, only its
    Oscilloscope. **Fixed on the way**: the Step Response Tests page's Wave Form was a bare 0-5 number whose tooltip
    said the reference does not document the values - it does; it is a named ComboBox now (`WAVE_FORMS`, index =
    wire value), README wording updated, test proves the sine's value 2 reaches the wire. Verdict given to the
    user: feasible as a host-side stepped-sine tool, but only after the real drive's telemetry rate is raised and
    measured; not built.
  - **Telemetry-frequency ceiling measured on the real drive (2026-09-25, user: "check telemetry frequency max on
    real drive").** New guarded bench tool `tools/telemetry_rate_probe.py` (same pattern as the other two tools/
    scripts: a guard bus lets through ONLY SDO reads and SDO writes of 0x3000:0x05 to the exact values it steps
    through; original restored in `finally`, read back, rate re-measured). Node 1 stated first. Result: the drive
    was at 9 Hz (3 Hz per message, receiving frequency 1000 Hz); 30 -> 30.6/s, 100 -> 100, 300 -> 333, 1000 ->
    1000, **3000 -> still 1000/s = 333 Hz per TPDO message: the drive's own cap** (its 1 kHz tick, the same as
    the receiving frequency); SDO reads answered in 1 ms with no failures at every step; restored to 9 Hz and
    confirmed. **The parameter applies IMMEDIATELY** - the catalog's `power_cycle` flag (from the importer's
    `POWER_CYCLE` set, which mirrors the vendor tool's own "Power Cycle" marks, not the PDF) and the README's
    old "applies after a power cycle" were wrong for this one register; the importer no longer flags it (the
    measurement is the evidence), the JSON regenerated through the importer, README corrected. Whether a written
    value also SURVIVES a power cycle was not tested (restored before ending). For a Bode plot this means 333
    samples/s per signal are available - a usable plot to a few tens of Hz once the loop's Reference and its
    response share one TPDO message. **Second pass (user: "see if you can go higher")**, same guard-and-restore
    pattern, three reversible experiments: (A) 1200/1500/2000/5000/10000 Hz -> exactly 1000/s every time, a
    firmware cap; (B) `CANopen: receiving frequency` 1000 -> 2000 Hz with telemetry at 3000 -> still 1000/s (the
    cap is not that tick, or the tick does not apply live - either way no gain), restored; (C) messages 1 and 2
    unmapped (their 0x1A01/0x1A02 slots written 0, the same live-applying mapping writes as the 2026-09-22 DC-bus
    remap) -> **message 0 alone arrives at 1000 frames/s**: the 1000/s budget is shared across the mapped
    messages, so ONE message with the signals that matter (8 bytes: e.g. Reference: Speed + Speed, both FLOAT32)
    is sampled at 1000 Hz. Mapping, receiving and telemetry frequency all restored and read back; fault bits 0
    throughout. That is the Bode plan's sampling answer: 1 kHz on the two loop signals, Nyquist 500 Hz.
  - **Frequency Response (Bode plot) built (2026-09-25, user: "build the bode plot").** Servo Drive > Advanced Tuning >
    Verification > Frequency Response (`gui/bode_page.py`, `FrequencyResponsePage`; the arithmetic in `sdr_tuner/bode.py`,
    pure functions, no Qt, like gcode.py/kinematics.py). A STEPPED SINE, built entirely on what the two probes of the
    same day established: the drive's Testing commands take Wave Form 2 = sine at 1/Period; the loop's reference
    (`Reference: Speed` 0x4000:0x21 / `Reference: Current` 0x4000:0x20 / `Reference: Work Zone Count` 0x4000:0x23) and
    its response (`Speed` / `FOC: Iq Current` 0x4001:0x03 / `Work Zone Count`) are telemetry; and 1000 messages/s is
    shared across the mapped messages. So a sweep (1) reads and remembers the telemetry frequency and all 12 mapping
    slots (`emit`ted to the GUI BEFORE any write, so a failed switch can still be undone), maps ONLY ref+response into
    message 0, empties messages 1-2, sets 1000 Hz; (2) per frequency (log-spaced, each snapped through the FLOAT16
    Period rounding - `bode.snap_frequency`) runs `client.run_test(key, 1/f, amplitude, 2)`, waits `settle` cycles
    (floor 0.3 s), then reads `measure` cycles (floor 0.5 s) of both signals from the client's own history (same
    frames -> identical timestamps, `bode.align`), fits each with least squares on cos/sin PLUS A CONSTANT
    (subtracting the mean instead biased the phase by 2 deg over 1.5 cycles - caught by the synthetic test), gain =
    amplitude ratio in dB, phase = the difference wrapped to +-180, quality = the share of power the fit explains
    (< 0.6 drawn hollow/amber); (3) Stop, then the mapping and frequency written back - on completion, STOP/Esc (the
    round button reaches it through `TunerPage.emergency`), a failed switch, too few samples, and `disconnecting`
    (SYNCHRONOUSLY there - the async restore would run after the port is gone). Results: -3 dB bandwidth relative to
    the lowest-frequency gain (interpolated in log f), the phase there, a resonance peak >= +1 dB; Save Data (CSV) /
    Save Image (both plots stacked, the Recorder's own pattern). Simulator: Wave Form 2 (and 3-5, approximated as the
    sine - the sweeps' range is undocumented) now really produce a sine; the references and a measured Iq (its own
    5 ms lag, `IQ_TAU`) are telemetry (`defs.TELEMETRY_KEYS` gained ref_speed_hz/ref_current/iq/ref_wz_count); the
    publish interval is `mapped messages / frequency`, mirroring the measured real-drive behaviour (the sim's own
    loop caps it near 200/s - enough). 7 pure tests (`tests/test_bode.py`, synthetic first-order lag to 0.2 dB / 1 deg,
    phase wrap, bandwidth/phase/resonance, csv) + 7 GUI tests: placement under Verification, the sim's sine, the whole
    sweep (3 points, 1/2/4 Hz, gain and phase within 1.5 dB / 12 deg of 1/(1+jwt) with t = sim.tau, mapping and rate
    back exactly), STOP keeps the points and restores, the round STOP, disconnect restores BEFORE closing, refusals.
    **Verified live** on the Simulator (own Desktop, my instance addressed by unix id - the user's PyCharm instance was
    running alongside): 10 points 0.3-20 Hz at 10 Hz amplitude, 45 s; gain the textbook -20 dB/decade roll-off
    past ~1 Hz (-6 dB at 2 Hz, -25 dB at 20 Hz), phase -15 -> -73 deg; the phase creeps back a few degrees at
    12-20 Hz - the SIMULATOR's 5 ms stepping (a sample is a tenth of a cycle there), not the page. 846 tests pass.
    **Never tried on a real drive** (README *Still to confirm* 4b): the motor moves for the whole sweep, so that needs
    the user's go-ahead like every motor test.
  - **The telemetry "tick" identified, and what the vendor IDE's embedded help holds (2026-09-25, user: "open tunning
    tool - original... there is a help file in software as well read and see").** No help file ships in the folder; the
    IDE's "Contextual Help" dock is fed from three zlib-compressed definition files inside `Servo-Drive-IDE.exe`
    (found by scanning for zlib headers and inflating - `scratchpad/exe_zlib/`): the general Servosila reference
    (217 KB, the one the 2026-09-22 session already saw), plus two smaller variants. Read-only reads on the REAL
    drive (node 1, `tools/usb_readonly_check.ReadOnlyBus` guard) of this drive's own Device Information telemetry:
    **`Timer ISR Frequency` 0x5000:0xA9 = 1000 Hz** - the telemetry cap measured earlier is one TPDO per timer
    interrupt; `Control Frequency` 0x5000:0xA2 = 20 850 Hz (the position/velocity/torque loops, 1:1:1 per the PDF;
    confirmed by the ADC `Sample Number` counter advancing 20 854/s), `Switching (PWM) Frequency` 0x5000:0xA3 =
    41 700 Hz, firmware date 20260921. So "CANopen: receiving frequency" is NOT the telemetry tick and raising it
    cannot lift the 1000/s; only firmware (a faster timer ISR, or several TPDOs per tick) could. **Leads in the
    vendor file, absent from this drive's PDF**: three commands commented out with `#` - `0x500 0xF0 Oscilloscope:
    Channel Configuration (Index, Sub-Index, Channel)`, `0xF1 Oscilloscope: Start (Trigger Event, Sampling
    Divider)`, `0xF2 Oscilloscope: Stop` - an on-drive capture at the 20.85 kHz sampling rate divided down, which
    would be the real answer for fast Bode/step data if the firmware implements it (NOT tried: undocumented
    commands are never sent to the real drive without the user's go-ahead - a question for Stimita); Wave Forms 6/7
    (Sawtooth up/down) and a `Testing: ADC Trigger Delay Sweep` (0x300:0xBE) exist in the general file only. The
    sweeps' frequency range is documented nowhere in the vendor file either.
  - **Parameter writes are RAM-only; the save-to-flash command found (2026-09-25, user: "change frequency from 1000
    to 2000 and check, I will do power cycle").** Wrote `CANopen: receiving frequency` 2000 (guarded, read back 2000,
    node 1), the user power-cycled: it read 1000 again - AND the drive came back at **node 7** with the **2026-09-22
    telemetry layout** (the 2026-09-24 "Speed instead of Commutation" remap gone, the node-1 change of the same day
    gone). So every SDO write is RAM-only; what survived earlier (DC-bus f32 remap, activation key) survived because
    an automatic tuning ran AFTER it - its documented step 8 "Save to Flash". README *Still to confirm* 5 answered,
    manual warns. **The vendor IDE's own Save to Flash, found in its machine code** (`Servo-Drive-IDE.exe`, 64-bit
    immediates next to `on_pushButtonSDOToFlash_clicked`): it sends the STANDARD CANopen store request - SDO
    expedited download `23 10 10 01 73 61 76 65` (`0x1010:0x01` <- 'save') and looks for the standard reply `60 10
    10 01 ...`; Factory Defaults sends `23 11 10 01 6C 6F 61 64` (`0x1011:0x01` <- 'load') - NEVER send that one.
    Neither object is in this drive's own PDF (only the vendor tool knows). Note the command specifier is the
    standard 0x23 (expedited, 4 bytes), not this tool's usual 0x20 "write, undefined size" - send the exact frame.
    **Sent, with the user's go-ahead** (`scratchpad/flash_probe.py`, guard allows only the listed writes and the
    exact save frame): receiving 2000 written, `23 10 10 01 save` sent to node 7 -> reply `60 10 10 01 73 61 76 65`
    (acknowledged); the user power-cycled; the drive came back with **receiving 2000 persisted, `Timer ISR
    Frequency` = 2000 Hz, and telemetry at 2000 messages/s** (666.5 per message with 3000 asked), SDO 0-1 ms,
    no faults. So the receiving frequency IS the timer tick (applied at boot only), the telemetry cap is one
    TPDO per tick, and the cap CAN be raised - 2x proven; higher untested (the PDF's CPU-congestion warning; the
    ISR execution-time telemetry 0x5000:0xA4-A6 is the way to judge headroom). Final configuration then written
    and saved (acknowledged, to node 1 after the node write - both 1 and 7 were heard for a moment): receiving
    1000, the 2026-09-24 Speed layout (0x1A00:3 = Operation Mode, 0x1A01:1/2 = Work Zone Count/Speed, 0x1A01:3 = 0),
    node ID 1 - the user's own choice for what should survive. **Save to Flash is therefore a real, working,
    undocumented-for-this-drive command - now the ribbon's Save to Flash button (next entry).** Final power cycle
    verified: the drive came back at node 1, receiving 1000, Timer ISR 1000 Hz, the Speed layout intact, 9 msg/s, no
    faults - everything the user chose is persistent. **The bench drive is at node 1 from now on.**
  - **Save to Flash button + the 2000 Hz Bode workflow built (2026-09-25, user: "add save to flash button but one thing
    we can do is when user want to use bode plot at that time you change the frequency to 2000 and ask user to do power
    cycle ... as soon as he is done then again switch back to 1000 and ask user to do save to flash and do power
    cycle").** `client.save_to_flash()` sends the vendor IDE's exact store request (`23 10 10 01 'save'` -> 0x1010:1),
    waits for the 0x60 acknowledgement through a frame listener (P.parse_response drops 0x60 acks by design, so the
    write waiter cannot see it), raises DriveError on an abort / DriveTimeout on silence; ribbon Parameters > Drive >
    **Save to Flash** (`MainWindow._save_to_flash`, for the drive whose page is shown, `_confirm_flash` first - it
    persists EVERYTHING in RAM, node ID and bit rate included). Frequency Response page: a **Telemetry rate (the
    drive's timer)** card reads `Timer ISR Frequency` 0x5000:0xA9 (first show, and on `probed` only while the page is
    visible - connecting itself must read nothing extra, the 16-reads test caught a 17th),
    **PREPARE 2000 Hz** / **BACK TO 1000 Hz** = confirm, write receiving frequency, `save_to_flash()`, then a standing
    amber instruction "POWER-CYCLE the drive, then Disconnect and Connect" that clears itself once the re-read timer
    matches; buttons enable/disable from the timer value; sweeps now set the telemetry frequency to the drive's TIMER
    rate (`_sample_hz`, "Sampled at N Hz" in the result) instead of the 1000 constant. Simulator: answers the 0x1010
    store (`flash_mode` ack/abort/silent, `flash`, `flash_saves`), serves 0x5000:0xA9 from `timer_isr_hz` (= receiving
    frequency AT BOOT), caps its publish rate at that tick, `reboot()` = a power cycle (RAM <- flash, timer <- RAM's
    receiving frequency; `flash` starts as the boot configuration, so an unsaved write is lost - as measured). Two
    __init__-ordering traps hit while adding sim attributes (a later `= None` silently overwrote the value set
    earlier next to `params`): check where in __init__ a new attribute lands. Tests: client store/abort/silence/
    reboot semantics; GUI: ribbon button asks, declines, sends (sim.flash_saves), refuses disconnected; the whole
    2000 Hz workflow (PREPARE -> written+saved+instruction -> sim.reboot() -> timer 2000 -> a sweep sampling at 2000
    -> BACK TO 1000). Verified live on the simulator: the ribbon button, the card, the PREPARE dialog and the
    instruction.
  - **The Bode plot tried on the real drive - and "FLOAT16" turned out to be fixed point (2026-09-25, user: "try the
    bode plot on the real drive, i will power cycle").** Driven in the REAL app on screen by an in-process script
    (`scratchpad/real_bode.py`: buttons pressed through their own `click()`, the page's real QMessageBox answered Yes
    by a timer after 1.5 s, `win.grab()` for screenshots - no synthetic mouse/keyboard at all, so nothing the user
    types can leak in; `apply_theme(app)` or the window comes up in Qt's white default). Node 1 stated, no fault,
    24 V bus. The sweep mechanism worked end to end on the real drive (telemetry switched to ref + response in
    message 0, 10 points, layout put back EXACTLY - checked by an independent read of all 12 slots + frequency
    before pressing PREPARE 2000 Hz, which then wrote + saved; the user power-cycled; the drive came back with
    Timer ISR 2000 Hz and 2000 frames/s). **But every point had fit quality ~0.** A guarded raw capture
    (`scratchpad/bode_raw_probe.py`, same guard pattern as tools/: only SDO reads, the 12 mapping writes to the
    exact codes, the listed command frames and Stop) of `Reference: Speed` during the drive's own sine test showed
    a 1 s Period running as a 60 s sine, and four periods (10, 1, 1/60, 1/600 s) always ran as (the 16-bit
    pattern sent) / 256 seconds. Sent as raw 256 the reference was a clean +-6 Hz sine at exactly 1.000 Hz
    (quality 1.000), raw 64 a clean 4 Hz one, and the square wave finally toggled. Read-only cross-check: the
    DC-bus "FLOAT16" register read raw 0x180A = 6154 / 256 = 24.04 V against the FLOAT32 twin's 24.03 V. **So the
    reference's "FLOAT16" is a signed 16-bit fixed-point number, value x 256** - `protocol.py` (`TYPES`,
    `encode_value`/`decode_value`, new `wire_value()` used by params_tab/tuner instead of `np.float16`),
    `bode.snap_frequency` (periods are multiples of 1/256 s: 256/n Hz at the top, `MAX_FREQUENCY` 256, raw 0
    clamped), `export.py` (INTEGER16 + an x256 note). Three earlier "findings" were this tool's own decoding: the
    2026-09-22 "broken DC-bus register" (24 x 256 as a half float = 0.002 V), the 2026-09-24 step test (Period
    0.5 s = 56 s, it just held +3 Hz), and **Direct Field Control's "0.1 V" that tripped the overcurrent
    protection - it was 46.4 V** (0.1 as a half float = 0x2E66 = 11878 / 256); the advanced auto-configuration
    (0xD1, FLOAT16 current limit) would have sent 63.6 A for 1.9 A - never run on a real drive, luckily. README
    (*Confirmed on a real drive*, items 4b/7/17), manual (Direct Field Control, step tests, Bode) and the rule
    at the top of this file corrected. **With the fix, on the real drive at 2000 Hz: the Iq current loop (0.5 A,
    2-256 Hz) gave a textbook plot** - 0 dB flat, -3 dB at 42.6 Hz with -37 deg, -13.8 dB / -69 deg at 256 Hz,
    quality 0.9 falling to 0.5 at 128 Hz; the speed loop (12 Hz = 144 rpm amplitude) is noise (quality <= 0.27)
    because its response is the sensorless speed ESTIMATE, garbage at these speeds on an encoder-less motor - not
    a page defect, documented in the manual. Then BACK TO 1000 Hz (written + saved), the user power-cycled again.
    Lesson: a protocol type name in a vendor PDF is a name, not a format - when a signal makes no sense, capture
    the raw bytes and vary one input before trusting any layer above. **Two more things the first real PNG showed,
    fixed the same day**: (1) a plot-sized BLANK strip under each plot - a widget grab on this Retina screen carries
    devicePixelRatio 2, and both the Bode page's and the Recorder's Save Image advanced by the grab's DEVICE height
    while painting it at its logical size (the Recorder's 2026-09-24 live check called that gap "each chart's own
    natural margin" - it was this bug; offscreen tests run at ratio 1 and could not see it); one shared
    `widgets.stack_pixmaps()` keeps the ratio and stacks in logical pixels, with a ratio-2 test that fails on the
    old code. (2) pyqtgraph's log-axis labels crowd into one another above 30 Hz - `_set_ticks()` puts 1-2-5 ticks
    per decade across the measured range. Also the sweep's confirmation named "1000 messages/s" whatever the timer
    ran at; it names the timer rate now. After BACK TO 1000 Hz and the user's power cycle, verified: node 1, Timer
    ISR 1000 Hz, receiving 1000, the Speed layout, 9 msg/s, no fault.
  - **Direct Field Control at a REAL 0.1 V on the bench drive (2026-09-25, user: "try direct field control at real
    0.1 V on the real drive").** Same in-process real-GUI driver (`scratchpad/real_dfc.py`): node 1 stated, no
    fault, 24 V; telemetry frequency raised to 100 (RAM, restored) so the watch saw ~33 current samples/s; the
    watch STOPs on any fault or a phase current above 80 % of Maximum Continuous Current (1.9 A). Rotation 3 Hz
    for 6 s: Operation Mode "Direct Field Control - Rotation", peaks 0.30 A / 0.19 A, no fault, STOP -> Idle.
    Electrical Position 1.25 rad for 4 s: mode 12, peaks 0.08 A / 0.17 A, clean. The status line read "0.1016 V"
    (raw 26). No visible rotation - 0.1 V / 1.14 ohm line-to-line is under 0.1 A. So the command works at the
    reference's starting point; the 2026-09-24 trip was the 46.4 V mis-encoding. **Then 0.5 V (user: "try it at
    0.5 V and watch the current")**, same driver (`real_dfc.py 0.5`): Rotation peaks 1.02 A / 0.93 A with Work
    Zone Speed swinging +-13 rps as the rotor followed the field, Electrical Position a steady 0.30 / 0.45 A DC
    hold; no fault, no watch STOP (limit 1.52 A), STOP -> Idle both times. Half the 1.9 A limit at 0.5 V on a
    1.14 ohm motor - 1 V would trip. Then 0.5 V at 10 Hz (user: "try at 0.5V 10 Hz and check first" - the
    driver's own pre-checks, node/fault/bus, ran first): peaks 1.22 A / 1.07 A, rotor slipping, no fault.
    **The ceiling on this motor is 0.5 V** (user: "update the memory and manual with the direct field control
    ceiling") - the manual's Direct Field Control entry carries it with the reasoning (current = V / R_phase,
    protection at ~1 V, heat with I^2, roughly 1.9 A x R_phase on another motor); above 0.5 V untried on purpose.
    README item 17 updated. Bench drive left: node 1, timer 1000 Hz, Speed layout, disconnected.
  - **The drive's test sine DRIFTS off the asked frequency the longer it runs - found chasing a bad 30-point
    Iq sweep (2026-09-25, user: "try the bode plot on the current loop again with more points").** The denser
    sweep (1-256 Hz, 26 points after snapping) on the real drive at 1000 Hz lost every point above ~20 Hz
    (quality 0.1 at 43 Hz) while a 3-point sweep and the no-GUI probe fitted the same frequencies at 0.85. Ruled
    out in order, each with data: the GIL/thread-switch interval (no change with `sys.setswitchinterval(0.001)`),
    the page's 0.5 s window (offline, 0.5 s windows of the probe's capture fit 0.85), host timestamp jitter
    (frames recorded inside the GUI run: 5000 per 5 s exactly, p99 1.4 ms, no duplicates), frame loss (none in
    steady state). The recorded reference itself, in frame order, was a sine at the WRONG frequency (38.5 Hz for
    42.67 asked, 88 for 128) late in the sweep. `scratchpad/drift_probe.py` (one 128 Hz test held 40 s): 128.00 Hz
    at 0.5 s, 127.87 at 2 s, 128.51 at 5 s, 127.23 from 10-30 s, 132.35 at 40 s - clean each time (q 1.000 at the
    real frequency), 128.00 again after a Stop. So the firmware's generator accumulates phase with a precision
    that drifts (a Stop resets it), and a long sweep that never stopped between points measured a drifting sine
    against the asked frequency. Fix: `_start_point` sends `client.stop()` before every point after the first,
    and `bode.response_point` now fits at the frequency the reference really shows (`refine_frequency`, a +-6 %
    search: coarse scan, parabola, fine scan; the point's frequency is the measured one). Tests: synthetic drifts
    of -0.6 % / +3.4 % at 128 Hz recover the real frequency to 0.02 % with quality > 0.95 (and < 0.7 unrefined);
    the GUI sweep test counts one Stop frame before each later point. README (*Confirmed on a real drive*) and
    the manual carry the finding; worth reporting to Stimita.
  - **Iq step cross-check on the real drive (2026-09-25, user: "do the iq step response cross check on the real
    drive").** `scratchpad/iq_step_probe.py` (guard like tools/; the Iq test as a square, +-0.5 A, Period 0.2 s =
    raw 51; four 2 s captures at 1000 Hz with message 0 remapped between them: ref+Iq, ref+ia, ref+ib, ia+ib).
    Iq estimate: 10-90 % rise 8.8 ms, 11 % overshoot; raw Phase B current: 8.0 ms, 9 % - identical shape, so the
    51 Hz is the loop, not telemetry filtering (Phase A carried almost nothing at that rotor angle; the ia+ib
    "current magnitude" idea was useless - the two values in one frame are too noisy/unsynchronised, |I|
    jumped 0.2-0.7 A sample to sample). Read-only gains: FOC Kp 0.3035 V/A, FOC T 1.671 ms = L/R of the motor
    (0.954 mH / 0.571 ohm per phase) - pole cancellation - so bandwidth = Kp/L = 50.6 Hz vs 50.7 measured. The
    Bode page is validated against theory to a hertz. README (*Confirmed*) and the manual say what to do with it
    (raise FOC Kp, sweep again). The user offered an encoder in 2-3 days (memory): then the speed loop, ENABLE /
    MOVE TO / Cyclic, 3D Live and the Recorder trigger on position all become testable.
  - **FOC Kp raised to 1.0 V/A and swept again (2026-09-25, user's instruction).** `real_bode.py measure ...
    '{"0x2002:0x12": 1.0}'` (the driver gained optional pre-sweep parameter writes, read back and logged; RAM
    only). Same 26-point Iq sweep: -3 dB 154 Hz, -39 deg, no peaking, quality 0.90-0.93 throughout (167 Hz
    predicted by Kp/L - the -3 dB point sits between the 128 and 256 Hz points, the sweep's coarse end). The
    Bode page therefore shows a tuning change exactly as expected. Then, user: "save it to flash" -
    `scratchpad/real_flash.py` pressed the ribbon's own Save to Flash in the real app after checking the RAM
    (a first attempt aborted on my own wrong address for the node ID - 0x3000:0x02 is `CAN: bitrate` = 1000,
    the node ID is 0x3000:0x03 - a pre-check that refuses is exactly what such a check is for), the real
    dialog answered Yes, "configuration saved to flash (node 1)" acknowledged. **The bench drive's flash now
    holds FOC Kp = 1.0 V/A** with node 1, bit rate 1000, receiving 1000, telemetry 9 and the Speed layout.
    Power-cycle verified right after (user: "i will power cycle now, verify kp after"; read-only): FOC Kp 1.0,
    FOC T 1.671 ms, node 1, bit rate 1000, receiving 1000, telemetry 9, the Speed layout, Timer ISR 1000 Hz, no
    fault, 24 V.
  - **Wave form 3 (the drive's frequency sweep) measured on the Iq loop (2026-09-25, user: "run the chirp on the
    iq loop and check the range" - asked first, after "can we find the resonating frequency?" was answered: not
    on an unloaded, encoder-less bench).** `scratchpad/chirp_probe.py` (guard as tools/): Period 0.1 s -> cycles
    of 1.016 s (10 x the fixed-point 0.1016 s) from 10.4 Hz, faster than exponential, to 400-500 Hz in the last
    few percent, then restart; Period 1 s -> from 1.05 Hz, restart at ~10.5 s. Amplitude constant. Welch
    cross-spectrum (numpy, Hann, 50 % overlap) of the 0.1 s capture matches the stepped-sine plot only where
    coherence > 0.8, i.e. up to ~60-100 Hz at 1000 Hz sampling - the sweep dwells too briefly up high; the 1 s
    cycle's Welch above 100 Hz is garbage (coherence ~0). So: the chirp is no better than the stepped sine for
    this loop; for a resonance search it would need Period >= 4 s and the 2000 Hz timer. `WAVE_FORMS[3]` now
    says the range. **Then built (user: "add a chirp mode to the frequency response page")**: `bode.py` gained
    `CHIRP`/`CHIRP_CYCLE`/`CHIRP_SHAPE`/`CHIRP_TOP_HZ`, `chirp_frequency()` (the measured sweep model, used by the
    simulator and the page's notes - never by the measurement), `welch_response()` (numpy only, Hann, 50 %
    overlap, H = Pxy/Pxx, coherence) and `chirp_points()` (12 log bands per octave, quality = mean coherence);
    `simulator.py` Wave Forms 3-5 accumulate the phase of that model (4/5 with an ASSUMED linear/quadratic
    amplitude decay); `bode_page.py` has a **Method** combo (`METHODS`: sine/chirp) showing either the From/To/
    Points/Settle/Measure rows or Period/Cycles + a note (`_chirp_text`), `values()` returning `(amplitude,
    period, cycles)` for the chirp (Period snapped to 1/256 s, raw 0 refused), `_start_chirp()` (one
    `run_test(key, period, amplitude, 3)`, a timer of cycles x 10 x Period + 0.3 s), `_chirp_done()` (history
    for that window, fs = the rate the frames REALLY came at - the test simulator publishes ~200/s while
    claiming a 1000 Hz timer - then Welch and band points between 1/Period and min(450, 0.45 fs)); the same
    switch/restore/Stop path as the stepped sine. `_summary()` now judges bandwidth/resonance on points at or
    above `QUALITY_FLOOR` when there are 3+ (both methods; the hollow-point note explains). Tests: the chirp
    model, a synthetic chirp through a lag recovered to 0.7 dB / 6 deg where coherence > 0.9 (test_bode), and
    in the GUI: fields shown/hidden and validated, a full chirp on the simulator's Iq loop matching 1/(1+jw
    IQ_TAU) to 1.5 dB / 12 deg for 2-20 Hz with the mapping/rate restored, STOP during a chirp.
    **First real run (Period 4 s, one cycle) was clean only to 19 Hz** - and chasing it taught the method: (1) the
    driver's recorded frames were poisoned before/after the mapping switch (frames decoded with the old layout -
    analyse the middle only); (2) the recording of cycles x 10 x Period cut the cycle's fast tail off, the only
    part with the high frequencies (`_chirp_seconds` records 8 % more now); (3) the real cause: a sweep excites
    each bin only while passing it, while the Iq telemetry's ~0.12 A rms broadband noise is in EVERY segment, so a
    plain Welch average drowns the bins above ~100 Hz - per-bin excited-segment selection (`EXCITED_FRACTION`,
    75 % overlap) on the earlier 6-cycle 0.1 s capture reproduced the Kp = 1 stepped sine to 260 Hz where the
    plain average had died by 130 Hz; `chirp_response` uses 2 s segments below `CHIRP_SPLIT_HZ` = 20 Hz and
    0.25 s above (short = selective; long = fine bins). Real drive through the page: Period 0.1 s x 6 -> coherent
    10-220 Hz (-3 dB 123 Hz); 0.5 s x 3 -> 3.5-210 Hz (-3 dB 181 Hz); stepped sine 154 Hz - agreement ~1 dB.
    Guidance corrected everywhere (manual, README, memory): SHORT Period, several cycles; a long Period spends
    the cycle below 25 x (1/Period). A test pins the selection beating the plain average under the measured noise.
  - **Automatic Test page built (2026-09-25, user: "can we make an automated process 1. just press one button and
    software will perform some test and give you the result 2. press another button and it will show what to
    change and if user agree then software automatically apply it and check again from process 1 and again 2
    until it resolves the issue (name 2nd function as beta)").** Asked four scoping questions first
    (AskUserQuestion): tests = current loop + speed loop (with an encoder) + the drive's automatic tuning first;
    target = "as fast as the drive allows"; changes = FOC Kp/T, notch filters, speed gains/filter; confirm ONCE
    then iterate. Mid-way the user asked "which speed you are talking about?" - answered: the loop's response
    speed (bandwidth), never the motor's rpm. Built: `sdr_tuner/autotest.py` (engine, no Qt, worker thread,
    `cancel` Event + `progress`): `read_values` (defaults for the two telemetry rates when a drive answers an
    error or 0 - the simulator answers 0 for 0x5000:0xA2, which made the cap 0 in the first Beta run),
    `health_checks`, `run_autoconfiguration` (the tuner's rules, synchronous), `measure_loop` (switch telemetry,
    square step, chirp, restore in `finally`), `step_metrics`/`chirp_metrics`, `run_tests`, `propose` (the
    policy: T to L/R; Kp x1.5 while clean, back to the last clean gain on peaking > 1 dB / overshoot > 20 %,
    stop at 0.3 x sampling or at `kp_cap` = 2 pi (control rate / 20) L; notch on a speed resonance; ESC Kp
    likewise), `apply`. `gui/autotest_page.py` (`AutomaticTestPage`, per drive incl. axes, tool key
    `auto_test`, ribbon Tuning group, `emergency()` from the round STOP): options, RUN TESTS, BETA (one plan
    dialog, then `MAX_ROUNDS` rounds in one worker job), STOP, SAVE TO FLASH, a result table + log. The
    simulator's current-loop tau now scales with 1/FOC Kp (`_step`), so Beta is testable end to end: it raises
    Kp round by round until 0.3 x the sim's ~160 Hz sampling. Same `_ended` fix as the Bode page (refresh must
    not replace an outcome with "Ready."). Tests: `tests/test_autotest.py` (8, pure: the bench numbers, step and
    chirp metrics, every branch of the policy) + 6 GUI (registration, RUN TESTS incl. auto-tuning first and the
    speed loop with/without an encoder, the Beta loop with its own Save to Flash, STOP via `emergency_stop`).
    **Real drive** (`scratchpad/real_autotest.py`, auto-tuning unchecked): RUN TESTS 13 OK / 1 check / 1 skip,
    current loop 186 Hz at Kp 1.0; then, user: "run beta on the real drive" - 4 rounds, Kp 1.0 -> 1.5 -> 2.25 ->
    3.375, bandwidth 199 -> 227 -> 257 -> 326 Hz, overshoot 12-13 %, peak <= 0.6 dB, stopped at the 0.3 x fs
    verification limit (326 > 300 Hz). At 3.375 V/A measured 326 vs Kp/L 563 Hz - the loop is past first order
    (control delay), the "vs Kp/L" row says CHECK. **Drive RAM: FOC Kp 3.375; flash: 1.0** - the user decides
    (SAVE TO FLASH or a power cycle back to 1.0). Then "Go further: PREPARE 2000 Hz, power cycle, run Beta again":
    PREPARE (real_bode.py's new `prepare`/`back` stages press the buttons only) saved the configuration WITH Kp
    3.375 - said so before pressing; after the user's power cycle Beta at 2000 Hz changed nothing: flat to 440 Hz
    (= the drive's chirp top, `CHIRP_TOP_HZ`), no -3 dB point in the coherent range -> "beyond what this
    measurement can verify". So the 326 Hz at 1000 Hz was the sampling talking; the real ceiling here is the test
    signal (chirp ~450 Hz, stepped sine 256 Hz), and Beta correctly refuses to raise a gain it cannot check.
    User's choice: "BACK TO 1000 Hz, step back to 2.25 V/A for margin against noise under a real load" - Kp
    2.25 written first, then BACK TO 1000 Hz (saves everything), power cycle, read-only read-back: **bench drive
    flash = node 1, bit rate 1000, receiving 1000 / Timer ISR 1000 Hz, telemetry 9, Speed layout, FOC Kp 2.25,
    FOC T 1.671 ms**, no fault, 24 V.
  - **Nyquist plot (2026-09-25, user: "now we will move towards nyquist plot / can you check whether can we
    implement that", then "build the nyquist plot").** Answered first with numbers: the two saved real sweeps
    converted offline gave the current loop as a pure integrator (phase -90 deg, 93-94 deg phase margin, no phase
    crossover) - exactly Kp/(sL). Built: `bode.open_loop` (L = T/(1-T)), `bode.margins` (`Margins` namedtuple:
    phase margin at the gain crossover, gain margin at the phase crossover, log-interpolated, unwrapped phase
    shifted so the bulk sits in (-360, 0]), `QUALITY_FLOOR` moved into bode.py (the page aliases it), CSV gains
    open-loop columns; the page's `_make_nyquist()` (aspect-locked, unit circle, -1, crossover diamond, view fixed
    to `NYQUIST_VIEW` = 2.5), `_redraw` feeds it, `_summary` states the margins, Save Image stacks three plots.
    Assumption stated on the page/manual: unity feedback with the reference at the summing point (a feed-forward
    feature would bend it). Tests: the lag (integrator, 90 deg, None gain margin, hollow points ignored), a
    three-lag loop with a real phase crossover matched to L computed directly, the GUI sweep test reads ~90 deg.
    First live render showed two layout faults, fixed: the aspect lock stretched the wide widget so the unit
    circle sat small at the left - the panel is now a fixed `NYQUIST_PX` = 460 square in its own row; and
    `stack_pixmaps` filled transparent, which viewers show as white beside the narrower third plot - it fills
    with `theme.BG` now. Live on the bench (Kp 2.25): stepped sine to 256 Hz and the chirp both show the
    integrator down the negative imaginary axis with |L| still above 1 at the top of the range, so the summary
    says "no gain crossover to read a phase margin at" - honest; at Kp 1.0 (the saved sweep) the crossover is
    at 190 Hz with 94 deg.
    User, from their own instance (a plot of scattered hollow rings - a sweep that did not follow its reference,
    the sensorless speed loop): "there should be line joining all points" - the curve now runs through EVERY
    point in frequency order like the gain/phase plots do, the hollow rings still marking the weak ones (the
    first cut joined only the trustworthy points, so an all-hollow plot had no line at all); the manual says
    what an all-hollow Nyquist means.
    Then "run a sweep on the current loop and show the nyquist plot" (evening): the first attempt hit "USB read
    failed ... multiple access on port?" - nothing held the port on retry (transient), the sweep then ran. At Kp
    2.25 the stepped sine's 256 Hz top leaves |L| = 2.5 everywhere, so the fixed +-2.5 view showed ONE point on
    its edge - the view now widens to 1.3 x the nearest trustworthy |L| (`NYQUIST_VIEW_MAX` 25) when the curve
    never comes inside, so a loop whose crossover is beyond the range still shows where it ends; test checks the
    Y range (the aspect lock trims X to the square panel). The chirp (coherent to ~440 Hz) at this Kp brings L to
    1.56 at best, approaching the unit circle from the right (small phase, falling gain) - still no crossover.
    User: "yes my software was connected" - so two processes shared the port and both read fragments.
    `usb.py` now opens the port with pyserial's `exclusive=True` (flock): the second open is refused with "... is
    already open in another program - another instance of this tool, or the vendor IDE? Disconnect there first",
    and the first keeps working. Tests: the flag is passed and the message is right (a fake Serial raising the
    lock error), and the lock is real on a pty of this machine (skips where a pty cannot be locked). Verified on
    the real device (user: "go ahed if you want to test it my instance is closed now"): a raw second
    `UsbSerialBus` open was refused while the first read the drive, and end to end with two real app instances
    (`real_bode.py hold 25` kept the port, a second instance's Connect got the dialog "Cannot open the CAN
    interface ... is already open in another program - another instance of this tool, or the vendor IDE?
    Disconnect there first." and the first disconnected normally afterwards). Rule for me: `lsof
    /dev/cu.usbmodem*` before a real-drive run, and ask the user to disconnect theirs (memory).
  - **The mouse wheel over a plot no longer stops the page from scrolling (2026-09-25, user's screenshot of the
    Bode plots' frequency-axis rows: "if you keep mouse pointer then scroll up and down will not work").**
    pyqtgraph's AxisItem and ViewBox accept every wheel event even when `setMouseEnabled(False, False)` is set,
    so a plot inside a QScrollArea page swallowed the wheel wherever the pointer sat over it. `theme.style_plot`
    now installs `_WheelToPage` (an event filter) on the PlotWidget AND its viewport: a Wheel event over a plot
    whose ViewBox has no mouse enabled is `ignore()`d and reported filtered - Qt's notify then walks up to the
    parent, and the page scrolls; a plot that re-enabled the mouse (Recorder, 2D Plotter) keeps the wheel for
    zooming. Every styled plot gets it (Bode, Nyquist, the Motion page's live telemetry). The exact culprit
    (pyqtgraph 0.14 `AxisItem.wheelEvent`): it forwards the wheel to the ViewBox and then calls `event.accept()`
    unconditionally, while `ViewBox.wheelEvent` itself ignores the event when the mouse is off - so the AXIS rows
    swallowed the wheel and the plot area did not (exactly the user's red-marked areas). **Testing gotcha:**
    `QApplication.sendEvent(child, QWheelEvent)` does NOT walk up to the parents (measured: a wheel sent to a
    plain child never reached its QScrollArea), so the unit test checks the flag Qt propagates on -
    `event.isAccepted()` False after the styled plot, True (and a zoom) after a zooming one. The real behaviour
    was verified in the real app with GENUINE OS scroll events (`scratchpad/real_wheel.py`: Quartz
    `CGEventCreateScrollWheelEvent` posted at the plot's global position, the pointer warped there first, the
    plot scrolled into the page's visible viewport first - positions outside the window silently reach nothing):
    an app-wide event filter showed the wheel arriving at the PlotWidget's viewport (spontaneous), then walking
    PlotWidget -> body -> the page's QScrollArea viewport -> its QScrollBar, and the page scrolled from the gain
    plot area and from the phase plot's axis row; over the Nyquist plot the page was already at its bottom and
    the event walked up to the MainWindow, as it should.
  - **Phase-margin floor as a Beta stopping rule (2026-09-25, user: "add phase margin floor as beta stopping
    rule").** `autotest.Measurement` gained `margins` (`bode.margins` of the chirp points, computed in
    `measure_loop`); `_limits_hit(m)` gathers every reason a measurement is not clean - peaking, overshoot, phase
    margin < `PHASE_MARGIN_FLOOR_DEG` (45), gain margin < `GAIN_MARGIN_FLOOR_DB` (6) - and `propose` uses it for
    both loops; a margin whose crossing is outside the range is None and does not count (the "as fast as the
    sampling can verify" rule already covers that case). `measurement_checks` adds a "<loop> margins" row (ok /
    warn "below the floor" / skip "crossover beyond the range"); the page's plan text and per-measurement log line
    state them. Tests: the floor backs off to the last clean gain with the reason in the proposal, an unreadable
    margin leaves the raise alone, the three table states. Not exercised on the bench - the current loop's margin
    is 93 deg, and at Kp 2.25 the crossover is beyond the range; it is for the speed loop with the encoder.
  - **Both Bode plots on screen at once (2026-09-25, user's screenshot of the gain plot alone filling the page: "make
    size of both graph small so it can fit in one screen so user can see both graph at the same time without scroll
    up or down").** A PlotWidget's size hint is its own current size (QGraphicsView reports its scene rect, which
    pyqtgraph sets to the viewport on every resize), so with only a minimum height the two plots kept whatever the
    first layout pass gave them - ~480 px each on the user's 756 px page. `FrequencyResponsePage._fit_plots` (from
    `resizeEvent`) gives each half the viewport's height, clamped to `PLOT_MIN_PX`..`PLOT_MAX_PX` (190..320), as a
    FIXED height. **Then, user: "nyquist plot is not visible in screen"** - the Nyquist square (460 px, below the
    pair) needed a scroll of its own, so `self.plots` became a row: the Bode pair stacked on the left, the Nyquist
    beside it on the right, a square as tall as the pair, capped at `NYQUIST_WIDTH_SHARE` (0.4) of the page's width
    and never under `NYQUIST_MIN_PX` (260) - all three plots on screen together; Save Image still stacks the three
    vertically. Test resizes the window to 1920x1049 /
    Also from the user's look at a real sweep: "nyquist plot that circle is in white colour which is rarely visible make
    it in red colour" - the dashed unit circle was `theme.LINE` (the hairline colour, near-background); it is `theme.RUBY`
    now, like the -1 marker (an existing accent, no palette change). Test reads the circle item's pen.
  - **Zoom on the Bode and Nyquist plots (2026-09-25, user: "make sure we can zoom the graph as we make graph size
    small to fit in one screen").** The plain wheel has to keep scrolling the page (the fix above), so the zoom needs
    Ctrl - Cmd on a Mac, Qt maps it to ControlModifier - or a trackpad pinch: `theme.enable_zoom(plot, reset)` turns
    the ViewBox mouse on (drag pans, right-drag zooms) and tags the widget; `theme._PlotMouse` (the wheel filter,
    renamed) ignores an unmodified wheel over such a plot (the page scrolls), lets a Ctrl-wheel through to pyqtgraph's
    own zoom, scales the ViewBox itself on a `QNativeGestureEvent` zoom (pyqtgraph does not handle pinches) around the
    pointer, and calls `reset` on a double-click. The Bode plots reset to auto-range, the Nyquist to
    `_fit_nyquist_view()` (its adaptive +-NYQUIST_VIEW box, extracted from `_redraw`); `start()` resets all three
    (`_reset_views`) so a zoom left from the last look never hides a new sweep's points; a hint line under the plots
    says how. Tests: a standalone zoomable plot (plain wheel unaccepted and unzoomed, Ctrl-wheel zooms, a synthetic
    pinch narrows the view, a double-click resets) and the page's reset at a sweep's start.
  - **Bode cursors (2026-09-25, user: "should we give cursor for more analysis in gain and phase both graph and sync"
    -> "build the cursors").** CURSORS button on the Frequency Response page: A and B, each a movable
    `pg.InfiniteLine` on the gain AND the phase plot (positions are log10(f) - the plots are in log mode), the
    Recorder's own `CURSOR_COLOURS`. `sigDragged` mirrors a line to its twin and the readout tracks the nearest point;
    `sigPositionChangeFinished` snaps both to that point (the response exists only at the measured frequencies) -
    signals blocked while a twin is set, so no ping-pong. A cursor remembers a FREQUENCY (`_cursor_hz`), not an index,
    so it stays put across sweeps (compare two sweeps at one point); untouched, A/B sit a third / two thirds along.
    `cursor_table` (the Recorder's table style): per cursor the frequency, gain, phase, quality, |L| and ∠L of the open
    loop; B − A differences and the gain slope in dB/decade; `nyquist_marks`/`nyquist_labels` mark the same two points
    on the Nyquist plot. `_redraw` re-places them, so they follow a sweep as it fills in. Save Data… appends a
    `cursor` column (A/B on their rows, empty otherwise - the header grows by one, pure `bode.csv_rows` unchanged).
    Tests: sync/snap/readout/slope/Nyquist marks on an exact first-order lag (`bode_lag_points` helper), and the CSV column.
    **Verified live** (`scratchpad/real_cursors.py`: the real app on the Simulator with the evening's REAL Iq sweep loaded
    from its CSV, CURSORS clicked, B dragged on the PHASE plot with a genuine Quartz mouse drag): B went from 28.4 Hz to
    the measured 21.3 Hz nearest the drop, the gain plot's twin followed and snapped identically, the view did not pan,
    the table read both points. **Automation lesson**: a Quartz drag posted from the Qt thread (with sleeps between the
    events) never registered - the app's loop was blocked, so macOS coalesced the queued drags and Qt saw no drag at all;
    posting the down/dragged/up sequence from a helper THREAD while the loop runs delivered all 15 events. Clicks and
    wheels tolerate the blocked loop, a drag does not.
  - **The full-contour Nyquist plot (2026-09-25, user, a textbook picture of G = 1/(s^2+s+1): "this is nyquist plot
    while our looks different i am confused" -> explained (theirs: a formula's G(jω) from 0 to ∞ with its mirror; ours:
    the measured OPEN loop L over the measured band only, an integrator whose start is off-screen and whose end never
    reaches the origin) -> "add both the mirror half and the closed loop T toggle but don't remove current one just add
    both in different graph below").** A second square plot (`_make_contour`, in a card below the summary, sized with
    the Nyquist plot above by `_fit_plots`): the measured half with symbols, its mirror (conjugate, dashed sapphire),
    a legend naming the two, the unit circle, the -1 point visible for L only; a **Show** ComboBox (`CONTOUR_VIEWS`)
    picks the closed loop T as measured (`cmath.rect` of each point - default, the textbook shape from (1, 0) toward
    the origin, view +-CONTOUR_VIEW_T = 1.5 grown for peaking) or the open loop L (`bode.open_loop`, the adaptive
    view shared with the plot above through `_open_loop_view`). `_redraw_contour` from `_redraw` and the combo;
    cursors A/B mark their points on it too (`contour_marks`/`contour_labels`, filled by `_read_cursors`), zoom via
    `theme.enable_zoom` with `_fit_contour_view` as the reset, `_reset_views` refits it at a sweep's start, Save Image
    stacks four plots. Test on the exact first-order lag: T's shape, the conjugate mirror, the -1 visibility and view
    per mode, the marks following the toggle, the four-plot PNG height.
  - **Both cursor tables were 7 px short on the real screen (2026-09-25, user's red box around the Bode cursor table:
    "some data ... is missing now")**: `28 + 24 n + 4` assumed a 28 px header, the theme's is 37 px (measured live:
    header 37 + 7 x 24 + 2 = 207 needed, 200 given), so the Slope row hid behind a vertical scroll bar and the scroll bar
    clipped the B - A column. `widgets.fit_table_height(table, rows=None)` sizes a read-out table from the header's own
    size hint, the row count and the frame, scroll bar off - used by the Bode cursor table and the Recorder's (same
    latent fault, same fix). Tests on both: height >= the measured need, no scroll bar.
  - **Automatic Test moved into Advanced Tuning (2026-09-25, user, a screenshot of the Servo Drive list: "put automatic
    test under advanced tunning below frequency response").** `TunerPage(advanced=True)` builds `self.auto_test =
    AutomaticTestPage(session)` (imported inside __init__ like bode_page - autotest_page imports `card` from tuner.py)
    as the last Verification page after Frequency Response, forwards its `message`, and `TunerPage.emergency()` calls
    its `emergency()` - so the round STOP reaches it for the primary AND every axis through the existing
    `stop_requested` -> `_stop_tuners` wiring, and each axis's own Advanced Tuning carries its own copy (the per-axis
    `"auto_test"` page kind is gone). `main_window.py` no longer builds `auto_test_page`, registers no `auto_test`
    tool (Servo Drive list, `PRIMARY_TOOL_KEY`, per-axis loop); the ribbon's Tuning > Automatic Test button stays and
    opens Advanced Tuning on that page (`_open_automatic_test`). Tests: `auto_test_ready` goes through the tuner; the
    registration test asserts the placement (index of Frequency Response + 1), the ribbon shortcut, and an axis's own
    copy bound to its own AxisContext; the pinned Servo Drive tool list and a list-index click updated.
  - **Automatic Tuning's Datasheet polling re-timed, and the poll counter gone from the status line (2026-09-26, user,
    a crop of "· Datasheet checked 13 times": "we don't need to show how many times you checked the data sheet so
    remove this line / and first check data sheet once user click on start automatic tunning then wait for 20 sec
    before start continuous data sheet checking at every 4 sec").** The once-at-START read is the existing 'before'
    snapshot (`start()` -> `_prepared`); `FIRST_POLL_S` = 20 s sets `_next_poll` at the command, `POLL_S` = 4 s
    thereafter (`_next_poll` replaces `_last_poll`); `_progress_text` shows only the head and the elapsed seconds.
    The Automatic Test engine's `run_autoconfiguration` (its own copy of the loop) follows the same timing. Tests run
    with `FIRST_POLL_S` = 1 s through an autouse fixture (else every tuning test would sit 20 s idle); one test marked
    `real_poll_timing` keeps the real 20 s and proves nothing is read before it; the 4 s spacing test replaces the 3 s one.
  - **The app icon rebuilt to macOS's Dock geometry, and committed for the first time (2026-09-26, user: "can you
    check we have added company logo / make sure logo match with other icons in mac dock and also icon look like 3
    dimensional").** `sdr_tuner/data/app_icon.png` was loaded by `app.py` (window + Dock via `setWindowIcon`) but had
    NEVER been committed (`??` in git status) - a fresh checkout had a blank Dock tile; the old file filled the whole
    1024 canvas with a circular-corner square, so in the Dock it rendered larger than its neighbours and flat. New
    `tools/make_app_icon.py` (Pillow + numpy, both in the venv) builds it from the company emblem, now in the repo as
    `sdr_tuner/data/brand/stimita_emblem.png` (the user's `Stimita_Motion_Control_icon_only.png`): Apple's template -
    a 1024 canvas, the tile an 824 px superellipse of exponent 5 (the continuous-corner "squircle", ~22 % corner
    radius) with a 100 px transparent margin, a drop shadow into the margin; depth from a top-to-bottom tile gradient
    (theme greys), a glossy sheen over the upper third, a rim light on the top edge and a shade on the bottom one; the
    emblem scaled to 560 px (68 % of the tile; 520 until 2026-09-26, "increase logo size ... little bit only") on the optical centre, shaded lighter at the top, with a bevel (upper-left edges lit,
    lower-right shaded) and its own soft shadow onto the tile. `tests/test_app_icon.py` checks the file, the 1024 size,
    the opaque bbox at (100, 100, 924, 924) +-3, a clear margin corner, the shadow spilling past the tile, the gradient
    and the emblem's red. Verified by capturing the real Dock with the app running (`screencapture` of the Dock strip).
    **Then, user: "change background from gray to something else where logo looks more luxury and distinguish ... you
    can also change logo colour as well"** - the generator gained `TILE_STYLE` ("graphite" = the grey above; "onyx" =
    near-black with a faint warm gold glow behind the emblem), `EMBLEM_COLOUR` ("red" as drawn; "gold" = `recolour_gold`:
    the red mapped onto a brushed diagonal gold sheet `metallic_gold` from GOLD_HI through GOLD, a sheen band, to
    GOLD_LO, the white gears to theme.TEXT ivory) and `BEZEL` (an 11 px brushed-gold band just inside the tile's edge
    with a dark inner lip). Four variants were rendered onto a slice of the REAL Dock capture and sent; shipped: onyx +
    the company's own red + the gold bezel (brand colour kept, the app's black + gold, the most distinct tile in the
    Dock); the all-gold emblem is one constant away. The screen is 2560x1440 points (not the 1920x1080 of earlier
    notes): the Dock strip is `screencapture -x -R 0,1320,2560,120` with autohide off.
  - **Why the Dock showed the Python rocket at quit, and the app bundle that fixes it (2026-09-26, user's screen
    recording of the Dock while closing: "while closing the application why our logo goes and old logo appears again").**
    Frames of the recording (19 fps, `select='gt(scene,0.003)'`) showed the rocket at full tile size for ~150 ms before
    the tile shrank away. Measured with a Dock-tile sampler (`screencapture -R` 8x/s, `scratchpad/dock_watch.py`) around
    an in-process auto-close (`scratchpad/quit_driver*.py`, `MainWindow.close()` from a timer - the user's own path):
    the tile kept OUR icon through the window close (t), the event loop's return (t+0.05 s) and the interpreter's
    teardown (t+0.7 s); the rocket appeared only AFTER the process had exited, as the Dock's own removal animation.
    Nothing in the process changes that: leaving the Dock (`NSApplication.setActivationPolicy_(2)`, PyObjC is in the
    venv) after exec returned did nothing (no loop to deliver it), and doing it from inside the loop on `aboutToQuit`
    removed the tile early but STILL under the rocket - the Dock draws a tile's removal with the icon of the process's
    own bundle, and `.venv/bin/python` resolves into the framework's Python.app. Fix: `tools/make_app_bundle.py`
    builds `dist/SDR Servo Tuning Tool.app` (Info.plist with CFBundleIconFile, `Resources/app.icns` from the new
    `write_icns` in `tools/make_app_icon.py` - an iconset packed by `iconutil` -, a bash launcher that `exec`s the
    project's venv python on main.py, so it stays the one PID the Dock launched); `dist/` is git-ignored (the launcher
    holds this machine's paths), the `.icns` is committed next to the PNG. Verified the same way: launched with `open`,
    the tile shrank away showing OUR icon. A PyCharm/terminal run keeps the rocket at quit - it is the interpreter's
    bundle; the manual says so (§2 + a troubleshooting row). Also learnt: `osascript ... quit` on the bundle gave
    "User cancelled (-128)" after ~35 s (an automation consent the terminal never gets) - close from inside instead.
  - **The rocket resolved for PyCharm/terminal runs too (2026-09-26, user, a recording of the quit from PyCharm: "now
    you see rocket icon so can you resolve it").** The Dock identifies a process by the bundle its EXECUTABLE lives in;
    `.venv/bin/python` is a stub that execs Python.app/Contents/MacOS/Python (`_NSGetExecutablePath` says so), hence the
    rocket. Proven first with a throwaway bundle: a COPY of that binary inside our .app, launched bare from a shell (no
    LaunchServices, exactly as PyCharm launches), and the Dock sampler showed our tile shrinking away, no rocket. Built
    in: `sdr_tuner/dock_identity.py` (no Qt) - `real_executable()`, `should_relaunch()` (darwin, not inside the bundle,
    `SDR_NO_RELAUNCH` unset, not under pytest), `ensure_bundle()` (builds via tools/make_app_bundle when the
    `interpreter.stamp` - the source binary's path/size/mtime - or the icon is stale), `relaunch()` (execve into
    `Contents/MacOS/SDR Servo Tuning Tool Python` on main.py with the venv's site-packages on PYTHONPATH - same PID, so
    PyCharm's Run/Stop and console keep working); `main.py` calls `relaunch_in_bundle_if_needed()` before importing Qt
    and falls back to the bare run with a printed reason. `tools/make_app_bundle.py` copies the binary (it links the
    framework by absolute path, so it runs anywhere), writes the launcher (`exec` of that binary, PYTHONPATH,
    SDR_NO_RELAUNCH=1) and SIGNS: first the interpreter copy, then the bundle, ad hoc - a bundle signature covers only
    the main executable (the launcher script), and the copy still carrying Python.org's hardened-runtime signature is
    KILLED (SIGKILL, exit 137) outside Python.app; `codesign` on the nested file directly is fine, on the bundle alone it
    is not. The copy cannot be compared with the source by size (signing rewrites it) - hence the stamp. Verified with
    the sampler on both paths: `.venv/bin/python main.py --sim` (the process's executable became the bundle's
    interpreter, our tile through the quit) and `open` of the bundle (same); the launch bounce moves the tile out of a
    fixed sample block for a few frames - `light` stays 0.00, that is the rocket's signature. `tests/test_dock_identity.py`
    (5, all with the real exec mocked) + the bundle test now checks the copy, its stamp, `bundle_is_current` and
    `codesign --verify`. `sys.executable` is the bundle's interpreter in a relaunched run - nothing in the package spawns
    python, checked.
  - **Drive Modes page: the special modes moved off Motion - Single Axis (2026-09-26, user: "keep velocity and position
    control in motion single axis, rest of the command parameters like kickstart or torque control, direct field control
    etc ... move somewhere else so we can keep motion single axis simple" -> "build it, move all of them including torque
    control").** New `gui/drive_modes.py` (`DriveModesPage(session, motion)`): Current / Torque, Position Control ·
    Cyclic, Direct Field Control, Kickstart, Power Generation, Brushed: Open Loop, Virtual Brake (its own card now, ENGAGE)
    - the card builders and run methods moved verbatim from drive_tab.py, which keeps Speed Control (RUN/STOP/OFF/RESET/
    ENABLE/DISABLE) and Position Control (MOVE TO/BY, SET HOME HERE) plus the readouts/LEDs/plots. Per drive like every
    Servo Drive page (tool key `drive_modes`, `PRIMARY_TOOL_KEY["modes"]`, axis kind "modes", listed right after Motion -
    Single Axis, ribbon Drive Modes button, glyph `sliders`). **The one shared thing is the client's single repeat slot**:
    `DriveTab.repeat_changed` (emitted by `_repeat_status`/`_clear_repeat_status`) clears the modes page's own note, and
    `DriveTab.external_repeat_started()` (called by the modes page before it sets its note) clears the motion page's note
    and its RUN/hold bookkeeping - so the "· sent every 100 ms" note always sits on exactly one card across both pages.
    `DriveTab.modes` (set by the window, primary and per axis) forwards a G-code torque line's mirroring
    (`note_gcode(kind="torque")` -> `DriveModesPage.mirror_torque`). The modes page's readout row reuses the motion page's
    own formatting (`_title`, `_card_text`) and layout knowledge (`cards`, `layout_changed`) - nothing extra is requested;
    it ticks only while visible. Its STOP is `motion.emergency_stop` (so tuners are told too). Tests: the moved tests
    address `win.drive_modes_page` (readout assertions stay on `win.drive_tab.cards`), three new ones (placement + per-axis
    binding, the slot changing hands both ways, readouts + the page's STOP). Gotcha met: `DriveTab._title(key)` KeyErrors
    for a key the drive's layout lacks - the modes page names its readouts from CARD_TITLES until a layout is known.
  - **The live plots on Drive Modes too, drawn only on the page on screen (2026-09-26, user: "show this live telemetry
    in that new page as well - just make sure which page is opened telemetry will show in that page only so software
    will not get much load").** The LIVE TELEMETRY card moved out of drive_tab.py into `gui/live_plots.py`
    (`LivePlots(with_speed_unit)`: the four linked plots, window/Freeze, `build_curves(card_keys, card_info, title_of)`,
    `set_speed_unit`, `set_live(age)`, `update(client, card_info, speed_unit)`, `clear()`; PLOTS/CURVES/SPEED_UNITS/
    UNIT_LABELS/UNIT_MIN_SPAN/STALE_AFTER/speed_shown live there now and are re-exported by drive_tab.py). The motion
    page keeps the Speed-in chooser (it writes the drive's mapping) and exposes `_curves`/`_plot_widgets`/`_curve_rows`/
    `_window_s` as properties over its card, so the ~15 tests that read them are unchanged; `plot_unit` is the unit the
    speed plot is drawn in. Drive Modes builds its own card from the motion page's layout on `layout_changed` (and once
    at construction - the first layout was applied before it existed). **The load rule**: each page's tick calls
    `plots.update` only behind `self.isVisible()`, so exactly one card redraws at a time (the readouts on Drive Modes
    already did this) - which is why every test that READS curve data now opens the page first (`open_tool`); one new
    test proves the hidden page's curves stand still while the shown one's grow, both ways, and that the Drive Modes
    card has no unit chooser.
  - **CONTROL MODE chooser on Motion - Single Axis (2026-09-26, user, a screenshot of the two cards: "give drop down from
    which user can select position mode or velocity mode instead of two you are showing at the same time, as only one of
    them can be used at a time").** `DriveTab._mode_row()` (a ComboBox `control_mode`, data "velocity"/"position") shows
    `velocity_box` (Speed Control: target, slider, limit, RUN) or `position_box` (MOVE TO/BY, SET HOME HERE) -
    `_sync_control_mode`; STOP / OFF / RESET / ENABLE / DISABLE and `self.status` moved to a new always-visible DRIVE card
    (`_drive_card`), since they are mode-independent (STOP must never hide). Switching the chooser sends nothing - the
    client already runs one command at a time; the chooser only decides what is shown. The ready text is now "Ready. Pick
    a control mode, set a target and press RUN or MOVE." **Test trap, again**: `qtbot.mouseClick` on a button inside the
    hidden card is silently a no-op, so every test that clicks MOVE TO/BY/SET HOME HERE selects Position first
    (`dt.control_mode.setCurrentIndex(1)`) and back to 0 before a RUN click; the new test checks both cards' visibility,
    the DRIVE card in both modes, and that switching sends nothing.
  - **The target speed typed in rpm, rps or °/s (2026-09-26, user: "give an option to user that user can enter velocity
    value in rpm or rps or degree per sec").** `ENTRY_UNITS` (label, factor from rpm, decimals, single step, slider steps
    per unit) and `DriveTab.entry_unit` (a ComboBox beside TARGET SPEED); `self.target` and `self.limit` hold the SHOWN
    unit, `_to_rpm`/`_from_rpm`/`limit_rpm()` convert, `_on_entry_unit_changed` converts both values in place (nothing
    sent), `_sync_ranges`/`_from_spin`/`_from_slider` use the unit's slider scale (1000 per rps, 10 otherwise), `_run`
    sends `_to_rpm(target)` and says "Commanding 1.500 rps = 90.0 rpm", `_set_speed_target_quietly(rpm)` and
    `note_gcode`'s limit check convert too. Pure mechanical conversions (rpm/60, rpm x 6) - the standing rule is about
    the POLE COUNT, which this never touches. Separate from the plot's Speed-in chooser (what the drive's telemetry is
    shown in, which writes the mapping). The 24 existing tests type in the rpm default and are unchanged; one new test.
  - **"deg/sec" written out, and deg/sec on the plot's Speed-in chooser too (2026-09-26, user: "write deg/sec ... and
    also add deg/sec in graph as well").** ENTRY_UNITS' third label is "deg/sec" (was the ° symbol). `live_plots.SPEED_UNITS`
    gained ("degs", "deg/sec") between rps and Hz, with `RPS_UNITS = ("rps", "rpm", "degs")` naming the units that come
    from the drive's Work Zone: Speed (rps) - `speed_shown` maps rps -> degs x 360 (and a drive sending rpm -> x 6);
    drive_tab's UNIT_FORMATS/UNIT_SOURCE/`_rps_unit` bookkeeping use RPS_UNITS instead of the old ("rps", "rpm") pair, so
    choosing deg/sec on an Hz-sending drive switches the mapping to rps exactly as rpm does. The pinned choices test
    reads ["rpm", "rps", "deg/sec", "Hz"]; a new test drives 120 rpm, chooses deg/sec and reads 720 on the card, the
    motion plot's axis and Drive Modes' plot axis.
  - **Positions in counts or degrees (2026-09-26, user: "add position as count and degree both" + "also show option for
    deg and count in graph as well").** NOT a pure conversion: degrees need counts per turn, and the drive has it as
    `Work Zone: Dimension` (0x4000:0x1C, telemetry; config twin 0x2034:0xC2 with a Customized feature flag; the
    encoder peripherals each have their own `counts per revolution` too). One page-wide `_pos_unit_key` on DriveTab,
    two mirrored choosers (`pos_unit` beside TARGET POSITION, `LivePlots.position_unit` on the motion page's plot card),
    both routed through `_choose_pos_unit(key)`: deg with no factor yet does ONE `client.read` of the dimension via
    run_async (never polled; no drive or a 0 answer -> back to counts with a reason), then `_apply_pos_unit` converts
    `pos_target`/`pos_delta` (DoubleSpinBox now, 0 or 2 decimals) in place, relabels the wz_count/wz_modulo cards,
    calls `plots.set_position_unit(label, factor)` and emits `layout_changed` so Drive Modes' card follows
    (`position_label`/`position_factor` properties). `_pos_counts`/`_pos_shown`/`_pos_text` at the move sites and in
    `_report_move` (G-code moves mirror in the shown unit). `_card_text` shows wz_count/wz_modulo in degrees when
    chosen. A disconnect forgets the factor and returns to counts. `defs.TELEMETRY_KEYS` gained (0x4000, 0x1C) ->
    "wz_dimension" and the simulator serves it from its SSI counts per revolution (65,536), the same number its
    wz_modulo already wraps at. Test: choose deg on the card (one read, 90.00 for 16,384 counts), MOVE TO 45 deg ->
    servo 8,192, the card reads 45.00, both plot axes say deg, choose counts on the PLOT and the card follows.
  - **Copy / paste telemetry mapping codes (2026-09-26, user, after declining an automatic Phase B remap: "if user want
    to change from workzone modulo to phase B or something else then give option to user that he can copy telemetry
    code and paste it in telemetry mapping code").** params_tab.py: `is_mapping_slot(reg)`, `describe_code(code)` (0 =
    unused; the catalog name of the parameter the code stands for), `clipboard_code()`; `tooltip_html(reg, ..., current)`
    adds a telemetry parameter's mapping code (with the copy/paste hint) and, on a Telemetry Mapping slot, "Currently:
    <name>" from `row.last`; the tree has a custom context menu (`_context_menu` -> `menu_for(row)`, returned so tests
    trigger its QActions without exec): a telemetry row copies its code to the clipboard (status message says where
    to paste), a writable mapping slot pastes the clipboard's code (disabled with the reason when the clipboard holds
    no whole number; the action text names the parameter), sets the slot unused, or copies its own code; every row
    copies its address. A paste only edits the row (`row.set` + `_mark_edited`): the WRITE stays the user's - the
    mapping write reloads the layout as before. Test: copy Phase B (5243142) from Telemetry Explorer, paste into
    0x1A02:0x02, Write -> sim.params updated and the ib card replaces wz_modulo; a non-code clipboard disables the
    paste; a telemetry row has no paste. The manual has a "Change what the drive broadcasts" task + quick-index row.
  - **A black rule between the stacked plots (2026-09-26, user: "give black line so user know where each graph is
    separated from each other").** `LivePlots.separators`: a 2 px `QFrame#plotSeparator` (SEPARATOR_PX, background
    #000000) above every plot but the first, hidden by `build_curves` whenever the plot under it is hidden or nothing
    is shown above it - so a no-speed layout has no rule above its first plot. Both pages' cards, since it is the
    shared widget. Test checks count, height, colour and the hide rule.
  - **Axes table: equal column widths (2026-09-26, user's screenshot of the page: "give equal spacing for each
    parameters").** `axes_page.py`: one `setSectionResizeMode(Stretch)` for the whole header replaces the 2026-09-25
    fixed widths (160/60/190/170/130 + Fault Bits stretch), which bunched Node/Status/Position and gave Fault Bits the
    rest. Then ("node and status are very near to each other and speed and fault bit also very close") every column
    went LEFT-aligned - with equal widths a right-aligned number touches the next column's text; the 2026-09-25
    per-column alignment machinery stays, just fed AlignLeft throughout. Then, on a 2000 px window, "don't need that
    much of space between every parameters", then "spacing between last three parameters are not equal compare with
    first two" - equal WIDTHS read as unequal beside a long header ("Position (counts)"): the eye measures the
    blank after the text. `_fit_columns()` (from __init__ and the end of every `_refresh`) sizes each of the first
    five columns to its widest text - `max(header.sectionSizeHint, table.sizeHintForColumn)` - plus `COLUMN_GAP` = 120 (40 first, then 50, 70, 120 - the user chose by eye),
    applied only when it changes; Fault Bits alone Stretches. The user asked to be shown the plan first ("check and
    confirm before doing anything") and approved it. Test: five Fixed columns each exactly widest + gap (after a
    connect, so cells exist), the last Stretch, everything left-aligned.
  - **Multi Axis Motion arranged the same way (2026-09-26, user: "can you do the same here as well and arrange it").**
    `multi_axis_motion.py` imports `COLUMN_GAP` from axes_page (one gap for both tables): Axis/Node/Position are
    widest text + gap (`_fit_columns`, from __init__, after `_refresh_rows` and at the end of `_tick`), Target and MOVE
    are their widget's own width (`TARGET_PX` 220 / `MOVE_PX` 96) + gap - a cell widget fills its column, so each is
    wrapped by `_with_gap()` (a QWidget whose layout has the gap as its right margin and the widget at a fixed width);
    Status alone Stretches; everything left-aligned (the 2026-09-25 right-aligned numbers went - beside a left-aligned
    neighbour they read as touching). Test: the three text columns exactly widest + gap, the widget columns px + gap,
    the widgets at their own width, all centred. **Then ("1 should be in the middle of node is written, 0 should be in
    the middle of position is written, do same for both images and every parameters")**: both tables centre every
    header and every value (AlignHCenter; the Target field and MOVE button centred in their columns by `_with_gap`'s
    stretches), and the last column (Fault Bits / Status) is sized by the same widest + gap rule instead of
    stretching, so it is centred too. **Then ("make sure yellow line reaches till end ... only shift status from its
    position")**: the header's gold rule is the sections' own underline, so it ended where the last Fixed column did -
    the last column (Status / Fault Bits) Stretches again, which carries the rule to the table's edge and, with its
    content centred, moves Status further from MOVE while MOVE stays put. Tests: the last column Stretch, the others
    widest + gap, all centred. The MOVE column's header reads "Command" (was blank; user: "write command above move button").
  - **Additional Axes, Kinematics Model and Tool Pose moved from Item Configuration to the Axes page (2026-09-26,
    user: "move three parameters ... from system configuration to motion / axis (on top) and show axis and other
    parameters below it").** New `gui/axes_setup.py`: `AxesSetupPanel(session, drive_tab_for, connection_of)` holds
    the three PropertyGroups with their code moved verbatim (the GcodeRunner for Tool Pose GO, the axis rows, the
    counts-per-unit rows, `kinematics_changed` refresh); `connection_of()` returns the primary's (channel, bit rate)
    from Item Configuration's fields, since `_add_axis` used to read them off its own page. `AxesStatusPage(session,
    primary_name, drive_tab_for, connection_of)` puts the panel above its note and table inside one QScrollArea (the
    table keeps a 220 px minimum) and forwards the panel's `message`; the window wires `axes_page.message` and cancels
    `axes_page.setup.runner` on STOP (was `config_page.runner`). `ItemConfigurationPage(session)` lost the groups, the
    runner and its `drive_tab_for` argument; it keeps General, Target Connection, Product Activation, Networks. Tests
    that reached the groups through `win.config_page` now use `win.axes_page.setup` (18 spots); the reflow test opens
    the Axes page first (a hidden page's geometry does not move). Manual quick-index rows and task headings, README
    mentions re-pointed. `config_page.py` still owns `KINEMATICS_SAMPLES_DIR`/`KINEMATICS_FILE_FILTER` and
    `PropertyGroup` (axes_setup imports them; config_page must never import axes_setup - a cycle). Then ("rearrange
    tool pose in red marked area" - the right half): the panel is two equal columns, Additional Axes over Kinematics
    Model on the left, Tool Pose on the right, each column with a stretch below its groups. **Bug found by the user
    right after** ("open kinematic model of radar 3 times ... adding 3 time"): `_rebuild_counts_per_unit` removed the
    SPIN BOXES it tracked and left each row's frame (with its label) in the layout - an old bug that moved with the
    code; `_counts_per_unit_frames` now tracks the rows and the rebuild removes those. Test loads radar three times.
  - **The primary named "Drive01 (SDR)" in the tables too (2026-09-26, user: "drive 01 instead of drive 01 sdr" -
    read first as "drop the suffix from the others", corrected at once: "add not drop", "add to Drive 01 SDR").**
    `axes_page.drive_label` returns `f"{name} (SDR)"` for EVERY node - the primary's own Target Name with the suffix,
    `DriveNN` with it for the others - so the Axes, 2D Plotter, Multi Axis Motion and Status Monitor rows all read
    the way the navigator's tree names a drive; the Additional Axes rows and the combo fallbacks keep "DriveNN (SDR)".
  - **2D Plotter with one axis (2026-09-26, user: "if i run g code from motion controller and check 2d plotter then i
    am not seeing anything").** Reproduced on the simulator: one drive -> `_y_node` stays None (the default pick needs
    two axes), the tick said "Pick two connected axes", but the Y combo showed Drive01 (a QComboBox selects its first
    item) - so it looked picked. `_refresh_axis_options` now inserts `PICK_PLACEHOLDER` ("— pick an axis —", data None)
    and selects it while a pick is None; `_tick` says `ONE_AXIS_TEXT` (needs two axes; add one under Motion → Axes →
    Additional Axes; watch one axis on Motion - Single Axis or the Recorder) when fewer than two axes are connected;
    the plot's axis labels fall back to "X axis"/"Y axis" for a placeholder. The plotter itself is unchanged: it
    traces one axis's Work Zone Count against another's, by design (the vendor's 2D Plotter does the same).
  - **Ivory tile (2026-09-26, user: "black background in red logo is not properly visible can you change to something
    else").** The emblem's left leaf is red LINE ART on transparent, so on the onyx tile its circuit traces read as dark
    and vanished. Rendered ivory + red + gold bezel, champagne + red, plain ivory and the onyx one on the Dock strip:
    shipped ivory + red + gold bezel (`TILES["ivory"]`, a warm gradient in theme.TEXT's family; `"champagne"` added
    too) - the logo as the company draws it, red on light, framed in gold; every line legible at Dock size. The icon
    test's gradient/red/geometry checks hold unchanged.
  - **White emblem on dark grey (2026-09-26, user: "can you make logo white and dark gray colour background").**
    `TILES["slate"]` (66,68,76 -> 30,31,36) and `EMBLEM_COLOUR = "white"` (`recolour_white`: the red -> WHITE, and the
    gears - white in the company's drawing - -> `GEAR_GREY` so they stay visible on the white leaf, a cut-out look);
    a soft silver fall-off (1.0 -> 0.86) instead of the red's shade. Rendered with/without the gold bezel and on the
    darker graphite; shipped slate + white + bezel. `tests/test_app_icon.py` now reads the generator's EMBLEM_COLOUR
    and checks the leaf pixel accordingly (red-dominant / gold / bright-neutral) plus a 60-lightness step off the tile.
  - **The user's own colour, #582040 (2026-09-26: "how about logo colour should be 582040").** `EMBLEM_COLOUR` now
    accepts any "#RRGGBB" (`recolour_flat` + `hex_rgb`: the red -> that colour, the white gears stay white; a stronger
    top light, 1.25 -> 0.85, since a dark colour needs it). Rendered on slate, ivory and champagne against the Dock:
    on the dark grey the plum went muted and the left leaf's lines faded (the red-on-black problem again), so shipped
    ivory + #582040 + the gold bezel; slate + plum is one constant away. The icon test checks a hex colour's leaf
    pixel within 0.8x..1.3x of the asked channels (the top light brightens it).
  - **A colour run through the emblem (2026-09-26, user: "can you make a mix of colour in logo to look eye catching").**
    `EMBLEM_COLOUR = "mix-<name>"` with `MIXES` (diagonal / vertical / sunset / split) and `recolour_mix` +
    `gradient_colours` (the red mapped onto a per-pixel run: u = a diagonal, the height, or a soft seam at the middle
    for the two-tone split; the white gears stay white). Six renders against the Dock (three on ivory, three on the
    dark tiles): shipped ivory + "sunset" (PLUM #582040 at the base -> MAGENTA -> BRAND_RED -> GOLD_HI at the tips) inside
    the gold bezel - the most vivid with every line legible; the same run on onyx is dramatic but its plum base sinks
    into the dark (the recurring lesson: dark-on-dark loses the left leaf's lines). Constants moved so GOLD_* are
    defined before MIXES uses them. The icon test's "mix-" branch checks a red-family pixel at the leaf's height.
  - **A free hand (2026-09-26, user: "use your own colour which every you like" / "no limitation on colour or its
    shades just do your best").** Shipped this file's own design: `TILES["midnight"]` (38,40,60 -> 7,8,14, a night sky),
    `EMBLEM_COLOUR = "mix-electric"` (`ELECTRIC_LEFT` teal -> cyan over the circuit leaf's height, `ELECTRIC_RIGHT`
    magenta -> coral -> amber over the gear leaf's, blended at a soft seam at the middle - cool tech against warm
    motion), the gold bezel, and a new **glow** (`build(glow=None)` -> on for every tile in `DARK_TILES`): the placed
    emblem blurred 18 px at 70 % alpha composited UNDER it, which is what finally keeps fine lines legible on a dark
    tile (the lesson of the red-on-black and plum-on-grey rounds). Also `MIXES["aurora"]`. Six candidates rendered
    against the Dock (electric/aurora on midnight, electric on ivory/onyx, without the bezel, the sunset before); the
    midnight electric one shipped. The icon test passes unchanged (a warm pixel in the gear leaf, the tile gradient).
  - **The user's "Plum Regent" palette (2026-09-26, a palette card: "try this colour combination" - Royal Plum
    #4A294B, Deep Wine #5F2147, Cream #F7F2EB, Gold #C7A05A, Warm Grey #9D9792).** The generator gained the four
    palette tiles (`cream`, `royal_plum`, `deep_wine`, `warm_grey`; the two dark ones in `DARK_TILES` so they get the
    glow), `GOLD_SETS` + `BEZEL_GOLD` (the bezel's brushed metal in the theme's gold or the palette's), `EMBLEM_GEARS`
    + `recolour_gears` (the emblem's white gear parts in a given colour, applied BEFORE the red is recoloured so a
    light emblem keeps its gears), and `MIXES["regent"]` (wine at the base to plum). Six pairings rendered against
    the Dock; shipped the CREAM emblem with GOLD gears on the ROYAL PLUM tile inside the palette-gold bezel - the
    light-on-dark direction keeps every line legible, the recurring lesson applied the other way round.
  - **An "SA" monogram instead of the emblem (2026-09-26, user: "try wine background an remove logo and add two
    latters SA in fancy manner (stylish)").** `EMBLEM_SOURCE = "monogram"`: `render_monogram(text, (file, face), style,
    overlap)` sets each glyph of `MONOGRAM_TEXT` on its own mask with Pillow's ImageFont (macOS's own luxury faces -
    Didot, Bodoni 72, Snell Roundhand, Zapfino live in /System/Library/Fonts/Supplemental as .ttc collections; the face
    index picks Regular/Italic/Bold) so the letters can overlap (`MONOGRAM_OVERLAP`), filled with the brushed-gold sheet,
    flat Cream or gold-then-cream; `build()` scales it to `MONOGRAM_WIDTH` of the tile and runs the same shade / glow /
    bevel / shadow pipeline (`emblem_colour = "as-is"`). Six faces rendered on the deep wine tile against the Dock;
    shipped Didot Italic in brushed gold (stylish and still legible at 67 px - the script faces thin out). The icon
    test's monogram branch counts brushed-gold pixels in the middle band instead of probing the leaf.
  - **Calligraphic (2026-09-26, user: "how about calligraphic").** Script faces lose their hairlines at 67 px, so
    `render_monogram` gained `MONOGRAM_WEIGHT` (`ImageFilter.MaxFilter(2w+1)` on each glyph mask at the 760 px render
    size: every stroke thickened by w px a side) and `build()` `mono_weight` / `mono_width`. Six renders on the wine
    tile against the Dock - Snell Roundhand Black +3, Bold +6, Apple Chancery +4, Zapfino +5, Snell in cream, a
    two-tone Snell; shipped Snell Roundhand Bold +6 in brushed gold (overlap 0.06, width 0.74 of the tile).
  - **The emblem back (2026-09-26, user: "how about replace letters with logo").** Six renders on the wine tile: the
    emblem in the monogram's brushed gold with cream gears, cream with gold gears, a gold-circuit / cream-leaf split
    (`MIXES["regent-split"]`), flat palette gold, the royal-plum one from before, and without the bezel; shipped
    `EMBLEM_SOURCE = "emblem"`, `EMBLEM_COLOUR = "gold"` (the regent gold sheet), `EMBLEM_GEARS = CREAM` on deep wine
    in the gold bezel - the metal the letters wore, now on the mark. The `metallic_gold` sheet follows `BEZEL_GOLD`, so
    the emblem's gold and the bezel's are the same alloy.
  - **"how about 4th logo" (2026-09-26)** = the 4th render of that wine strip: the emblem in the palette's FLAT gold
    (`EMBLEM_COLOUR = "#C7A05A"`, lit at the top by the hex branch's 1.25 -> 0.85 shade) with cream gears, the brushed
    sheet kept for the bezel only. Shipped as such.
  - **Glass cream + burgundy (2026-09-26, user: "change background to glass like cream and logo as burgundy").**
    `TILE_FINISH = "glass"` -> `glass_layers()`: a curved reflection (an ellipse wider than the tile whose lower edge
    arcs across the upper third, white 65 % at the top fading to 10 %), an inner shadow around the inside of the edge
    (thickness) and a bright inner line along the bottom (light caught in the glass); `BURGUNDY` (#7A1E33, a classic
    burgundy - the palette's Deep Wine leans plum) as the emblem's flat colour, white gears, the gold bezel. Six renders
    (Deep Wine vs burgundy, with/without the bezel, gold gears, matte for comparison, a gold/cream split); shipped
    glass cream + burgundy + bezel.
  - **Glass cream + brushed antique gold (2026-09-26, user: "try golden logo").** Six gold renders on the glass cream
    (the palette's brushed gold, the GUI's brighter gold, flat palette gold, a flat antique gold, burgundy gears, no
    bezel). The palette's own gold went soft on the cream at Dock size, so the emblem's brushed sheet now uses a new,
    deeper `GOLD_SETS["antique"]` via `EMBLEM_GOLD` (the bezel keeps its regent gold); the icon test's stand-off check
    (the leaf's lightness 60 points off the tile's) failed the first antique set at 57 and passes the shipped one at 83.
    The emblem is also a little larger since this morning: `EMBLEM_HEIGHT` 560 (was 520).
  - **The software's own gold (2026-09-26, user: "match colour with software gold colour").** `EMBLEM_GOLD` and
    `BEZEL_GOLD` are both "theme" now (theme.GOLD_HI / GOLD / GOLD_LO = #EBCB6B / #D4AF37 / #8F7420, the GUI's palette),
    so the emblem's brushed sheet and the bezel are one gold. The icon test's stand-off check sampled one pixel at the
    lit top of the leaf (59 of the 60 points it wants) against the tile at its bottom edge (inside the glass finish's
    inner shadow); for the brushed gold it now judges the MEDIAN lightness of the emblem's gold pixels against the tile
    just under the emblem (70 points; the palette gold that went soft on the Dock measures 59 the same way, so the
    guard still means something) - the single-pixel probe stays for every flat colour.
  - **The theme gold, exactly (2026-09-26, user: "colour is still different and its huge difference check again").**
    Measured instead of eyeballed: the emblem's rendered gold averaged (219, 187, 86) against theme.GOLD (212, 175, 55) -
    the brushed sheet spends most of its area near GOLD_HI (its sheen band) and the branch's 1.06 top light paled it
    further. Now `EMBLEM_COLOUR = "#D4AF37"` (the flat theme gold) with a new `EMBLEM_SHADE = (1.0, 1.0)` (overrides any
    branch's top-to-bottom light; None = the branch's own), so the median emblem pixel IS (212, 175, 55); `MIXES["theme-
    button"]` (GOLD_HI -> GOLD_LO, the GUI's primary-button gradient) added as an option. The comparison strip carried the
    app's REAL gold widgets rendered offscreen with `theme.apply_theme` (page title, brand label, primary button, slider,
    progress bar, a flat chip) and a measured-mean chip per variant - the way to settle a colour question, not a look.
    Perception note: the same gold reads paler on the cream tile than on the app's black; the onyx/slate renders in the
    strip show it as it looks in the app, one constant away (`TILE_STYLE`).
  - **Glass onyx + the theme gold (2026-09-26, user, pointing at that strip's onyx render: "can you make this logo").**
    `TILE_STYLE = "onyx"` with everything else as just shipped: the glass finish, the theme-gold bezel, the emblem the
    flat theme gold exactly, the dark tile's own glow under the emblem. The Dock tile is the app's own black and gold.
  - **Sharper at Dock size (2026-09-26, user: "logo sharpness can you increase").** The screen is a 1x 2560x1440 panel
    (`system_profiler SPDisplaysDataType`: UI looks like 2560x1440, not Retina) and the Dock tile is 87 px (`defaults read
    com.apple.dock tilesize`), drawn from the 128 px iconset image - the emblem's circuit lines soften there, not at 1024.
    Three constants: `GLOW_BLUR` 8 (was 18 - the wide glow hazed the edges), `EMBLEM_SHARPEN` (2, 100) (an unsharp mask on
    the emblem at the 1024 render, colour and alpha, via `sharpen()`), `ICON_SHARPEN` (1.0, 80) on every iconset size at or
    under `ICON_SHARPEN_MAX_PX` 256 after the LANCZOS downscale (`icon_at()`, used by `write_icns`). Judged on a strip of
    the six treatments at the Dock's OWN sizes (64 / 128 px, magnified NEAREST - never LANCZOS-upscaled, which hides the
    difference). Sources checked: `brand/stimita_emblem.png` is 1024x1055; the website (stimitamc.com) serves the logo as a
    1024x1536 PNG with the name and no SVG (its favicon 404s) - nothing sharper exists to download; a vector from the
    company would let each size be drawn instead of downscaled.
  - **A 2000 px source (2026-09-26, user: "i hope this will help", a 1539x2000 WebP of the mark on white).** Keyed into
    `brand/stimita_emblem_2000.png` (1500x1955 RGBA): alpha = the distance from white (min channel 60..245 -> 1..0), the
    colour un-premultiplied against white, the background AND the leaf interiors transparent (as the 1024 source has them),
    the three gears' six white parts kept opaque = the enclosed white components inside the solid right leaf (x > 55 % of
    the width, not touching the border, >= 50 px) - a rule by position because the circuit rings' white centres are also
    enclosed and must stay transparent. Labelling with scipy in a scratch venv (`scratchpad/keyvenv`; PIL's floodfill
    silently filled nothing). `EMBLEM` points at it; the old file stays. The site's own logo file (1024x1536) was no help.
    Next: the user is sending an SVG - render each iconset size from it with QtSvg (`QSvgRenderer`, in the venv) instead
    of downscaling a raster, then the glass/bezel/glow pipeline as now.
  - **The SVG master (2026-09-26, user: `~/Downloads/stimita_logo.svg`, "check older 4K image was more good or this svg").**
    A real vector (11 paths, one horizontal red gradient, no <image>), auto-traced from a bitmap (potrace-style path data,
    scale 0.1): cleaner edges than the keyed WebP at 2000 px (no keying/compression halo on the gear teeth), a touch better
    at the Dock's own sizes - and the tracer cut the three gears as HOLES, not white. `brand/stimita_emblem.svg` is the
    master; `tools/render_emblem.py` renders it with QtSvg (offscreen QGuiApplication) 2000 px tall and fills the enclosed
    transparent pockets in the right leaf (x > 55 %, >= 50 px) white - 6 pockets, 37 432 px, matching the raster sources'
    gears - into `brand/stimita_emblem_svg_2000.png`, which `EMBLEM` reads. **Two Pillow traps**: `ImageDraw.floodfill`
    silently fills NOTHING on the read-only image `Image.fromarray()` returns (`.copy()` first; the loop spun for 5 min
    before that was found), and the crop puts the left leaf's tip exactly in the top-left corner, so the "outside" flood
    must start on a padded transparent border (`ImageOps.expand`) or it marks the LEAF and every transparent region
    becomes a "pocket" (869 001 white px on the first run). A vector is still no magic at 87 px: the circuit leaf's lines
    are below one Dock pixel wide; the ICON_SHARPEN pass is what keeps them readable.
    1360x932 / 1120x700 and checks the whole plots block fits the viewport each time, the Nyquist beside (not under)
    the pair and no taller than it, capped on the tall window and shrinking on the short one (fails on the old code by
    timeout). Verified live (`scratchpad/real_plot_size.py`,
    the real app maximised on the Simulator): 320 + 8 + 320 = 648 px on a 671 px viewport, both plots visible together.
  - **Optimisation pass before packaging (2026-09-27, user: "before we take any action make sure the code is well
    optimised to run very smoothly").** Measured before touching anything (`scratchpad/profile_app.py`: the real app on
    the simulator, offscreen, whole-process CPU per phase plus a cProfile - which on Python 3.12+ covers every thread):
    idle with the Motion page shown at the drive's 9 msg/s = 14 % of one core, the motor running 18 %, that page hidden
    4 %, the simulator's ~200 msg/s 30 % (11 % hidden); startup 0.9 s (`MainWindow()` 0.6 s spread over 42 pages -
    nothing silly). The visible cost is the four live plots repainting at 10 Hz (the price of live plots; ~3 % in
    pyqtgraph's axis `QPicture.play`). The real risks were at the 1000-2000 Hz telemetry rates: (1) `history_snapshot`
    rebuilt a numpy array from a deque of up to 20 000 tuples per curve per tick, in the GUI thread - 1.6 ms per call
    measured at a full history, 8 % of a core for five curves and a stall every tick, more with the Recorder - the
    history is `_History` now (client.py: two preallocated numpy rings, a snapshot is a slice copy, 0.035 ms), `.get`
    so a value never seen makes no history, and `_on_tpdo` takes the polled-keys lock only while something is polled;
    (2) pyqtgraph drew every point - the live plots' and the Recorder's curves are `setDownsampling(auto=True,
    method="peak")` with `autoDownsampleFactor` 1.0 (one bucket per pixel column, its min and max drawn - pyqtgraph's
    default keeps ~5 samples per pixel, still 8000 of 20 000 points; nothing changes while a curve has fewer points
    than pixels); (3) hidden-page waste: the Axes and Multi Axis Motion tables refitted their columns every tick
    (`sectionSizeHint`/`sizeHintForColumn` per column) - only when a text changed now; the Terminal inserted one row
    per frame and trimmed the overflow with `removeRow(0)` once per surplus row, each shifting every remaining row -
    one `setRowCount` per batch, one `model().removeRows(0, n)`, and `scrollToBottom` only while shown (again in
    `showEvent`); `Readout._fit_text` builds its up-to-16 `QFontMetrics` once per size (`_mono_metrics`). Left alone,
    with the reason: `sim3d._tick` while hidden (~0.3 % of a core with a model loaded, and five tests read its
    transforms without opening the page), the tuner's `_sync` (runs on edits, not per tick), the simulator and
    python-can's virtual bus (test-only; its per-receiver deepcopy is what phase F measures), `usb.py` (one read per
    frame, fine). **A pitfall met**: the ring's `append(t, v)` replaced the deque's `append((t, v))` and `_record` still
    passed the tuple - the rx thread died on the TypeError, invisible in the GUI (a daemon thread), and every
    telemetry test failed until the client tests were run alone. Tests: the ring (wrap, order, copies), an unseen key,
    the curves' downsampling flags on both live cards and a Recorder chart, the Terminal overflow keeping the newest
    MAX_ROWS. **Result** (old code in a copy of the tree vs the new, same profiler, a phase that fills the four curves'
    history to the full 20 000 samples with a 0.5 Hz swing plus noise): the Motion page with a full history went from
    71 % of a core to 27 % (drawPath 3.95 s -> 0.57 s per 8 s, the numpy.array rebuild gone); one tick with the paint
    8.5 ms; the other phases unchanged within noise. **A measuring trap**: the first synthetic signal, a full-amplitude
    sawtooth at 1 kHz, made every pixel column a tall spike - Qt's rasteriser pays per AREA, so 480 downsampled points
    still cost 4 ms a paint and the gain looked absent; profile with a signal shaped like the real one. **Known, not
    changed**: HISTORY_SAMPLES = 20 000 per signal is 1.8 h at 9 msg/s but 10 s at the 2000 Hz timer - a Recorder
    recording longer than that at the fast rate keeps only its last 10 s (README says so).
  - **An async result is dropped once its page is gone (2026-09-27, from the optimisation pass's own full-suite run).**
    Two teardowns saw a Drive Load / Read All finish AFTER the window had closed and write into a deleted
    `QStackedWidget` / `QCheckBox` ("Internal C++ object already deleted" raised inside the event loop; the same thing
    happens in the real app when a page is closed or an axis disconnected mid-read - a traceback on stderr, nothing
    worse). `workers.run_async(..., owner=None)`: `owner` is the QObject the callbacks write to; the relay checks
    `shiboken6.isValid(owner)` before every `on_progress` / `on_done` / `on_failed` and drops the delivery when the
    owner has been deleted (no parenting - that would move destruction; the check is enough). Every page-level call
    site passes `owner=self` (22 of them, inserted by an AST-located patch so the multi-line calls were safe);
    `feedback_gate.check` passes its guard when that is a QObject (the button pressed); the Session's two probes and
    the window's Save to Flash keep no owner (they outlive every page). Test: a job released after its owner was
    `shiboken6.delete`d reaches nothing and raises nothing; a living owner still gets progress and the result.
  - **The installable builds made (2026-09-27, the "action" the optimisation pass came before: user, 2026-09-26, "can we
    make this software in .exe file so it can be installed on windows pc, for mac and linux as well").**
    `tools/build_release.py` (PyInstaller 6.22, one-folder, windowed; dmg via hdiutil, zip + Inno Setup, AppImage/tar.gz)
    ran here for **macOS**: `dist/release/SDR Servo Tuning Tool-0.2.0-macOS-arm64.dmg` (68 MB; the .app 153 MB, ad-hoc
    signed, Info.plist version/icon/hi-res set) - launched offscreen with `--sim`, alive and silent after 15 s, its
    data folder bundled. **Windows** was built in the Parallels "Windows 11" VM, which is ARM64 and had no Python:
    `prlctl exec` runs headless as SYSTEM and sees `\\Mac\Home`, so the tree was robocopied to `C:\build\servo`, the
    python.org **x64** 3.13.15 installer (29 MB) and Inno Setup 6.7.3 (its GitHub release) were downloaded and
    installed silently, a venv made (`-r requirements.txt pyinstaller pillow` - Pillow writes the .ico; now in
    requirements-dev), and the build gave `SDR Servo Tuning Tool-0.2.0-Setup.exe` (54 MB; Program Files, Start menu +
    desktop shortcut, offers to start) and `...-Windows-x64.zip` (76 MB) - the .exe launched offscreen in the VM with
    `--sim` stayed alive with Qt6Core and python313 loaded. **One fix from it**: `os_tag()` named the zip `arm64`
    (`platform.machine()` is the OS's; on Windows it now takes the interpreter's `sysconfig.get_platform()`, since an
    x64 Python under Windows-on-ARM emulation packages an x64 .exe; macOS/Linux keep the machine - a universal2 Python
    here still builds for the arm64 it runs as; test in test_app_bundle.py). **Linux: not built** (no Linux machine or
    VM; the script's AppImage/tar.gz path is untested). Neither build is code-signed: the manual (§2, "Installing the
    packaged app") says how to get past Gatekeeper / SmartScreen. The VM was shut down afterwards; it keeps the
    toolchain, so a rebuild is: start it, robocopy, `C:\build\venv\Scripts\python.exe tools\build_release.py`.
`sdr_tuner/protocol.py` frames, `defs.py` + `data/` definitions (`data/gcode_samples/*.nc`, `data/kinematics_samples/*.kinematics`),
`client.py` thread-safe drive client, `simulator.py` simulated drive, `usb.py` USB cable, `shared_bus.py` (lets several `DriveClient`s
share one physical CAN connection - real multi-axis), `kinematics.py` (forward/inverse kinematics from a vendor `.kinematics` file - no
Qt, pure functions like `gcode.py`), `gui/` (`main_window.py` studio window, `ribbon.py`, `navigator.py`, `shell.py`, `config_page.py`
(Item Configuration, incl. Additional Axes and Kinematics Model), `drive_tab.py`, `feedback_gate.py` (the Feedback Settings check,
shared by DriveTab and the G-code dispatch), `params_tab.py`, `log_tab.py` (Terminal + the G-code console), `gcode.py` (the G-code
dialect, pure functions, incl. `Txx` and `parse_line()`'s concatenated-line splitting), `trajectory.py` (pure arc geometry for G02/G03,
no Qt, mirrors gcode.py/kinematics.py), `gcode_runner.py` (dispatches a parsed G-code action, or several concatenated on one line
(`run_actions()`), to whichever connected axis/axes it names, solving Inverse Kinematics for a `Txx` line and discretising an arc into
waypoints first), `motion_controller.py` (multi-line G-code programs: RUN/PAUSE/RESUME/STOP, Loop),
`axes_page.py` (one status row per connected axis), `recorder.py`, `tuner.py`, `peripherals.py`, `session.py` (the primary connection,
the additional-axis registry `connect_axis`/`AxisContext`, and the loaded kinematics model/counts-per-unit), `sim3d.py` (the 3D
Simulation page, `Sim3DPage` - live, from connected axes only, via `kinematics.KinematicModel.body_transforms()`), `icons.py` drawn
in code, `theme.py`, `widgets.py`, `motor_constants_dialog.py` (`MotorConstantsDialog`, standalone, no session needed),
`params_comparison_dialog.py` (`ParametersComparisonDialog`, standalone, file-based - no session needed either)).
`mesh.py` (outside `gui/`, no Qt - the `.obj` parser `sim3d.py` uses, same "pure functions,
testable standalone" style as `kinematics.py`/`gcode.py`). `motor_constants.py` (outside `gui/`, no Qt - Ke/Kv/Kt conversions,
same style). `export.py` (outside `gui/`, no Qt - `build_eds()`/`build_html_report()`, same style). Pages are opened
with `win.open_tool(key)`; an additional axis's own
Motion page is opened with the axis-qualified key `f"motion:{node_id}"` (`main_window._axis_tool_key`).

## Where things stand
- Item Configuration also has **Product Activation** and **Networks** groups (`gui/config_page.py`, `ParameterGroup`): the drive's own
  parameters (Activation Key; CAN bit rate, node ID, heartbeat timeout, telemetry/receiving frequency, USB2CAN, USB serial), read/written
  directly with their own Read / Write / Revert - separate from Target Connection above them, which is only this tool's own link settings.
  Nothing is read until the user presses Read (connecting reads only pole count / mapping / device identity, per the rule below); Write
  asks for a confirmation (power-cycle and bit-rate/node-ID warnings, like Parameters Explorer).
- Built: the studio shell and the pages Item Configuration, Single Axis Motion, **Recorder** (`gui/recorder.py`: two charts, cursors, CSV export,
  contextual *Recording* ribbon tab), **Quick Tuning / Advanced Tuning** (`gui/tuner.py`), Parameters Explorer, Telemetry Explorer, Terminal.
- **Tuning = the drive's own automatic configuration** (the user: "auto-configuration is for tuning purpose only"): Autoconfiguration `0xD0`
  (brushed `0xD4`), Advanced `0xD1` with General System Dynamics. The tuner sends the command ONCE (never repeated: a copy could start it
  again) and keeps the drive alive with harmless reads (`client.set_heartbeat`, a quarter of the drive's heartbeat timeout, about 1 s). **Completion = the
  Datasheet changed** (user's rule): while a run that the user started is going it reads the six measured Datasheet values (`MEASURED_KEYS`, not the two it
  sent) from `FIRST_POLL_S` (20 s) after the command and then every `POLL_S` seconds (4 s; user 2026-09-26: "first check data sheet once user click on start automatic tunning then wait for 20 sec before start continuous data sheet checking at every 4 sec" - before that 3 s from the start, before that 1 s) and STOPS reading at the first change (`MIN_CHANGED = 1`: the user said a
  repeat run changes few values, even 2 must count); then ONE final read of Datasheet + Control Laws that the pages reuse (no reload of every page); nothing
  is read before START / after the run (the user does not want the bus busy; tests `nothing_is_read_from_the_drive_unless...`, `readings_stop...`). Operation
  Mode 3 is only a hint: leaving it is NOT an end (the user: it takes 30 s to 2 min, unknown, only the Datasheet tells) - **but once the Datasheet has
  changed, the wait for Operation Mode to clear is capped at `SETTLE_MAX` (10 s), not open-ended** (user, 2026-09-22, live on the real drive: stuck at
  "still finishing... 29 s" - the Datasheet change already means it is done; the cap is only a courtesy for Control Laws/flash to catch up). It refuses to
  start on a fault, with the DC bus under 5 V or with a speed command running; it asks for a
  confirmation; STOP / Esc end it by sending **Reset** (the user: Stop works in velocity / position / other modes but NOT in the configuration mode; Reset ends the
  procedure, clears errors and cuts the current supply to the motor). Every STOP (ribbon, Esc, the motion page) tells the tuners (`DriveTab.stop_requested`),
  and also sends a Reset itself when the broadcast shows Operation Mode 3; Disconnect / closing the window mid-run resets first (`Session.disconnecting`). Forms write only on Apply (with a confirmation). Never run
  it on a real drive without the user saying so: it moves the motor and overwrites the Datasheet and Control Laws in flash.
  Never tried on a real drive; the simulator's model (`SimulatedDrive.autoconfig_seconds`, `bus_volts`) is an assumption.
- **Tuner pages:** Quick = Motor and Feedback (Motor Settings, Feedback Settings) > Automatic Tuning. Advanced adds Results (Measured Motor
  Data, Control Laws, Filters = the 2 notch filters), Application (Application Settings = accel/decel, Work Zone, Kickstart/Micro-Speeding/
  Gimbal, starter-generator; Limits and Protections = fault reporting, current limit, electromagnetic + virtual brake), Verification (Step
  Response Tests) and Summary. The drive's on/off Feature switches (`0x2004:*`) sit next to the gain or setting they control (Control Laws,
  Feedback Settings, Application Settings), not on their own page. Coverage: Advanced 193/242 configuration parameters, Quick 129/242; the
  rest (Activation Key, Networks, RC PWM / GPIO / Thermistor / Inverter) are Parameters-Explorer only for now - next on the user's list
  (2026-09-22, in order): Activation Key + Networks cards on Item Configuration (done), a step-response test page (done), a Peripherals tool (done - all 4 items on the user's list are built).
- **Peripherals page** (`gui/peripherals.py`, `PeripheralsPage`): a **PERIPHERAL COMMANDS** card (Electromagnetic Brake Engage/Disengage, GPIO: Generic Output Set High/Low, GPIO: PWM Output duty cycle + Send - all 3 one-shot, not continuous) above a flat set of `FormPage` groups (RC PWM/Electronic Gearing, GPIO, **Digital Inputs & Safety (read-only)** - 2026-09-25, see below, Thermistor, Inverter - 33 parameters) with its own Read All / Write Changed / Revert, same confirmation and power-cycle/RISKY warnings as Parameters Explorer. Not a wizard; opened like Parameters/Telemetry Explorer (`win.peripherals_page`, tool key `peripherals`).
- **Status Monitor page** (`gui/status_monitor.py`, `StatusMonitorPage`, 2026-09-25): the user picks ANY of the drive's ~417 registers
  (config or telemetry) per row via a searchable `ComboBox` (`QCompleter`, contains-match), on any connected axis (a per-row Target combo,
  not per-axis itself - workspace-level like Axes/2D Plotter). A row whose picked key is currently broadcast updates live every tick from
  `client.telemetry_snapshot()` (Read disabled - nothing to press); a row whose key is not broadcast shows "not broadcast" with its own
  Read enabled (one-shot `client.read()` via `run_async` - never a background poll, matching `drive_tab.py`'s own "never asks the drive
  for telemetry" rule). Fault Bits gets its own bitwise formatting (`defs.fault_names_text`, matching every other page that shows it) -
  every other register goes through the generic `show_value` (options-mapped text, or the raw number). A new `Gauge` widget (`widgets.py`)
  gives an optional horizontal fill bar per row, scaled by the register's own `.min`/`.max` - disabled when a register has neither (most
  enums/bools - nothing to scale against). Rows are in-memory only, not persisted across a restart. Tool key `status_monitor`, ribbon
  `"tools"`, in Drive Setup and Motion's list. **Verified live**: Fault Bits live-updated with no extra reads; CANopen: Node ID showed "not
  broadcast" / "1-127" until Read, then showed the simulator's real value (7); the searchable Signal combo correctly filtered on "fault"
  and "node id" typed live.
- **Multi Axis Motion page** (`gui/multi_axis_motion.py`, `MultiAxisMotionPage`, 2026-09-25): one row per connected axis (workspace-level,
  built the exact way `axes_page.py` is - `session.all_clients()` at construction, rebuilt whole on `connected`/`disconnected`/
  `axis_connected`/`axis_disconnected`), each with its own live Position (broadcast-only, same rule as everywhere else) and an editable
  Work Zone Count Target. MOVE reuses the EXACT command and safety check `DriveTab`'s own MOVE TO already uses -
  `feedback_gate.check(client, row.move_btn, ...)` then `client.hold_position(target)` - not a reimplementation of either; a row correctly
  refuses with the same "Motor Encoder or Servo Encoder" warning `DriveTab` shows when none is configured. MOVE ALL loops every row's own
  MOVE - still uncoordinated, each axis moves independently (for coordinated motion, a G-code Txx line already exists). Tool key
  `multi_axis_motion`, ribbon `"tools"`, in Drive Setup and Motion's list, right after Status Monitor. **Verified live**: MOVE on a
  connected axis correctly showed "Moving to 0 counts · sent every 100 ms" and dispatched the real command over the bus - live keyboard
  typing into the Target field itself could not be exercised through this session's own GUI-automation tooling (a `SpinBox` quirk under
  synthetic key events specifically, not a real app defect - `qtbot.keyClicks`/`setValue` in the automated tests exercise the exact same
  code path directly and all 6 pass), so the non-zero-target path is verified by the automated suite rather than live typing this time.
- **Dockable multi-pane workspace / Docked View** (`gui/dock_workspace.py`, `DockWorkspace`, 2026-09-25): opt-in, ribbon-toggled
  (`main_window.py`'s "Docked View" button, `"tools"` ribbon page's own "Workspace" group) alternative to `PageHost`'s single-page
  `QStackedWidget` swap - Drive Setup and Motion ONLY (System Configuration and everything else always single-page, untouched,
  matching EAS3's own split). `DockWorkspace` is a nested `QMainWindow` (an empty `QWidget` as its own central widget, so the dock
  areas alone fill it) added to `self.host.stack` like any other tool page; it has no idea the stack even exists - `MainWindow`'s
  own `_show_docked_tool()` is the only thing that ever takes a page OUT of the stack and hands it to `DockWorkspace.show_tool()`,
  or the reverse via `close_tool()`/`close_all()`. `MainWindow.open_tool()` picks the surface per call: `self._docked_mode and
  tool.activity == "setup"` docks, anything else keeps using `self.host.show_page()` exactly as before - one extra `if`, no
  existing call site touched. An axis-qualified tool (e.g. `motion:2`) docks under its own already-distinct title (`Tool.title`
  already carries "DriveNN (SDR) - ..." for these, built in `_on_axis_connected` - free, no new naming logic needed here).
  **A real correctness gap, not just plumbing**: `_current_tuner()`/`_current_recorder()` used to read `self.host.current_page()`
  directly to decide which tuner/recorder the ribbon's contextual Apply/Revert/Start/Stop buttons act on - correct in single-page
  mode (that IS the page showing) but wrong in docked mode, where `self.host.current_page()` is always `dock_workspace` itself,
  never the actual focused tool; unfixed, an axis's own docked Advanced Tuning panel would silently drive the PRIMARY's tuner
  instead through the ribbon. Fixed with a new `_focused_page()` (returns `self.tools[self._current_key].page` while docked-and-
  setup, `self.host.current_page()` otherwise - `_current_key` is the same thing `open_tool()` already updates on every click,
  docked or not) - `_tuner_page_changed()` deliberately left alone, since the single shared outer crumb has nothing coherent to
  say once several panels can be open and focused at once (each dock shows its own title on its own bar instead). Persistence:
  `QMainWindow.saveState()` + the actual list of open tool keys (needed too - `restoreState()` only affects docks that already
  exist, so which ones to recreate has to be remembered separately) through the existing `QSettings`, saved in `closeEvent()`,
  restored in `_load_settings()` - an axis-qualified key not yet reconnected at that point is silently skipped (not in `self.tools`
  yet), matching how axis pages already don't exist until `_on_axis_connected` runs. **Reset Layout** closes every dock, hands
  the pages back to the stack, clears the saved keys/state, and (if still in docked mode) reopens whatever was current, fresh.
  Disconnecting an axis closes any of ITS OWN docked panels first (`dock_workspace.close_tool()`, harmless no-op if it wasn't
  docked) before `page.deleteLater()` - otherwise a `QDockWidget` would go on holding a about-to-be-destroyed page. New QSS:
  `QDockWidget`/`QDockWidget::title` (theme.py), same gradient/gold convention `pageHeader`/`pageTitle` already use, Qt's own
  built-in close/float buttons only re-skinned, not replaced. 11 new tests (toggle swaps the surface; two tools dock as two real
  `QDockWidget`s showing the right pages; closing one and reopening from the sidebar reuses the same instance, not a rebuild;
  toggling off hands pages back to the stack; System Configuration never docks even with the toggle on; an axis-qualified tool's
  own title; disconnecting an axis closes its own docked panels; the ribbon-context fix, directly; two Reset Layout cases;
  layout persists across a real restart - two `MainWindow` instances, same settings backing, matching the project's own existing
  `test_connection_settings_are_remembered` pattern). **A second real bug, caught only by live verification, not the automated
  suite** (tests run `QT_QPA_PLATFORM=offscreen` - no real paint, so a missing QSS rule can't fail an assertion): `#ribbonButton`
  had NO `:checked` style at all - `docked_view_btn.setCheckable(True)` (the same pattern `rec_cursors_btn` already used, so this
  gap was latent there too, just never noticed) looked "on" right after a click only because the cursor was still hovering it
  (`:hover` has a visible border; `:checked` didn't exist) - move the mouse away, or restart the app so nothing was ever hovered,
  and there was no visible way to tell Docked View was active at all. Fixed with one QSS rule, `QToolButton#ribbonButton:checked`,
  matching `#activityButton:checked`'s own gold-accent convention - benefits `rec_cursors_btn` too, not just this button.
  **Verified live**, connected + a second axis: two, then three, then four simultaneous docks (Motion, Axes, 3D Simulation,
  Drive02's own Motion, the last correctly titled "Drive02 (SDR) - Motion"), each genuinely live and independent; Reset Layout
  collapsed four panels down to just the current one; the toggle correctly returns pages to single-page mode, current one shown;
  and, closing the app for real (Cmd+Q, not a kill - confirmed via the live `.plist` this actually reaches `closeEvent`, not
  assumed) and relaunching restored both Docked View itself and its "axes" panel (the other saved key, an axis not yet
  reconnected, correctly skipped) - with the `:checked` fix, genuinely visibly so this time, not just internally.
- **Step Response Tests** (`StepResponsePage`, Advanced only): the drive's built-in test commands (0xB0 FOC/current, 0xB4 ESC/speed, 0xB8
  servo, 0xBC Iq current) - a square wave (Period, Amplitude, Wave Form 0-5 undocumented) into that loop, continuous like the speed command
  via the same repeating slot in `DriveClient` (`run_test()`; `commanded`/`_esc` generalized to hold a scalar (speed) or a tuple (a test), so
  starting a test replaces a running speed command or another test, and Stop/Off/Reset end whichever is repeating). NOT tried on a real
  drive - assumes Stop ends a test (this is an ordinary mode, unlike the automatic configuration's own "configuration" mode where only Reset
  does). "Open in Recorder" opens the Recorder to watch the response.
- Next, in the user's order: **Status Monitor** (pick signals), Terminal docked under the motion page, Parameters Comparison (and, later, a
  Recorder trigger). Elmo tools that do not exist on this drive (Maestro, ECAM, scripts, SIL) are left out.
- Not yet tried on the real drive: the ESC RPM command, writing the telemetry mapping (beyond the DC-bus remap above, which WAS
  confirmed live), the 156 parameters new in revision L, a powered motor, and the servo (hold-position / ENABLE) command - see the
  note above - including whether it actually holds position with no Motor/Servo Encoder configured.
- To learn how Elmo Application Studio III looks, the user records the screen (a .mov); frames are pulled out with a scratch venv and
  `imageio-ffmpeg` (the video is HDR: brighten with ffmpeg's `eq=gamma=2.3`). Claude cannot operate the user's other apps.
