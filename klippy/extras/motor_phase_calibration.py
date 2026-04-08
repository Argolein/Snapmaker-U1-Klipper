# Motor phase calibration and compensation for TMC2240 / Snapmaker U1
#
# Copyright (C) 2026  Snapmaker U1 Klipper contributors
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, time, logging, pathlib, json, struct
from . import bus

VALID_STEP_DIRS = {'forward': 1, 'backward': -1}

class MotorPhaseExec:
    def __init__(self, spi):
        self._spi = spi
        self._mcu = spi.get_mcu()
        self._oid = self._mcu.create_oid()
        
        # Register config command (Must match command.c)
        self._mcu.add_config_cmd(
            "config_mpe oid=%d spi_oid=%d"
            % (self._oid, spi.get_oid()))
        
        # Commands are looked up lazily to avoid startup crashes
        self._start_cmd = None
        self._stop_cmd = None
        self._update_table_cmd = None
        self._clear_cmd = None
        self._query_cmd = None

    def _get_cmds(self):
        if self._start_cmd is None:
            self._start_cmd = self._mcu.lookup_command(
                "mpe_start oid=%c interval=%u phase_index=%u"
                " breakaway_scale=%c cruise_scale=%c breakaway_events=%hu"
                " start_clock=%u phase_advance=%hi phase_offset=%u"
                " map_flags=%c")
            self._stop_cmd = self._mcu.lookup_command(
                "mpe_stop oid=%c")
            self._update_table_cmd = self._mcu.lookup_command(
                "mpe_update oid=%c offset=%u data=%*s table=%c")
            self._clear_cmd = self._mcu.lookup_command(
                "mpe_clear oid=%c")
            self._query_cmd = self._mcu.lookup_query_command(
                "mpe_query oid=%c",
                "mpe_state oid=%c state=%c phase_index=%u"
                " coil_scale=%c interval=%u depth=%c max_depth=%c"
                " overflow_count=%u event_count=%u transfer_count=%u"
                " last_phase_sent=%u last_scale_sent=%c",
                oid=self._oid)

    def update_table(self, offset, data, table_id):
        self._get_cmds()
        self._update_table_cmd.send([self._oid, offset, data, table_id])

    def clear(self):
        self._get_cmds()
        self._clear_cmd.send([self._oid])

    def start(self, interval, phase_index, breakaway_scale, cruise_scale,
              breakaway_events, start_clock,
              phase_advance,
              phase_offset=0, map_flags=0):
        self._get_cmds()
        self._start_cmd.send(
            [self._oid, interval, phase_index, breakaway_scale,
             cruise_scale, breakaway_events, start_clock, phase_advance,
             phase_offset, map_flags],
            reqclock=start_clock)

    def stop(self):
        self._get_cmds()
        self._stop_cmd.send([self._oid])

    def query(self):
        self._get_cmds()
        return self._query_cmd.send([self._oid])

class MotorPhaseCalibration:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        logging.info("Motor Phase Calibration Phase 4 - ACTIVE (Build 2026-04-07-FINAL-FIX)")
        
        self.default_speed = config.getfloat('speed', 40.0, above=0.)
        self.default_distance = config.getfloat('distance', 40.0, above=0.)
        self.default_accel = config.getfloat('accel', 0., minval=0.)
        self.default_settle_time = config.getfloat('settle_time', 0.2, minval=0.)
        self.output_dir = config.get('output_dir', '/userdata/gcodes/motor_phase_data')
        self.accel_chip_name = config.get('accel_chip')
        self.exec_irun_pct = config.getint('exec_irun_pct', 50, minval=10,
                                           maxval=100)
        
        exec_stepper = config.get('exec_stepper', None)
        self._exec_channels = {}
        if exec_stepper is not None:
            spi_mode = config.getint("exec_spi_mode", 3)
            spi_speed = config.getint("exec_spi_speed", 4000000)
            self._register_exec_channel(
                exec_stepper, config.get("exec_spi_bus"),
                config.get("exec_cs_pin"), spi_mode, spi_speed)
            partner_stepper = config.get('exec_partner_stepper', None)
            partner_spi_bus = config.get('exec_partner_spi_bus', None)
            partner_cs_pin = config.get('exec_partner_cs_pin', None)
            if partner_stepper is not None:
                if partner_spi_bus is None or partner_cs_pin is None:
                    raise config.error(
                        "exec_partner_stepper requires exec_partner_spi_bus"
                        " and exec_partner_cs_pin")
                self._register_exec_channel(
                    partner_stepper, partner_spi_bus, partner_cs_pin,
                    spi_mode, spi_speed,
                    phase_offset=config.getint("exec_partner_phase_offset", 0),
                    map_flags=self._build_map_flags(
                        config.getint("exec_partner_swap_coils", 0,
                                      minval=0, maxval=1),
                        config.getint("exec_partner_invert_a", 0,
                                      minval=0, maxval=1),
                        config.getint("exec_partner_invert_b", 0,
                                      minval=0, maxval=1)))
            self.gcode.register_command(
                "MOTOR_PHASE_EXEC_RUN", self.cmd_MOTOR_PHASE_EXEC_RUN,
                desc="Run high-rate MCU motor phase executor")
            self.gcode.register_command(
                "MOTOR_PHASE_LOAD_PAYLOAD", self.cmd_MOTOR_PHASE_LOAD_PAYLOAD,
                desc="Load correction profile")
        
        self.loaded_payloads = {}
        self.gcode.register_command(
            "MOTOR_PHASE_MEASURE", self.cmd_MOTOR_PHASE_MEASURE,
            desc="Measure motor phase vibrations")
        if exec_stepper is not None:
            self.gcode.register_command(
                "MOTOR_PHASE_AUTO_CALIBRATE",
                self.cmd_MOTOR_PHASE_AUTO_CALIBRATE,
                desc="Auto-find partner phase offset via accelerometer sweep")
            self.gcode.register_command(
                "MOTOR_PHASE_DIRECTION_PROBE",
                self.cmd_MOTOR_PHASE_DIRECTION_PROBE,
                desc="Single-motor direction test with partner at zero current")

    def _get_output_dir(self):
        return pathlib.Path(self.output_dir).expanduser()

    def _lookup_accel_chip(self):
        return self.printer.lookup_object(self.accel_chip_name)

    def _get_axis_limits(self):
        toolhead = self.printer.lookup_object("toolhead")
        status = toolhead.get_status(self.printer.get_reactor().monotonic())
        return status["axis_minimum"], status["axis_maximum"]

    def _stage_xy_position(self, x, y, speed):
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.manual_move([x, y], speed)
        toolhead.wait_moves()

    def _lookup_stepper(self, stepper_name):
        for s in self.printer.lookup_object('force_move').steppers.values():
            if s.get_name() == stepper_name:
                return s
        raise self.printer.command_error("Unknown stepper %s" % (stepper_name,))

    def _lookup_stepper_driver(self, stepper_name):
        return self.printer.lookup_object("tmc2240 " + stepper_name)

    def _build_map_flags(self, swap_coils, invert_a, invert_b):
        map_flags = 0
        if swap_coils:
            map_flags |= 1
        if invert_a:
            map_flags |= 2
        if invert_b:
            map_flags |= 4
        return map_flags

    def _register_exec_channel(self, stepper_name, spi_bus, cs_pin, spi_mode,
                               spi_speed, phase_offset=0, map_flags=0):
        main_mcu = self.printer.lookup_object("mcu")
        mcu_spi = bus.MCU_SPI(main_mcu, spi_bus, cs_pin, spi_mode, spi_speed)
        self._exec_channels[stepper_name] = {
            "stepper_name": stepper_name,
            "exec": MotorPhaseExec(mcu_spi),
            "phase_offset": phase_offset & 1023,
            "map_flags": map_flags,
        }

    def _lookup_exec_channel(self, stepper_name):
        channel = self._exec_channels.get(stepper_name)
        if channel is None:
            raise self.printer.command_error(
                "No executor configured for %s" % (stepper_name,))
        return channel

    def _lookup_partner_channel(self, stepper_name):
        others = [c for name, c in self._exec_channels.items()
                  if name != stepper_name]
        if len(others) > 1:
            raise self.printer.command_error(
                "Ambiguous executor partner for %s" % (stepper_name,))
        return others[0] if others else None

    def _executor_currents(self, phase_index, coil_scale, phase_offset=0,
                           map_flags=0):
        phase = (phase_index + phase_offset) & 1023
        rad = 2.0 * math.pi * phase / 1024.0
        ca = int(round(coil_scale * math.sin(rad)))
        cb = int(round(coil_scale * math.cos(rad)))
        if map_flags & 1:
            ca, cb = cb, ca
        if map_flags & 2:
            ca = -ca
        if map_flags & 4:
            cb = -cb
        # TMC xdirect mode uses swapped coil ordering.
        ca, cb = cb, ca
        return max(-255, min(255, ca)), max(-255, min(255, cb))

    def _map_physical_currents(self, cur_a, cur_b, map_flags=0):
        if map_flags & 1:
            cur_a, cur_b = cur_b, cur_a
        if map_flags & 2:
            cur_a = -cur_a
        if map_flags & 4:
            cur_b = -cur_b
        return cur_a, cur_b

    def _pack_xdirect_currents(self, cur_a, cur_b):
        # TMC xdirect mode uses swapped coil ordering.
        return max(-255, min(255, cur_b)), max(-255, min(255, cur_a))

    def _read_mscuract(self, driver):
        raw = driver.mcu_tmc.get_register("MSCURACT")
        cur_a = driver.fields.get_field("cur_a", raw, "MSCURACT")
        cur_b = driver.fields.get_field("cur_b", raw, "MSCURACT")
        return cur_a, cur_b

    def _phase_from_currents(self, cur_a, cur_b):
        if not cur_a and not cur_b:
            return None
        phase = int(round(math.atan2(cur_a, cur_b) * 1024.0
                          / (2.0 * math.pi)))
        return phase & 1023

    def _prepare_executor_driver(self, driver, toolhead, coil_scale,
                                 phase_offset=0, map_flags=0,
                                 exec_irun_pct=50):
        print_time = toolhead.get_last_move_time()
        self._set_tmc_field(driver, "intpol", 0, print_time)
        self._set_tmc_field(driver, "mres", 0, print_time) 
        toolhead.dwell(0.05)
        toolhead.wait_moves()
        current_phase = driver.mcu_tmc.get_register("MSCNT") & 0x3ff
        cur_a, cur_b = self._read_mscuract(driver)
        saved_ihold_irun = driver.mcu_tmc.get_register("IHOLD_IRUN")
        irun = driver.fields.get_field("irun", saved_ihold_irun, "IHOLD_IRUN")
        current_vector_phase = self._phase_from_currents(cur_a, cur_b)
        auto_phase_offset = 0
        if current_vector_phase is not None:
            auto_phase_offset = (current_vector_phase - current_phase) & 1023
        effective_phase_offset = (phase_offset + auto_phase_offset) & 1023
        print_time = toolhead.get_last_move_time()
        direct_irun = max(4, min(31, int(round(irun * exec_irun_pct / 100.0))))
        ihold_irun = driver.fields.set_field(
            "ihold", direct_irun, saved_ihold_irun, "IHOLD_IRUN")
        ihold_irun = driver.fields.set_field(
            "irun", direct_irun, ihold_irun, "IHOLD_IRUN")
        driver.mcu_tmc.set_register("IHOLD_IRUN", ihold_irun, print_time)
        cur_a, cur_b = self._map_physical_currents(cur_a, cur_b, map_flags)
        ca, cb = self._pack_xdirect_currents(cur_a, cur_b)
        self._set_tmc_field(driver, "coil_a", ca, print_time)
        self._set_tmc_field(driver, "coil_b", cb, print_time)
        self._set_tmc_field(driver, "direct_mode", 1, print_time)
        toolhead.dwell(0.05)
        toolhead.wait_moves()
        return (current_phase, saved_ihold_irun, effective_phase_offset,
                auto_phase_offset, current_vector_phase, cur_a, cur_b,
                direct_irun)

    def _prime_executor_start_group(self, runtime_channels, toolhead,
                                    coil_scale):
        # Prime all active CoreXY motors in lockstep and in the intended
        # rotation direction so the carriage sees a coherent launch torque.
        for delta in (32, 64, 96, 128, 160, 192, 224, 256):
            prime_scale = max(12, int(round(coil_scale * delta / 256.0)))
            print_time = toolhead.get_last_move_time()
            for runtime in runtime_channels:
                lead_sign = 1 if runtime["phase_advance"] >= 0 else -1
                launch_phase = (runtime["start_phase"]
                                + lead_sign * delta) & 1023
                ca, cb = self._executor_currents(
                    launch_phase, prime_scale,
                    runtime["effective_phase_offset"],
                    runtime["map_flags"])
                self._set_tmc_field(runtime["driver"], "coil_a", ca,
                                    print_time)
                self._set_tmc_field(runtime["driver"], "coil_b", cb,
                                    print_time)
                runtime["launch_phase"] = launch_phase
            toolhead.dwell(0.012)
            toolhead.wait_moves()

    def _restore_executor_driver(self, driver, toolhead, saved_ihold_irun):
        print_time = toolhead.get_last_move_time()
        self._set_tmc_field(driver, "coil_a", 0, print_time)
        self._set_tmc_field(driver, "coil_b", 0, print_time)
        toolhead.dwell(0.05)
        toolhead.wait_moves()
        print_time = toolhead.get_last_move_time()
        self._set_tmc_field(driver, "direct_mode", 0, print_time)
        toolhead.dwell(0.05)
        toolhead.wait_moves()
        print_time = toolhead.get_last_move_time()
        driver.mcu_tmc.set_register("IHOLD_IRUN", saved_ihold_irun, print_time)
        self._set_tmc_field(driver, "intpol", 1, print_time) 
        self._set_tmc_field(driver, "mres", 2, print_time)
        toolhead.dwell(0.05)
        toolhead.wait_moves()

    def _enter_xdirect_hold_zero(self, driver, toolhead, exec_irun_pct):
        """Enter xDirect mode with zero current. Partner stays passive."""
        print_time = toolhead.get_last_move_time()
        self._set_tmc_field(driver, "intpol", 0, print_time)
        self._set_tmc_field(driver, "mres", 0, print_time)
        toolhead.dwell(0.05)
        toolhead.wait_moves()
        saved_ihold_irun = driver.mcu_tmc.get_register("IHOLD_IRUN")
        irun = driver.fields.get_field("irun", saved_ihold_irun, "IHOLD_IRUN")
        direct_irun = max(4, min(31, int(round(irun * exec_irun_pct / 100.0))))
        ihold_irun = driver.fields.set_field(
            "ihold", direct_irun, saved_ihold_irun, "IHOLD_IRUN")
        ihold_irun = driver.fields.set_field(
            "irun", direct_irun, ihold_irun, "IHOLD_IRUN")
        print_time = toolhead.get_last_move_time()
        driver.mcu_tmc.set_register("IHOLD_IRUN", ihold_irun, print_time)
        self._set_tmc_field(driver, "coil_a", 0, print_time)
        self._set_tmc_field(driver, "coil_b", 0, print_time)
        self._set_tmc_field(driver, "direct_mode", 1, print_time)
        toolhead.dwell(0.05)
        toolhead.wait_moves()
        return saved_ihold_irun

    def _wait_executor_idle(self, runtime_channels, timeout=2.5):
        reactor = self.printer.get_reactor()
        deadline = reactor.monotonic() + timeout
        while True:
            all_idle = True
            states = []
            for runtime in runtime_channels:
                params = runtime["channel"]["exec"].query()
                state = params["state"]
                states.append("%s=%d/%d depth=%d max=%d ovf=%d ev=%d tx=%d" % (
                    runtime["channel"]["stepper_name"],
                    state, params["coil_scale"], params["depth"],
                    params["max_depth"], params["overflow_count"],
                    params["event_count"], params["transfer_count"]))
                if state != 0:
                    all_idle = False
            if all_idle:
                self.gcode.respond_info(
                    "motor_phase_exec_run: decel_complete %s"
                    % (" ".join(states),))
                return
            now = reactor.monotonic()
            if now >= deadline:
                raise self.printer.command_error(
                    "Executor decel timeout before direct-mode restore (%s)"
                    % (" ".join(states),))
            reactor.pause(now + 0.02)

    def _log_runtime_states(self, runtime_channels, prefix):
        state_parts = []
        for runtime in runtime_channels:
            params = runtime["channel"]["exec"].query()
            state_parts.append(
                ("%s=state:%d phase:%d scale:%d interval:%d"
                 " depth:%d max:%d ovf:%d ev:%d tx:%d last_phase:%d"
                 " last_scale:%d") % (
                    runtime["channel"]["stepper_name"], params["state"],
                    params["phase_index"], params["coil_scale"],
                    params["interval"], params["depth"],
                    params["max_depth"], params["overflow_count"],
                    params["event_count"], params["transfer_count"],
                    params["last_phase_sent"], params["last_scale_sent"]))
        self.gcode.respond_info(
            "motor_phase_exec_run: %s %s"
            % (prefix, " ".join(state_parts)))

    def _monitor_executor_run(self, runtime_channels, duration,
                              poll_interval=1.0):
        reactor = self.printer.get_reactor()
        deadline = reactor.monotonic() + duration
        index = 0
        while True:
            now = reactor.monotonic()
            remaining = deadline - now
            if remaining <= 0.:
                break
            reactor.pause(now + min(poll_interval, remaining))
            index += 1
            self._log_runtime_states(runtime_channels, "run[%d]" % (index,))

    def _set_tmc_field(self, driver, field_name, value, print_time):
        reg_name = driver.fields.lookup_register(field_name, None)
        reg_val = driver.fields.set_field(field_name, value)
        driver.mcu_tmc.set_register(reg_name, reg_val, print_time)

    def _upload_correction_table(self, exec_handle, harmonics):
        exec_handle.clear()
        if not harmonics:
            return
        table_a = [0] * 1024
        table_b = [0] * 1024
        for h, amp, phase_deg in harmonics:
            rad_offset = math.radians(phase_deg)
            for i in range(1024):
                angle = 2.0 * math.pi * h * i / 1024.0
                table_a[i] += int(amp * math.sin(angle + rad_offset))
                table_b[i] += int(amp * math.cos(angle + rad_offset))
        for table_id in [0, 1]:
            table = table_a if table_id == 0 else table_b
            for offset in range(0, 1024, 32):
                chunk = table[offset:offset+32]
                data = b"".join([struct.pack("<h", x) for x in chunk])
                exec_handle.update_table(offset, data, table_id)

    def _lookup_payload_harmonics(self, profile_name, direction):
        if not profile_name:
            return None
        payload = self.loaded_payloads.get(profile_name)
        if payload is None:
            raise self.printer.command_error(
                "Unknown profile %s" % (profile_name,))
        return payload.get("directions", {}).get(direction, {}).get(
            "prototype_direct_profile")

    def _corexy_phase_sign(self, stepper_name, carriage_axis):
        if carriage_axis == "x":
            return 1
        if carriage_axis != "y":
            raise self.printer.command_error(
                "Unsupported carriage axis %s" % (carriage_axis,))
        if stepper_name == "stepper_x":
            return 1
        if stepper_name == "stepper_y":
            return -1
        raise self.printer.command_error(
            "CoreXY executor only supports stepper_x/stepper_y")

    def _motor_rotation_sign(self, stepper_name):
        stepper = self._lookup_stepper(stepper_name)
        return -1 if stepper.get_dir_inverted()[0] else 1

    def cmd_MOTOR_PHASE_EXEC_RUN(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        direction = gcmd.get("DIRECTION", "forward").lower()
        if direction not in VALID_STEP_DIRS:
            raise gcmd.error("Invalid DIRECTION=%s" % (direction,))
        carriage_axis = gcmd.get("CARRIAGE_AXIS", "y").lower()
        speed = gcmd.get_float("SPEED", 30.0, above=0.)
        distance = gcmd.get_float("DISTANCE", 40.0, above=0.)
        coil_scale = gcmd.get_int("COIL_SCALE", 80, minval=1, maxval=255)
        prime_coil_scale = gcmd.get_int(
            "PRIME_COIL_SCALE", coil_scale, minval=1, maxval=255)
        breakaway_coil_scale = gcmd.get_int(
            "BREAKAWAY_COIL_SCALE", prime_coil_scale, minval=1, maxval=255)
        breakaway_ms = gcmd.get_int(
            "BREAKAWAY_MS", 0, minval=0, maxval=5000)
        stride = gcmd.get_int("PHASE_STRIDE", 4, minval=1)
        write_csv = gcmd.get_int("WRITE_CSV", 0, minval=0, maxval=1)
        profile_name = gcmd.get("PROFILE", None)
        phase_offset = gcmd.get_int("PHASE_OFFSET", 0) & 1023
        swap_coils = gcmd.get_int("SWAP_COILS", 0, minval=0, maxval=1)
        invert_a = gcmd.get_int("INVERT_A", 0, minval=0, maxval=1)
        invert_b = gcmd.get_int("INVERT_B", 0, minval=0, maxval=1)
        map_flags = self._build_map_flags(swap_coils, invert_a, invert_b)
        exec_irun_pct = gcmd.get_int("EXEC_IRUN_PCT", self.exec_irun_pct,
                                     minval=10, maxval=100)
        partner_phase_offset_override = gcmd.get("PARTNER_PHASE_OFFSET", None)
        partner_swap_coils = gcmd.get("PARTNER_SWAP_COILS", None)
        partner_invert_a = gcmd.get("PARTNER_INVERT_A", None)
        partner_invert_b = gcmd.get("PARTNER_INVERT_B", None)
        harmonics = self._lookup_payload_harmonics(profile_name, direction)

        toolhead = self.printer.lookup_object("toolhead")
        limits_min, limits_max = self._get_axis_limits()
        self._stage_xy_position(0.5*(limits_min.x+limits_max.x), 
                                0.5*(limits_min.y+limits_max.y), 150.0)

        primary_channel = self._lookup_exec_channel(stepper_name)
        partner_channel = self._lookup_partner_channel(stepper_name)
        active_channels = [primary_channel]
        if partner_channel is not None:
            active_channels.append(partner_channel)

        if harmonics:
            self.gcode.respond_info(
                "Uploading correction table to %s..." % (stepper_name,))
            q14_harmonics = [[h, int(a * 10), p] for h, a, p in harmonics]
            self._upload_correction_table(primary_channel["exec"],
                                          q14_harmonics)
        else:
            primary_channel["exec"].clear()
        if partner_channel is not None:
            partner_channel["exec"].clear()

        mcu = self._lookup_stepper(stepper_name).get_mcu()
        freq = mcu.get_constant_float("CLOCK_FREQ")
        interval = int(((0.8 / 1024.0) * stride / speed) * freq + 0.5)
        breakaway_events = 0
        if breakaway_ms and breakaway_coil_scale > coil_scale:
            breakaway_events = max(
                1, int(round((breakaway_ms / 1000.0) * freq / interval)))
        duration = distance / speed
        direction_sign = VALID_STEP_DIRS[direction]

        aclient = None
        if write_csv:
            aclient = self._lookup_accel_chip().start_internal_client()

        runtime_channels = []
        for channel in active_channels:
            channel_phase_offset = channel["phase_offset"]
            channel_map_flags = channel["map_flags"]
            if channel["stepper_name"] == stepper_name:
                channel_phase_offset = (
                    channel_phase_offset + phase_offset) & 1023
                channel_map_flags ^= map_flags
            elif partner_channel is not None:
                if partner_phase_offset_override is not None:
                    channel_phase_offset = (
                        int(partner_phase_offset_override) & 1023)
                if (partner_swap_coils is not None
                        or partner_invert_a is not None
                        or partner_invert_b is not None):
                    channel_map_flags = self._build_map_flags(
                        int(partner_swap_coils or 0),
                        int(partner_invert_a or 0),
                        int(partner_invert_b or 0))
            driver = self._lookup_stepper_driver(channel["stepper_name"])
            (start_phase, saved_ihold_irun, effective_phase_offset,
             auto_phase_offset, current_vector_phase,
             current_cur_a, current_cur_b, direct_irun) = self._prepare_executor_driver(
                driver, toolhead, coil_scale, channel_phase_offset,
                channel_map_flags, exec_irun_pct)
            phase_advance = (
                self._corexy_phase_sign(channel["stepper_name"], carriage_axis)
                * self._motor_rotation_sign(channel["stepper_name"])
                * direction_sign * stride)
            runtime_channels.append({
                "channel": channel,
                "driver": driver,
                "start_phase": start_phase,
                "saved_ihold_irun": saved_ihold_irun,
                "effective_phase_offset": effective_phase_offset,
                "map_flags": channel_map_flags,
                "launch_phase": start_phase,
                "phase_advance": phase_advance,
            })
            self.gcode.respond_info(
                "motor_phase_exec_run: starting stepper=%s mscnt=%d"
                " mscuract_a=%d mscuract_b=%d current_vector_phase=%s"
                " auto_phase_offset=%d effective_phase_offset=%d"
                " phase_advance=%d direct_irun=%d map_flags=%d carriage_axis=%s direction=%s" % (
                    channel["stepper_name"], start_phase,
                    current_cur_a, current_cur_b,
                    "none" if current_vector_phase is None
                    else str(current_vector_phase),
                    auto_phase_offset, effective_phase_offset,
                    phase_advance, direct_irun, channel_map_flags,
                    carriage_axis, direction))
        self._prime_executor_start_group(runtime_channels, toolhead,
                                         prime_coil_scale)
        for runtime in runtime_channels:
            self.gcode.respond_info(
                "motor_phase_exec_run: primed stepper=%s launch_phase=%d"
                % (runtime["channel"]["stepper_name"],
                   runtime["launch_phase"]))
        eventtime = self.printer.get_reactor().monotonic()
        start_print_time = max(toolhead.get_last_move_time(),
                               mcu.estimated_print_time(eventtime)) + 0.250
        start_clock = int(mcu.print_time_to_clock(start_print_time))
        self.gcode.respond_info(
            "motor_phase_exec_run: synchronized_start"
            " start_print_time=%.6f start_clock=%d"
            % (start_print_time, start_clock))

        try:
            for runtime in runtime_channels:
                runtime["channel"]["exec"].start(
                    interval, runtime["launch_phase"], breakaway_coil_scale,
                    coil_scale, breakaway_events, start_clock,
                    runtime["phase_advance"],
                    runtime["effective_phase_offset"],
                    runtime["map_flags"])
            self._monitor_executor_run(runtime_channels, duration)
            for runtime in runtime_channels:
                runtime["channel"]["exec"].stop()
            self._wait_executor_idle(
                runtime_channels,
                timeout=max(2.5, duration + 0.5))
        finally:
            for runtime in runtime_channels:
                self._restore_executor_driver(
                    runtime["driver"], toolhead,
                    runtime["saved_ihold_irun"])
            if aclient:
                aclient.finish_measurements()

        if write_csv and aclient:
            samples = aclient.get_samples()
            if samples:
                output_dir = self._get_output_dir()
                output_dir.mkdir(parents=True, exist_ok=True)
                fname = output_dir / ("motor-phase-exec-%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
                aclient.write_to_file(str(fname))
                self.gcode.respond_info("Data written to %s" % fname)
        self.gcode.run_script_from_command("G28 X Y")

    def cmd_MOTOR_PHASE_LOAD_PAYLOAD(self, gcmd):
        name = gcmd.get("PROFILE")
        payload_path = pathlib.Path(gcmd.get("PAYLOAD")).expanduser()
        self.loaded_payloads[name] = json.loads(payload_path.read_text())
        self.gcode.respond_info("Profile %s loaded from %s" % (name, payload_path))

    def _score_accel_magnitude(self, samples):
        """Return total DC acceleration magnitude across both axes.

        Used for single-motor direction tests where the carriage moves
        diagonally rather than along a pure X or Y axis.
        """
        if not samples or len(samples) < 10:
            return 0.0
        n = len(samples)
        mean_x = sum(s.accel_x for s in samples) / n
        mean_y = sum(s.accel_y for s in samples) / n
        return math.sqrt(mean_x * mean_x + mean_y * mean_y)

    def _run_single_motor_burst(self, stepper_name, carriage_axis,
                                channel, partner_channel,
                                toolhead, mcu,
                                coil_scale, interval, duration, stride,
                                phase_advance_sign, exec_irun_pct):
        """Run primary motor executor only; partner held at zero current.

        The partner is put into xDirect mode at coil_a=0, coil_b=0 so it
        offers no resistive torque. The primary executor runs with the given
        phase_advance sign.

        Returns (score, samples) where score is the total DC magnitude.
        """
        driver = self._lookup_stepper_driver(stepper_name)
        (start_phase, saved_ihold_irun, effective_phase_offset,
         _ao, _vp, _ca, _cb, _direct_irun) = self._prepare_executor_driver(
            driver, toolhead, coil_scale, channel["phase_offset"],
            channel["map_flags"], exec_irun_pct)
        phase_advance = (
            self._corexy_phase_sign(stepper_name, carriage_axis)
            * self._motor_rotation_sign(stepper_name)
            * phase_advance_sign * stride)
        runtime = {
            "channel": channel,
            "driver": driver,
            "start_phase": start_phase,
            "saved_ihold_irun": saved_ihold_irun,
            "effective_phase_offset": effective_phase_offset,
            "map_flags": channel["map_flags"],
            "launch_phase": start_phase,
            "phase_advance": phase_advance,
        }
        # Partner: xDirect at zero current, no executor started
        partner_driver = self._lookup_stepper_driver(
            partner_channel["stepper_name"])
        partner_saved = self._enter_xdirect_hold_zero(
            partner_driver, toolhead, exec_irun_pct)
        self._prime_executor_start_group([runtime], toolhead, coil_scale)
        reactor = self.printer.get_reactor()
        eventtime = reactor.monotonic()
        start_print_time = max(toolhead.get_last_move_time(),
                               mcu.estimated_print_time(eventtime)) + 0.250
        start_clock = int(mcu.print_time_to_clock(start_print_time))
        aclient = self._lookup_accel_chip().start_internal_client()
        try:
            channel["exec"].start(
                interval, runtime["launch_phase"],
                coil_scale, coil_scale, 0,
                start_clock, phase_advance,
                effective_phase_offset, channel["map_flags"])
            # Pause must outlast the MCU start_clock.
            # start_print_time is pipeline_lag seconds ahead of wall time
            # (typically ~0.35s = 0.25 offset + ~0.1s Klipper buffer).
            # Compute remaining time until MCU fires, add burst duration + margin.
            now2 = reactor.monotonic()
            pipeline_lag = max(0., start_print_time
                               - mcu.estimated_print_time(now2))
            reactor.pause(now2 + pipeline_lag + duration + 0.050)
            channel["exec"].stop()
            self._wait_executor_idle([runtime],
                                     timeout=max(2.5, duration + 0.5))
        finally:
            self._restore_executor_driver(driver, toolhead, saved_ihold_irun)
            self._restore_executor_driver(
                partner_driver, toolhead, partner_saved)
            aclient.finish_measurements()
        samples = aclient.get_samples()
        score = self._score_accel_magnitude(samples)
        return score, samples

    def _score_accel_samples(self, samples, carriage_axis):
        """Return a motion score for accelerometer samples.

        Higher score = more directed linear acceleration in the target axis.
        Uses |mean| as primary signal (DC = net linear movement) and rewards
        higher total energy so "motor running but going nowhere" scores lower
        than "motor actually pushing the carriage".
        """
        if not samples or len(samples) < 10:
            return 0.0
        if carriage_axis == "x":
            values = [s.accel_x for s in samples]
        else:
            values = [s.accel_y for s in samples]
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std_dev = math.sqrt(variance) + 0.01
        rms = math.sqrt(sum(v ** 2 for v in values) / n)
        # |mean| = directed carriage acceleration
        # rms/std_dev ≈ SNR: rewards clean directional signal over pure noise
        return abs(mean) + 0.1 * rms / std_dev

    def _calibrate_single_run(self, stepper_name, carriage_axis,
                               primary_channel, partner_channel,
                               toolhead, mcu,
                               interval, breakaway_scale, coil_scale,
                               breakaway_events, duration, stride,
                               partner_phase_offset, partner_invert_b,
                               exec_irun_pct):
        """Run one calibration burst and return an accelerometer motion score."""
        active_channels = [primary_channel, partner_channel]
        direction_sign = 1
        runtime_channels = []
        for channel in active_channels:
            channel_phase_offset = channel["phase_offset"]
            channel_map_flags = channel["map_flags"]
            if channel["stepper_name"] != stepper_name:
                channel_phase_offset = partner_phase_offset & 1023
                channel_map_flags = self._build_map_flags(
                    0, 0, partner_invert_b)
            driver = self._lookup_stepper_driver(channel["stepper_name"])
            (start_phase, saved_ihold_irun, effective_phase_offset,
             _auto_offset, _vec_phase,
             _cur_a, _cur_b, direct_irun) = self._prepare_executor_driver(
                driver, toolhead, coil_scale, channel_phase_offset,
                channel_map_flags, exec_irun_pct)
            phase_advance = (
                self._corexy_phase_sign(channel["stepper_name"], carriage_axis)
                * self._motor_rotation_sign(channel["stepper_name"])
                * direction_sign * stride)
            runtime_channels.append({
                "channel": channel,
                "driver": driver,
                "start_phase": start_phase,
                "saved_ihold_irun": saved_ihold_irun,
                "effective_phase_offset": effective_phase_offset,
                "map_flags": channel_map_flags,
                "launch_phase": start_phase,
                "phase_advance": phase_advance,
            })
        self._prime_executor_start_group(runtime_channels, toolhead,
                                         breakaway_scale)
        reactor = self.printer.get_reactor()
        eventtime = reactor.monotonic()
        start_print_time = max(toolhead.get_last_move_time(),
                               mcu.estimated_print_time(eventtime)) + 0.250
        start_clock = int(mcu.print_time_to_clock(start_print_time))
        aclient = self._lookup_accel_chip().start_internal_client()
        try:
            for runtime in runtime_channels:
                runtime["channel"]["exec"].start(
                    interval, runtime["launch_phase"], breakaway_scale,
                    coil_scale, breakaway_events, start_clock,
                    runtime["phase_advance"],
                    runtime["effective_phase_offset"],
                    runtime["map_flags"])
            # Pause must outlast the MCU start_clock.
            # start_print_time is pipeline_lag seconds ahead of wall time
            # (typically ~0.35s = 0.25 offset + ~0.1s Klipper buffer).
            now2 = reactor.monotonic()
            pipeline_lag = max(0., start_print_time
                               - mcu.estimated_print_time(now2))
            reactor.pause(now2 + pipeline_lag + duration + 0.050)
            for runtime in runtime_channels:
                runtime["channel"]["exec"].stop()
            self._wait_executor_idle(
                runtime_channels, timeout=max(2.5, duration + 0.5))
        finally:
            for runtime in runtime_channels:
                self._restore_executor_driver(
                    runtime["driver"], toolhead, runtime["saved_ihold_irun"])
            aclient.finish_measurements()
        samples = aclient.get_samples()
        return self._score_accel_samples(samples, carriage_axis)

    def cmd_MOTOR_PHASE_DIRECTION_PROBE(self, gcmd):
        """Test which phase_advance sign produces carriage motion for one motor.

        Runs the primary executor with the partner held at zero current
        (no holding torque), so the carriage can move diagonally without
        fighting the partner belt path.  Both positive and negative
        phase_advance are tested.  Uses accelerometer total-magnitude scoring
        so diagonal motion is detected regardless of axis.

        Usage:
          MOTOR_PHASE_DIRECTION_PROBE STEPPER=stepper_y CARRIAGE_AXIS=y

        Options:
          COIL_SCALE=120      test current (default 120 for reliable signal)
          SPEED=2.0           executor rotation speed mm/s
          DURATION=0.25       burst duration in seconds
          EXEC_IRUN_PCT=      TMC current % of normal (default from config)
        """
        stepper_name = gcmd.get("STEPPER")
        carriage_axis = gcmd.get("CARRIAGE_AXIS", "y").lower()
        coil_scale = gcmd.get_int("COIL_SCALE", 120, minval=40, maxval=255)
        duration = gcmd.get_float("DURATION", 0.25, above=0.)
        speed = gcmd.get_float("SPEED", 2.0, above=0.)
        exec_irun_pct = gcmd.get_int("EXEC_IRUN_PCT", self.exec_irun_pct,
                                     minval=10, maxval=100)

        channel = self._lookup_exec_channel(stepper_name)
        partner_channel = self._lookup_partner_channel(stepper_name)
        if partner_channel is None:
            raise gcmd.error(
                "DIRECTION_PROBE requires exec_partner_stepper in config")

        toolhead = self.printer.lookup_object("toolhead")
        limits_min, limits_max = self._get_axis_limits()
        cx = 0.5 * (limits_min.x + limits_max.x)
        cy = 0.5 * (limits_min.y + limits_max.y)
        self._stage_xy_position(cx, cy, 150.0)
        mcu = self._lookup_stepper(stepper_name).get_mcu()
        freq = mcu.get_constant_float("CLOCK_FREQ")
        stride = 4
        interval = int(((0.8 / 1024.0) * stride / speed) * freq + 0.5)

        scores = {}
        for sign in [1, -1]:
            self.gcode.respond_info(
                "DIRECTION_PROBE: testing stepper=%s sign=%+d ..."
                % (stepper_name, sign))
            score, _ = self._run_single_motor_burst(
                stepper_name, carriage_axis, channel, partner_channel,
                toolhead, mcu, coil_scale, interval, duration, stride,
                sign, exec_irun_pct)
            scores[sign] = score
            self.gcode.respond_info(
                "DIRECTION_PROBE: stepper=%s sign=%+d score=%.3f"
                % (stepper_name, sign, score))
            self.gcode.run_script_from_command("G28 X Y")
            self._stage_xy_position(cx, cy, 150.0)

        best_sign = 1 if scores[1] >= scores[-1] else -1
        natural_sign = (
            self._corexy_phase_sign(stepper_name, carriage_axis)
            * self._motor_rotation_sign(stepper_name))
        consistent = (best_sign == natural_sign)
        self.gcode.respond_info(
            "DIRECTION_PROBE result: stepper=%s\n"
            "  score(sign=+1)=%.3f  score(sign=-1)=%.3f\n"
            "  best_sign=%+d  natural_sign(code)=%+d  consistent=%s\n"
            "  %s"
            % (stepper_name, scores[1], scores[-1],
               best_sign, natural_sign,
               "YES" if consistent else "NO",
               "Motor direction is as expected."
               if consistent
               else "MISMATCH — check dir_pin inversion or map_flags."))

    def cmd_MOTOR_PHASE_AUTO_CALIBRATE(self, gcmd):
        """Adaptive sweep to find the optimal PARTNER_PHASE_OFFSET.

        Three phases:
          1. Single-motor direction probe — verifies each motor moves at the
             chosen current before any partner scan begins.
          2. Coarse sweep — scans PARTNER_PHASE_OFFSET 0..1023 in PHASE_STEP
             increments × PARTNER_INVERT_B ∈ {0,1} with settle time and a
             periodic re-home to prevent carriage drift.
          3. Fine sweep — narrows down around the best coarse result using
             PHASE_STEP//4 step size (skipped if FINE_SCAN=0).

        Default parameters are tuned for reliable accelerometer detection:
          COIL_SCALE=100, SPEED=2.0, DURATION=0.20, PHASE_STEP=32.

        Usage (minimal):
          MOTOR_PHASE_AUTO_CALIBRATE STEPPER=stepper_y

        Full options:
          CARRIAGE_AXIS=y|x        target carriage axis  (default y)
          COIL_SCALE=100           steady-state current  (default 100)
          BREAKAWAY_COIL_SCALE=    launch current        (default COIL_SCALE)
          BREAKAWAY_MS=0           breakaway window ms   (default 0)
          SPEED=2.0                executor speed mm/s   (default 2.0)
          DURATION=0.20            burst duration s      (default 0.20)
          PHASE_STEP=32            step through 0..1023  (default 32 → 32 pts)
          SETTLE_TIME=0.5          pause between bursts  (default 0.5 s)
          REHOME_INTERVAL=8        re-home every N tests (default 8)
          FINE_SCAN=1              fine sweep after coarse (default 1)
          SKIP_DIRECTION_PROBE=0   skip phase 1 test     (default 0)
          EXEC_IRUN_PCT=           current % of normal   (default from config)
        """
        stepper_name = gcmd.get("STEPPER")
        carriage_axis = gcmd.get("CARRIAGE_AXIS", "y").lower()
        coil_scale = gcmd.get_int("COIL_SCALE", 100, minval=1, maxval=255)
        breakaway_scale = gcmd.get_int(
            "BREAKAWAY_COIL_SCALE", coil_scale, minval=1, maxval=255)
        breakaway_ms = gcmd.get_int("BREAKAWAY_MS", 0, minval=0, maxval=2000)
        test_speed = gcmd.get_float("SPEED", 2.0, above=0.)
        duration = gcmd.get_float("DURATION", 0.20, above=0.)
        phase_step = gcmd.get_int("PHASE_STEP", 32, minval=8, maxval=256)
        settle_time = gcmd.get_float("SETTLE_TIME", 0.5, minval=0.)
        rehome_interval = gcmd.get_int("REHOME_INTERVAL", 8, minval=1)
        fine_scan = gcmd.get_int("FINE_SCAN", 1, minval=0, maxval=1)
        skip_direction_probe = gcmd.get_int(
            "SKIP_DIRECTION_PROBE", 0, minval=0, maxval=1)
        exec_irun_pct = gcmd.get_int(
            "EXEC_IRUN_PCT", self.exec_irun_pct, minval=10, maxval=100)

        primary_channel = self._lookup_exec_channel(stepper_name)
        partner_channel = self._lookup_partner_channel(stepper_name)
        if partner_channel is None:
            raise gcmd.error(
                "AUTO_CALIBRATE requires exec_partner_stepper in config")

        toolhead = self.printer.lookup_object("toolhead")
        limits_min, limits_max = self._get_axis_limits()
        cx = 0.5 * (limits_min.x + limits_max.x)
        cy = 0.5 * (limits_min.y + limits_max.y)
        self._stage_xy_position(cx, cy, 150.0)
        mcu = self._lookup_stepper(stepper_name).get_mcu()
        freq = mcu.get_constant_float("CLOCK_FREQ")
        stride = 4
        interval = int(((0.8 / 1024.0) * stride / test_speed) * freq + 0.5)
        breakaway_events = 0
        if breakaway_ms and breakaway_scale > coil_scale:
            breakaway_events = max(
                1, int(round((breakaway_ms / 1000.0) * freq / interval)))
        reactor = self.printer.get_reactor()

        # Phase 1: single-motor direction probe
        if not skip_direction_probe:
            self.gcode.respond_info(
                "AUTO_CALIBRATE phase 1: single-motor direction probe ...")
            score_pos, _ = self._run_single_motor_burst(
                stepper_name, carriage_axis, primary_channel, partner_channel,
                toolhead, mcu, coil_scale, interval, duration, stride,
                1, exec_irun_pct)
            self.gcode.run_script_from_command("G28 X Y")
            self._stage_xy_position(cx, cy, 150.0)
            score_neg, _ = self._run_single_motor_burst(
                stepper_name, carriage_axis, primary_channel, partner_channel,
                toolhead, mcu, coil_scale, interval, duration, stride,
                -1, exec_irun_pct)
            self.gcode.run_script_from_command("G28 X Y")
            self._stage_xy_position(cx, cy, 150.0)
            natural_sign = (
                self._corexy_phase_sign(stepper_name, carriage_axis)
                * self._motor_rotation_sign(stepper_name))
            detected_sign = 1 if score_pos >= score_neg else -1
            self.gcode.respond_info(
                "AUTO_CALIBRATE dir_probe: score+1=%.3f score-1=%.3f"
                " natural_sign=%+d detected_sign=%+d consistent=%s"
                % (score_pos, score_neg, natural_sign, detected_sign,
                   "YES" if detected_sign == natural_sign else "NO"))
            if score_pos < 0.5 and score_neg < 0.5:
                self.gcode.respond_info(
                    "AUTO_CALIBRATE WARNING: single-motor scores are very low"
                    " (%.3f / %.3f). COIL_SCALE may be too low or the motor"
                    " is not engaging. Consider raising COIL_SCALE."
                    % (score_pos, score_neg))

        # Phase 2: coarse partner offset sweep
        n_phases = 1024 // phase_step
        n_total = n_phases * 2
        self.gcode.respond_info(
            "AUTO_CALIBRATE phase 2: coarse sweep %d tests "
            "(PHASE_STEP=%d COIL_SCALE=%d SPEED=%.1f DURATION=%.2fs"
            " SETTLE=%.1fs REHOME_EVERY=%d)"
            % (n_total, phase_step, coil_scale, test_speed, duration,
               settle_time, rehome_interval))

        best_score = -1.0
        best_offset = 0
        best_invert_b = 0
        coarse_results = []

        test_num = 0
        for invert_b in [0, 1]:
            for phase_offset in range(0, 1024, phase_step):
                test_num += 1
                if settle_time > 0 and test_num > 1:
                    reactor.pause(reactor.monotonic() + settle_time)
                try:
                    score = self._calibrate_single_run(
                        stepper_name, carriage_axis,
                        primary_channel, partner_channel,
                        toolhead, mcu,
                        interval, breakaway_scale, coil_scale,
                        breakaway_events, duration, stride,
                        phase_offset, invert_b, exec_irun_pct)
                except Exception as e:
                    self.gcode.respond_info(
                        "AUTO_CAL: [%d/%d] offset=%d invert_b=%d FAILED: %s"
                        % (test_num, n_total, phase_offset, invert_b, str(e)))
                    score = -1.0
                coarse_results.append({
                    "partner_phase_offset": phase_offset,
                    "partner_invert_b": invert_b,
                    "score": score,
                })
                self.gcode.respond_info(
                    "AUTO_CAL: [%d/%d] offset=%d invert_b=%d score=%.3f%s"
                    % (test_num, n_total, phase_offset, invert_b, score,
                       " ← best" if score > best_score else ""))
                if score > best_score:
                    best_score = score
                    best_offset = phase_offset
                    best_invert_b = invert_b
                if test_num % rehome_interval == 0:
                    self.gcode.run_script_from_command("G28 X Y")
                    self._stage_xy_position(cx, cy, 150.0)

        # Phase 3: fine sweep around best coarse result
        fine_results = []
        if fine_scan and phase_step >= 16:
            fine_step = max(8, phase_step // 4)
            fine_start = (best_offset - phase_step + 1024) % 1024
            n_fine = (2 * phase_step) // fine_step
            self.gcode.respond_info(
                "AUTO_CALIBRATE phase 3: fine sweep %d tests around"
                " offset=%d invert_b=%d (step=%d)"
                % (n_fine, best_offset, best_invert_b, fine_step))
            for i in range(n_fine):
                fine_offset = (fine_start + i * fine_step) % 1024
                if settle_time > 0 and i > 0:
                    reactor.pause(reactor.monotonic() + settle_time)
                try:
                    score = self._calibrate_single_run(
                        stepper_name, carriage_axis,
                        primary_channel, partner_channel,
                        toolhead, mcu,
                        interval, breakaway_scale, coil_scale,
                        breakaway_events, duration, stride,
                        fine_offset, best_invert_b, exec_irun_pct)
                except Exception as e:
                    score = -1.0
                fine_results.append({
                    "partner_phase_offset": fine_offset,
                    "partner_invert_b": best_invert_b,
                    "score": score,
                })
                self.gcode.respond_info(
                    "AUTO_CAL fine: [%d/%d] offset=%d score=%.3f%s"
                    % (i + 1, n_fine, fine_offset, score,
                       " ← best" if score > best_score else ""))
                if score > best_score:
                    best_score = score
                    best_offset = fine_offset
                if (i + 1) % rehome_interval == 0:
                    self.gcode.run_script_from_command("G28 X Y")
                    self._stage_xy_position(cx, cy, 150.0)

        self.gcode.run_script_from_command("G28 X Y")

        run_cmd = (
            "MOTOR_PHASE_EXEC_RUN STEPPER=%s CARRIAGE_AXIS=%s"
            " COIL_SCALE=%d BREAKAWAY_COIL_SCALE=%d BREAKAWAY_MS=%d"
            " PARTNER_PHASE_OFFSET=%d PARTNER_INVERT_B=%d EXEC_IRUN_PCT=%d"
            % (stepper_name, carriage_axis, coil_scale,
               breakaway_scale, breakaway_ms,
               best_offset, best_invert_b, exec_irun_pct))
        self.gcode.respond_info(
            "MOTOR_PHASE_AUTO_CALIBRATE result:\n"
            "  PARTNER_PHASE_OFFSET=%d  PARTNER_INVERT_B=%d  score=%.3f\n"
            "  Recommended run command:\n  %s"
            % (best_offset, best_invert_b, best_score, run_cmd))

        result_data = {
            "partner_phase_offset": best_offset,
            "partner_invert_b": best_invert_b,
            "coil_scale": coil_scale,
            "score": best_score,
            "run_command": run_cmd,
            "coarse_results": coarse_results,
            "fine_results": fine_results,
            "calibration_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        output_dir = self._get_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "auto_calibrate_result.json"
        result_path.write_text(json.dumps(result_data, indent=2))
        self.gcode.respond_info("Saved to %s" % result_path)

    def cmd_MOTOR_PHASE_MEASURE(self, gcmd):
        self.gcode.respond_info("Use MOTOR_PHASE_EXEC_RUN for Phase 4 testing")

def load_config(config):
    return MotorPhaseCalibration(config)
