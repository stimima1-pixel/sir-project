# STIMITA MOTION CONTROL - SDR Servo Tuning Tool

A desktop GUI (PySide6) for the **SDR-CB-60R-D** CAN servo drive, built from the **Stimita Device Reference, revision L**
(August 2026). Everything is done from the window: connect, run the motor, watch live telemetry, read / write every
configuration parameter.

**Looking for how to use a specific feature, or where something lives in the window?** See
[`OPERATIONAL_MANUAL.md`](OPERATIONAL_MANUAL.md) - a quick-lookup guide, kept current alongside the software. This
file is the full technical reference underneath it.

The window is laid out like **Elmo Application Studio III**, in this tool's own black and gold:

* **Ribbon** on top with tabs (*File*, *Parameters*, *System Configuration*, *Floating Tools*, and one for the tool that is open: *Recording*, *Drive Quick Tuner*, *Drive Advanced Tuner*), groups of big icon buttons, and the round **STOP** button (also the `Esc` key).
* **Left column**: the workspace tree (Workspace "Default" > your drive), under it the tools of the current activity, and at the bottom the list of **activities**: *System Configuration* and *Drive Setup and Motion*. The `«` button folds the column.
* **Centre**: the page that is open, with a header (page title, drive name). **Bottom bar**: what is open, or the latest message; the connection state on the right.

| Page (activity > tool) | What it does |
|---|---|
| **System Configuration > Item Configuration** | The drive's name, its type, **Device Type and both serial numbers** (read once at connect; the drive's maker needs them for the activation key), and the connection this tool uses: type (simulator, USB cable, PCAN, ...), port, bit rate, node ID. **On a USB cable the Node ID field is disabled** - a USB cable reaches only the one drive at its end, so nothing is guessed: the client always listens for whatever node is broadcasting telemetry (it always is) and connects to it, and the field then shows what was found. The port is found the same way (by its USB vendor/product ID) and re-checked right before Connect if it is still empty, e.g. the drive was plugged in after the port list was last read. A saved port from an earlier session is used only when nothing is found live (the OS can assign the drive a different port name each time it is plugged in). On a shared CAN bus (PCAN, Kvaser, ...), where more than one drive could be on the wire, the configured Node ID is still tried first, and the client only switches if it is silent while exactly one other node answers. **Connect / Disconnect** are in the ribbon. Two more groups read and write the drive's own parameters directly (not this tool's connection settings): **Product Activation** (the Activation Key that clears fault 15, "Activation Key is Missing") and **Networks** (the drive's own CAN bit rate, node ID, heartbeat timeout, telemetry and receiving frequency, USB2CAN routing and 29-bit ID support, USB serial number). Each has its own **Read** / **Write** / **Revert**; nothing is read until you press Read (connecting itself reads only the pole count, the telemetry mapping and the device identity), and Write asks for a confirmation that lists what needs a power cycle and warns if the bit rate or node ID would change. Two more groups: **Additional Axes** (connect more physical drives - see *Multi-axis* below) and **Kinematics Model** / **Tool Pose (Orientation IK)** (load a robot/turret geometry file and solve position-only or full-pose Inverse Kinematics against it - see *Inverse Kinematics* below) |
| **Drive Setup and Motion > Quick Tuning** | The drive tunes itself: one command, **Autoconfiguration** (`0xD0`, or `0xD4` for a brushed motor), makes it measure the motor's resistance, inductance, back-EMF constant and inertia, detect the Hall sensors, write the Datasheet, compute the Control Laws and save them to its flash (the motor makes rapid moves and beeps at the start and the end). Three pages in a tree: **Motor Settings** (motor type, maximum continuous current, poles number: shown as the drive holds them, edited values are marked, *Apply* writes them after a confirmation, *Revert* puts the drive's values back, *Errors…* lists what is not valid), **Feedback Settings** (the Hall sensors and the motor / servo encoders, with the settings of each encoder type you choose shown below the choice, as in Advanced Tuning) and **Automatic Tuning**. That page shows what will be sent, a live list of checks (*valid motor data*, *no fault*, *DC bus supplied*, *no speed command running*, and a warning if the broadcast lacks Operation Mode or Fault Bits) and refuses to start while one of the first four fails; **START** then asks for a confirmation that says what will be overwritten. The command is sent once (a second copy could start the procedure again). Because the procedure takes a while, the page then **reads the values the procedure measures - phase resistance, inductance, back-EMF constant, inertia, viscous damping, Hall sensors - every 3 seconds (it takes minutes, so there is no need to check more often, and it keeps the bus quieter); as soon as one of them differs from what it was before the start, the automatic tuning has completed successfully and the reading stops**. Nothing is read before START is pressed and confirmed, and nothing after the run: the bus is quiet otherwise. (The two values it was sent, current and poles, do not count: the drive may store those at once.) One changed value is enough (a repeat run on the same motor measures nearly the same values again, so often only a couple differ). It then waits **at most 10 seconds** for the drive to still be reporting Operation Mode 3 (Autoconfiguration) - a short courtesy wait in case a value was written early and Control Laws / flash need a moment to follow, not an open-ended one - and reads the Datasheet and Control Laws **once** to list what changed (before → after); the pages show those values, nothing is read a second time. While it runs, one harmless read request about once a second (a quarter of the drive's own heartbeat timeout, 3.5 s by default) keeps the drive from stopping itself; **STOP** / the round STOP button / `Esc` end the run by sending the drive's **Reset** in the background (Stop does not work in the configuration mode; Reset ends the procedure, clears the drive's errors and cuts the current supply to the motor); the same happens when you disconnect or close the window during a run, and the motion page's STOP and the round STOP button also send a Reset whenever the broadcast shows Operation Mode 3, even if this tool did not start it. The run takes anywhere from about half a minute to a couple of minutes, so no time is assumed: only the Datasheet ends it as a success (a fault or **STOP** end it otherwise; if the Datasheet never changes it is stopped after 15 minutes). Leaving Operation Mode 3 is not treated as an end - the drive may pass through other modes - the page only says so after 5 s and keeps watching |
| **Drive Setup and Motion > Advanced Tuning** | The same, with the advanced command (`0xD1`), which also takes the **General System Dynamics** (1.5 - 15 nominal, presets for a direct drive, a gearbox or traction drive, a high-speed low-inductance motor, and 12 - 32 as a last resort: the higher, the more aggressive the control laws). Pages, in a tree: **Motor and Feedback** (Motor Settings with the gear ratio and ball-screw lead, Feedback Settings with the Hall sensors, which encoder measures speed, and the motor / servo encoders: **the settings of each encoder type you choose appear below the choice** - counts per revolution, bias, inversion and, for SSI / BiSS-C, SPI, RS485 and dual encoders, the packet layout; for a quadrature encoder the speed-measurement settings; for a PWM encoder its pulse timing - and the Hall wiring when the motor is *Sensored*; settings of a type that is not chosen are hidden and are neither applied nor counted as errors), **Automatic Tuning**, **Results** (Measured Motor Data; Control Laws, with the on/off feature next to the gain it enables; Filters, the two notch filters) and **Application** (Application Settings: acceleration/deceleration, Work Zone travel limits, starting techniques - Kickstart, Micro-Speeding, Gimbal Control - and starter-generator power regulation; Limits and Protections: which faults are reported, the current limit factor, the electromagnetic and virtual brakes), **Verification > Step Response Tests** (runs one of the drive's built-in step-response tests - Speed, Current / Field Oriented Control, Iq Current, or Servo - a Wave Form into that loop - a square wave reversing every Period, a positive-only square wave, a sine wave, or a frequency sweep (plain, or with linear / quadratic amplitude decay) - until STOP; starting one replaces any speed command or other test already running; watch the response on the Recorder) **Verification > Frequency Response** (a Bode plot - gain in dB and phase in degrees against frequency - of the current, speed or servo loop, measured with the drive's own sine-wave test one frequency at a time: for the length of the sweep the drive's telemetry is switched to carry only that loop's reference and response in one message at 1000 messages/s, then put back exactly as it was; reads off the -3 dB bandwidth, the phase there and any resonance peak; Save Data…/Save Image…), and **Summary** (recommended actions before leaving, and the changes of the last run). Advanced Tuning covers 193 of the drive's 243 configuration parameters (Quick Tuning covers 129); the rest - the Activation Key, the Networks settings (CAN bit rate, node ID, heartbeat timeout...) and the RC PWM / GPIO / Thermistor / Inverter peripherals - are in the Parameters Explorer. The ribbon's tuner tab has *Drive Load*, *Apply*, *Revert*, *Apply All*, *Revert All* |
| **Servo Drive > Motion - Single Axis** | A **CONTROL MODE** chooser (Velocity / Position - one at a time, since the drive runs one continuous command; switching sends nothing) shows either **Speed Control · ESC** (a target typed in rpm, rps or deg/sec - a pure conversion, the drive always gets its rpm command - with a speed-limit clamp in the same unit, RUN) or **Position Control · Servo** (MOVE TO a typed Work Zone Count, MOVE BY a relative amount, SET HOME HERE - typed and shown in counts or in degrees, the latter from the drive's own Work Zone: Dimension read once when deg is chosen; the plot card's *Position in* mirrors the choice); a **DRIVE** card with STOP / OFF / RESET / ENABLE / DISABLE and the status line stays under either - plus the readout cards, fault LEDs and plots described below. **Servo Drive > Drive Modes** (2026-09-26, its own page right below, per drive, with the same readouts in a small row and a STOP) holds the seven special modes, one card each - the same commands and rules as before the move, and one repeat slot shared with the motion page (a mode started on one page ends what the other was sending, and the "· sent every 100 ms" note follows): **Position Control · Cyclic** (a MODE choice - **Modulo (cyclic)**: a single-turn target plus a Direction - Normal / Negative / Positive / Shortest Cyclic Route, the default - for a rotary actuator meant to keep spinning past its own Work Zone Count range rather than being held to one absolute multi-turn count; **Turns + Modulo (split)**: the same kind of multi-turn target MOVE TO/BY already sends, split into a single-turn part and a whole-turns part instead of one number - and MOVE); **Current / Torque Control** (a MODE choice - Current (FOC) in A or Torque (ETC) in N·m - and RUN); **Direct Field Control** (a MODE choice - **Rotation (Hz)** or **Electrical Position (rad)** - a Voltage, a Speed/Position target, and RUN; open-loop, with a standing warning that even 0.2-0.3 V can burn a low-resistance motor); **Kickstart** (a Speed target in electrical Hz and RUN - an alternate startup technique for a sensorless brushless motor, tuned via Control Laws); **Power Generation** (a Voltage target and RUN - turns the drive into a generator's control unit); **Brushed: Open Loop Control** (two independent Voltage: Channel #1/#2 targets and RUN - direct open-loop control of 1-2 brushed motors/solenoids, relevant when Datasheet's Motor Type is Brushed); **Virtual Brake** (ENGAGE), the same live plots (`gui/live_plots.py`, one card per page, each redrawn only while its page is on screen). Back on Motion - Single Axis: one readout card per value in the drive's telemetry (with the default broadcast: speed, DC bus, operation mode, fault bits **shown as names, not a hex number** (e.g. "Activation Key is Missing", several joined with a comma if more than one is set; "No Faults" when clear), Phase A current, work-zone count, commutation mode, work-zone modulo), fault-bit LEDs, scrolling plots of speed, position, current and DC bus, and a **Speed in** choice (rpm / rps / deg/sec / Hz - the first three from the drive's Work Zone: Speed in rps, times 60 / 1 / 360). It shows **exactly what the drive broadcasts** (about 9 messages per second) and nothing else; the tool does not ask the drive for telemetry. It changes the drive's telemetry mapping only when you pick a different speed unit and confirm. **ENABLE**, **MOVE TO** and **MOVE BY** all use the drive's plain **servo** command (COB `0x300` code `0x31`, Servo Control), targeting a Work Zone Count: ENABLE takes the current one already in the broadcast (never requested, holding the motor still), MOVE TO a typed one, MOVE BY the current one plus a typed delta - all repeated every 100 ms until STOP / OFF / RESET / a new command. Each first checks (one on-demand read, only when pressed) that a **Motor Encoder or Servo Encoder** is configured (Datasheet → Feedback Settings) - with neither, Work Zone Count is not a real position, so a target on it would not mean anything, and the command is refused with that explanation instead of being sent. **DISABLE** is the same as OFF. **VIRTUAL BRAKE** sends the drive's **Virtual Brake** command (COB `0x200` code `0x50`, no parameters at all - the first zero-parameter command to go through the client's own continuous-command repeat slot), repeated the same way; unlike ENABLE/MOVE TO/BY, NOT gated behind a Feedback Settings check - the reference says the drive still resists motion usefully without a Motor/Servo Encoder, just less efficiently (falling back to a statically held field). **Position Control · Cyclic**'s MOVE sends the drive's **Servo: Modulo** (COB `0x300` code `0x32`, INT32 target + INT16 direction) or **Servo: Turns and Modulo** (code `0x34`, INT32 modulo + INT16 turns) command, whichever MODE is chosen, repeated the same way and gated by the same Feedback Settings check; the Direction combo's own index IS the drive's Direction enum value (0 Normal, 1 Negative, 2 Positive, 3 Shortest Cyclic) - no translation table. **SET HOME HERE** zeroes the Work Zone Count at wherever the motor is right now (the drive's **Reset Work Zone** command, `0xE0`) after sending a Stop - this drive has no automatic homing sequence of its own, so "homing" here means jogging to the reference point with Speed Control, then pressing this. **Current / Torque Control** sends the drive's `current_control_field_oriented_control_foc` (`0x10`, direct FOC current) or `electronic_torque_control_etc` (`0x14`, converts a torque to current internally) command, whichever MODE is chosen, repeated the same way; no Feedback Settings check (it doesn't touch Work Zone Count). **Direct Field Control** sends **Direct Field Control: Rotation** (`0x40`, FLOAT16 Voltage + FLOAT32 Speed Hz) or **Direct Field Control: Electrical Position** (`0x44`, FLOAT16 Voltage + FLOAT32 Position rad), whichever MODE is chosen, repeated the same way; also no Feedback Settings check - this open-loop mode never touches Work Zone Count either. **Kickstart** sends the drive's **Kickstart** command (`0x58`, a single FLOAT32 Speed Hz - shape-identical to ESC's own Speed Control, so it reuses the client's existing single-value repeat helper directly), repeated the same way; no Feedback Settings check either. **Power Generation** sends the drive's **Power Generation** command (`0x60`, a single FLOAT32 Voltage), repeated the same way; a genuinely DIFFERENT physical operation from every other card (converts power FROM an externally-spinning shaft, rather than driving the motor toward a target), which the simulator has no model for at all - Voltage has no effect there, only Operation Mode changes. **Brushed: Open Loop Control** sends the drive's **Brushed: Open Loop Control (1-2 motors)** command (`0x90`, two FLOAT16 channel voltages - #1 on terminals A/B, #2 on C/B, sharing terminal B), repeated the same way; neither channel affects the simulator either (no brushed-motor model exists - the existing phase-current signature is specific to the 3-phase BLDC/PMSM representation). All eight cards' continuous commands share one repeat slot in the client, so starting any one of them (a speed, a position hold/move, a current/torque, an open-loop field, a startup technique, a generator target, a brushed-channel pair, or a step-response test) always fully replaces whatever was running before, and the one STOP / OFF / RESET above ends whichever is active |
| **Drive Setup and Motion > Recorder** | Records the signals the drive broadcasts and shows them in **Chart #1 / Chart #2** (choose each chart's signals from its *Signals* menu; speed follows the *Speed in* unit). **Start** / **Stop** / **Clear**, *Record for* (until stopped, 10 s ... 10 min), **Fit**, two **Cursors** (A and B, draggable, the same in both charts) with a table of each signal's value at A and B and the difference, **Save…** to a CSV file (a row whenever any signal was received; a value holds until the next one), and **Save Image…** - both charts, stacked exactly as shown, as one PNG (`chart.grab()` composed with `QPainter`, the same approach the 2D Plotter's own Save Image already used), and **Trigger…** - an optional condition (a chosen signal crossing a level, Rising or Falling edge) that must fire before Start actually begins recording; pressing Start with one configured goes ARMED instead, watching the drive's own telemetry (never requested) until the crossing happens, then begins recording from that exact instant - matches Elmo Application Studio III's own Trigger Editor (Source/Slope), checked directly against it rather than guessed. Zoom with the wheel, pan by dragging. It only listens: nothing is requested from the drive. The ribbon gets a *Recording* tab while it is open, mirroring Start/Stop/Clear/Save/Save Image. A recording ends by itself if the drive is disconnected or its telemetry layout changes, and keeps what it has |
| **Drive Setup and Motion > Parameters Explorer** | All **243 configuration parameters** (242 from the Device Reference PDF + Controlword, the CiA 402 remap patch's own object - see its own section below): read, edit (validated), write with read-back verification, save / load a parameter file, filter. Whether a row can be written follows THAT parameter's own access mark in the Device Reference (`r`/`r,w`), not which page it is on - checked directly against the PDF page by page: every one of the 242 PDF-sourced ones is marked read/write and every one of the 176 telemetry parameters is marked read-only, so today the two pages happen to split cleanly, but the split is not hardcoded. The table shows the parameter, its value and its units - no type, address or state columns, so the names are shown whole. The details are one hover away: the tooltip has the address, type, access, units, description and the last result (*read*, *edited - not written*, or why the drive refused); a name turns amber when edited and red when refused; the counts and failures are in the status bar Parameters that apply only after a power cycle say so. The ribbon's *Parameters* tab has **Upload** (read from the drive) and **Download** (write the changed ones); **Open** and **Save** (file) are on the *File* tab. Below the table, **Raw Register Access**: read or write ANY index:sub-index directly (pick the wire type yourself - BOOL/(U)INT8/16/32/FLOAT32/16), for a register not in the 243 above (undocumented, or added in a firmware revision newer than this tool's own catalog). Reuses the exact same read/write/verify path every named row uses - a write still asks for confirmation, still gets read back and verified - it just has no idea whether the address is real or genuinely writable until the drive itself answers |
| **Drive Setup and Motion > Telemetry Explorer** | All **176 telemetry parameters** (175 from the Device Reference PDF + Statusword, the CiA 402 remap patch's own object) (device information, ADC, sensorless / FOC state, encoders, ...), read-only per the Device Reference (verified page by page, not assumed) or, for Statusword, computed live by the remap patch, read from the drive once each time you press Read Telemetry / READ ALL / Read. Styled to be told apart from Parameters Explorer at a glance - a **READ-ONLY** badge and a plain (not gold) READ ALL button, since the two show completely different registers (243 read/write configuration parameters vs 176 read-only telemetry ones - no overlap) but share the same table layout (a sapphire table header was tried and dropped - the user preferred the gold line kept) |
| **Drive Setup and Motion > Terminal** | Every frame on the bus, decoded with the parameter and command names. A **G-code console** at the top translates one typed line of the drive's G-code dialect (see *G-code* below) into this drive's own command, sent the same way any other page sends it - so the resulting frame(s) appear right below in the same log |
| **Drive Setup and Motion > Motion Controller** | A small editor for a **G-code program** (several lines): RUN executes it top to bottom, stopping at the first line that fails; **PAUSE** freezes it before its NEXT line (a line already in flight always finishes - nothing is cut off mid-command), **RESUME** continues it; **STOP** aborts it and stops every connected axis. A **Loop** count (1 by default, "Forever" at 0) repeats the whole program that many times, or until STOP. The currently-running line is highlighted, and each line's result is logged below as it runs. A **Samples** menu loads a bundled example program; **Open…**/**Save…** work with `.nc` files, and saving into the bundled samples folder (the default) makes a program a selectable Sample from then on. **SIMULATE** runs the exact same program with no drive at all, real or simulated - it previews the motion on the 3D Simulation page instead (`gcode_sim.py`; always starts from the loaded model's own home pose; a speed command spins that axis continuously in the preview, a torque command shows no motion, arcs are not yet supported there); shares RUN's own PAUSE/RESUME/STOP/Loop, since the two are just different ways to execute the same program, never both at once. See *G-code* below for the full command table |
| **Drive Setup and Motion > 3D Simulation** | A live 3D (OpenGL, `pyqtgraph.opengl`) render of whichever kinematics model is loaded on Item Configuration, one mesh per body from the matching same-stem `.obj` file (e.g. `radar.obj.kinematics` → `radar.obj`) - a body with no bundled mesh shows as a small placeholder marker instead of being silently omitted. Three modes, radio buttons: **Live** (the default) - each connected axis's real Work Zone Count (never requested) is converted to its kinematics unit and applied as that body's own transform, exactly matching `kinematics.body_transforms()`; a body whose own actuated ancestors are not all connected simply stays at its last known (or home) pose. **Simulation** - driven instead by Motion Controller's own **SIMULATE** button (see below) - no drive, real or simulated, needed at all. **Cursor** - a slider + numeric field per actuated axis in a scrollable panel on the right, drag or type a joint value directly and the pose updates live - purely local to this page, no drive or G-code program involved at all; each axis's own range comes from its `.kinematics` file's own Axis Limits when given, or a generic default (±180° / ±500 mm) otherwise; **Reset to Home** zeroes every axis. **Reset View** reframes the camera on the loaded model. The vendor's own `.obj` files are authored in metres - converted to millimetres once at load (matching every `.kinematics` file's own unit) so a moving joint's rotation pivots correctly relative to its own mesh, not ~1000x off |
| **Drive Setup and Motion > 2D Plotter** | Traces two connected axes' own Work Zone Count against each other, live, as telemetry arrives - so a genuine 2-axis interpolated move (a G02/G03 circle, say) draws the actual shape it interpolates, not two separate value-vs-time charts. Pick **X Axis** / **Y Axis** from any connected drives; there is no Start/Stop - it is always tracing, growing a new point whenever both axes report a Work Zone Count that differs from the last one plotted, until **Clear All**. If a selected axis disconnects, the trace freezes exactly where it left off rather than clearing. **Save Image…** and **Save Data…** (CSV) export the plot and the recorded X/Y pairs. Nothing is requested from the drive - sits right below Motion Controller, since it's the companion view for a multi-axis G-code program's own interpolated moves |
| **Drive Setup and Motion > Axes** | Every connected axis (the primary drive and any added under *Multi-axis*) in one table - node ID, online/stale, Work Zone Count, Work Zone Speed and fault bits (as names, like the motion page's own Fault Bits card) - each row from that axis's own telemetry broadcast, refreshed live, nothing requested |
| **Drive Setup and Motion > Peripherals** | A **Peripheral Commands** card (three one-shot commands, not registers - the reference never asks for them to be repeated): **Electromagnetic Brake** (COB `0x400` code `0x52`, ENGAGE/DISENGAGE), **GPIO: Generic Output** (`0x400`/`0xA0`, SET HIGH/SET LOW) and **GPIO: PWM Output** (`0x200`/`0xA4`, a duty-cycle field 0.0-1.0 + SEND). None have an Operation Mode of their own (checked the full enum - they're independent peripheral I/O, orthogonal to whichever control law is driving the motor), so status goes to the window's status bar, not a readout card. Below that, the remaining configuration parameters that fit no tuning wizard: **RC PWM or Electronic Gearing** (12), **GPIO** (6: Emergency Stop and limit switches, a generic output and input), **Thermistor** (1) and **Inverter** (9: current sensing, PWM). Read once when the drive connects; **Write Changed** asks for a confirmation, like the tuners and Parameters Explorer |
| **Floating Tools > Motor Constants** | A standalone Ke/Kv/Kt converter dialog - no drive connection needed. Set **Poles Number** first, pick which of Back-EMF Constant (Ke, V/(rad/s), peak, line-to-neutral, electrical), Velocity Constant (Kv, RPM/V, peak, line-to-line, mechanical) or Torque Constant (Kt, N·m/A) you know with the radio buttons, type it in - the other two are computed live. `Kt = 3/4 x Poles x Ke`; `Kv = 45 / (pi x sqrt(3) x Kt)` (this second relation has no Poles term - Kt and Kv are already both mechanical-domain quantities; Poles only bridges to/from the electrical-domain Ke) - reverse-derived from the standard 3-phase PMSM torque equation and verified bit-for-bit against the vendor's own equivalent tool (`sdr_tuner/motor_constants.py`) |
| **Ribbon > Parameters > Export** | **Export HTML** / **Export EDS**: the whole object dictionary (all 419 configuration + telemetry parameters, from both explorer pages at once, whatever value each was last actually read as) as either a readable HTML report or a genuine CANopen Electronic Data Sheet (`.eds`, CiA 306) for other CANopen tools. This drive answers none of the standard identity objects (1000h/1001h/1018h - its own Device Type/serials are manufacturer-specific, 5000h:9Fh-A1h instead), so the EDS's `[MandatoryObjects]` section is genuinely empty, not padded with objects this hardware does not have; every register is its own independent VAR object (`[<index>sub<sub>]`) since none of them use sub-index 0 the way a real CANopen ARRAY/RECORD would. `FLOAT16` registers (only the DC-bus voltage one) get `UNSIGNED16` as the nearest honest DataType - CANopen has no standard 16-bit float - flagged with a comment (`sdr_tuner/export.py`) |
| **Ribbon > Parameters > Save Snapshot** / **Floating Tools > Compare Parameters** | **Save Snapshot** writes a small JSON file of every parameter this session has actually read a value for. **Compare Parameters** loads two such files (File 1 / File 2) and lists every parameter, highlighting the ones that differ (ruby); **Differences only** (default on) hides the rest; a parameter only one file ever read shows as "(not in file)" rather than an implicit match. Matches Elmo Application Studio III's own Parameters Comparison tool (checked directly), file-based like theirs rather than a live two-drive read (`sdr_tuner/export.py`'s `build_snapshot`/`diff_snapshots`, `gui/params_comparison_dialog.py`) |

Not built yet (they exist in Elmo Application Studio III and would fit this drive): a **Status Monitor** (pick the signals to
watch), and a Terminal docked under the motion page. Checked and genuinely NOT applicable to this drive: **Error Mapping**
(a position-feedback linearization table - needs a real encoder with known systematic error, this drive has none) and **ECAM
Table Editor** (a master/slave electronic-cam curve editor - needs a synchronized second axis). **Automatic Tuning has run for
real** on the bench drive (2026-09-24 "try each and every button" sweep): pre-flight checks, the confirmation dialog, a real
~30 s run and 19 changed Datasheet/Control Law values, all correct. A recording is limited by the
client's history (20000 samples per signal, held in preallocated numpy rings - 1.8 hours at the drive's default 9 messages/s, but only 10 s at the 2000 Hz timer rate, so a recording longer than that at the fast rate keeps its last 10 s; the live plots and the Recorder draw their curves peak-downsampled to one bucket per pixel column (its min and max), so a 20000-point curve costs little more to draw than a short one). **Elmo's "Stepper [UM]" drive mode has no
equivalent here**: the SDR-CB-60R-D is a BLDC/PMSM/brushed servo drive, not a stepper controller, and the Device Reference has no
stepper-specific command (the closest thing, `brushed_open_loop_control_1_2_motors`, is open-loop voltage control for a brushed
motor - a different concept, not exposed as a "mode" on the motion page). **Homing is DIY, not automatic**: the Device Reference
has no homing command or sequence (no switch-search, no home offset register - checked directly, nothing in `registers.json` or
`commands.json` mentions homing) - **SET HOME HERE** (Reset Work Zone) is this tool's answer: jog to the reference point with Speed
Control, then zero the count there by hand.

## Open in PyCharm

1. **File > Open...** and choose this folder (`SDR - drive software dev / servo tunning tool`).
2. PyCharm normally picks up `.venv` by itself. If not: **Settings > Project > Python Interpreter > Add Interpreter >
   Existing**, and choose `.venv/bin/python` (macOS / Linux) or `.venv\Scripts\python.exe` (Windows).
   On a new machine create the environment first: `python3 -m venv .venv` then `.venv/bin/python -m pip install -r requirements-dev.txt`.
3. Right-click `main.py` > **Run 'main'**.

No hardware yet? Pick **Simulator (no hardware)** in the interface list (it is the default unless a drive's USB cable is plugged in) and press CONNECT.
The simulator behaves like a drive: it answers reads / writes, follows the speed / stop / off / reset commands, sends
telemetry in the layout the drive's telemetry-mapping parameters say, and - like the real drive - ignores every command but
Reset while a fault is latched.

## Installable builds (.dmg / Setup.exe / AppImage)

`tools/build_release.py` packages the tool for the OS it runs on, with PyInstaller (one-folder, windowed):
`dist/release/SDR Servo Tuning Tool-<version>-macOS-<arch>.dmg` (the .app inside, ad-hoc signed), `...-Windows-x64.zip`
plus `...-Setup.exe` when Inno Setup's `iscc` is on the PATH, `...-Linux-<arch>.AppImage` when `appimagetool` is (else a
tar.gz). PyInstaller does not cross-compile: run it on each OS with that OS's own venv (`pip install -r requirements-dev.txt`;
on Windows the Pillow it lists writes the .ico). The Windows build was made in the Windows 11 ARM VM with an **x64**
Python installed there, so the .exe is a plain x64 program (an ARM64 Python would have made an ARM-only one). Nothing from
the build machine's venv is referenced at run time, unlike `tools/make_app_bundle.py`'s developer bundle (§ below), which
wraps this machine's venv and exists for the Dock icon during development. `sdr_tuner.dock_identity` skips its venv
relaunch inside a frozen build (`sys.frozen`). `--identity "Developer ID Application: ..." --notarize <profile>` signs with the hardened runtime, notarises and staples the
.app and the .dmg (see the script's docstring) - without it, every other Mac shows the Gatekeeper dialog. OPERATIONAL_MANUAL.md §2
says how each file is installed, how to get past the unsigned-app warnings, and what the certificate needs.

## Connecting to a real drive

### USB cable (no CAN adapter needed)

Plug the drive's USB cable in and start the tool: **USB cable to the drive** is pre-selected and the serial port is
filled in (found by the drive's USB ID `0483:5740`). Press CONNECT. The tool listens to the telemetry the drive is
already broadcasting, finds the drive's **node ID by itself** (yours may not be 1) and switches the node box to it.
Then it reads the drive's **telemetry mapping** (which parameters are in each broadcast message - it is configurable),
so the values are decoded the way *this* drive is set up. **Nothing from the broadcast is decoded or shown before that
mapping is known.** If it cannot be read, the window says so (the reference's default layout is then assumed).

The USB cable is a virtual serial port that carries CAN frames as text (SLCAN format, `t18780080FD1700000000`).
The tool sends nothing when it opens or closes the port - only the requests you ask for.

### CAN adapters

Choose the CAN adapter, channel, bit rate and node ID in the header, then CONNECT. The tool remembers them.

| Adapter | Interface | Channel example | Needs |
|---|---|---|---|
| PEAK PCAN-USB | PCAN | `PCAN_USBBUS1` | PEAK's PCAN-Basic driver (Windows) / PCBUSB library (macOS) |
| Kvaser | Kvaser | `0` | Kvaser CANlib |
| CANable (slcan firmware) | CANable / slcan | `COM5` or `/dev/tty.usbmodem...` | nothing extra (pyserial is installed) |
| Linux SocketCAN | SocketCAN | `can0` | `ip link set can0 up type can bitrate ...` |

The pill in the header shows `ONLINE` when the drive answers, `NO RESPONSE` if the bus opened but the node is silent
(check node ID, bit rate, termination, wiring), `SIMULATED DRIVE` for the simulator.

**Speed control is in rpm** (motor shaft speed). RUN sends the drive's own *ESC, RPM* command (`0x24`); the drive converts
it with its **Poles Number** parameter (Datasheet section - set it to the motor's real pole count before running).
The speed limit box defaults to 500 rpm.

### The speed shown, and the unit ("Speed in")

What the speed card shows is decided by the **Telemetry Mapping Code** the drive holds in its telemetry message, read
before anything is displayed. A code is the hexadecimal Index and Sub-Index written together, read as a decimal number
(the reference's example: `0x4000` + `0x14` = hex `400014` = 4194324):

| Code the drive holds | Parameter | Shown as |
|---|---|---|
| **4194320** = `0x400010` | Speed (Electrical Frequency), Hz | **Hz** |
| **4194326** = `0x400016` | Work Zone: Speed, RPS | **rps**, or **rpm = rps x 60** |

Nothing is converted through the pole count: Hz is shown as Hz, rps as rps, and rpm is the rps value times 60. So if
someone changes that slot from Hz to rps, the tool knows before it displays anything. The **Speed in** choice
(rpm / rps / Hz) follows what the drive sends, and picking another unit **changes the drive's telemetry mapping** to
match, after you confirm (you see which register is written and to which code):

* **Hz** -> the drive is told to send `4194320` (Speed, Hz)
* **rps** -> the drive is told to send `4194326` (Work Zone: Speed, RPS), shown as it is
* **rpm** -> the drive is told to send `4194326`, and the tool shows it times 60

Only the slot that already carries the speed is replaced (both values are the same size, so nothing else in the message
moves). If the drive's telemetry has no speed at all, only the display unit changes. Nothing is written when you
connect, and a drive that sends Hz is shown in Hz until you pick rpm or rps. The change is also picked up if you edit
the mapping by hand in the Parameters Explorer: every write into a mapping register makes the tool read the layout again.
If someone changes the mapping with another program while this tool is connected, reconnect.

**Safety:** STOP (the round button in the ribbon, or `Esc` from any page) sends the drive's *Stop* command and resets the target to 0. Disconnecting or
closing the window while the motor runs stops it first. The target speed can never exceed the *Speed limit* box.
A speed command is re-sent every 100 ms while running (the drive stops itself 3.5 s after its last message).
The ESC RPM command has not been tried on the bench drive yet (see "Still to confirm").

### Before the first run with a motor: the drive must be activated

A drive with no activation key reports fault bit 15 `0x8000` = **"Activation Key is Missing"** and Operation Mode
**Fault**. Per the Device Reference a faulted drive **ignores every command except Reset**, so it will not spin a motor
until it is activated. The activation key is issued by Stimita technical support; for it they need the drive's
**Device Type**, **Device Serial Number** and **Firmware Serial Number** (System Configuration > Item Configuration
shows them after you connect). The key is written to **Activation Key** right there on that same page (System
Configuration > Item Configuration > *Product Activation*, `0x20FF:0x02`) - confirmed working on the bench drive
(2026-09-22): entering the real key and pressing Write cleared fault 15 at once, no Reset or power cycle needed.

## Scrolling a page never changes a value

Every spin box, combo box and slider in this tool - a target speed or position, a parameter's enum value, the CAN
interface or bit rate, a mapping code, anything - ignores the mouse wheel (`sdr_tuner/gui/widgets.py`: `SpinBox`,
`DoubleSpinBox`, `ComboBox`, `Slider`, built once and used everywhere instead of the plain Qt classes). By default Qt
lets any of these change value on a wheel scroll even without focus, which is a real hazard in a tool that is almost
all scrollable forms: **caught live (2026-09-22)** on a recording where scrolling down the Single Axis Motion page
changed a new MOVE TO target from 0 to -3 counts just by passing the mouse over the field on the way past - if that
had been ENABLE's target on a real drive under load, it would have moved. The value can still be typed, or changed
with the arrows/steppers/a click; only the wheel is given up, and scrolling now always scrolls the page instead.

## G-code

The dialect is the drive maker's own **motion-controller G-code** (its G-code reference, revision D, December 2023) -
the same one the vendor's own IDE (`Servo-Drive-IDE.exe`, a copy of it in `Servo tunning tool-original/`) speaks.
Parsing lives in
`sdr_tuner/gui/gcode.py` (no Qt - a pure `parse(line)` function); translating a parsed line into this drive's own
commands lives in `sdr_tuner/gui/gcode_runner.py` (`GcodeRunner`), shared by both places G-code can be typed:

- **Terminal** (`log_tab.py`) has a single-line console at the top: type one line, press RUN (or Enter).
- **Motion Controller** (`motion_controller.py`, its own page right after Terminal) is a small editor for a whole
  **program** (several lines) - RUN executes it top to bottom, one line at a time, stopping at the first line that
  fails rather than guessing and carrying on; STOP aborts the program and stops the drive, the same way the round
  STOP button does. Here, unlike the single-line console, **G04 (Dwell) genuinely pauses** before the next line -
  the single-line console has no "next line" to pause before, so there it is only ever a countdown. The line
  currently executing is highlighted. The editor (`GcodeEditor`) shows its own **line numbers** down the left edge,
  matching the numbers the output log below already reports ("Line N: ..."). A **Samples** menu loads one of a few
  bundled example programs
  (`sdr_tuner/data/gcode_samples/*.nc`) into the editor; **Open…**/**Save…** work with any `.nc` file - **Save…**
  defaults to that same samples folder, so naming and saving a program there makes it a selectable Sample from then
  on, in this and future sessions.

Both places send exactly what a Single Axis Motion page's own buttons would - the same client calls, the same
Feedback Settings safety check, the same continuous-repeat mechanism - never a separate, parallel implementation.
The tool can now connect to more than one drive at once (**Additional Axes**, at the top of the Axes page under Motion - see *Multi-
axis* below); a G-code line commands whichever of ITS OWN named axes are actually connected:

| G-code | Action | This tool's command |
|---|---|---|
| `G1000` | Reset | `client.reset()` |
| `G1001` | Off | `client.off()` |
| `G1002` | Stop | `client.stop()` |
| `G1010` | Capture (energize, hold at the current position) | same as **ENABLE**: `client.hold_position(current wz_count)`, Feedback Settings check |
| `G1011` | Release (stop repeating - no explicit stop sent, the drive is left to time out on its own heartbeat) | `client.end_repeat()` |
| `G00` / `G1020` | Move to a target | same as **MOVE TO** / **MOVE BY**: `client.hold_position(target)`, Feedback Settings check |
| `G01` | Linear move at a feed rate | `client.move_linear(target, feed_rate)` - **F is always counts/sec**, this tool does not track G93/G94 |
| `G1030` | Speed (RPM) | `client.set_speed_rpm(rpm)` |
| `G1040` | Torque (N·m) | `client.set_torque(nm)` |
| `G02` / `G03` | Arc (clockwise / counterclockwise), by radius `R` or centre offset `I`/`J`/`K` | discretised into waypoints (`sdr_tuner/gui/trajectory.py`) and dispatched one at a time, each the same as `G00`/`G01` above - see *Arcs* below |
| `G04 P<seconds>` | Dwell | Motion Controller: a real pause before the next line. Terminal's console: a countdown only, no next line to pause before |

**An axis selector identifies a drive, but is NOT the same number as its CAN node ID** (`X`/`Y`/`Z`/`A`/`B`/`C`
name axes 0-5, `U`/`V`/`W` the same axes 0-2 relative, `[N]`/`P<n>` name axis `N` explicitly) - **node ID = axis +
1**, since a real CAN node ID is never 0 (1-127 only), so axis 0 is simply wired as the first valid node (the
user's own correction: "node id 1 means axis 0, node is 2 means axis 1"). On the real, multi-axis system a line
only ever affects the axis(es) it names. With several drives connected (see *Multi-axis* below), a line naming
several axes commands EACH one whose own node is actually connected, in turn, combining their results into one
status message (e.g. `G1030 [1]300 [2]-300`, with axis 1 (node 2) and axis 2 (node 3) both connected, commands
both, reporting `"Axis 2: Commanding 300.0 rpm.; Axis 3: Commanding -300.0 rpm."` - labelled by the real node, since
several DRIVES answered); an axis it names whose own node is **not** connected is silently not reached, exactly as
it would not affect that other, absent drive on a real system - with only ONE of a line's named axes actually
connected (the common case), the result reads exactly as it always did, with no "Axis N:" label. A no-axis
`G1000`/`G1001`/`G1002`/`G1010`/`G1011` (no `P<n>` at all) means "every connected axis", not just the primary one.
`U`/`V`/`W` give a target **relative** to that axis's own current Work Zone Count (read from the broadcast, never
requested) instead of absolute; this tool has no G90/G91 modal state to track across lines, so relative moves always
use U/V/W, per-line, exactly as the real dialect's own stateless alternative to G91 does. The bundled sample
programs all address axis 0 (`X`/`[0]`, i.e. node 1) - **change that to match your connected drive's own node ID
before running them** (the Motion Controller's Samples menu and its own log say so too). Comments in parentheses,
or after `;`, are stripped - each on its own physical line (a `(...)` comment that wraps across two lines is not
recognised as one comment, since nothing here tracks state between lines other than the running program itself).

**`Txx` (Inverse Kinematics) works once a `.kinematics` model is loaded** (the Axes page's *Kinematics Model*
group - see *Inverse Kinematics* below) - `G00`/`G01`/`G1020`/`G02`/`G03 Txx X.. Y.. Z..` solves the named tool's
own chain for that Cartesian target (mm) and dispatches the result to each of the chain's own joints, through the
same multi-axis mechanism as any other line. With no model loaded, or an unknown tool number, or not every one of
that chain's own axes connected, the line is refused with a clear reason instead of a guess.

**Several G-codes may be concatenated on one physical line** (`gcode.parse_line()`), the way real multi-axis
programs move several axes/tools together in one step - the vendor's own `DOG.NC` demo does this for its gait, four
tab-separated commands (each its own `Txx`) on one line. Each segment is parsed and dispatched exactly as it would
be on its own line; the Terminal console and Motion Controller both support it, combining every segment's own
result into one message (`"T3: ...; T12: ...; T6: ...; T9: ..."` for more than one - a single command on a line
still reads exactly as it always did, with no label).

**Accepted as harmless no-ops, with a note** (so a line copied from a real multi-axis program does not hard-fail
on unrelated modal setup): `G09`/`G20`/`G21`/`G61`/`G64`/`G90`/`G91`/`G93`/`G94`/`G1050`.

### Arcs (G02/G03)

This tool does not track a G17/G18/G19 modal plane, so an arc's own plane is simply whichever two axes the line
itself gives a target for - a plain arc names two of the drive's own raw axes (`G02 X1000 Y2000 R1000`, axes 0 and
1); a `Txx` arc gives exactly two of `X`/`Y`/`Z` (the vendor's own real line, verbatim: `G03 T6 Z-400 X0 R500` -
`X`/`Z` given, `Y` not - the arc lies in the `XZ` plane, `Y` held wherever the tool's tip already is). The centre
comes from a radius `R` (signed: positive picks the arc that sweeps ≤180°, negative the one that sweeps >180°) or a
centre offset `I`/`J`/`K` (only paired with axes 0/1/2, i.e. `X`/`Y`/`Z`, in this tool) - exactly one of the two,
whichever the line gives. `F` (feed rate) is optional here too, just like `G00`/`G01`: with one, each waypoint is
timed by its own largest single-axis (or, for `Txx`, single-joint) delta divided by `F`; without one - the vendor's
own `DOG.NC` never gives its arcs an `F`, relying on a modal `G93`/`G94` this tool does not track - it is a "rapid"
arc, waypoints sent back-to-back as fast as the bus allows, matching `G00`'s own instant target. A `Txx` arc solves
Inverse Kinematics once per waypoint, each search seeded from the previous waypoint's own solution. The arc's own
START point is always wherever the drive (or the chain's own tool tip) already is - never something a line can
give directly, since `parse()` has no idea what that currently is (`gcode_runner.py` fills it in via telemetry, or
forward kinematics for `Txx`). See `sdr_tuner/gui/trajectory.py` for the underlying geometry.

## Multi-axis

**Additional Axes**, at the top of the Axes page (Motion), connects more servo drives on the SAME CAN bus as the primary
connection above it (`Session.connect_axis`) - real multi-axis: several drives coordinated by one G-code program,
the way the vendor's own multi-axis controller works. Each
additional axis needs its own Node ID and appears as a FLAT sibling of the primary drive in the workspace tree
(never an expandable subtree of its own), named **`DriveNN (SDR)`** (`NN` its own real node ID, zero-padded like
the primary's own default "Drive01") - the same naming shown in the Axes page's own Additional Axes list and
the Axes status page. Additional axes are always listed in ascending node-ID order, primary first, regardless of
the order they were actually connected in (connecting axis 5 before axis 3 still lists 3 above 5). Selecting it -
exactly like selecting the primary drive's own item - re-points the SAME
**Drive Setup and Motion** tool list below the tree at its own pages: **Quick Tuning**, **Advanced Tuning**,
**Motion** (RUN/STOP/telemetry), **Recorder**, **Parameters** and **Telemetry Explorer**, and **Peripherals** -
each entirely independent of the primary drive's own pages (its own connection, its own rows, its own
reads/writes/recordings/tuning run) - only those 7 rows change meaning; **Terminal**, **Motion Controller** and
**Axes** stay exactly as they are in that same list, since each is one shared, workspace-level page regardless of
which drive is currently selected (a G-code program coordinates several axes from one file; the Axes page shows
every one of them at once; the **2D Plotter** traces any two of them against each other). The single **Recording**
and **Drive Quick/Advanced Tuner** ribbon tabs always act on
whichever page of that kind is currently open, primary or an axis's own. **Stopping is scoped correctly**: an
axis's own round-equivalent STOP
(its Motion page) ends only THAT axis's own running automatic tuning, never a different axis's or the primary's;
the round STOP button (and the ribbon's) still ends every connected axis's motor AND its own tuning run, all at
once. The drive's own dedicated USB cable reaches exactly one drive and can never be used for more than one axis -
a shared CAN adapter (PCAN, Kvaser, CANable, SocketCAN, Vector) is needed. The **Axes** page shows every connected
axis at once (node, online, position, speed, fault bits); Motion Controller's RUN/PAUSE/RESUME/STOP and Loop act
across every axis a program's lines address, and its own STOP stops every connected axis too. Every page is now
per-axis. Never tried on real hardware - built and tested entirely against the simulator (two or three independent
simulated drives at once); real validation needs a second drive and a shared CAN adapter.

## Inverse Kinematics

This tool has no robot geometry of its own, so a G-code `Txx` line needs a **`.kinematics` model loaded** first
(the Axes page's *Kinematics Model* group: **Open…** any `.kinematics` file, or pick one of the bundled
**Samples** - `sdr_tuner/data/kinematics_samples/*.kinematics`, the vendor's own demo robots, kept verbatim).
`sdr_tuner/kinematics.py` parses the vendor's own file format (one row per body/joint - a name, a parent, the
0-based axis index it is driven by (the same numbering G-code's own axis letters/brackets use - **not** a CAN node
ID directly, see *G-code* above), its rotation/translation axis, and, on an end-effector row, a Tool Tip) into one
or more
**chains** - the joints that move together to place one tool. Forward and inverse kinematics use the **Product of
Exponentials** formulation (each joint's own axis given once, in a single fixed frame - see the module's own
docstring for why this reading of the file was chosen; the tip's home ORIENTATION - needed for `solve_pose` below -
is taken to be the fixed frame's own identity, the natural default since the file gives no other frame to prefer),
solved with damped least squares and a numeric Jacobian, the same code for a 3-axis turret or a 34-axis humanoid.

Two solvers exist, for two different callers: **`Chain.solve`** is **position-only** (an X/Y/Z target, no
orientation) - what a G-code `Txx` line uses, since G-code's own move commands have no orientation to give it.
**`Chain.solve_pose`** (2026-09-24) solves a **FULL pose** - position AND orientation together - for Item
the Axes page's own **Tool Pose (Orientation IK)** GUI control (see *Where do I find X?* in `OPERATIONAL_MANUAL.md`),
which has no G-code line to parse a target from and so can ask for whatever it likes; `kinematics.euler_to_matrix`
turns a human-typed roll/pitch/yaw (degrees) into the rotation matrix it needs. The GUI's own dispatch
(`gcode_runner.py`'s `move_to_pose`) asks for a looser position tolerance (0.05 mm) than the solver's own default
(1 micron) - found live against a real drive: the GUI's own input fields only offer 2 decimal places, coarser than
that default tolerance, so a genuinely reachable typed target could otherwise fail to "converge" purely from
rounding.

**Counts per unit**, next to the loaded model's own summary, converts the model's units (degrees for a rotary
joint, mm for a linear one) into this drive's own raw Work Zone Count - there is no way to derive this
automatically, so it defaults to 1.0 per axis until set. **Tool numbering** follows the vendor's own `DOG.NC` demo
program (a bundled G-code sample - see *G-code* above): a chain's tool number is its end-effector's own axis
number plus 1. A `.kinematics` file's own "Axis" column is a 0-based axis index, the same numbering G-code's own
axis letters/brackets use - **not** a CAN node ID directly: node ID = axis + 1 (node IDs are never 0), so axis 0 is
simply the first axis, reachable as node 1 - `gcode_runner.py`'s own `_axis_to_node()` does this conversion at
dispatch time. IMU getters
(`sdr_tuner.kinematics.read_imu`) exist as a placeholder, keyed off the model's own per-body IMU column - always
"not present" today, since no real IMU hardware exists on this drive. **`Chain.solve_pose`/Orientation IK confirmed
working against the real bench drive (2026-09-24)**, both via a headless script and live in the running GUI - a
single-joint test model (`bench_node7.kinematics`, node 7's own axis), since only one physical drive exists on the
bench; correctly solves, then correctly refuses to dispatch (no Motor/Servo Encoder configured on this drive, the
same reason MOVE TO/BY refuse). Real multi-axis IK (more than one physical drive) is still simulator-only.

## Tests

```
.venv/bin/python -m pytest
```

Protocol frames (against the Device Reference figures and real captures), the definitions imported from the PDF, every
configuration and telemetry parameter read / written / verified against the simulator, the speed / stop / off / reset
commands, the keep-alive and the safe stop, telemetry mapping and rate, the USB text-frame transport (including three
frames captured from a real drive), the real window driven like a user (connect, read all, edit and write every
parameter type, power-cycle warnings, invalid input, run / stop / off / reset, fault names, the speed unit choice and the
mapping codes, that nothing is decoded before the layout is read, link loss,
saved settings, USB connection with node detection, the Telemetry Explorer, and that the tool asks the drive for nothing while it shows the broadcast), and the
two hardware tools below (their safety guards, a full dry run against the simulator, and injected failures).
They need no hardware and no display.

## What the values are, and how often they update

**The Single Axis Motion page shows only what the drive itself broadcasts** and asks the drive for nothing. Each of the drive's 3
telemetry messages arrives about 3 times per second, so 9 messages per second in total (measured on your drive over USB;
the simulator sends 100 per second). The readouts and plots are redrawn every 100 ms. When you press CONNECT the tool
reads three things, **once**: the pole count, the drive's telemetry mapping and who the drive is (16 read requests). After that it only listens.
The only other reads are the ones you start yourself: READ ALL / Read on the Parameters Explorer and Telemetry Explorer, and the
layout read that follows a write into a mapping register.

What the drive broadcasts by default (revision L):

| Message | Contents |
|---|---|
| `0x180` + node | Fault Bits, DC bus voltage, Speed (electrical frequency) |
| `0x280` + node | Operation Mode, Commutation Mode, Work Zone Count |
| `0x380` + node | Phase A current, Work Zone Modulo |

**The cards and plots are built from the drive's own telemetry mapping**, read once at connect: one card for every value
the drive sends, and nothing for values it does not send. With the default broadcast above there is no Phase B card
(Phase B is not in it), no speed plot on a drive that does not send speed, and so on. A value the tool has no special card
for is shown with its own name and unit from the reference. The tool writes the mapping only for the speed unit choice above.

The drive's own way to change the broadcast is in the Parameters Explorer: **CANopen: telemetry frequency** (`0x3000:0x05`,
total for the 3 messages - measured on the bench drive 2026-09-25: it applies IMMEDIATELY, no power cycle, and the drive caps at 1000 messages/s = 333 Hz per message, see `tools/telemetry_rate_probe.py`) and the three **Telemetry Mapping** sections (`0x1A00`-`0x1A02`,
up to 4 parameters per message; the code is the decimal of the hex `index` + `sub-index`, e.g. 4194320 for `0x4000:0x10`).
`tools/usb_readonly_check.py` prints every slot with the code it holds and the parameter that code stands for.

The client library can also poll values by request (`client.start_polling(...)`); only the bench tool
`tools/usb_readonly_check.py` uses it, to measure how fast the drive answers.

## Extending it when the Device Reference grows

Nothing is hard-coded in the GUI - it is built from three files in `sdr_tuner/data/`, which
`tools/import_device_reference.py` generates from the Device Reference PDF:

```
.venv/bin/python -m pip install pymupdf
.venv/bin/python tools/import_device_reference.py "/path/to/Stimita-Device-Reference-0xA020192.pdf"
```

* `registers.json` - the configuration and telemetry parameters (section, name, units, type, index, sub-index, access,
  options, min / max, description, power-cycle flag).
* `commands.json` - all 32 commands (COB, code, parameters with payload position and type, whether they must be re-sent).
  The tool itself uses ESC RPM (the Single Axis Motion page), ESC Hz (the write-check bench tool), Stop, Off, Reset and Reset Work Zone. The three commands with a 24-bit float
  (velocity / torque feed-forward) cannot be encoded yet and are refused with a clear message.
* `telemetry.json` - the default telemetry messages (TPDO) and the fault-bit names.

`python -c "from sdr_tuner import defs; print(defs.check_definitions())"` checks the files (duplicate addresses,
bad types, telemetry that does not match its parameter); the test suite runs it too.
The list of settings that need a power cycle (`POWER_CYCLE` in the importer) comes from the vendor's own tool - the PDF
does not say which ones (see "Notes on the PDF").

## Checking a drive on USB

Two small tools for a drive on its USB cable. Both put a **guard** between the tool and the port that refuses to send
anything outside its allow-list, and both print exactly what they sent.

```
.venv/bin/python tools/usb_readonly_check.py /dev/cu.usbmodem...
.venv/bin/python tools/usb_write_check.py    /dev/cu.usbmodem...        # motor NOT connected, DC bus unpowered
```

* `usb_readonly_check.py` - read requests only. Finds the node, reads the drive's telemetry mapping, all configuration
  and telemetry parameters, measures the telemetry rate and how fast polling can go. It prints to the terminal; add
  `| tee tools/last_usb_check.txt` to keep it.
* `usb_write_check.py` - saves a backup of all readable configuration parameters (`tools/backups/`, in the tool's own
  file format: load it in the Parameters Explorer and press WRITE CHANGED to restore by hand), writes 6 unused encoder /
  work-zone parameters to a new value and back, then sends a 5 Hz speed command and the Stop command. It only sends a
  speed command when the DC bus reads below 2 V, stops immediately if the bus appears, and puts every original value
  back in a `finally` block. Its guard lets through only reads, those 6 writes, 5.0 / 0.0 Hz speed frames and the Stop
  command. Record of the last run on the bench drive (before revision L): `tools/last_usb_write_check.txt`.
* `telemetry_rate_probe.py` - finds the usable ceiling of `CANopen: telemetry frequency`: reads it, steps it up
  (30, 100, 300, 1000, 3000 Hz) while counting the frames really received per TPDO message, stops as soon as
  reads degrade or the rate stops rising, and restores the original in a `finally` block. Its guard lets through
  only reads and writes of that one parameter to exactly those values. Bench drive, 2026-09-25: applies at once
  (no power cycle), caps at 1000 messages/s = 333 Hz per message, SDO reads still 1 ms.
* `test_signal_probe.py` - records the RAW `Reference: Speed` / `Speed` telemetry (mapped alone into message 0 at the
  drive's timer rate) while the drive's own ESC test runs the wave form and period you list, fits a sine at the
  expected frequency and writes each window to CSV; guard, restore and read-back like the others. **The motor moves.**
  This is what found (2026-09-25) that "FLOAT16" is fixed point x256 - a 1 s Period ran for 60 s until then.
  A second pass showed the 1000/s is a firmware cap (1200-10000 Hz all give 1000) shared by however many messages are
  mapped: with messages 1 and 2 unmapped, message 0 alone arrives at 1000 frames/s. To sample a few signals faster, map
  them (up to 8 bytes) into ONE message. The cap itself is the drive's timer interrupt, which `CANopen: receiving
  frequency` sets AT BOOT: written 2000, saved to flash (see item 5 below) and power-cycled, the drive reported a
  2000 Hz Timer ISR and sent 2000 messages/s (SDO still 0-1 ms, no faults). The bench drive was put back to 1000.

`tools/backups/node7_20260921_174523.json` holds the bench drive's first 86 parameters as they were before any test
(the current tool reads 243, all of which include those 86 addresses).

## Confirmed on a real drive (SDR-CB-60R, node 7, USB, no motor, DC bus unpowered)

Everything here was measured before the revision L data was added; the 86 parameters tested are all still in it, at the same addresses.

* **The 86 parameters tested answer** at the addresses and with the data types of the Device Reference; round trip about 1 ms.
* **The USB port is opened exclusively** (pyserial `exclusive=True`, 2026-09-25): a second program opening it - another
  instance of this tool while one is connected, which is what happened - is refused with a message naming the cause,
  and the connected one keeps working; before, both shared the port and both failed with a cryptic read error.
* Values are sent **little-endian**; telemetry CAN IDs are COB ID + node ID. **"FLOAT16" is NOT a half-precision float
  on this drive: it is a 16-bit fixed-point number, the value x 256** (measured 2026-09-25, see below) - the reference
  only names the type.
  The broadcast values equal the values read by request.
* **Writes work** for every data type (FLOAT32, BOOL, UINT16, UINT32, INT32, option list): each is **acknowledged** with
  `0x60` + index + sub-index + the value echoed, and reads back correctly. Written values were restored and all 86
  parameters were identical to the backup afterwards.
* **Speed command**: the ESC frame (`0x207`, `20 00 00 00` + FLOAT32) is accepted and never answered; nothing changed
  (the drive was faulted, see the activation section above, and ignores commands).
* The drive broadcasts telemetry only **3 times per second** per message (9 messages per second for its 3 messages).
  Asking for the values by request also works (round trip about 1 ms), but the tool does not do that any more.
* **"FLOAT16" is fixed point (value x 256), found 2026-09-25 - and it explains three earlier findings.** The first
  real Bode sweep fitted nothing: the drive's sine test asked for a 1 s period ran a 60 s one. Working back from four
  periods (10 s, 1 s, 1/60 s, 1/600 s -> 72.9, 60.0, 36.3, 22.7 s measured) the period the drive ran was always the
  16-bit pattern sent, divided by 256; with the period sent as raw 256 (1.0 x 256) the reference was a clean +-6 Hz
  sine at exactly 1 Hz, raw 64 a clean 4 Hz one, and the square wave toggled every half period. The DC-bus
  `Voltage: DC bus (Udc)` register (`0x5001:0x04`, "FLOAT16") read raw `0x180A` = 6154 / 256 = 24.04 V while the
  FLOAT32 twin (`0x5001:0x0D`) said 24.03 V. So: (1) the "always ~0 V" DC-bus reading of 2026-09-22 was this tool
  decoding 24 V x 256 as a half float (0.002), not a firmware defect - both registers decode to `vbus` and the bench
  drive's remapped slot is harmless, just no longer necessary; (2) the 2026-09-24 Step Response Test (Period 0.5 s)
  never toggled - it held +3 Hz for the whole 56 s period it was really given; (3) **Direct Field Control's "0.1 V"
  that tripped the overcurrent protection was really 46.4 V** (0.1 as a half float is 0x2E66 = 11878 / 256) - the
  protection did its job, the burn-risk warning stands, and the command is untested at a REAL 0.1 V. `protocol.py`
  now encodes and decodes "FLOAT16" as a signed 16-bit integer of the value x 256 (step 1/256; the sign convention
  for a negative value - only the Brushed channel voltages can be negative - is assumed two's complement, untested).
  Also affected and now correct: the four test commands' Period, the advanced automatic configuration's current
  limit and General System Dynamics (0xD1 - never run on a real drive), the Brushed channel voltages, and the EDS
  export (INTEGER16 with an x256 note, not UNSIGNED16).
* **The drive's own test sine drifts off the asked frequency the longer one test runs (2026-09-25).** Held at 128 Hz
  (Iq test, 0.5 A, `tools/`-style guarded capture): 128.00 Hz half a second in, 127.87 at 2 s, 128.51 at 5 s, 127.23
  from 10 to 30 s, 132.35 Hz at 40 s - a clean sine each time (fit quality 1.000 at the real frequency; at the asked
  one 0.98, 0.79, 0.61 and 0.01). A Stop resets it (fresh: 128.00 again). At 43 and 64 Hz the drift is smaller but
  the same. Looks like an accumulated-phase precision effect in the firmware's generator (worth telling Stimita); on
  this tool it is why a long Frequency Response sweep lost every point above ~20 Hz until the page was changed to
  send a Stop before every point and to fit each point at the frequency the reference really shows
  (`bode.refine_frequency`, +-6 %). A Step Response Test left running for minutes is affected the same way.
* **The current loop's ~51 Hz bandwidth is real, and it is exactly what the auto-tuned gains say (2026-09-25).**
  Cross-checked with the Iq test as a SQUARE wave (+-0.5 A, an edge every 99.6 ms) captured at 1000 Hz
  (`scratchpad/iq_step_probe.py`, guarded like the tools): the `FOC: Iq Current` estimate rises 10-90 % in 8.8 ms
  with 11 % overshoot, and the RAW `Phase B current` (ADC) rises in 8.0 ms with 9 % - the same, so the roll-off is
  the loop, not filtering in the telemetry. Read-only: FOC Kp = 0.3035 V/A, FOC T = 1.671 ms, phase resistance
  1.141 ohm and inductance 1.907 mH line-to-line (0.57 ohm / 0.954 mH per phase, L/R = 1.67 ms = FOC T: the
  auto-tuner cancels the motor's electrical pole with the PI zero), so the closed current loop is first order with
  bandwidth Kp / L = 0.3035 / 0.000954 = 318 rad/s = **50.6 Hz** - the sweep measured 50.7 Hz. The Bode page is
  measuring the real loop to within a hertz. **Done the same day (user: "raise FOC Kp to 1 V/A and sweep again"):
  Kp written 0.3035 -> 1.0 V/A (read back; then SAVED TO FLASH at the user's request through the ribbon's own
  Save to Flash button - acknowledged, node 1, together with the node ID, bit rate 1000, receiving 1000, telemetry
  9 and the Speed mapping, all as intended) and the same 26-point sweep
  gave -3 dB at 154 Hz, -39 deg there, no peaking (gain never above +0.2 dB), every fit at quality 0.9 -
  against 167 Hz predicted by Kp/L; the -3 dB point is interpolated between the 128 and 256 Hz points, the coarse
  end of the sweep, so that is within the measurement's resolution.** Higher Kp: within what the 41.7 kHz PWM and
  current-sense noise allow - a sweep after each change shows it, and a peak appearing is the sign to stop.
* **Wave form 3, the drive's own frequency sweep, measured (2026-09-25, user: "run the chirp on the iq loop and
  check the range").** Iq loop, 0.5 A, captured at 1000 Hz (`scratchpad/chirp_probe.py`): it starts at **1/Period**
  (1.05 Hz for Period 1 s, 10.4 Hz for 0.1 s), rises faster than exponentially and reaches a few hundred hertz
  (the zero crossings say 400-500 Hz, close to the 1000 Hz sampling's limit) only in the last few percent of a
  cycle of **10 x Period** (1.016 s for the fixed-point 0.1016 s, ~10.5 s for 1 s), then starts over; the
  amplitude stays constant. A Welch cross-spectrum of the capture reproduces the stepped-sine current-loop plot
  where the coherence is good, which at 1000 Hz sampling is only up to ~60-100 Hz - the sweep spends too little
  time up high. For a resonance search the chirp would need a long Period (4 s or more, ~2 s in the 100-500 Hz
  band) and the 2000 Hz timer; the stepped sine's own limit up there is the 1/256 s period step (only 64, 85, 128,
  256 Hz). **The page now has a Chirp method** (same day, user: "add a chirp mode to the frequency response page"):
  Period and Cycles, the drive's sweep recorded, `bode.welch_response` (Hann, 50 % overlap, H = Pxy/Pxx, coherence)
  and `bode.chirp_points` (12 bands per octave, quality = coherence); the simulator's Wave Forms 3-5 follow the
  measured sweep shape (`bode.chirp_frequency`). Bandwidth and any peak in the summary are now judged on the
  trustworthy points only (quality at or above the floor), for both methods - the very first real sweep's "+63 dB
  at 13 Hz" came from a hollow point. **What made the chirp usable** (found on its first real run, which was clean
  only to 19 Hz): a sweep excites each frequency only while it passes it, and the Iq telemetry carries ~0.12 A rms of
  broadband noise, so a plain Welch average summed 40 s of noise against 0.3 s of signal per bin. `welch_response`
  now counts, for every bin, only the segments in which the reference's energy there is within 10 dB of the best
  one (`EXCITED_FRACTION`), with 75 % overlap, and `chirp_response` uses 2 s segments below 20 Hz and 0.25 s ones
  above (`CHIRP_SPLIT_HZ`). Real drive, Kp = 1.0: Period 0.1 s x 6 cycles -> a clean curve 10-220 Hz (-3 dB
  123 Hz); Period 0.5 s x 3 -> 3.5-210 Hz (-3 dB 181 Hz); the stepped sine said 154 Hz - the methods agree to ~1 dB
  at 130-180 Hz. A LONG Period is the wrong instinct: the sweep spends ~95 % of its cycle in the first 25 x (1/Period).
* **Automatic Test page (under Advanced Tuning → Verification since 2026-09-25; 2026-09-25, user: "just press one button and software will perform some test and give you
  the result / press another button and it will show what to change and if user agree then software automatically
  apply it and check again ... name 2nd function as beta").** `sdr_tuner/autotest.py` (no Qt, runs in a worker):
  `run_tests` = read-only checks (`health_checks`), optionally the drive's own automatic configuration first
  (`run_autoconfiguration`, the tuner page's rules in one synchronous function: the command once, keep-alive reads,
  completion = the Datasheet changed, a capped wait for Operation Mode, Reset on STOP), then `measure_loop` on the
  current loop and - with an encoder - the speed loop (the Frequency Response page's switch-telemetry / step test /
  chirp / restore, synchronously; `step_metrics`, `chirp_metrics`). `propose` is Beta's policy, "as fast as the drive
  allows" (the user's choice): FOC T to L/R, FOC Kp x1.5 per round while clean (peak <= 1 dB, overshoot <= 20 %), back
  to the last clean gain when a limit appears, stop at 0.3 x the sampling rate or at `kp_cap` (control rate / 20), a
  notch on a speed-loop resonance, ESC Kp likewise; `apply` writes and reads back. The page (`gui/autotest_page.py`)
  asks once for RUN TESTS and once for BETA (the plan and its limits), then runs up to `MAX_ROUNDS` = 5 rounds on its
  own, logs each round, and has its own SAVE TO FLASH. Per drive (an additional axis gets its own page). The
  simulator's current loop now speeds up in proportion to FOC Kp (as the real drive's does) so Beta can be tested
  end to end on it.
* **Nyquist plot on the Frequency Response page (2026-09-25, user: "build the nyquist plot").** `bode.open_loop`
  turns each measured closed-loop point into L = T / (1 - T) (unity feedback, the reference at the summing point);
  `bode.margins` reads the phase margin at the gain crossover (|L| = 1) and the gain margin at the phase crossover
  (-180 deg), interpolated in log frequency between the trustworthy points, None where the crossing is outside the
  range; the page draws L in the complex plane with the unit circle, the -1 point and the crossover, fixed to
  +-2.5 (the pass band's |L| is huge and mostly noise - the margins live near the unit circle); the summary and the
  CSV carry the margins / the open loop. Checked offline against the saved real sweeps before building: the bench
  drive's current loop comes out as the integrator Kp/(sL) the PI theory predicts - phase -82..-95 deg across 1-256
  Hz, 93 deg of margin at 53 Hz (Kp 0.30) and 94 deg at 190 Hz (Kp 1.0), no phase crossover in range. Tests: a
  first-order lag (integrator, 90 deg, no gain margin) and a three-lag loop whose margins from the closed-loop
  points match the ones computed from L directly. **Beta uses them as a stopping rule** (same day, user: "add phase
  margin floor as beta stopping rule"): `autotest.Measurement` carries `bode.margins` of the chirp; `_limits_hit`
  counts a phase margin under `PHASE_MARGIN_FLOOR_DEG` = 45 or a gain margin under `GAIN_MARGIN_FLOOR_DB` = 6 like
  peaking or overshoot (back to the last clean gain), but only where the crossing is inside the measured range - an
  unreadable margin is not a failed one; the result table has a "margins" row per loop. **Tried on the bench drive the same day (user: "run beta on the real drive"), automatic tuning
  off:** RUN TESTS - 13 OK, 1 to check (sensorless), 1 skipped (speed loop), current loop -3 dB 186 Hz at Kp 1.0
  against 167 predicted, 2.2 ms rise, 13 % overshoot, 13 s in all. BETA - four rounds in 45 s: Kp 1.0 -> 1.5 -> 2.25
  -> 3.375 V/A, bandwidth 199 -> 227 -> 257 -> 326 Hz, overshoot 12-13 % and peaking <= +0.6 dB throughout, stopped
  because 326 Hz is past 0.3 x the 1000 Hz sampling ("as fast as the sampling can verify"); at 3.375 V/A the loop is
  no longer the first-order Kp/L (563 Hz predicted, 326 measured - the control delay shows), which the table flags.
  Then (user: "Go further: PREPARE 2000 Hz, power cycle, run Beta again") PREPARE 2000 Hz saved the configuration -
  Kp 3.375 with it - the user power-cycled, and Beta at 2000 Hz sampling changed NOTHING: at 3.375 V/A the current
  loop is flat (within 3 dB) to 440 Hz, the top the drive's own chirp reaches, 1.0 ms rise, 14 % overshoot, +0.3 dB;
  the 326 Hz "bandwidth" seen at 1000 Hz was the sampling's own limit, not the loop's. The next step (5.06 V/A,
  845 Hz predicted) cannot be verified by any test this drive offers (the chirp tops out near 450 Hz, the stepped
  sine at 256 Hz), so Beta stops there - the honest ceiling of the measurement, not of the loop. The user then chose
  "BACK TO 1000 Hz, step back to 2.25 V/A for margin against noise under a real load": Kp written 2.25, BACK TO 1000 Hz
  saved everything, a power cycle, and a read-only read-back confirmed **the bench drive's flash: node 1, bit rate
  1000, receiving 1000 Hz (Timer ISR 1000), telemetry 9, the Speed layout, FOC Kp 2.25 V/A, FOC T 1.67 ms** - a
  257 Hz current loop, one step below what the measurement could verify.
* The three captured broadcast frames decode with the revision L mapping to: fault bits `0x8000` (Activation Key is
  Missing), Operation Mode 2 (Fault), Commutation Mode 1 (Kickstart), DC bus 23.99 V (fixed point - it read "about
  0 V" while FLOAT16 was decoded as a half float), everything else 0.
  (Earlier versions of this tool decoded them with a different layout and showed the mode number as an electrical angle.)

## Still to confirm

1. **Stop, Off and Reset on a real drive** - the tool sends them as the reference describes (Stop `0x04`, Off `0x06`,
   Reset `0x01`, all on COB `0x200`, no parameters), but none has been tried on the bench drive yet. The simulator and
   the tests only prove the tool sends what the reference says. Whether a **Reset clears "Activation Key is Missing"** is
   also not known: the simulator assumes it does not. What the user reports from the real drive (not tried by this tool): during the automatic configuration Stop does nothing (it works in the velocity, position and other modes) while Reset ends the procedure, clears errors and cuts the current supply to the motor.
2. **Speed command with a powered motor** - not tried yet (needs an activated drive, a motor and a DC bus). The DRIVE
   tab sends the ESC RPM command (`0x24`), which has never been sent to the bench drive (the earlier test sent ESC Hz).
   Check the commanded and displayed rpm agree once a motor turns. *Work Zone: Speed* (the rps value behind the rpm
   display) is, per the reference, the speed of the *payload* measured with a servo encoder or estimated from the motor
   encoder, Hall sensors or sensorless: with a gearbox it is not the motor shaft speed.
2b. **Writing the telemetry mapping on the real drive** - confirmed 2026-09-22: a write to a mapping register (`0x1A00:0x02`,
   remapping the DC bus slot - see the note on that below) took effect on the live broadcast immediately, no power cycle
   needed. Still not tried: whether it *survives* a power cycle, and the speed-unit case specifically (Hz vs rps/rpm)
   with a motor running - check that the Hz value then equals rps times the pole pairs.
3. **Speed command keep-alive** - the ESC command is re-sent every 100 ms while running. The drive stops itself after
   3.5 s without any message (over CAN or USB); confirm this with a motor.
4. **The 156 parameters added by revision L** were not read from the real drive yet - run
   `tools/usb_readonly_check.py` (read-only) to check them. Reading the *Activation Key* may be refused; the tool copes.
4b. **Frequency Response (Bode) - tried on the real bench drive 2026-09-25 (node 1).** The mechanism works end to end: the telemetry switch to reference + response in message 0 at the drive's timer rate (1000 Hz, then 2000 Hz after the PREPARE workflow's power cycle), ten points, the layout and rate put back exactly, no fault. The first sweep fitted nothing - the "FLOAT16" Period (see *Confirmed on a real drive*) - and with that fixed `Reference: Speed` (0x4000:0x21) follows the sine exactly (fit quality 1.000 at 1 Hz and 4 Hz, amplitude 6.00 for 6 asked). What remains real-hardware knowledge: the speed loop's RESPONSE on this sensorless, encoder-less motor is the sensorless estimate of `Speed (Electrical Frequency)`, which is noise at a 6 Hz amplitude (min -72 / max +105 Hz spikes around a 6 Hz sine), so a speed-loop Bode plot needs a larger amplitude or an encoder; the current loop's response (`FOC: Iq Current`, ADC-measured) does not have that problem: with the Stop-per-point and real-frequency fit (see *Confirmed on a real drive*, the sine drift) a 26-point 1-256 Hz sweep at 0.5 A is clean throughout - -3 dB at 50.7 Hz, -43 deg there, -13.3 dB / -72 deg at 256 Hz. Above ~25 Hz the Period's 1/256 s step makes the points coarse (256/n Hz). The motor moves continuously for the whole sweep: choose the amplitude as for a step-response test.
5. **Saving to flash - ANSWERED 2026-09-25, and it matters: a plain parameter write does NOT survive a power cycle.** Measured on the bench drive: `CANopen: receiving frequency` written 1000 -> 2000 (read back 2000), power cycle, read 1000 again; the same power cycle also put the node ID back from 1 to 7 and the telemetry mapping back to the 2026-09-22 layout (the 2026-09-24 "show Speed instead of Commutation" remap was lost) - everything written since the last automatic tuning run. The only documented save is step 8 of the automatic configuration ("Save to Flash: all the updated configuration parameters are automatically saved"), which is why the DC-bus remap and the activation key of 2026-09-22 survived: an auto-tuning ran after them. The vendor IDE has a *Save to Flash* button (`pushButtonSDOToFlash`) whose command is in no definition file - the standard CANopen store object (0x1010) is not in the drive's own reference either. Found and CONFIRMED the same day: the vendor IDE's button sends the standard CANopen store request - an SDO expedited download of `save` to `0x1010:0x01` (frame `23 10 10 01 73 61 76 65`, reply `60 10 10 01 ...`) - and the drive honours it: receiving frequency 2000 saved this way survived a power cycle. Its twin `load` to `0x1011:0x01` is Factory Defaults - never send it. Now a button: ribbon *Parameters* > **Save to Flash** (`client.save_to_flash()`, asks first), and the Frequency Response page's *PREPARE 2000 Hz* / *BACK TO 1000 Hz* use it for the receiving-frequency switch. The original text of this item followed: After entering motor values,
   power-cycle the drive and read them back. Settings marked "applies after a power cycle" need it.
6. **Whether Work Zone Count (`0x4000:0x19`) can be read by request** - the older FLOAT32 version of it (`0x4000:0x18`,
   not in revision L) could not; the new INT32 one is untested.
7. **The automatic configuration on a real drive** - not tried, and it needs a motor, an activated drive and a supplied DC bus (the
   tuner refuses to start on a fault or with the bus below 5 V). What is assumed: (a) the reference says the command is
   sent "continuously"; the tuner sends it **once** and keeps the drive informed with a read request about once a second instead (the keep-alive, separate from the once-every-3-seconds Datasheet check), on the reading that the
   heartbeat timeout counts any message - if the real drive ends the procedure after a few seconds, this is the place to change
   (`AutoTuningPage`, `heartbeat_period`; the client's `set_heartbeat`); (b) the drive answers read requests while it works (if it does not, the once-a-second readings are simply asked again and the change is seen after it has finished), and the Datasheet is written by the procedure before it computes the Control Laws (hence the wait for Operation Mode 3 to end before the final reading; if the drive never shows Operation Mode 3 the wait is only half a second; Operation Mode 3 is only a hint, a drive that never shows it is still handled); (c) what the user reports from the real drive, which the simulator now follows: Stop works in the velocity, position and other modes but **not in the configuration mode**; **Reset** ends the procedure, clears the drive's errors and cuts the current supply to the motor - so that is what STOP sends (see Quick Tuning); Off is not known to end it; (d) it keeps the maximum current and poles it was given in the Datasheet. The advanced
   command carries the current as a "FLOAT16" - fixed point, value x 256 - so a value such as 6.53 A reaches the drive as 6.53125 (the nearest 1/256). Until 2026-09-25 this tool encoded it as a half float, which the drive would have read as 63.6 A for 1.9 A - the advanced command was never sent to a real drive while that was so.
8. **CAN adapters** - the CAN bus path (PCAN, Kvaser, ...) is simulator-tested only; the default bit rate is 1 Mbit/s.
   USB needs no bit rate.
9. **ENABLE / MOVE TO / MOVE BY (position control)** - not tried on a real drive; the simulator has no mechanical model,
   so it just snaps Work Zone Count to the target and holds it there, with no notion of the motor fighting a real load
   or overshooting on the way. Whether the plain **servo** command actually holds/moves with only Hall-sensor/sensorless
   commutation and no Motor/Servo Encoder is the open question the user raised ("if encoder is not attached then i need
   to rethink") - this tool's answer is to refuse in that case (see the table above) rather than guess at untested
   behaviour.
10. **CURRENT / TORQUE CONTROL** - not tried on a real drive; the simulator has no torque constant or inertia modelled,
    so a commanded current/torque only shows up as extra phase current (`_ia`/`_ib`), 1:1 for both units, purely as a
    visible signature - it does not turn the simulated motor. Whether `electronic_torque_control_etc`'s N·m actually
    matches the motor's real torque constant is unverified.
11. **SET HOME HERE (Reset Work Zone)** - not tried on a real drive; assumed to zero Work Zone Count immediately, like
    Reset does. Whether it needs the motor to be fully stopped first (this tool always sends a Stop right before it,
    to be safe) or works while moving is untested.
12. **G-code (the Terminal console and the Motion Controller page)** - not tried on a real drive; both send exactly
    the same commands as the buttons the Single Axis Motion page already uses (ENABLE, MOVE TO/BY, RUN,
    Current/Torque Control), which themselves are covered by items 9-10 above, so nothing new is at stake beyond
    that - but neither path, end to end, nor a whole multi-line program running several lines in a row with real
    dwells between them, has been tried against anything but the simulator.
13. **3D Simulation, Live mode** - the rendering pipeline and live per-axis transform (mesh load, camera framing,
    the metre-to-millimetre conversion, a moving joint's own transform) were all verified live in the real, running
    app, not just headlessly: a 12-axis model (`robot-dog.obj`) rendered correctly at its home pose, and driving a
    real speed command on one connected axis visibly rotated that body around its own hip joint, exactly as
    expected. This used the **Simulator** interface, not real CAN hardware.
    **Tried against the real bench drive 2026-09-24** (node 7, `bench_node7.kinematics` - the only sample with axis 6,
    a single joint with no matching mesh, so it renders as the generic placeholder sphere): Live mode correctly
    showed "Live: 1 of 1 axis(es) connected." in green - the first real-CAN-hardware Live connection. No rotation
    was visible in the viewport, but that is inconclusive by construction here, not a finding either way: this
    sample file's one body shares the base's own origin, so a symmetric placeholder sphere spinning in place
    has nothing to show even if the underlying transform is correct (a genuinely multi-body live render against
    real hardware, with an offset joint and a real mesh, is still untried - would need a second real drive, same as
    multi-axis in general - see OPERATIONAL_MANUAL.md). So the underlying data was checked directly instead, via
    the Terminal's own CAN trace: with the motor genuinely spinning under a real `G1030 [6]50` (50 rpm ESC RPM
    speed command) - confirmed by continuously fluctuating `ia`/`ib` phase currents in the `0x387` telemetry -
    **Work Zone Count (`wz_count`) stayed frozen at 0 in every single `0x287` broadcast frame throughout**, never
    once incrementing. This is **not a bug in this tool**: this bench drive has no Motor/Servo Encoder configured
    (sensorless commutation only, per item 9 above), so it has no position feedback to report during a pure speed
    command - Work Zone Count genuinely cannot track anything on this hardware in this mode. Practical consequence:
    since Live mode's per-axis transform is driven by `wz_count`, **Live mode correctly renders no joint motion on
    this specific bench drive during a speed command** - an accurate reflection of the drive's own real telemetry,
    not a rendering defect. Whether Live mode can show real motion on a drive that *does* have a Motor/Servo Encoder
    configured remains untested (needs an encoder-equipped drive, not available on this bench). The motor was
    stopped cleanly with `G1002` (confirmed: `op_mode` 6→0, phase currents back to near-zero baseline) and the
    drive disconnected (Connection State: DISCONNECTED) before ending the session.
14. **3D Simulation, Simulation mode + Motion Controller's SIMULATE button** - confirmed live in the real running
    app (2026-09-24): SIMULATE ran the bundled `speed_ramp.nc` program to completion with **zero drive connected
    at all** (a 12-axis `robot-dog.obj` model, "Simulation finished." after all 8 speed steps and the final
    reset); a plain `G1030 X30` (no end command) was then watched spinning continuously on screen across two
    screenshots a second and a half apart, the affected leg visibly in two different rotated positions each time,
    status reading "Simulation: 1 axis(es) with a commanded value (1 spinning)." - and a `G1002` correctly froze
    it. Arc (G02/G03) support within Simulate remains explicitly scoped out, not built.
15. **3D Simulation, Cursor mode** - confirmed live in the real running app (2026-09-24), loaded against
    `robot-dog.obj` (real Axis Limits, unlike `radar.obj`): typing 80 into `leg-1-1`'s own field visibly rotated
    that leg far from its home stance, the slider moving to match; dragging `leg-1-2`'s own slider to its 135°
    limit visibly bent the leg further at a second joint, composing correctly with the first (`body_transforms()`
    correctly applying both simultaneously, exactly as the headless tests already proved mathematically); **Reset
    to Home** correctly zeroed both fields, both sliders, and returned the model to its exact original stance.
16. **Position Control · Cyclic (Servo: Modulo / Servo: Turns and Modulo)** - **tried on the real bench drive
    2026-09-24**: correctly REFUSED with "This needs a Motor Encoder or Servo Encoder configured... this drive
    currently has none" - identical to MOVE TO/BY's own refusal, confirming the Feedback Settings check applies
    consistently to the new Cyclic modes too. Whether Direction actually changes which way a real drive travels,
    and whether Turns and Modulo combines correctly with the drive's own configured counts-per-revolution, remain
    untested - this bench drive has no encoder, so the actual MOVE itself has never been sent on real hardware.
17. **Direct Field Control (Rotation / Electrical Position)** - **tried on the real bench drive 2026-09-24 at what
    the page called 0.1 V, 3 Hz, Rotation** - the drive's own FIRMWARE-CONTROLLED OVERCURRENT PROTECTION TRIPPED
    IMMEDIATELY; RESET cleared it cleanly. **Corrected 2026-09-25: that was not 0.1 V.** The Voltage is a "FLOAT16",
    which this tool then encoded as a half float: 0.1 -> 0x2E66, which the drive reads as 11878 / 256 = **46.4 V**
    of open-loop field. The protection did exactly its job. With the fixed-point codec 0.1 V goes out as raw 26
    (= 0.1016 V, the page's real resolution is 1/256 V, not 0.001 V). **Tried at that real 0.1 V the same day, through
    the page itself (node 1, telemetry watched at 100 Hz): Rotation at 3 Hz for 6 s and Electrical Position at 1.25 rad
    for 4 s - Operation Mode reported each mode, phase currents peaked at 0.30 A / 0.19 A (rotation) and 0.08 A / 0.17 A
    (position) against the 1.9 A limit, no fault, STOP put it back to Idle both times.** The rotor did not visibly turn:
    0.1 V across 1.14 ohm line-to-line is under 0.1 A, not enough torque - which is the point of the reference's
    "start low, raise gradually while watching the phase currents". **Then 0.5 V, same run (user: "try it at 0.5 V
    and watch the current")**: Rotation 3 Hz for 6 s - phase currents a rotating pattern with peaks of 1.02 A / 0.93 A
    (about 0.5 V over the 0.57 ohm per phase), Work Zone Speed swinging as the rotor is dragged around by the field,
    no fault, STOP -> Idle; Electrical Position for 4 s - a steady 0.30 A / 0.45 A DC hold, no fault. So on THIS
    motor 0.5 V already draws half of Maximum Continuous Current (1.9 A) at the peaks; 1 V would reach ~2 A and
    the protection - the reference's burn warning is about right for a 1.1 ohm motor. Rotation at 0.5 V, 10 Hz (user:
    "try at 0.5V 10 Hz"): peaks 1.22 A / 1.07 A, the rotor slipping rather than following (Work Zone Speed swinging
    +-10 rps), no fault. **Ceiling on this motor: 0.5 V** (current = voltage / winding resistance, so 1 V = ~2.2 A
    peaks = the protection; heating goes with the square) - written into the manual. Higher than 0.5 V: untried, on
    purpose.
18. **Virtual Brake** - **tried on the real bench drive 2026-09-24**: engaged cleanly (Operation Mode "Virtual
    Brake"), with real phase current (~1.4-1.8 A) confirming the drive genuinely drove a holding field on this
    sensorless motor, exactly as the reference describes for a motor with no Hall sensors/Motor Encoder. STOP
    ended it cleanly, no faults. Not left engaged long enough to observe the reference's own heating caveat for
    sustained sensorless use.
19. **Kickstart** - **tried on the real bench drive 2026-09-24**, target 5 Hz: the motor genuinely spun up (Speed
    reached ~84 rpm, Operation Mode "Kickstart", real sinusoidal phase-current commutation waveform visible on
    the Recorder) - a real, working sensorless startup, not just a telemetry-mode change. STOP ended it cleanly,
    no faults.
20. **Power Generation** - **tried on the real bench drive 2026-09-24**, target 24 V: accepted cleanly (Operation
    Mode "Power Generation"), no faults, DC bus unchanged (expected - nothing external is spinning this motor's
    shaft to actually generate power from). Whether the drive genuinely regulates the commanded output voltage
    under a real external rotation, and what feeding that through a BMS to a battery actually looks like, remains
    untested - this bench setup has no way to spin the shaft externally.
21. **Brushed: Open Loop Control** - still not tried; this bench drive's own Motor Type (Datasheet) reads
    "Brushless Motor", confirmed live 2026-09-24, so this command is not applicable to the current hardware setup
    at all, not merely untested. Would need a real brushed motor/solenoid and Motor Type set to Brushed to try.
22. **Peripheral Commands (Electromagnetic Brake, GPIO: Generic/PWM Output)** - not tried on a real drive; the
    simulator only tracks the last-commanded value of each (for tests and live observation), with no physical
    effect at all - no brake, solenoid or GPIO hardware model exists. A real, readable feedback register does
    exist for the brake ("Electromagnetic Brake: mode", 0x5003:0x02) but isn't wired into this tool's telemetry
    yet, so even on real hardware this page cannot currently confirm a command actually took effect - only that
    it was sent.

## Firmware-level CiA 402 remap (separate project, same bench drive)

A sibling project, `SDR-CB-60R-D firmware/` (its own directory, own `README.md`/`OPERATIONAL_MANUAL.md`/
`NOTES.md`), reverse-engineers this exact drive's compiled firmware (no source exists) and adds a real
CiA 402 Controlword/Statusword to it via a binary patch — flashed over the STM32 DFU bootloader, nothing
to do with this GUI's own codebase. **Confirmed live on the real bench drive (2026-09-29)**: writing the
new `0x6040:00` Controlword object genuinely drives Operation Mode (Off/Servo/Stop all confirmed), the new
`0x6041:00` Statusword is computed live every cycle, and the drive's own comm-timeout safety watchdog was
confirmed to correctly override a stale Controlword rather than being silently defeated by it.

These two objects don't come from the Device Reference PDF — they're added to `sdr_tuner/data/registers.json`
by a small, clearly-marked `EXTRA_PARAMS` step in `tools/import_device_reference.py`, appended after the
PDF parsing completes (so a future re-import from a newer PDF revision never silently drops them). They
show up as ordinary named rows: Controlword under its own "CiA 402 (firmware patch)" section on Parameters
Explorer, Statusword under "Telemetry - CiA 402 (firmware patch)" on Telemetry Explorer - filter, read,
write and export them exactly like any other parameter. See the firmware project's own
`OPERATIONAL_MANUAL.md` for the Controlword bit table, Statusword bit table, and current limitations
(fault-reset is simplified, STO polarity in Statusword bit 4 is unverified, Servo-hold has only been
tested at position 0 since this bench drive has no encoder yet).

## Notes on the PDF (revision L)

* Commands with a 24-bit float (`FLOAT24`) are listed but not supported by this tool (3 of 32) - the PDF labels the
  type but never defines its 3-byte bit layout anywhere in its 100 pages (checked directly, 2026-09-24); the real
  spec would need to come from Stimita support or a proper decompile of the vendor's own IDE, neither done yet.
* **Fault bits:** this drive reports 13: bits 0-10, 14 (Emergency Stop) and 15 (Activation Key is Missing), as its own documentation lists
  them (value 3 = 1+2, 9 = 1+8, 10 = 2+8 are combinations). The generic PDF also has bit 11 (RS485 / Dual Encoder); it is left out, and the names
  follow the drive's list (`SDR_FAULT_BITS` in the importer). The motion page has a lamp for each of the 13; a bit outside the list is still named
  in the fault text as "Bit N (not in the drive's list)".
* Names are taken as printed, except that a word split by the PDF's line wrapping ("De celeration") is repaired.
* The PDF does not say which parameters need a power cycle. Only the Networks parameters that the vendor's own tool
  marks "Power Cycle" (seen in its screenshot) are flagged - a fixed list in the importer (`POWER_CYCLE`). Others may
  need one too.

## Layout

```
main.py                 run this
sdr_tuner/
  protocol.py           CAN frame encode / decode
  defs.py, data/*.json  parameter, telemetry and command definitions (generated from the PDF)
  data/gcode_samples/*.nc   bundled example G-code programs for the Motion Controller page
  client.py             thread-safe drive client (read / write, telemetry, mapping, polling, commands, speed / position / current / torque keep-alive)
  simulator.py          simulated drive on python-can's virtual bus (also the automatic configuration: Operation Mode 3, the heartbeat timeout, the results)
  usb.py                the drive's USB cable (text CAN frames over a serial port)
  gui/                  theme, icons (drawn in code), ribbon, navigator, page host, session, gcode.py (G-code parsing) +
                        gcode_runner.py (translating it into commands), the pages (item configuration, drive tab, recorder,
                        tuner = Quick / Advanced Tuning, parameters, telemetry, terminal, motion_controller = G-code programs)
                        and the studio main window
tools/import_device_reference.py   PDF -> sdr_tuner/data/*.json
tools/usb_readonly_check.py        read-only bring-up check for a drive on USB
tools/usb_write_check.py           guarded write + speed-command check (restores everything)
tools/telemetry_rate_probe.py      guarded telemetry-frequency ceiling probe (restores the original)
tools/test_signal_probe.py         guarded raw capture of the drive's own test wave (the motor moves; restores everything)
tests/                  pytest suite
```
