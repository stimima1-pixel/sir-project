"""Software model of an SDR servo drive on a python-can *virtual* bus.

Lets the GUI and the automated tests run without hardware. It speaks the protocol of the device reference: read / write
requests (with the 0x60 write acknowledgement of the real drive), error responses, the speed / stop / off / reset / servo
(position hold or move) / current / torque / reset-work-zone commands and the telemetry messages, whose contents follow
the drive's telemetry-mapping parameters. The motor is a first-order lag towards the commanded speed; a commanded
current or torque has no real dynamics, only a visible signature in the phase currents. Like the real drive, it ignores
every command but Reset while a fault is latched.
"""
import math
import random
import struct
import threading
import time

import can

from . import bode, defs, protocol as P

DEFAULTS = {   # (index, sub) -> initial value; anything not listed starts at 0 / False
    (0x2000, 0x02): 5.0, (0x2000, 0x03): 14, (0x2000, 0x04): 0.8, (0x2000, 0x05): 0.0006, (0x2000, 0x06): 0.045,
    (0x2000, 0x07): 1.0e-4, (0x2000, 0x08): 2.0e-5, (0x2000, 0x09): 1, (0x2000, 0x0A): 2, (0x2000, 0x0C): 1.0,
    (0x2000, 0x0D): 5.0,
    (0x2002, 0x02): 0.005, (0x2002, 0x12): 0.5, (0x2002, 0x13): 0.002, (0x2002, 0x22): 0.05, (0x2002, 0x23): 0.05,
    (0x2002, 0x32): 10.0, (0x2002, 0x33): 0.5, (0x2002, 0x34): 50.0, (0x2002, 0x36): 10, (0x2002, 0x54): 1.0,
    (0x2004, 0x73): True, (0x2004, 0x52): True, (0x2004, 0x42): True,
    (0x2014, 0x03): 300.0, (0x2014, 0x04): 50.0,
    (0x2034, 0xB2): -100000, (0x2034, 0xB3): 100000,
    (0x2044, 0x04): 4.0,
    (0x2025, 0x03): 12.0, (0x2025, 0x04): 6.0, (0x2025, 0x07): 0.1,
    (0x2024, 0x03): 0.5, (0x2024, 0x04): 0.05, (0x2024, 0x05): 0.2,
    (0x2049, 0x03): 5.0, (0x2049, 0x04): 3.0,
    (0x3013, 0x02): 65536, (0x3013, 0x10): 1, (0x3013, 0x11): 20, (0x3013, 0x12): 1, (0x3013, 0x13): 1,
    (0x3013, 0x14): 32, (0x3013, 0x21): 16,
    (0x3000, 0x02): 1000, (0x3000, 0x04): 3.5, (0x3000, 0x06): 1000.0,
}
AUTOCONFIG = ("autoconfiguration_brushless_motor", "autoconfiguration_advanced_brushless_motor", "autoconfiguration_brushed_motor")
MODE_AUTOCONFIG = 3
IQ_TAU = 0.005   # s: the simulated current loop's own time constant (~32 Hz bandwidth) - far faster than the speed loop's `tau`
TEST_MODES = {   # step-response test command key -> Operation Mode while it runs (a square wave, sign flipping every 'period' seconds)
    "testing_field_oriented_control_foc": 15, "testing_electronic_speed_control_esc": 16, "testing_servo_control": 17,
    "testing_iq_current_control": 20,
}
MEASURED = {   # what the simulated procedure "measures" and computes (a real drive measures its own motor)
    (0x2000, 0x04): 0.87, (0x2000, 0x05): 0.00071, (0x2000, 0x06): 0.0512, (0x2000, 0x08): 3.1e-5, (0x2000, 0x09): True,
}
GAINS = {(0x2002, 0x12): 0.62, (0x2002, 0x13): 0.0021, (0x2002, 0x22): 0.058, (0x2002, 0x23): 0.041, (0x2002, 0x32): 12.0}   # scale with the dynamics
DEVICE_INFO = {(0x5000, 0x9F): 0x0A020192, (0x5000, 0xA0): 100234, (0x5000, 0xA1): 26092101}    # device type, serial numbers
ABORT_NO_OBJECT, ABORT_READ_ONLY = 0x06020000, 0x06010002    # CANopen-style abort codes
ACTIVATION_MISSING = 0x8000
MODE_IDLE, MODE_OFF, MODE_FAULT, MODE_FOC, MODE_ESC, MODE_SERVO = 0, 1, 2, 4, 6, 8
MODE_KICKSTART = 9
MODE_VIRTUAL_BRAKE = 10
MODE_DFC_ROTATION, MODE_DFC_POSITION = 11, 12
MODE_POWER_GENERATION = 14
MODE_BRUSHED = 19


class SimulatedDrive:
    def __init__(self, channel="sdr-sim", node_id=1, *, tau=0.15, tpdo_hz=100.0, ack_writes=True,
                 sdo_unreadable=(), activated=True, autoconfig_seconds=1.5):
        # tpdo_hz: how often each telemetry message is sent. sdo_unreadable: telemetry keys that answer a read request
        # with an error. activated=False: the drive reports "Activation Key is Missing" (fault bit 15); it is ASSUMED
        # here that a Reset does not clear it (the fault comes back at once) - not confirmed on a real drive.
        self.node, self.tau, self.ack_writes, self.sdo_unreadable = node_id, tau, ack_writes, set(sdo_unreadable)
        self.sticky_faults = 0 if activated else ACTIVATION_MISSING
        regs = defs.load_registers()
        self.dtypes = {r.key: r.dtype for r in regs}
        self._catalog = defs.telemetry_catalog()
        self.params = {r.key: self._initial(r) for r in regs if not r.telemetry}
        self.params[(0x3000, 0x03)] = node_id
        self.params[(0x3000, 0x05)] = 3.0 * tpdo_hz                      # telemetry frequency: all 3 messages together
        self.flash = dict(self.params)                                   # what a reboot loads: the boot configuration,
                                                                          # replaced by every 'save' (0x1010:1) - the drive's flash
        self.timer_isr_hz = float(self.params[(0x3000, 0x06)])           # the real drive's timer interrupt = the
        # receiving frequency as it was at power-up (measured 2026-09-25); it caps the telemetry rate, see _run/reboot
        for message, tpdo in enumerate(defs.load_telemetry()[0]):        # default telemetry mapping
            for slot, f in enumerate(tpdo.fields, 1):
                self.params[(0x1A00 + message, slot)] = defs.mapping_code(f.index, f.sub)
        self._commands = {(c.cob, c.code): c for c in defs.load_commands().values() if c.defined and c.supported}
        # motor state
        self.esc_target_hz = 0.0
        self._iq = 0.0                          # the measured Iq current, lagging its reference by IQ_TAU
        self.flash_saves = 0
        self.flash_mode = "ack"                 # how 0x1010 'save' is answered: "ack", "abort" or "silent" (tests)
        self._test_ref = 0.0                    # the running test's own reference value (see _step)
        self.speed_hz = 0.0
        self.elec_pos = 0.0
        self.wz_count = 0.0
        self.fault_bits = 0                     # latched faults; tests may set it
        self.off = False
        self._ia = self._ib = 0.0
        self._vbus = 24.0
        self.bus_volts = 24.0                   # the supply of the power stage; 0 = not powered (like the bench drive)
        self.autoconfig_seconds = autoconfig_seconds
        self._autoconfig = None                 # the automatic configuration in progress: {"until", "values", "key"}
        self._test = None                       # a step-response test in progress: {"key", "period", "amplitude", "signature", "phase_start"}
        self._servo_target = None               # a Work Zone Count being held/moved to by the plain "servo" command, else None
        self._foc_current = self._foc_torque = None   # a steady current / torque being commanded, else None (mutually exclusive)
        self._direct_field_mode = None          # "rotation" or "position" while a Direct Field Control command is active, else None
        self._virtual_brake = False             # True while a Virtual Brake command is being repeated
        self._kickstart_mode = False            # True while a Kickstart command is being repeated
        self._power_generation = False          # True while a Power Generation command is being repeated
        self._brushed_mode = False              # True while a Brushed Open Loop Control command is being repeated
        self.brake_engaged = False              # last commanded Electromagnetic Brake state (discrete, no repeat)
        self.gpio_output = False                # last commanded GPIO: Generic Output state (discrete, no repeat)
        self.gpio_pwm_duty = None               # last commanded GPIO: PWM output duty cycle, else None (discrete)
        self._last_rx = time.monotonic()        # when the drive last heard from the host (any message)
        self.autoconfig_runs = self.autoconfig_aborted = 0
        self.flash_saved = False
        self.commands_received = []             # (command key, values) of every command that was carried out
        self.ignored_commands = []              # commands received while a fault was latched
        self.bus = can.Bus(interface="virtual", channel=channel)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sdr-sim", daemon=True)
        self._thread.start()

    @staticmethod
    def _initial(reg):
        """The starting value of a parameter: the listed default, else zero moved into the parameter's valid range/options
        (the real drive only ever holds valid values)."""
        value = DEFAULTS.get(reg.key, 0.0 if P.is_float(reg.dtype) else 0)
        if reg.options and value not in reg.options and reg.dtype != "BOOL":
            value = min(reg.options)
        if reg.min is not None and value < reg.min:
            value = reg.min
        if reg.max is not None and value > reg.max:
            value = reg.max
        return value

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.bus.shutdown()

    # ---- main loop -------------------------------------------------------------------------
    def _run(self):
        last = next_tpdo = time.monotonic()
        while not self._stop.is_set():
            try:
                msg = self.bus.recv(timeout=0.005)
                if msg is not None:
                    self._handle(msg)
                now = time.monotonic()
                if now - last >= 0.005:
                    self._step(now - last)
                    last = now
                self._autoconfig_step(now)
                if now >= next_tpdo:
                    mapped = self._publish()
                    # the mapped messages share the telemetry frequency - measured on the real drive (2026-09-25,
                    # tools/telemetry_rate_probe.py): with one message mapped, that one gets the whole budget
                    # ...and never faster than the timer interrupt, one message per tick (2026-09-25: 1000 asked
                    # for 3000 gave 1000/s until the receiving frequency was saved as 2000 and power-cycled)
                    next_tpdo = now + max(mapped, 1) / max(min(self.params[(0x3000, 0x05)], self.timer_isr_hz), 0.1)
            except can.CanError:
                return

    def _step(self, dt):
        previous = self.speed_hz
        test = self._test
        test_key, ref = None, 0.0
        if test is not None:
            elapsed = time.monotonic() - test["phase_start"]
            test_key, amplitude, wave = test["key"], test["amplitude"], test["wave_form"]
            cycle = int(elapsed / test["period"]) % 2 == 0
            # The drive's own Wave Form values (Device Reference pp. 89-91/95): 0 square, 1 square positive-only, 2 sine,
            # 3-5 frequency sweeps. The sweep is modelled on what the bench drive really does (bode.chirp_frequency,
            # measured 2026-09-25): from 1/Period, faster than exponential, to a few hundred hertz at the end of a cycle
            # of 10 x Period, then over again; 4 and 5 are ASSUMED to sweep the same way with the amplitude decaying
            # linearly / quadratically over the cycle (not measured). The square waves flip every Period.
            if wave == 1:
                ref = amplitude if cycle else 0.0
            elif wave >= 3:
                test["chirp_phase"] = test.get("chirp_phase", 0.0) + 2.0 * math.pi * bode.chirp_frequency(elapsed, test["period"]) * dt
                x = (elapsed % (bode.CHIRP_CYCLE * test["period"])) / (bode.CHIRP_CYCLE * test["period"])
                decay = 1.0 if wave == 3 else (1.0 - x) if wave == 4 else (1.0 - x) ** 2
                ref = amplitude * decay * math.sin(test["chirp_phase"])
            elif wave == 2:
                ref = amplitude * math.sin(2.0 * math.pi * elapsed / test["period"])
            else:
                ref = amplitude if cycle else -amplitude
            if test_key == "testing_electronic_speed_control_esc":
                self.esc_target_hz, self.off = ref, False       # the wave form in the speed target: the tau-based
                                                                  # dynamics below give it a realistic-looking response
        self._test_ref = ref
        if self.faults:
            self.esc_target_hz = 0.0                            # a fault powers the motor off: it coasts down
        self.speed_hz += (self.esc_target_hz - self.speed_hz) * (1.0 - math.exp(-dt / self.tau))
        self.elec_pos = (self.elec_pos + 2 * math.pi * self.speed_hz * dt) % (2 * math.pi)
        poles = self.params[(0x2000, 0x03)] or 2
        if test_key == "testing_servo_control":
            self.wz_count = ref                                 # the wave form as the target (no mechanical model between it and the motor)
        elif self._servo_target is not None:
            self.wz_count = self._servo_target                  # held still at the target (no mechanical model to fight)
        else:
            self.wz_count += self.speed_hz / (poles / 2) * self.params[(0x3013, 0x02)] * dt
        amp = 0.3 + 0.02 * abs(self.speed_hz) + 0.004 * abs((self.speed_hz - previous) / dt)
        current_ref = (ref if test_key in ("testing_field_oriented_control_foc", "testing_iq_current_control")
                       else self._foc_current if self._foc_current is not None else 0.0)
        # the current loop: its own, much faster lag - and, as on the real drive (bandwidth = Kp / L, measured 2026-09-25),
        # faster in proportion to FOC Kp: IQ_TAU at the default Kp of 0.5 V/A, half that at 1.0 V/A
        tau_iq = IQ_TAU * 0.5 / max(float(self.params.get((0x2002, 0x12), 0.5)), 0.01)
        self._iq += (current_ref - self._iq) * (1.0 - math.exp(-dt / tau_iq))
        if test_key in ("testing_field_oriented_control_foc", "testing_iq_current_control"):
            amp += abs(ref)                                     # the test's own current shows up as extra phase current
        if self._foc_current is not None:
            amp += abs(self._foc_current)                       # a commanded current shows up the same way
        if self._foc_torque is not None:
            amp += abs(self._foc_torque)                        # no torque constant modelled: shown 1:1 with current, for a visible signature only
        self._ia = amp * math.sin(self.elec_pos) + random.gauss(0, 0.005)
        self._ib = amp * math.sin(self.elec_pos - 2 * math.pi / 3) + random.gauss(0, 0.005)
        self._vbus = self.bus_volts - 0.05 * amp + random.gauss(0, 0.01) if self.bus_volts else 0.0

    @property
    def faults(self):
        return self.fault_bits | self.sticky_faults

    def _live(self):
        count = (int(self.wz_count) + 2**31) % 2**32 - 2**31    # Work Zone Count is INT32 telemetry: wrap like a real
                                                                 # counter instead of letting encode_value raise and
                                                                 # silently kill this thread (~33 min at 1000 rpm)
        pole_pairs = (self.params[(0x2000, 0x03)] or 2) / 2
        mode = (MODE_FAULT if self.faults else MODE_AUTOCONFIG if self._autoconfig
                else TEST_MODES[self._test["key"]] if self._test
                else MODE_FOC if (self._foc_current is not None or self._foc_torque is not None)
                else MODE_SERVO if self._servo_target is not None
                else MODE_DFC_ROTATION if self._direct_field_mode == "rotation"
                else MODE_DFC_POSITION if self._direct_field_mode == "position"
                else MODE_VIRTUAL_BRAKE if self._virtual_brake
                else MODE_KICKSTART if self._kickstart_mode
                else MODE_POWER_GENERATION if self._power_generation
                else MODE_BRUSHED if self._brushed_mode
                else MODE_OFF if self.off else MODE_ESC if self.esc_target_hz else MODE_IDLE)
        test_key = self._test["key"] if self._test else None
        ref_wz = (self._test_ref if test_key == "testing_servo_control"
                  else self._servo_target if self._servo_target is not None else self.wz_count)
        return {"fault_bits": self.faults, "vbus": self._vbus, "speed_hz": self.speed_hz, "op_mode": mode, "comm_mode": 2,
                "wz_count": count, "wz_modulo": count % max(int(self.params[(0x3013, 0x02)]), 1),
                "wz_dimension": int(self.params[(0x3013, 0x02)]),          # one turn of the Work Zone, in counts
                "ia": self._ia, "ib": self._ib, "elec_pos": self.elec_pos, "wz_speed": self.speed_hz / pole_pairs,
                # the loop references and the measured Iq - what the Frequency Response page maps for a sweep
                "ref_speed_hz": self.esc_target_hz, "iq": self._iq,
                "ref_current": (self._test_ref if test_key in ("testing_field_oriented_control_foc", "testing_iq_current_control")
                                else self._foc_current if self._foc_current is not None else 0.0),
                "ref_wz_count": (int(ref_wz) + 2**31) % 2**32 - 2**31}

    def _value(self, reg, live):
        value = DEVICE_INFO.get(reg.key, live.get(defs.TELEMETRY_KEYS.get(reg.key), 0))
        return float(value) if P.is_float(reg.dtype) else int(value)

    def _publish(self):
        """-> how many telemetry messages are mapped (and were sent)."""
        live = self._live()
        mapped = 0
        for message, cob in enumerate(defs.TPDO_COBS):
            data, offset, used = bytearray(8), 0, False
            for slot in range(1, 5):
                code = self.params.get((0x1A00 + message, slot), 0)
                if not code:
                    continue
                reg = self._catalog.get(defs.parse_mapping_code(code))
                if reg is None or offset + P.type_size(reg.dtype) > 8:
                    break
                raw = P.encode_value(reg.dtype, self._value(reg, live))
                data[offset:offset + len(raw)] = raw
                offset += len(raw)
                used = True
            if used:
                mapped += 1
                self._send(cob, bytes(data))
        return mapped

    def _send(self, cob, data):
        self.bus.send(can.Message(arbitration_id=P.can_id(cob, self.node), data=data, is_extended_id=False))

    # ---- protocol --------------------------------------------------------------------------
    def _handle(self, msg):
        if msg.is_extended_id or len(msg.data) < 4:
            return
        cob, node = P.split_can_id(msg.arbitration_id)
        if node != self.node:
            return
        self._last_rx = time.monotonic()
        data = bytes(msg.data).ljust(8, b"\0")
        if cob == P.COB_REQUEST:
            self._sdo(data)
        elif (cob, data[0]) in self._commands:
            self._command(self._commands[(cob, data[0])], data)

    def _start_exclusive_mode(self):
        """Clears the state of every OTHER mutually-exclusive continuous drive mode (speed, position hold/move,
        current, torque) - a new one always fully takes over, like on the real drive, whatever order commands arrive
        in. Each branch below sets its own state on top of this."""
        self.esc_target_hz, self.off = 0.0, False
        self._test = None                        # a step-response test is one of these modes too: a new speed/position/
                                                  # current command must end it, or _step keeps replaying its square wave
        self._servo_target = self._foc_current = self._foc_torque = None
        self._direct_field_mode = None
        self._virtual_brake = False
        self._kickstart_mode = False
        self._power_generation = False
        self._brushed_mode = False

    def _command(self, cmd, data):
        values = tuple(P.decode_value(p.dtype, data[p.offset:p.offset + P.type_size(p.dtype)]) for p in cmd.params)
        if self.faults and cmd.key != "reset":                   # a latched fault: everything but Reset is ignored
            self.ignored_commands.append((cmd.key, values))
            return
        self.commands_received.append((cmd.key, values))
        if cmd.key == "reset" and self._autoconfig:
            self._autoconfig = None                              # only Reset ends the procedure (the user reports that Stop does not)
            self.autoconfig_aborted += 1
        if cmd.key in ("stop", "off", "reset"):
            self._test = None                                    # a step-response test is an ordinary mode: Stop ends it like ESC
            self._servo_target = None                            # ...and so does a position hold/move (Enable, Move To/By)
            self._foc_current = self._foc_torque = None          # ...and so does a commanded current/torque
            self._direct_field_mode = None                       # ...and so does an open-loop Direct Field Control command
            self._virtual_brake = False                          # ...and so does Virtual Brake
            self._kickstart_mode = False                         # ...and so does Kickstart
            self._power_generation = False                       # ...and so does Power Generation
            self._brushed_mode = False                           # ...and so does Brushed Open Loop Control
        if cmd.key in AUTOCONFIG:
            if not self._autoconfig:                             # a repeated command does not start it again
                self._autoconfig = {"until": time.monotonic() + self.autoconfig_seconds, "values": values, "key": cmd.key}
                self.autoconfig_runs += 1
        elif cmd.key in TEST_MODES:
            period, amplitude, wave_form = values                # (Period, Amplitude, Wave Form), in the order commands.json lists them
            signature = (round(period, 6), round(amplitude, 6), wave_form)
            if self._test is None or self._test["key"] != cmd.key or self._test["signature"] != signature:
                self._start_exclusive_mode()                     # a new test takes over from a speed/position/current
                                                                  # command exactly like any other mode does
                self._test = {"key": cmd.key, "period": max(period, 0.005), "amplitude": amplitude, "signature": signature,
                              "wave_form": int(wave_form), "phase_start": time.monotonic()}
        elif cmd.key == "kickstart":
            self._start_exclusive_mode()
            self._kickstart_mode = True
            self.esc_target_hz = values[0]           # same tau dynamics as ESC: a different STARTING technique on
                                                       # the real drive, not a different final behaviour to model
        elif cmd.key == "power_generation":
            self._start_exclusive_mode()
            self._power_generation = True             # the target Voltage has no effect: no generator/regen model
                                                       # exists (nothing external spins the shaft in this simulator)
        elif cmd.key == "brushed_open_loop_control_1_2_motors":
            self._start_exclusive_mode()
            self._brushed_mode = True                 # both channel voltages have no effect: no brushed-motor
                                                       # model exists (the existing _ia/_ib signature is specific
                                                       # to the 3-phase BLDC/PMSM representation via elec_pos)
        elif cmd.key == "esc_hz":
            self._start_exclusive_mode()
            self.esc_target_hz = values[0]
        elif cmd.key == "esc_rpm":
            self._start_exclusive_mode()
            self.esc_target_hz = values[0] * ((self.params[(0x2000, 0x03)] or 2) / 2) / 60.0
        elif cmd.key == "servo":
            self._start_exclusive_mode()
            self._servo_target = float(values[0])
        elif cmd.key == "servo_linear_interpolation":
            self._start_exclusive_mode()
            self._servo_target = float(values[0])          # feed rate has no effect: no mechanical model to feed towards
        elif cmd.key == "servo_modulo":
            self._start_exclusive_mode()
            self._servo_target = float(values[0])          # direction has no effect: only the route differs, not the rest point
        elif cmd.key == "servo_turns_and_modulo":
            self._start_exclusive_mode()
            self._servo_target = float(values[0])          # turns has no effect: no counts-per-revolution to combine it with
        elif cmd.key == "direct_field_control_rotation":
            self._start_exclusive_mode()
            self._direct_field_mode = "rotation"
            self.esc_target_hz = values[1]                  # voltage has no effect: no electrical/thermal model to apply it to;
                                                              # reuses the same tau-based dynamics ESC uses - "open vs closed loop"
                                                              # is not something a no-mechanical-model simulator can distinguish
        elif cmd.key == "direct_field_control_electrical_position":
            self._start_exclusive_mode()
            self._direct_field_mode = "position"
            self.elec_pos = values[1] % (2 * math.pi)        # held static, like a servo target but for the electrical angle
            self.esc_target_hz = 0.0
        elif cmd.key == "virtual_brake":
            self._start_exclusive_mode()
            self._virtual_brake = True               # no external-force model: holding still is trivially satisfied
        elif cmd.key == "current_control_field_oriented_control_foc":
            self._start_exclusive_mode()
            self._foc_current = float(values[0])
        elif cmd.key == "electronic_torque_control_etc":
            self._start_exclusive_mode()
            self._foc_torque = float(values[0])
        elif cmd.key == "stop":
            self.esc_target_hz = 0.0
        elif cmd.key == "off":
            self.esc_target_hz, self.off = 0.0, True
        elif cmd.key == "reset":
            self.esc_target_hz, self.off, self.fault_bits, self.wz_count = 0.0, False, 0, 0.0
        elif cmd.key == "reset_work_zone":
            self.wz_count = 0.0
        elif cmd.key == "electromagnetic_brake":
            self.brake_engaged = bool(values[0])          # discrete: no Operation Mode of its own, independent
                                                            # of whatever continuous command is driving the motor
        elif cmd.key == "gpio_generic_output":
            self.gpio_output = bool(values[0])             # discrete, same as above
        elif cmd.key == "gpio_pwm_output":
            self.gpio_pwm_duty = values[0]                 # discrete, same as above

    def _autoconfig_step(self, now):
        run = self._autoconfig
        if run is None:
            return
        if now - self._last_rx > self.params[(0x3000, 0x04)]:    # nothing heard from the host: the drive stops the procedure
            self._autoconfig = None
            self.autoconfig_aborted += 1
        elif now >= run["until"]:
            values = run["values"]                               # (max current, poles[, general system dynamics])
            self.params[(0x2000, 0x02)], self.params[(0x2000, 0x03)] = float(values[0]), int(values[1])
            self.params.update(MEASURED)
            scale = float(values[2]) / 3.16 if len(values) > 2 else 1.0
            self.params.update({key: value * scale for key, value in GAINS.items()})
            self.flash_saved = True                              # the procedure saves everything to flash by itself
            self._autoconfig = None

    def reboot(self):
        """A power cycle: RAM goes back to what the flash holds (the boot configuration, or the last 'save'), and the
        timer interrupt takes the receiving frequency that is now in RAM - a write never saved is lost, as measured
        on the real drive."""
        self.params = dict(self.flash)
        self.timer_isr_hz = float(self.params[(0x3000, 0x06)])

    def _sdo(self, data):
        code, index, sub = data[0], data[1] | data[2] << 8, data[3]
        key = (index, sub)
        head = bytes([index & 0xFF, index >> 8, sub])
        if code == 0x23 and key == (0x1010, 0x01) and data[4:8] == b"save":     # the standard CANopen store request
            if self.flash_mode == "ack":                                          # (what the vendor IDE's Save to Flash sends)
                self.flash, self.flash_saves = dict(self.params), self.flash_saves + 1
                self._send(P.COB_RESPONSE, bytes([P.RESPONSE_ACK]) + head + b"save")
            elif self.flash_mode == "abort":
                self._abort(head, ABORT_NO_OBJECT)
            return
        if code == P.CMD_READ:
            if key == (0x5000, 0xA9):                                             # Timer ISR Frequency: fixed at boot
                self._respond(head, "FLOAT32", self.timer_isr_hz)
                return
            if key in self.params:
                self._respond(head, self.dtypes[key], self.params[key])
            elif key in self._catalog:
                reg = self._catalog[key]
                if defs.TELEMETRY_KEYS.get(key) in self.sdo_unreadable:
                    self._abort(head, 0)
                else:
                    self._respond(head, reg.dtype, self._value(reg, self._live()))
            else:
                self._abort(head, ABORT_NO_OBJECT)
        elif code == P.CMD_WRITE:
            if key in self.params:
                self.params[key] = P.decode_value(self.dtypes[key], data[4:8])
                if self.ack_writes:
                    self._send(P.COB_RESPONSE, bytes([P.RESPONSE_ACK]) + head + data[4:8])     # echoes the value, like the real drive
            else:
                self._abort(head, ABORT_READ_ONLY if key in self._catalog else ABORT_NO_OBJECT)

    def _respond(self, head, dtype, value):
        raw = P.encode_value(dtype, value)
        size_code = {1: 0x4F, 2: 0x4B, 3: 0x47, 4: 0x43}[len(raw)]
        self._send(P.COB_RESPONSE, bytes([size_code]) + head + raw.ljust(4, b"\0"))

    def _abort(self, head, abort_code):
        self._send(P.COB_RESPONSE, bytes([P.RESPONSE_ERROR]) + head + struct.pack("<I", abort_code))
