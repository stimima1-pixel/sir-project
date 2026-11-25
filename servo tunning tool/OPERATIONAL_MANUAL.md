# STIMITA MOTION CONTROL — Operational Manual

A practical, look-it-up guide to using the SDR Servo Tuning Tool. If you just need to remember **where a feature
lives**, start with the table below. For the full technical detail behind any feature (protocol frames, exact
register addresses, command codes), see `README.md`.

This manual is kept up to date alongside the software — whenever a new feature ships, a row or section is added
here too.

---

## 1. Where do I find X? (quick index)

The window has three **activities** at the bottom of the left column: **System Configuration** (connection, identity,
activation, kinematics model), **Servo Drive** (one drive's own tools - the list follows whichever drive is selected
in the workspace tree) and **Motion** (everything that involves several axes: G-code programs, the 2D/3D views,
every axis's status, multi-axis moves, the Terminal). Clicking a drive in the tree opens that drive's Item
Configuration from System Configuration, or its own Motion - Single Axis page from the other two.
Every folding header folds and unfolds with a **single click** - an Item Configuration group, a Parameters/Telemetry
Explorer section, a tuning wizard's headings in its own tree, and the workspace tree's folder.

| I want to... | Go to |
|---|---|
| Connect / disconnect a drive, see its serial numbers | **System Configuration** → Item Configuration |
| Enter the Activation Key (fixes "Activation Key is Missing") | **System Configuration** → Item Configuration → *Product Activation* |
| Change the drive's own CAN bit rate / node ID / heartbeat | **System Configuration** → Item Configuration → *Networks* |
| Connect a second (or third...) physical drive | **Motion** → Axes → *Additional Axes* (top of the page; since 2026-09-26) |
| Load a robot/turret geometry file for Inverse Kinematics | **Motion** → Axes → *Kinematics Model* (or 3D Simulation's own Open… / Samples…) |
| Aim a tool at a position **and** orientation (Orientation IK) | **Motion** → Axes → *Tool Pose (Orientation IK)* |
| Run the motor by hand (speed / position) | **Servo Drive** → Motion - Single Axis (pick Velocity or Position in the CONTROL MODE chooser at the top) |
| Change what the drive broadcasts (e.g. Phase B instead of Work Zone Modulo) | **Servo Drive** → Telemetry Explorer: right-click the parameter, *Copy telemetry mapping code*; then Parameters Explorer → Telemetry Mapping: right-click the slot, *Paste mapping code*, Write (see §3) |
| The drive's special modes (current / torque, cyclic position, Direct Field Control, Kickstart, Power Generation, brushed open loop, Virtual Brake) | **Servo Drive** → Drive Modes (right below Motion - Single Axis; since 2026-09-26) |
| Auto-tune a new motor, the simple way | **Servo Drive** → Quick Tuning |
| Auto-tune with full control over every setting | **Servo Drive** → Advanced Tuning |
| Drive a control loop with a square, sine or swept-sine test signal (and watch it on the Recorder) | **Servo Drive** → Advanced Tuning → Verification → *Step Response Tests* → *Wave Form* (Period in steps of 1/256 s; the sweep runs from 1/Period up to a few hundred Hz over 10 × Period, then repeats) |
| Check the drive and measure its loops with one button, or let the software tune the current loop round by round (Beta) | **Servo Drive** → Advanced Tuning → Verification → *Automatic Test* (or the ribbon's Tuning → Automatic Test) → **RUN TESTS** / **BETA: TUNE** |
| Measure a loop's frequency response - a Bode plot (bandwidth, phase, resonance) | **Servo Drive** → Advanced Tuning → Verification → *Frequency Response* |
| Record a chart of what happened and export it | **Servo Drive** → Recorder |
| Read/write any of the 243 configuration parameters | **Servo Drive** → Parameters Explorer |
| Make parameter changes survive a power cycle (they are RAM-only until then) | Ribbon → **Parameters** → *Save to Flash* |
| Sample a Bode sweep at 2000 Hz instead of 1000 | **Servo Drive** → Advanced Tuning → Verification → *Frequency Response* → *PREPARE 2000 Hz* (then power-cycle) |
| Get a continuous response curve fast (the drive's own sweep, analysed by spectrum) | **Servo Drive** → Advanced Tuning → Verification → *Frequency Response* → *Method: Chirp* (short Period, several cycles) |
| Zoom into a Bode or Nyquist plot, pan it, or put it back | Over the plot: **Cmd/Ctrl + scroll** or a trackpad **pinch** zooms, **drag** pans, **double-click** resets the view; the plain scroll wheel keeps scrolling the page. All three plots (gain, phase, Nyquist) fit one screen; the full-contour Nyquist plot sits below the summary |
| Read exact values off a Bode plot, or the slope between two frequencies (dB/decade) | **Servo Drive** → Advanced Tuning → Verification → *Frequency Response* → **CURSORS**: drag A and B on the gain or phase plot (they move together), read the table under the plots |
| See the Nyquist plot the textbook way (closed loop T from (1, 0) to the origin, with the mirror half for negative frequencies) | **Servo Drive** → Advanced Tuning → Verification → *Frequency Response* → the **Nyquist - the full contour** plot at the bottom of the page; **Show** switches it between the closed loop T and the open loop L |
| Read a loop's stability margins (phase margin, gain margin) on a Nyquist plot | **Servo Drive** → Advanced Tuning → Verification → *Frequency Response* → the third plot and the text under it |
| Find out why Connect is refused with "already open in another program" | Another program (a second copy of this tool, the vendor IDE) holds the USB port - disconnect there first (§3) |
| Read any of the 176 read-only telemetry values | **Servo Drive** → Telemetry Explorer |
| Type one G-code line and run it | **Motion** → Terminal |
| Write and run a multi-line G-code program | **Motion** → Motion Controller |
| Preview a G-code program's motion with no drive connected at all | **Motion** → Motion Controller → SIMULATE |
| Watch a loaded robot/turret model move live, in 3D | **Motion** → 3D Simulation |
| Load a robot/turret geometry file right from the 3D view | **Motion** → 3D Simulation → *Open…*/*Samples…* |
| Pose a loaded model by hand, no drive or program needed | **Motion** → 3D Simulation → *Cursor* |
| Watch two axes trace a shape against each other (e.g. a circle) | **Motion** → 2D Plotter |
| See every connected drive's status in one table | **Motion** → Axes |
| Watch any signal live, on any connected axis, without hunting for it | **Motion** → Status Monitor |
| Move several axes at once, no G-code | **Motion** → Multi Axis Motion |
| Configure RC PWM, GPIO, a thermistor or the inverter | **Servo Drive** → Peripherals |
| Engage/disengage the electromagnetic brake, or set a GPIO output | **Servo Drive** → Peripherals → *Peripheral Commands* |
| Convert between a motor's Ke, Kv and Kt constants | Ribbon → **Floating Tools** → *Motor Constants* (no drive needed) |
| Export the whole object dictionary as HTML or a CANopen .eds file | Ribbon → **Parameters** → *Export HTML* / *Export EDS* |
| Save a snapshot of every parameter read so far, to compare later | Ribbon → **Parameters** → *Save Snapshot* |
| Compare two saved parameter snapshots and see what changed | Ribbon → **Floating Tools** → *Compare Parameters* |
| Open several Servo Drive or Motion tools at once, side by side | Ribbon → **Floating Tools** → *Docked View* |
| Read or write a register that isn't in the parameter table | **Servo Drive** → Parameters Explorer → *Raw Register Access* (bottom of the page) |
| Try the experimental firmware-level CiA 402 Controlword/Statusword (`0x6040`/`0x6041` — a separate patch project, this bench drive only) | **Servo Drive** → Parameters Explorer (Controlword) / Telemetry Explorer (Statusword) — named rows like any other parameter; see §6 and the sibling firmware project's own docs |
| Stop everything, right now | The round **STOP** button (always visible, top right) or the **Esc** key — every axis, AND a running G-code program, Terminal line or Tool Pose move |

---

## 2. Getting started

1. **Launch**: open the project in PyCharm and run `main.py` (or `.venv/bin/python main.py` from a terminal).
2. **Pick a connection**, top of System Configuration → Item Configuration → *Target Connection*:
   - **Simulator (no hardware)** — the default. No real drive needed; behaves like a real one (answers reads/writes,
     runs commands, sends telemetry).
   - **USB cable to the drive** — plug the drive in first. The port fills in by itself and the Node ID field
     disables itself (a USB cable only ever reaches one drive, so it's found automatically, never guessed).
   - **PCAN / Kvaser / CANable / SocketCAN / Vector** — for a real CAN adapter. Set the channel, bit rate and node
     ID yourself, then Connect.
3. Press **Connect**. The bottom-right pill shows **ONLINE · NODE N** in **green** for a real, answering drive;
   **SIMULATED DRIVE · NODE N** or **NO RESPONSE · NODE N** both show in **amber** (green is reserved for a
   genuine, confirmed real connection — don't mistake amber for "connected and real"). The status bar shows the
   pole count and whether the telemetry layout was read from the drive.
4. **First time with a brand-new real drive**: it will likely show fault bit **"Activation Key is Missing"**. Get
   the Device Type and both serial numbers from Item Configuration, send them to Stimita support, and enter the key
   they give you under *Product Activation* on that same page. A faulted drive ignores every command except Reset.

---

### Launching it as a Mac app (its own icon in the Dock, pinnable)
**dist/SDR Servo Tuning Tool.app** is a real Mac application around this project: it carries its own copy of the Python
interpreter and the tool's icon, so the Dock knows the process as the tool - its own icon while it runs, while it quits
and when it is pinned. `main.py` builds it on first run and keeps it fresh (`.venv/bin/python tools/make_app_bundle.py`
builds it by hand; rebuild after moving the project folder - the bundle holds this machine's paths). Double-click it,
or drag it to the Dock and choose *Keep in Dock*.

A run started from PyCharm or a terminal gets the same treatment: `main.py` re-launches itself through the bundle's
interpreter before the window opens (same process, PyCharm's Run/Stop and console keep working), so the quit animation
shows the tool's icon there too. Set `SDR_NO_RELAUNCH=1` in the run configuration to skip that and run under the bare
interpreter (the Dock then shows Python's rocket for a moment at quit - that is the interpreter's own icon).

### Installing the packaged app (no Python, no project folder)

Each operating system gets its own installable file, built by `tools/build_release.py` on that OS (a build cannot be
made for another OS). The packaged app carries everything it needs - Python, Qt, the register catalogue, the bundled
G-code and kinematics samples - and behaves exactly like a run from the project folder.

| OS | File | Install |
|---|---|---|
| macOS (Apple silicon) | `SDR Servo Tuning Tool-<version>-macOS-arm64.dmg` | Open the .dmg and drag **SDR Servo Tuning Tool** onto the **Applications** folder shown next to it. Until the build is signed with a Developer ID and notarised (see *Shipping without the Gatekeeper dialog* below), the first launch on another Mac shows "Apple could not verify ... is free of malware" with only **Done** / **Move to Bin**: press Done, then open **System Settings > Privacy & Security**, scroll down to the line saying the app was blocked, press **Open Anyway** and confirm - once; it opens normally afterwards. (Same effect from Terminal: `xattr -dr com.apple.quarantine "/Applications/SDR Servo Tuning Tool.app"`.) An Intel Mac needs a build made on an Intel Mac. |
| Windows (64-bit) | `SDR Servo Tuning Tool-<version>-Setup.exe` (or the `...-Windows-x64.zip`) | Run the Setup: it installs into Program Files, adds a Start-menu entry and a desktop shortcut, and offers to start the tool. It is not code-signed, so SmartScreen may show "Windows protected your PC" - **More info > Run anyway**. The zip needs no installation: unzip it anywhere and run `SDR Servo Tuning Tool.exe` inside. Runs on Windows 10/11 x64, and on an ARM PC through Windows' own x64 emulation. On the USB cable the drive appears as a COM port, found automatically as on a Mac. |
| Linux | `SDR Servo Tuning Tool-<version>-Linux-<arch>.AppImage` (or the `.tar.gz`) | `chmod +x` the AppImage and run it; the tar.gz extracts to a folder with the executable and a `.desktop` file. Not built yet - it needs a Linux machine or VM to build on. |

The packaged app keeps its settings (last connection, window layout) in the same place the project run does, so the
two can be used interchangeably on one machine.

**Shipping without the Gatekeeper dialog (macOS).** The "Apple could not verify" dialog appears on every Mac other
than the one that built the app, because the app is only ad-hoc signed. Apple's one and only way around it: the app
must be signed with a **Developer ID Application** certificate and **notarised** by Apple. That needs an Apple
Developer Program membership (US$99 a year, at developer.apple.com) - the developer is Remi, and an individual
membership in Remi's name is enough (the certificate is then named "Developer ID Application: Remi <surname>
(<TEAMID>)"; `security find-identity -v -p codesigning` prints the exact text once it is installed). Then, once, on
the build Mac: create the "Developer ID Application" certificate (Xcode > Settings > Accounts > Manage Certificates,
or the developer portal) and store the notarisation login with `xcrun notarytool store-credentials remi` (Apple ID,
an app-specific password from appleid.apple.com, the Team ID). After that every build is one command:

```
.venv/bin/python tools/build_release.py --identity "Developer ID Application: Remi <surname> (<TEAMID>)" --notarize remi
```

It signs the app with the hardened runtime, has Apple notarise it, staples the ticket, and does the same for the
.dmg - the result opens on any Mac, online or offline, with no dialog at all. Windows has the same story with a
different vendor: SmartScreen's "Windows protected your PC" goes away with a code-signing certificate from a
certificate authority (an EV certificate removes it immediately; a standard one after enough installs build up a
reputation) - not built in yet.

## 3. Common tasks, step by step

### Run the motor by hand
**Servo Drive → Motion - Single Axis.** A **CONTROL MODE** chooser at the top (Velocity or Position — only one can
be in use at a time, so only the chosen card is shown; switching it sends nothing), the chosen card, and a **DRIVE**
card that is always there. Since 2026-09-26 the drive's special modes have their own page, **Drive Modes**, right
below it in the list - see the next task.
- **Velocity → Speed Control · ESC** — type a target and press RUN. The small chooser beside TARGET SPEED picks the
  unit you type in — **rpm**, **rps** (turns per second) or **deg/sec**; the target, the slider and
  the speed limit all follow it, and switching converts the numbers without sending anything. A pure conversion (a
  turn is a turn): the drive always receives its own rpm command, and the status line shows both ("Commanding 1.500
  rps = 90.0 rpm"). This is separate from *Speed in* on the plot, which is the unit the drive's own telemetry is
  SHOWN in - rpm, rps, deg/sec or Hz (rpm, rps and deg/sec are the drive's Work Zone: Speed in rps times 60, 1 or
  360; Hz is its electrical frequency - choosing across the two families writes the drive's telemetry mapping, after
  you confirm).
- **Position → Position Control · Servo** — MOVE TO a specific Work Zone Count, or MOVE BY a relative amount.
  **SET HOME HERE** zeroes the count wherever the motor currently is (there's no automatic homing — jog it there in
  Velocity mode first, then press this). The chooser beside TARGET POSITION picks **counts** or **deg**: degrees
  are not a plain conversion — they need counts per turn, so choosing deg reads the drive's own *Work Zone:
  Dimension* once (the status line then says "1 turn = N counts") and the fields, the Work Zone Count / Modulo
  cards and the plots follow; the same choice sits on the plot card as *Position in*, and the two mirror each
  other. A disconnect goes back to counts (another drive may have another dimension). The status line shows both
  ("Moving to 45.00 deg (8,192 counts)").
- **DRIVE** (both modes) — STOP, OFF / RESET, ENABLE / DISABLE and the status line. ENABLE holds the motor at its
  current position; DISABLE is the same as OFF.

Beside them: one readout card per value the drive broadcasts, the fault LEDs and the scrolling plots.

### Change what the drive broadcasts (the telemetry layout)
The cards, the plots and the Recorder show exactly what the drive broadcasts - three messages of up to 8 bytes,
each slot holding a *telemetry mapping code* that names one parameter. To swap a value (say, Phase B current
instead of Work Zone Modulo):
1. **Servo Drive → Telemetry Explorer**: find the value you want, right-click it, **Copy telemetry mapping code**
   (the code is also in the row's tooltip - a decimal number such as 5243142 for Current: Phase B).
2. **Servo Drive → Parameters Explorer**, section *Telemetry Mapping*: the twelve slots (three messages, four
   slots each). Read them first to see what each holds - a slot's tooltip names its current parameter. Right-click
   the slot to replace and choose **Paste mapping code** (the menu names the parameter the code stands for), or
   **Set this slot unused (0)**. Press **Write**: the drive switches its broadcast at once and the tool re-reads
   the layout, so the new card and curve appear immediately.
3. Mind the 8-byte budget per message (a FLOAT32 or INT32 takes 4 bytes, a UINT16 2): a slot that does not fit
   shows the amber mapping warning. The change lives in the drive's RAM: **Save to Flash** (Parameters ribbon) to
   keep it across a power cycle.

### Use one of the drive's special modes
**Servo Drive → Drive Modes.** One card per mode, a small readout row at the top right (Operation mode, the speed,
Phase A/B, DC bus, Fault bits — the same telemetry Motion - Single Axis shows), the same **live telemetry plots**
below it (window length and Freeze; the *Speed in* chooser stays on Motion - Single Axis, since it writes the drive -
this card follows it) and a STOP. A page's plots only redraw while that page is the one on screen, so having the
plots on both pages costs nothing extra. Every mode is one
continuous command the drive repeats every 100 ms: starting one ends whatever was being sent before, on either
page — the "· sent every 100 ms" note moves with it — and STOP / OFF / RESET on either page ends it.
- **Current / Torque Control** — pick Current (FOC) or Torque (ETC), type a target, RUN. A G-code torque line
  (G1040) is mirrored on this card too.
- **Position Control · Cyclic** — for a rotary actuator that keeps spinning past its own Work Zone Count range
  (e.g. a turret), rather than being held to one absolute multi-turn count. MODE **Modulo (cyclic)**: a
  single-turn target plus a **Direction** (Normal, Negative, Positive, or Shortest Cyclic Route — the default).
  MODE **Turns + Modulo (split)**: the same kind of multi-turn target MOVE TO/BY already sends, just split into a
  single-turn part and a whole-turns part instead of one number. Needs an encoder, like MOVE TO/BY.
- **Direct Field Control** — an OPEN-LOOP mode: a chosen Voltage directly drives the stator coils, either
  **Rotation (Hz)** (rotating the field at a chosen speed) or **Electrical Position (rad)** (holding it static at
  a chosen angle). ⚠️ **Even 0.2–0.3 V can burn a low-resistance motor** — start around 0.1 V and raise it
  gradually while watching Phase A/B current against Maximum Continuous Current. No Feedback Settings check
  needed (it never touches Work Zone Count).
  **On the real bench drive (2026-09-24) "0.1 V" tripped the drive's Overcurrent Protection at once - corrected
  2026-09-25: the tool was sending 46.4 V.** The Voltage travels as a fixed-point number (value × 256) that this
  tool encoded as a half-precision float until that day; the drive protected itself correctly. **A real 0.1 V was
  then tried on the bench drive (Rotation 3 Hz and Electrical Position, a few seconds each): no fault, phase
  currents under 0.3 A, the rotor did not visibly move - too little current for torque, which is why the reference
  says to raise the voltage gradually.** At 0.5 V the same motor drew peaks of about 1 A in Rotation at 3 Hz (a
  rotating current pattern, the rotor dragged along), 1.2 A at 10 Hz, and a steady 0.3-0.45 A in Electrical
  Position - still no fault.
  **⚠️ Ceiling on the bench motor (1.1 Ω line-to-line, 1.9 A continuous limit): 0.5 V.** The current is set by
  the winding resistance alone (about 0.5 V ÷ 0.57 Ω per phase), so the peaks scale with the voltage: 0.5 V is
  already 1-1.2 A, 0.75 V would be ~1.6-1.8 A (at the limit) and 1 V about 2.2 A, where the drive's overcurrent
  protection trips - and the heat in the winding goes with the current squared. Do not go above 0.5 V on this
  motor; on another motor, expect the ceiling at roughly (1.9 A × phase resistance) and always start at 0.1 V
  while watching Phase A/B. Turning the field faster does not reduce the current much (1.0 A at 3 Hz, 1.2 A at
  10 Hz here). The voltage is sent in steps of 1/256 V (about 0.004 V), so 0.1 goes out as 0.102 V; the status
  line shows the value the drive really received.
- **Kickstart** — an alternate startup technique for a *sensorless* brushless motor: type a target Speed
  (electrical Hz) and press RUN. Tuned via Control Laws, not this page.
- **Power Generation** — turns the drive into a generator's control unit: type a target output Voltage and press
  RUN. Enough to charge a battery through a BMS, if one is used.
- **Brushed: Open Loop Control** — direct open-loop voltage on 1-2 brushed motors/solenoids (relevant when
  Datasheet's Motor Type is Brushed). Motor #1 on terminals A/B, #2 (if present) on C/B — type each channel's
  Voltage and press RUN.
- **Virtual Brake** — ENGAGE uses the motor itself to resist unwanted motion — no target needed, and no Feedback
  Settings check (it still does something useful without an encoder, just less efficiently).

All position-based actions (ENABLE, MOVE TO, MOVE BY, and both Cyclic modes) need a **Motor Encoder or Servo
Encoder configured** (Datasheet → Feedback Settings). With neither configured, these are refused with a clear
message instead of doing something meaningless — this is by design, not a bug.

### Tune a new motor
**Servo Drive → Quick Tuning** (simple) or **Advanced Tuning** (full control, plus General System
Dynamics, filters, application settings, limits/protections). Both send the drive's own auto-configuration command
— it measures the motor, computes control laws, and saves them to flash; the motor makes rapid moves and beeps at
the start and the end. Press **START AUTOMATIC TUNING** (on the Automatic Tuning page), confirm, and wait —
completion is detected automatically once the Datasheet values actually change (not a fixed timer; capped at 15
minutes if they never do): the Datasheet is read once when you press START, then from 20 s after the command every
4 s, and the reading stops at the first change. **Never run this against a real motor without knowing what it's
connected to** — it will move. Run on the real bench drive on 2026-09-24 and 2026-09-25 (see §4).

### Check and tune with one button (Automatic Test)
**Servo Drive → Advanced Tuning → Verification → Automatic Test** (or the ribbon's Tuning → Automatic Test). Tick what to run - when the drive's own automatic tuning is ticked, it is watched the same way as on the Automatic Tuning page (the Datasheet read once at the start, then from 20 s on every 4 s) - the drive's own **automatic tuning first** (the motor moves for
about 30 s and the Datasheet and Control Laws are rewritten in flash), the **current loop** measurement (a square-wave
step test and a chirp at a quarter of Maximum Continuous Current, at most 1 A, about 10 s of motion) and the **speed
loop** measurement (only when an encoder is configured; skipped with a note otherwise) - then press **RUN TESTS** and
confirm. The result table lists every check with OK / CHECK / FAIL / SKIPPED: connection and faults, DC bus, motor data,
motor type, feedback, telemetry layout, timer rate, whether FOC T matches L/R (the PI zero on the motor's electrical
pole), the current-loop bandwidth predicted from Kp/L, and, once measured, each loop's step rise and overshoot, -3 dB
bandwidth, peaking, its Nyquist margins (phase margin at the gain crossover, gain margin at the phase crossover, or
"crossover beyond the range") and how far the chirp was coherent. Nothing is written by RUN TESTS except what the automatic
tuning itself writes.

**BETA: TUNE** asks once, shows the plan, and then works on its own, up to five rounds: measure, decide, apply,
measure again. The aim is "as fast as the drive allows": FOC T is set to L/R if it is off by more than 20 %; FOC Kp
is raised by half per round while the current loop stays clean (no gain peak above 1 dB, no step overshoot above
20 %, and - wherever the Nyquist crossings fall inside the measured range - a phase margin of at least 45° and a
gain margin of at least 6 dB; a margin whose crossing is beyond the range is reported as such, not counted against
the loop); when a limit appears the last clean gain goes back and Beta stops; it also stops when the bandwidth outruns
what the telemetry sampling can verify (0.3 × the rate) or the gain reaches its cap (a current-loop bandwidth of a
twentieth of the control rate - about 6 V/A on the bench motor). With an encoder it also puts a notch filter on a
resonance peak of the speed loop (centre at the peak, bandwidth a quarter of it) and raises ESC Kp the same way.
Every round is logged with what changed and why. **STOP** (or the round STOP / Esc) ends either function at once: the
drive is stopped and its telemetry put back. What Beta wrote lives in the drive's RAM: **SAVE TO FLASH** on the page
(or on the Parameters ribbon) makes it survive a power cycle.

### If Connect says the port is already open in another program
Only one program can use the drive's USB cable at a time. If a second copy of this tool (or the vendor's IDE) tries to
connect while another is connected, it is refused with that message and the first keeps working; disconnect in the
other program first. Before this rule (2026-09-25) both would share the port and both would fail with "USB read
failed: device reports readiness to read but returned no data".

### Measure a Bode plot of a control loop (Frequency Response)
**Servo Drive → Advanced Tuning → Verification → Frequency Response.** Pick the **Loop** (Speed, Current, Iq Current
or Servo), the **Method** (see below), an **amplitude** (the same units as that loop's step-response test - keep it as
small as a step test's), and the method's own settings. The check list must show no ✖. Press **START SWEEP** and
confirm.

*Stepped sine* (the default): the frequency range (**From** / **To**, Hz), how many **Points** (log-spaced), and how
many cycles to **Settle** before measuring and to **Measure** per point. Each frequency is run on its own, fitted, and
gives one point. Exact and clean, but slow, and above 25 Hz the points can only sit where the drive's period steps
allow (51, 43, 37, 32 ... 128, 256 Hz).

*Chirp*: the drive's own frequency sweep. Set the **Period** - the sweep starts at 1/Period and rises, faster and
faster, to a few hundred hertz at the end of each cycle of 10 × Period, then starts over - and how many **Cycles** to
record. The recording is analysed by spectrum (Welch's method, counting for each frequency only the moments the sweep
was actually passing it) into points averaged twelve per octave; each point's quality is the coherence there, so a
hollow point means the sweep put too little energy at that frequency or the response did not follow. A continuous
curve with fine frequency resolution, which is what a resonance search needs. Use a **short Period with several
cycles**: on the bench motor's current loop, Period 0.1 s for 6 cycles (7 s) gave a clean curve from 10 to about
220 Hz and Period 0.5 s for 3 cycles (16 s) from 3.5 to about 210 Hz, both matching the stepped sine to within a
decibel. A long Period does not help: the sweep spends almost all of its cycle at the low end, so a 4 s Period
puts 40 s into the first 25 Hz and a fraction of a second into everything above. The 2000 Hz telemetry rate extends
the top end.

What happens in either case:
the drive's telemetry is switched to carry only this loop's reference and its response, in one message at 1000
messages/s (every other page shows only those two signals until the sweep ends); at each frequency the drive's own
sine-wave test runs, settles, is recorded and fitted; the gain (dB) and phase (°) points appear on the two plots as
they are measured (the two plots share the page's height, so gain and phase are on screen together without scrolling;
the Nyquist plot sits beside them on the right, so all three plots are on screen together). Being small, the plots can be looked into: **Cmd/Ctrl + scroll** or a trackpad **pinch** zooms a plot, a **drag** pans it, a **double-click** resets its view; the plain scroll wheel keeps scrolling the page. **CURSORS** puts two frequency cursors, A and B, on the gain and phase plots: drag one on either plot and it moves on both, and when you let go it snaps to the nearest measured point; the table under the plots reads each point (gain, phase, quality, the open loop behind it) and, in the B − A column, the differences and the **slope in dB/decade** (−20 is a first-order roll-off, −40 a second-order one); the Nyquist plot marks the same two points as A (square) and B (triangle). A cursor keeps its frequency across sweeps, so two sweeps can be compared at the same point. Save Data… marks the two rows in a `cursor` column.

Below the summary a second plot, **Nyquist - the full contour**, draws the response the way a textbook does: the measured half (0 ≤ ω, gold, one point per frequency) and its mirror image for negative frequencies (dashed blue - the complex conjugate, added to close the contour; it carries no new information). **Show** picks what it draws: the **closed loop T** as measured (the default - it starts near (1, 0), where the loop tracks its reference perfectly, and heads for the origin as the gain falls, the familiar shape) or the **open loop L = T / (1 − T)**, the same curve as the margins plot beside the Bode plots, with the −1 point marked in that view only. Both begin and end where the measurement does, so a curve rarely closes on the origin the way a formula's does. The cursors mark their two points here as well. When the sweep ends - or on **STOP** / Esc, or when you disconnect - the drive is stopped and its
telemetry layout and rate are put back exactly as they were. The text under the plots reads off the **-3 dB
bandwidth**, the phase there, and any **resonance peak** worth a notch filter; a point drawn with a hollow amber ring
was not a clean sine (noise, saturation or a limit) - treat it with care. Beside the two Bode plots, on their right, a **Nyquist plot**
draws the open loop behind the measurement - L = T / (1 − T), the loop as if the feedback were opened - in the
complex plane: one line through every point in frequency order (hollow rings still mark the points not to trust), the
dashed unit circle in red, the −1 point in red, and a blue diamond where the curve crosses the unit circle (the gain
crossover). A plot of scattered hollow rings with no shape is a measurement that did not follow its reference - on a
sensorless motor that is what the speed loop gives; use the current loop, or fit an encoder. The text reads off the **phase margin** there and, where the curve's phase reaches
−180°, the **gain margin** - the two classic stability margins. The pass band's points fall far outside the view
(there L is huge and mostly noise) and that is fine: the margins live near the unit circle. The view assumes plain
feedback with the reference entering at the loop's summing point; a feed-forward feature on the loop bends it.
**Save Data…** writes the points as CSV with the open loop alongside, **Save Image…** the three plots as one PNG. The motor moves continuously for the whole sweep (typically 30-90 s). If
the app ever dies mid-sweep, the drive keeps the measurement telemetry layout until its next power cycle (it was
never saved to flash): power-cycle it, or put the original mapping back by hand in Parameters Explorer.
Three things learnt on the bench drive (2026-09-25): the drive's test period is sent in steps of 1/256 s, so above
about 25 Hz the points land on 256/n Hz (51.2, 42.7, 36.6, 32, 28.4, 25.6 ...), not exactly where the log spacing
asked; the drive's sine slowly drifts off the asked frequency the longer one test runs (a few tenths of a percent
after 10 s, 3 % after 40 s at 128 Hz), so the page stops the drive before every point and fits each point at the
frequency the reference really shows - the frequency on the plot is that measured one; and on a sensorless motor
with no encoder the **Speed** loop's response is the drive's own speed estimate, which is mostly noise at a small
amplitude (a few Hz) - use a larger amplitude, or measure the **Current / Iq** loop, whose response is measured by
the drive's ADC. On the bench motor the Iq loop measured a -3 dB bandwidth of about 51 Hz with 43° of phase lag
(26 points, 1-256 Hz, every fit clean) - and that is exactly the bandwidth the auto-tuned gains give (FOC Kp
divided by the phase inductance), confirmed by a step test on the raw phase current (8 ms rise). So the plot is
a true picture of the loop: raise *FOC Kp* in Control Laws and the bandwidth rises in proportion; sweep again
to see it and to check nothing started to peak (tried on the bench motor: Kp 0.30 → 1.0 V/A moved the -3 dB
point from 51 Hz to 154 Hz with no peaking). A gain written this way lives in the drive's RAM until you press
**Save to Flash** - a power cycle brings the old value back.

**Faster sampling - 2000 Hz.** The drive sends one telemetry message per tick of its own timer, normally 1000 Hz. The
*Telemetry rate* card on the same page shows the timer's rate and offers **PREPARE 2000 Hz**: it writes *CANopen:
receiving frequency* 2000, saves the drive's whole configuration to flash (after asking you), and then tells you to
**power-cycle the drive and reconnect** (Disconnect, then Connect). From that boot the timer runs at 2000 Hz and every
sweep samples at 2000 Hz - the page's rate note says so. When you are done, **BACK TO 1000 Hz** does the same in
reverse: write 1000, save to flash, power-cycle, reconnect. Measured on the bench drive: reads still answer in under a
millisecond at 2000 Hz and no fault appears; higher rates are untested.

### Record and export data
**Servo Drive → Recorder.** Two charts, pick signals from each chart's own *Signals* menu. **Start /
Stop / Clear**, *Record for* a fixed duration or until stopped, **Fit** to zoom to the data, two draggable
**Cursors** (A/B) with a value-and-difference table, **Save…** to CSV, and **Save Image…** to a PNG (both charts,
stacked exactly as shown on screen). Purely passive — nothing is requested from the drive, it only listens to
what's already broadcasting.

**Trigger…** lets you delay the start of a recording until something actually happens, instead of recording from
whenever you happened to press Start. Pick a **Source** signal, a **Level**, and whether you want the **Rising**
or **Falling** edge — Start then goes **ARMED** (shown in the status area) and waits, with nothing recorded yet,
until that signal genuinely crosses the level in that direction; recording then begins from that exact moment.
Pressing Stop while armed just cancels it — nothing is kept. The trigger you set stays configured for the next
recording too, until you change it back to "(No Trigger)" in the same dialog.

### Read or write parameters directly
- **Parameters Explorer** — all 243 configuration parameters (242 from the Device Reference + Controlword,
  the CiA 402 remap patch's own object). **READ ALL**, edit a value (turns amber), **WRITE
  CHANGED** (asks for confirmation, warns about power-cycle-only parameters). **SAVE TO FILE…** / **LOAD FROM
  FILE…** for a parameter snapshot.
- **Telemetry Explorer** — all 176 read-only telemetry values (175 from the Device Reference + Statusword),
  same READ ALL button, marked with a blue **READ-ONLY** badge so it's never confused with Parameters Explorer.

### Connect a second physical drive (multi-axis)
**Motion → Axes → Additional Axes** (the top of the Axes page; on Item Configuration until 2026-09-26). Type the new drive's Node ID, press **Add Axis**
(needs a shared CAN adapter — a USB cable only ever reaches one drive). It appears in the workspace tree as
**DriveNN (SDR)**, with its own complete set of pages (Motion, Recorder, Parameters/Telemetry Explorer,
Peripherals, Quick/Advanced Tuning) — entirely independent of the primary drive. **Terminal**, **Motion
Controller**, **Axes** and **2D Plotter** are shared, workspace-level pages that always show/address every
connected drive, not just whichever one is selected in the tree.

### Write and run a G-code program
**Motion → Terminal** for one line at a time, or **Motion Controller** for a whole program.
Whatever a G-code line commands on a drive also shows on that drive's own **Servo Drive → Motion - Single
Axis** page, the way its own buttons would have shown it: a speed command fills the target speed field and the
Speed Control note ("Commanding 100.0 rpm (G-code) · sent every 100 ms"), a torque command the Current/Torque
card, a position move the Position Control target and note, and Stop/Off/Reset put the target back to 0 with
"Stop sent. (G-code)". A speed above that page's own Speed Limit is sent as written and reported as such -
the field is never shown a clamped value the drive did not get.
Motion Controller has RUN / PAUSE / RESUME / STOP, a **Loop** count, a **Samples** menu of bundled example
programs, and Open…/Save… for your own `.nc` files (saving into the Samples folder makes it selectable from then
on). See `README.md`'s G-code section for the full command table — the short version: `G1030` is a speed command,
`G00`/`G1020` a position move, `G02`/`G03` an arc, `Txx` solves Inverse Kinematics for a loaded robot model.
**An axis letter/number is not the same as a CAN node ID** — node ID = axis + 1 (e.g. axis 0 / `X` is node 1).

### Aim at a position and orientation together (Orientation IK)
**Motion → Axes → Tool Pose (Orientation IK)**, right below Kinematics Model at the top of the Axes page.
1. First, load a `.kinematics` model in the **Kinematics Model** group above it (Open… or pick a bundled Sample).
2. Pick which **Tool** (chain) to move, from the ones the loaded model defines.
3. Type a **Target Position** (X/Y/Z, mm) and **Target Roll/Pitch/Yaw** (degrees).
4. Press **GO**. It solves for every joint of that chain and moves them all together, through the same mechanism
   any other multi-axis move uses.

Unlike G-code (which can only ever give a position, never an orientation), this is the one place in the tool that
can aim a chain's own tool tip a specific direction, not just place it at a point — useful for anything where a
turret or end-effector's own facing matters, not just its position. Needs every axis of the chosen chain actually
connected, or it refuses with a clear reason instead of guessing.

### Watch a loaded model move live, in 3D
**Motion → 3D Simulation.** Renders whichever model is currently loaded, using its matching `.obj`
mesh, framed on the model's own centre with a floor grid sized to it (its cells are a round 10/20/50/100 mm
step, so the grid doubles as a scale); **Reset View** returns to that framing after you orbit or zoom.
mesh if one is bundled alongside it (the vendor samples all have one; a model with none, like `bench_node7`, shows
a small placeholder marker per body instead). **Open…**/**Samples…** at the top load a `.kinematics` file right
here — the exact same model the Axes page's own Kinematics Model group shows and loads too; loading from
either page updates both, so there's no need to switch pages just to pick a different model. ("Counts per unit"
per axis is still set on Item Configuration only — this page has no editor for it.) Three modes, radio buttons at
the top:
- **Live** (the default) — each connected axis's own body moves from its real Work Zone Count, converted through
  the same "Counts per unit" Item Configuration already uses — nothing is requested from the drive. A body whose
  own axis isn't connected just stays at its last known (or home) pose, rather than guessing.
- **Simulation** — no drive at all, real or simulated — driven instead by Motion Controller's own **SIMULATE**
  button (see below). Switch to this mode, load a program on Motion Controller, press SIMULATE, and watch it play
  out here.
- **Cursor** — pose the model by hand. Selecting it opens a scrollable panel on the right with one slider + a
  precise numeric field per actuated axis; drag or type a value and the pose updates immediately. No drive, no
  G-code program, needed at all — purely local to this page. Each axis's own range comes from its `.kinematics`
  file's own limits when it has them, or a generous default (±180° for a rotary joint, ±500 mm for a linear one)
  when it doesn't. **Reset to Home** zeroes every axis at once. Switching away and back keeps the pose you set —
  only Reset to Home, or loading a *different* model, clears it.

**Reset View** reframes the camera on whatever's loaded, in any mode.

### Preview a G-code program with no drive at all
**Motion → Motion Controller → SIMULATE.** Runs the program currently in the editor exactly like
RUN does (same PAUSE/RESUME/STOP/Loop), except nothing is sent anywhere — it updates a purely virtual pose instead,
which the 3D Simulation page's own **Simulation** mode shows live. Needs a kinematics model loaded first (Item
Axes → Kinematics Model). Always starts from the model's own home pose, so a repeat run always looks the
same. What you'll see for each kind of command:
- **A move (G00/G01/G1020, or a Txx tool move)** — the body moves to its target. G01 (or a Txx move with a feed
  rate) animates smoothly over the time the feed rate implies; a plain G00/G1020 jumps there instantly (there's no
  real drive to ask how fast it would actually accelerate).
- **A speed command (G1030)** — that axis spins continuously at the commanded rate, for as long as the speed stays
  commanded — this is what makes a pure speed-ramp program (like the bundled `speed_ramp.nc`) actually show motion.
- **A torque command (G1040)** — no motion is shown; there's no load or inertia to model without real hardware, so
  the body just holds still, and the log says so plainly.
- **Reset/Off/Stop/Capture/Release** — the affected axis (or axes) holds wherever it currently is.
- **An arc (G02/G03)** — not supported yet in Simulate; the line is refused with a clear message, like any other
  unsupported line.

### Compare two axes' motion visually
**Motion → 2D Plotter.** Pick an X Axis and Y Axis from any connected drives; it continuously
traces their Work Zone Counts against each other. It needs **two** connected axes - with one drive connected the
page says so (the Y Axis shows "— pick an axis —") and traces nothing; to watch a single axis, use Motion - Single
Axis's live plot or the Recorder. Genuinely useful for confirming an interpolated multi-axis move
(a G02/G03 arc, say) actually traces the shape it's supposed to, rather than reading two separate time-series
charts. **Save Image…** / **Save Data…** to export.

### Check on every connected drive at once
**Motion → Axes.** One row per connected drive: its name (e.g. Drive01), node, online/stale, Work
Zone Count, Work Zone Speed, fault bits — refreshed live, nothing requested.

### Watch any signal live, on any connected axis
**Motion → Status Monitor.** Pick a **Target** (which connected axis) and a **Signal** (searchable —
type part of a name, e.g. "fault" or "node id", to filter) per row; **+ Add Signal** for another row, **Remove** to
drop one. A signal the drive already broadcasts (e.g. Fault Bits, Work Zone Count/Speed) updates live for free,
same as everywhere else in the tool — nothing extra is requested from the drive. A signal that isn't broadcast
shows **"not broadcast"** until you press that row's own **Read** button (a one-off read, not a poll). Tick a
row's **Gauge** checkbox for a fill-bar view instead of a bare number — only available for a signal that has a
known min/max (an enum or on/off value can't be usefully bar-graphed, so its checkbox stays disabled). Rows aren't
saved between restarts — set them up fresh each session.

### Move several axes at once, no G-code
**Motion → Multi Axis Motion.** One row per connected axis — its name, node ID, live Position (Work
Zone Counts, updates for free from the broadcast), a **Target** field, and its own **MOVE** button. MOVE sends that
one axis's Servo command (the exact same one its own Motion page's MOVE TO uses) and needs a Motor Encoder or
Servo Encoder configured first, same requirement as there — if it isn't, you'll see the same warning Motion gives.
**MOVE ALL** dispatches every row's own target at once — each axis still moves independently, not coordinated
together; for a coordinated multi-axis move, use a G-code **Txx** line (Inverse Kinematics) instead.

### Open several Servo Drive or Motion tools at once, side by side
**Ribbon → Floating Tools → Docked View.** Switches Servo Drive and Motion from one page at a time to a
dockable workspace — click any tool in the sidebar list and it opens as its own panel instead of replacing what
was already open, so you can watch Motion, Axes, a Recorder and a second drive's own Motion together. Each panel
has its own title bar. **To move a panel, drag it by its title bar**: it lifts off and *drop guides* appear - an
arrow at each edge of the workspace (let go on one to dock the panel along that whole side, left, right, top
or bottom) and, while you hover over another panel, a cluster of five on it (left / right / above / below
that panel, or the centre one to stack the two as tabs). The guide under the mouse lights up and a gold
preview shows the space the panel will take. Let go anywhere else and the panel simply stays as a floating
window, which you can drag by the same title bar back onto a guide any time. **❐** on the title bar (or a
double-click on it) floats the panel or docks it back; **✕** closes just that one (the sidebar reopens it).
Resize two docked panels by dragging the edge between them; a panel made smaller than its page simply scrolls.
An edge guide always means the whole edge - the panel you drop there last is the outermost one on that side.
One thing to know: when one panel sits on the top or bottom edge and you drop another on the left or right
edge, the split between the two is chosen by the layout itself and may be uneven - drag the separator
between them to set it.
An axis's own tool (e.g. Drive02's Motion) docks under its own name, same as a single-page tab would show. Only
Servo Drive and Motion are dockable (they share one workspace, so a drive's own Motion page can sit next to the 2D Plotter) — System Configuration always stays a single page, switching to it and back
leaves your docked panels exactly as you left them. **Reset Layout** closes every open panel and starts fresh
with just the current one. Your arrangement (Docked View on/off, which panels were open, and their sizes) is
remembered across restarts, the same way your connection settings are.

### Convert between a motor's Ke, Kv and Kt constants
**Ribbon → Floating Tools → Motor Constants.** A standalone calculator, no drive connection needed. Set the motor's
**Poles Number** first, pick which of the three constants you actually know with the radio buttons (Back-EMF
Constant Ke, Velocity Constant Kv, or Torque Constant Kt), type it into its own field — the other two are computed
immediately. Poles matters because Ke is defined per *electrical* radian while Kv and Kt are mechanical-domain
quantities; get Poles wrong and both computed values will be wrong too.

### Export the whole object dictionary
**Ribbon → Parameters → Export HTML / Export EDS.** Covers all 419 configuration + telemetry parameters at once
(both explorer pages combined), with whatever value each was last actually read as — nothing is requested from
the drive just for the export. **Export HTML** makes a readable report you can open in any browser. **Export EDS**
makes a genuine CANopen Electronic Data Sheet (`.eds`) that other CANopen configuration tools can read. A couple
of honest limitations worth knowing: this drive doesn't answer the standard CANopen identity objects, so the
file's own "Mandatory Objects" section is genuinely empty — that's correct, not missing data; and the one
register that broadcasts a 16-bit float (DC-bus voltage) is stored as an unsigned 16-bit integer in the .eds,
since CANopen itself has no 16-bit float type — the file says so in a comment right above that entry.

### Compare two parameter snapshots
**Ribbon → Parameters → Save Snapshot**, then **Ribbon → Floating Tools → Compare Parameters**. Save Snapshot
writes a small file of every parameter this session has actually read a value for — e.g. save one before a
tuning pass, change some settings, save another after. Compare Parameters opens a dialog with two **Load…**
pickers (**File 1** / **File 2**); once both are loaded, **Compare** lists every parameter, highlighting the
ones that differ. **Differences only** (on by default) hides everything that matches, so you see just what
changed; untick it to see the full comparison. A parameter only one of the two files ever read shows as
"(not in file)" rather than being silently skipped. Useful for comparing a drive's own settings before/after a
change, or two drives' configurations side by side — it's file-based, not a live read from two drives at once.

### Read or write a register that isn't in the parameter table
**Servo Drive → Parameters Explorer → Raw Register Access** (the box below the table). For a register
this tool doesn't already know by name — something undocumented, or added in a drive firmware revision newer
than this tool's own catalog. Type the **Index** and **Sub-Index** (hex, e.g. `0x2000` and `0x03`), pick the
**Type** the register actually uses on the wire, then **Read** or **Write**. This is the exact same read/write/
verify path every named row already uses underneath — a write still asks you to confirm first, and is still read
back and checked afterward — the difference is this tool has no idea whether the address you typed is real, or
genuinely writable, until the drive itself answers. Only use it for a register you already know the meaning of.

---

## 4. Real hardware — what's been validated so far

**A parameter write does not survive a power cycle** (measured 2026-09-25: node ID, receiving frequency and the telemetry
mapping all came back as they were after the last automatic tuning). Use **Ribbon → Parameters → Save to Flash** after
your changes: it sends the drive the same store request the vendor's own tool sends, the drive acknowledges it, and the
values then survive power cycles (confirmed on the bench drive). It saves EVERYTHING the drive holds at that moment,
node ID and CAN bit rate included, so check those first.

The bench drive (Stimita SDR-CB-60R-D) connects over its own USB cable and has had a real motor attached since
2026-09-24. **Its flash, as verified after a power cycle at the end of 2026-09-25: node 1, CAN bit rate 1000,
receiving frequency 1000 Hz, the "Speed" telemetry layout, FOC Kp 2.25 V/A with FOC T 1.67 ms** (a current loop of
about 257 Hz). It is still sensorless - no encoder - which is why the position modes refuse and the speed loop
cannot be measured. Validated on it on 2026-09-25, all through the GUI:

- **Save to Flash** (ribbon, the Frequency Response page's PREPARE/BACK TO buttons, the Automatic Test page) - the
  drive acknowledges the store and the values come back after a power cycle; the 2000 Hz telemetry rate too.
- **Frequency Response**: stepped sine and chirp on the current loop, the telemetry layout put back exactly every
  time; the Nyquist plot's margins (94° at 190 Hz at Kp 1.0); the speed loop gives only noise here (sensorless).
- **Automatic Test**: RUN TESTS (13 checks OK, current loop measured in 13 s) and BETA (four rounds raised FOC Kp
  from 1.0 to 3.375 V/A, 199 to 326 Hz, and stopped at the verification limit; at 2000 Hz it correctly changed
  nothing). The final 2.25 V/A was the user's choice for margin under a real load.
- **Direct Field Control** at a real 0.1 V and 0.5 V, both modes, no fault - and the 0.5 V ceiling for this motor
  (§3). The earlier "0.1 V" that tripped the protection was a 46 V mis-encoding, since fixed.
- **Two instances on one USB cable**: the second is refused with a clear message, the first keeps working.
- **The current loop at the saved FOC Kp 2.25 V/A, both methods (evening of 2026-09-25)**: the stepped sine
  (0.5 A, 2-256 Hz, 24 points, every fit quality 0.88-0.91) is flat within ±0.3 dB to 64 Hz, -0.44 dB / -11° at
  128 Hz and -0.73 dB / -21° at 256 Hz - no -3 dB point inside the range; the chirp (0.5 A, Period 0.1 s x 6, 58
  points, coherent to 440 Hz) agrees within ~0.3 dB where they overlap and reads its -3 dB point at about 276 Hz,
  at the edge of the sweep's energy (treat it as roughly 280-350 Hz). The open loop stays outside the unit circle
  across the whole range, so no phase margin is readable at this gain (at Kp 1.0 it was 94° at 190 Hz).
- **Quick Tuning's automatic tuning, run a third time on the real drive (late on 2026-09-25)**, after a report that
  it did nothing: through the same START button it put the drive into Autoconfiguration within half a second, the
  Datasheet changed at 44 s and the page reported completion at 62 s with 19 values changed - the procedure and the
  page both work. It rewrote the Datasheet and Control Laws as it always does, so the drive now holds the auto-tuned
  set again (FOC Kp 0.30 V/A, phase resistance 1.13 Ω, inductance 1.89 mH, Ke 0.0148 V/(rad/s)); by the user's
  decision it stays there until the encoder arrives. If START ever seems to do nothing after Yes, quit and restart
  the app, reconnect and try again; if it repeats, note the text under the START button and the status-bar line.
- **The Bode page's cursors and the full-contour Nyquist plot were checked on that real sweep** (the points reloaded
  from its CSV): cursor B dragged with a real mouse on the phase plot snapped to the measured 21.3 Hz and the gain
  plot's twin followed; the closed loop T draws the expected short arc from (1.01, 0) to (0.86, -0.33), and the
  speed loop on this sensorless motor gives only a scatter of hollow rings - a measurement that failed, as documented.

Earlier sessions (2026-09-24, node 7 at the time):

- **Connecting, reading all 417 parameters (242 config + 175 telemetry) — confirmed working, both via the GUI and
  the command-line check scripts** (`tools/usb_readonly_check.py`), zero failures.
- **Real motor speed commands (ESC RPM)** — confirmed working via `tools/real_motor_speed_test.py`: commanded 50,
  100 and 200 rpm, watched real phase currents and (noisy at low speed, clean at 200 rpm — this drive has no
  physical encoder, so speed is estimated, not measured) Work Zone Speed, Stop correctly returns it to rest.
- **Orientation IK** — confirmed working end to end against the real drive (single-joint test rig, since only one
  physical drive exists on the bench) — solves correctly and, correctly, refuses to actually move the motor
  because this drive has no Motor/Servo Encoder configured (same reason MOVE TO/BY refuse).
- **Quick Tuning → Automatic Tuning** — confirmed working on the real drive: START AUTOMATIC TUNING measured the
  motor (~30 s), beeped, wrote 19 Datasheet/Control Law values (phase resistance/inductance, back-EMF constant,
  inertia, damping, FOC/ESC/Servo/Micro-Speeding gains) and saved them to the drive's flash — "Auto tuning
  completed successfully.", pre-flight checklist green again afterward, no fault.
- **Advanced Tuning (every page), Recorder, Parameters Explorer, Peripherals, Terminal, and Motion Controller** —
  all confirmed working on the real drive in the same 2026-09-24 session ("try each and every button and function
  from GUI"): Advanced Tuning's Measured Motor Data / Control Laws pages showed the exact values the Quick Tuning
  run above had written; a real Step Response Test (small 3 Hz amplitude) ran and stopped cleanly; the Recorder's
  Start/Stop/Fit/Cursors/Save all worked against live telemetry; a Parameters Explorer row and a Peripherals field
  each did a real Write round-trip (written back to their original value afterward); the Terminal G-code console
  and a real 3-line Motion Controller program both commanded the real motor correctly. See `README.md` for the
  full detail. The 2D Plotter opened correctly but genuinely can't demonstrate a two-axis trace — only one
  physical drive exists on this bench.
- **3D Simulation, Live mode** — verified live in the real running app (not just headless tests): a 12-axis model
  rendered correctly at its home pose, and a real speed command on one connected axis visibly rotated that body
  around its own hip joint. This used the **Simulator** interface, not the real bench drive — no bundled model
  both uses axis 6 (this bench's node 7) and has a real mesh, so a genuinely multi-body live render against real
  hardware is still untried (needs a second real drive, like multi-axis in general below).
  **Tried against the real bench drive too (2026-09-24)**: connected to node 7, loaded `bench_node7.kinematics`
  (the only sample using axis 6 — one joint, no mesh, shown as the generic placeholder sphere). Live mode correctly
  read "Live: 1 of 1 axis(es) connected." in green — nothing was visibly rotating, but that's inconclusive here by
  construction, not a real finding: this sample's one body shares the base's own origin, so a symmetric placeholder
  sphere has nothing to show regardless of whether the transform is right. Checked the underlying data directly
  instead, via the Terminal's own CAN trace: while genuinely spinning the real motor at 50 rpm (`G1030 [6]50` —
  confirmed by fluctuating phase currents), **Work Zone Count stayed at 0 the whole time**. This is expected, not a
  bug — this bench drive has no Motor/Servo Encoder configured, so it has no position feedback to give during a
  speed command. In practice, this means **Live mode correctly shows no motion on this drive during a speed
  command** — the render is honestly following the drive's own real telemetry, which itself has nothing to report.
  Whether Live mode shows real motion on an encoder-equipped drive is still untested (none available on this
  bench). Motor was stopped cleanly with `G1002` and the drive disconnected afterward.
- **3D Simulation, Simulation mode + Motion Controller's SIMULATE** — confirmed live in the real app: SIMULATE ran
  a full sample program (`speed_ramp.nc`) to completion with zero drive connected, and a continuous speed command
  was watched visibly spinning a leg on screen (two screenshots a second and a half apart, clearly different
  positions), with STOP correctly freezing it.
- **3D Simulation, Cursor mode** — confirmed live: typing a value and dragging a slider both moved the loaded
  `robot-dog.obj` model correctly on screen (two different joints at once, composing correctly), and Reset to
  Home returned it to its exact original stance.
- **Multi-axis (two or more real drives at once) and homing** — **not yet tried on real hardware**, simulator-only
  so far (a second drive/CAN adapter hasn't arrived).

**Before sending anything to a real drive that isn't already covered above**: state exactly what you're about to
send and get an explicit go-ahead first. Never write a parameter or send a motor command without that. Always
confirm the drive's node ID before sending anything (checked once at connect, not before every single command).

---

## 5. Troubleshooting — what a message means

| You see... | What it means |
|---|---|
| **NO RESPONSE · NODE N** (pill, amber) | The bus opened but the drive isn't answering. Check node ID, bit rate, termination, wiring |
| **"Activation Key is Missing"** (fault bit) | The drive has no activation key yet — get one from Stimita support (see §2) |
| **"...needs a Motor Encoder or Servo Encoder configured..."** | A position-based command (ENABLE, MOVE TO/BY, or Orientation IK's GO) was refused because Work Zone Count isn't known to track a real position on this drive. Configure an encoder under Datasheet → Feedback Settings, or use Speed/Current/Torque control instead |
| **"...inverse kinematics did not converge..."** | The typed target is outside what the loaded chain can actually reach (or, rarely, right at the edge of solver precision) — nothing was sent |
| **WARNING - this drive's own telemetry mapping does not fit an 8-byte message** (status bar right after connecting, AND a persistent amber banner on Motion - Single Axis, above the cards) | The drive's own configured telemetry layout has too many bytes for one CAN message — one of the configured fields (named in the message) silently won't appear as a card or curve. Not a fault. The status-bar copy disappears the moment you navigate elsewhere (it's a single line, overwritten by the next thing that happens) — the banner on the Motion page does not, and stays up for as long as the condition holds, so you don't lose track of why a value is missing. Fix it by reconfiguring the drive's own telemetry mapping (Parameters Explorer, filter "mapping" — 12 registers, 4 slots × 3 CAN messages) to drop something else and free room, or read the missing field on demand instead (Telemetry Explorer) in the meantime |
| **"T_ needs axis(es) [...], not connected"** | A multi-axis G-code line or Orientation IK GO named a chain that needs a drive that isn't currently connected |
| The Dock shows the Python rocket for a moment when the app quits | The run is under the bare interpreter: `SDR_NO_RELAUNCH` is set, or the console printed "running under the bare interpreter" (the bundle could not be built or launched - read the reason it printed). Normally `main.py` re-launches itself through **dist/SDR Servo Tuning Tool.app**'s own interpreter, from PyCharm and terminals too (§2), and the Dock draws every stage with the tool's icon |
| macOS says "Apple could not verify ... is free of malware" (only Done / Move to Bin), or Windows SmartScreen blocks the Setup | The builds are signed ad hoc / not code-signed (no developer certificate). macOS: press Done, then **System Settings > Privacy & Security > Open Anyway** (once). Windows: **More info > Run anyway**. To ship without any of this, sign and notarise - §2, *Shipping without the Gatekeeper dialog* |
| The page won't scroll while the pointer is over a plot | Fixed 2026-09-25: the live telemetry plot passes the wheel to the page; the Bode and Nyquist plots do too (they zoom only with Cmd/Ctrl held, or a pinch); the Recorder and 2D Plotter keep the plain wheel for zooming - move the pointer off those two to scroll |
| I can't zoom into a Bode / Nyquist plot | Hold Cmd (Ctrl on Windows/Linux) while scrolling, or pinch on the trackpad; drag to pan; double-click to reset the view. The plain wheel scrolls the page on purpose |
| A field won't accept the value you typed | Every field here ignores the mouse scroll wheel on purpose (a past real-world near-miss — see `README.md`) — type the value, or use the arrows/steppers |
| **"... is already open in another program - another instance of this tool, or the vendor IDE?"** (Connect) | Only one program can hold the drive's USB cable. Disconnect in the other program (or close it) and connect again; the one that is connected is not affected |
| **"USB read failed: device reports readiness to read but returned no data (device disconnected or multiple access on port?)"** | Either the cable was pulled, or - before the exclusive lock of 2026-09-25 - two programs were reading the same port. Reconnect; make sure nothing else has the port |
| **"N point(s) drawn hollow"** under a Bode / Nyquist plot | Those points did not follow their reference cleanly (noise, saturation, a limit, or too little sweep energy) and are left out of the bandwidth, peak and margins. An all-hollow plot is a measurement that failed - on a sensorless motor that is what the speed loop gives |
| **"Beta finished: ... as fast as ... sampling can verify"** or **"... flat to N Hz - beyond what this measurement can verify"** | The loop's bandwidth is now above what the telemetry sampling (or the drive's own test signal, ~450 Hz) can check for peaking, so Beta stops raising the gain rather than push it blind. PREPARE 2000 Hz extends the reach to about 600 Hz |
| **"Beta finished: FOC Kp backed off to ..."** | The last raise produced peaking, overshoot or a margin under its floor; the previous, clean gain was put back. That gain is the practical ceiling for this motor |
| **"Beta finished: FOC Kp is at the cap ..."** | The gain reached the cap for this motor and control rate (a current-loop bandwidth of a twentieth of the control rate); going further would run into the loop's own sampling and current-sense noise |
| **"A parameter write does not survive a power cycle"** (a value you wrote is back to its old value after power-up) | Writes go to the drive's RAM. Use **Save to Flash** (Parameters ribbon, or the Automatic Test page) after your changes |

---

## 6. Where to go for more

- **`README.md`** — the full technical reference: exact protocol frames, command codes, register addresses, the
  complete G-code command table, connection details for every CAN adapter.
- **`tools/`** — standalone scripts for checking a real drive outside the GUI (`usb_readonly_check.py`,
  `usb_write_check.py`, `real_motor_speed_test.py`, `telemetry_rate_probe.py`), each with a hard safety guard limiting exactly what they can
  send.
- **The sibling `SDR-CB-60R-D firmware` project** (a separate directory, own README/NOTES) — a firmware-level
  binary patch on this same bench drive that adds real CiA 402 `Controlword`/`Statusword` objects
  (`0x6040`/`0x6041`), confirmed live to genuinely drive Operation Mode and respect the comm-timeout
  watchdog. Now in this tool's own parameter catalog (section "CiA 402 (firmware patch)") — Controlword
  is a normal row on Parameters Explorer, Statusword on Telemetry Explorer, like any other parameter.
  See that project's own `OPERATIONAL_MANUAL.md` for the register bit map and current limitations.
