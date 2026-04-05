# Motor phase tuning helpers for the Snapmaker U1
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import math
import pathlib
import time
import csv
from . import bus


VALID_STEP_DIRS = {
    "forward": 1.,
    "fwd": 1.,
    "+": 1.,
    "backward": -1.,
    "bck": -1.,
    "rev": -1.,
    "reverse": -1.,
    "-": -1.,
}
VALID_SWEEP_DIRECTIONS = {
    "forward": ("forward",),
    "fwd": ("forward",),
    "+": ("forward",),
    "backward": ("backward",),
    "bck": ("backward",),
    "rev": ("backward",),
    "reverse": ("backward",),
    "-": ("backward",),
    "both": ("forward", "backward"),
    "bidirectional": ("forward", "backward"),
}
VALID_DIRECT_MEASURE_VARIANTS = {
    "profile": "profile",
    "baseline": "baseline",
}
DIRECT_PROFILE_SHIFT_TOLERANCE_DEG = 1.0e-6


class MotorPhaseCalibration:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.loaded_payloads = {}
        self.accel_chip_name = config.get("accel_chip").strip()
        self.default_distance = config.getfloat("distance", 40., above=0.)
        self.default_speed = config.getfloat("speed", 40., above=0.)
        self.default_accel = config.getfloat("accel", 0., minval=0.)
        self.default_settle_time = config.getfloat("settle_time", 0.2,
                                                   minval=0.)
        self.default_output_dir = config.get("output_dir", None)
        self.default_profile_dir = config.get(
            "profile_dir", "/home/lava/printer_data/config/motor_phase_profiles")
        self.autoload_profiles = tuple(
            self._normalize_profile_names(
                config.getlist("autoload_profiles", ()), config))
        self.gcode.register_command(
            "MOTOR_PHASE_MEASURE", self.cmd_MOTOR_PHASE_MEASURE,
            desc=self.cmd_MOTOR_PHASE_MEASURE_help)
        self.gcode.register_command(
            "MOTOR_PHASE_SWEEP", self.cmd_MOTOR_PHASE_SWEEP,
            desc=self.cmd_MOTOR_PHASE_SWEEP_help)
        self.gcode.register_command(
            "MOTOR_PHASE_STEP_TRACE", self.cmd_MOTOR_PHASE_STEP_TRACE,
            desc=self.cmd_MOTOR_PHASE_STEP_TRACE_help)
        self.gcode.register_command(
            "MOTOR_PHASE_CAPTURE_SYNC",
            self.cmd_MOTOR_PHASE_CAPTURE_SYNC,
            desc=self.cmd_MOTOR_PHASE_CAPTURE_SYNC_help)
        self.gcode.register_command(
            "MOTOR_PHASE_DIRECT_SAMPLE",
            self.cmd_MOTOR_PHASE_DIRECT_SAMPLE,
            desc=self.cmd_MOTOR_PHASE_DIRECT_SAMPLE_help)
        self.gcode.register_command(
            "MOTOR_PHASE_DIRECT_SCAN",
            self.cmd_MOTOR_PHASE_DIRECT_SCAN,
            desc=self.cmd_MOTOR_PHASE_DIRECT_SCAN_help)
        self.gcode.register_command(
            "MOTOR_PHASE_DIRECT_MEASURE",
            self.cmd_MOTOR_PHASE_DIRECT_MEASURE,
            desc=self.cmd_MOTOR_PHASE_DIRECT_MEASURE_help)
        self.gcode.register_command(
            "MOTOR_PHASE_DIRECT_SCHEDULED_MEASURE",
            self.cmd_MOTOR_PHASE_DIRECT_SCHEDULED_MEASURE,
            desc=self.cmd_MOTOR_PHASE_DIRECT_SCHEDULED_MEASURE_help)
        self.gcode.register_command(
            "MOTOR_PHASE_LOAD_PAYLOAD",
            self.cmd_MOTOR_PHASE_LOAD_PAYLOAD,
            desc=self.cmd_MOTOR_PHASE_LOAD_PAYLOAD_help)
        self.gcode.register_command(
            "MOTOR_PHASE_SHOW_PAYLOAD",
            self.cmd_MOTOR_PHASE_SHOW_PAYLOAD,
            desc=self.cmd_MOTOR_PHASE_SHOW_PAYLOAD_help)
        self.gcode.register_command(
            "MOTOR_PHASE_STORE_PAYLOAD",
            self.cmd_MOTOR_PHASE_STORE_PAYLOAD,
            desc=self.cmd_MOTOR_PHASE_STORE_PAYLOAD_help)
        self.gcode.register_command(
            "MOTOR_PHASE_LOAD_PROFILE",
            self.cmd_MOTOR_PHASE_LOAD_PROFILE,
            desc=self.cmd_MOTOR_PHASE_LOAD_PROFILE_help)
        self.gcode.register_command(
            "MOTOR_PHASE_LIST_PROFILES",
            self.cmd_MOTOR_PHASE_LIST_PROFILES,
            desc=self.cmd_MOTOR_PHASE_LIST_PROFILES_help)
        self._autoload_profiles_from_storage(config)
        # Optional MCU executor (config params exec_stepper / exec_spi_bus /
        # exec_cs_pin must all be present to enable MOTOR_PHASE_EXEC_RUN)
        exec_stepper = config.get("exec_stepper", None)
        if exec_stepper is not None:
            self._exec_stepper = exec_stepper
            spi_bus   = config.get("exec_spi_bus")
            cs_pin    = config.get("exec_cs_pin")
            spi_mode  = config.getint("exec_spi_mode", 3)
            spi_speed = config.getint("exec_spi_speed", 2000000)
            main_mcu  = config.get_printer().lookup_object("mcu")
            mcu_spi   = bus.MCU_SPI(main_mcu, spi_bus, cs_pin,
                                    spi_mode, spi_speed)
            self._exec = MotorPhaseExec(mcu_spi)
            self.gcode.register_command(
                "MOTOR_PHASE_EXEC_RUN", self.cmd_MOTOR_PHASE_EXEC_RUN,
                desc=self.cmd_MOTOR_PHASE_EXEC_RUN_help)
        else:
            self._exec = None
            self._exec_stepper = None

    def _lookup_accel_chip(self):
        chip = self.printer.lookup_object(self.accel_chip_name, None)
        if chip is None:
            raise self.printer.command_error(
                "Unable to find accelerometer '%s'" % (self.accel_chip_name,))
        return chip

    def _lookup_force_move(self):
        force_move = self.printer.lookup_object("force_move", None)
        if force_move is None:
            raise self.printer.command_error(
                "motor_phase_calibration requires a [force_move] section")
        return force_move

    def _lookup_stepper(self, stepper_name):
        force_move = self._lookup_force_move()
        try:
            mcu_stepper = force_move.lookup_stepper(stepper_name)
        except self.printer.config_error as e:
            raise self.printer.command_error(str(e))
        return force_move, mcu_stepper

    def _lookup_tmc_driver(self, stepper_name):
        driver = self.printer.lookup_object("tmc2240 %s" % (stepper_name,), None)
        if driver is None:
            raise self.printer.command_error(
                "Unable to find TMC2240 object for '%s'" % (stepper_name,))
        return driver

    def _set_tmc_field(self, driver, field_name, value, print_time):
        reg_name = driver.fields.lookup_register(field_name, None)
        if reg_name is None:
            raise self.printer.command_error(
                "Unknown TMC field '%s'" % (field_name,))
        reg_val = driver.fields.set_field(field_name, value)
        driver.mcu_tmc.set_register(reg_name, reg_val, print_time)

    def _get_tmc_field(self, driver, field_name):
        reg_name = driver.fields.lookup_register(field_name, None)
        if reg_name is None:
            raise self.printer.command_error(
                "Unknown TMC field '%s'" % (field_name,))
        return driver.fields.get_field(field_name)

    def _read_tmc_register(self, driver, reg_name):
        return driver.mcu_tmc.get_register(reg_name)

    def _baseline_executor_currents(self, phase_index, coil_scale):
        # Match the MCU baseline executor orientation:
        # coil_a = cos(phase), coil_b = sin(phase)
        electrical_phase = (2.0 * math.pi * (phase_index & 1023)) / 1024.0
        coil_a = int(round(coil_scale * math.cos(electrical_phase)))
        coil_b = int(round(coil_scale * math.sin(electrical_phase)))
        coil_a = max(-255, min(255, coil_a))
        coil_b = max(-255, min(255, coil_b))
        return coil_a, coil_b

    def _prepare_executor_driver(self, driver, toolhead, coil_scale):
        # Prusa's phase-stepping path first disables interpolation, switches to
        # 256 microsteps, reads MSCNT at full resolution, and only then enables
        # direct mode with currents synchronized to the current phase.
        # The baseline U1 executor needs the same preparation to avoid entering
        # direct mode from an arbitrary phase zero.
        original = {
            "intpol": self._get_tmc_field(driver, "intpol"),
            "mres": self._get_tmc_field(driver, "mres"),
        }
        print_time = toolhead.get_last_move_time()
        self._set_tmc_field(driver, "intpol", 0, print_time)
        self._set_tmc_field(driver, "mres", 0, print_time)  # 256 microsteps
        toolhead.dwell(0.05)
        toolhead.wait_moves()
        current_phase = self._read_tmc_register(driver, "MSCNT") & 0x3ff
        # Read GSTAT once before the run so stale latched bits do not leak into
        # the post-run diagnosis.
        self._read_tmc_register(driver, "GSTAT")
        coil_a, coil_b = self._baseline_executor_currents(
            current_phase, coil_scale)
        print_time = toolhead.get_last_move_time()
        self._set_tmc_field(driver, "coil_a", coil_a, print_time)
        self._set_tmc_field(driver, "coil_b", coil_b, print_time)
        self._set_tmc_field(driver, "direct_mode", 1, print_time)
        toolhead.dwell(0.05)
        toolhead.wait_moves()
        return original, current_phase

    def _restore_executor_driver(self, driver, toolhead, original):
        print_time = toolhead.get_last_move_time()
        self._set_tmc_field(driver, "coil_a", 0, print_time)
        self._set_tmc_field(driver, "coil_b", 0, print_time)
        self._set_tmc_field(driver, "direct_mode", 0, print_time)
        self._set_tmc_field(driver, "mres", original["mres"], print_time)
        self._set_tmc_field(driver, "intpol", original["intpol"], print_time)
        toolhead.dwell(0.05)
        toolhead.wait_moves()

    def _load_runtime_payload(self, payload_path, speed_mm_s, direction):
        payload_path = pathlib.Path(payload_path)
        with payload_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload_items = payload.get("runtime_payload", payload)
        if isinstance(payload_items, dict):
            payload_items = [payload_items]
        for item in payload_items:
            item_speed = item.get("speed_mm_s")
            if item_speed is None:
                continue
            if abs(float(item_speed) - speed_mm_s) > 1e-6:
                continue
            directions = item.get("directions", {})
            if direction not in directions:
                continue
            return item, directions[direction]
        raise self.printer.command_error(
            "Unable to find runtime payload for speed %.3f and direction '%s'"
            % (speed_mm_s, direction))

    def _store_loaded_payload(self, profile_name, payload_item):
        self.loaded_payloads[profile_name] = payload_item

    def _get_loaded_payload(self, profile_name):
        payload_item = self.loaded_payloads.get(profile_name)
        if payload_item is None:
            raise self.printer.command_error(
                "Unknown loaded payload profile '%s'" % (profile_name,))
        return payload_item

    def _validate_profile_name(self, profile_name, gcmd=None):
        if not profile_name.replace("-", "").replace("_", "").isalnum():
            if gcmd is not None:
                raise gcmd.error("Invalid PROFILE parameter")
            raise self.printer.command_error("Invalid PROFILE parameter")
        return profile_name

    def _normalize_profile_names(self, profile_names, config=None):
        normalized = []
        seen = set()
        for profile_name in profile_names:
            profile_name = profile_name.strip()
            if not profile_name:
                continue
            if not profile_name.replace("-", "").replace("_", "").isalnum():
                if config is not None:
                    raise config.error(
                        "Invalid autoload_profiles entry '%s'" % (
                            profile_name,))
                raise self.printer.command_error(
                    "Invalid profile name '%s'" % (profile_name,))
            if profile_name in seen:
                continue
            seen.add(profile_name)
            normalized.append(profile_name)
        return normalized

    def _get_profile_dir(self):
        return pathlib.Path(self.default_profile_dir)

    def _get_profile_path(self, profile_name):
        self._validate_profile_name(profile_name)
        return self._get_profile_dir() / ("%s.json" % (profile_name,))

    def _write_profile_file(self, profile_name, payload_item):
        profile_dir = self._get_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_path = self._get_profile_path(profile_name)
        stored_payload = {
            "profile_name": profile_name,
            "storage_kind": "u1_motor_phase_profile_v1",
            "payload_item": payload_item,
        }
        with profile_path.open("w", encoding="utf-8") as handle:
            json.dump(stored_payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return profile_path

    def _read_profile_file(self, profile_name):
        profile_path = self._get_profile_path(profile_name)
        if not profile_path.is_file():
            raise self.printer.command_error(
                "Stored profile '%s' not found at %s" % (
                    profile_name, profile_path))
        with profile_path.open("r", encoding="utf-8") as handle:
            stored_payload = json.load(handle)
        payload_item = stored_payload.get("payload_item")
        if not isinstance(payload_item, dict):
            raise self.printer.command_error(
                "Stored profile '%s' is missing payload_item" % (
                    profile_name,))
        return profile_path, payload_item

    def _list_profile_paths(self):
        profile_dir = self._get_profile_dir()
        if not profile_dir.exists():
            return []
        return sorted(
            path for path in profile_dir.glob("*.json")
            if path.is_file())

    def _autoload_profiles_from_storage(self, config):
        for profile_name in self.autoload_profiles:
            try:
                _, payload_item = self._read_profile_file(profile_name)
            except Exception as e:
                raise config.error(
                    "Unable to autoload motor phase profile '%s': %s" % (
                        profile_name, str(e)))
            self._store_loaded_payload(profile_name, payload_item)
            logging.info(
                "motor_phase_calibration: autoloaded stored profile %s",
                profile_name)

    def _resolve_direction_payload(self, gcmd):
        direction = gcmd.get("DIRECTION", "forward").lower()
        profile_name = gcmd.get("PROFILE", None)
        payload_path = gcmd.get("PAYLOAD", None)
        if (profile_name is None) == (payload_path is None):
            raise gcmd.error("Specify either PROFILE or PAYLOAD")
        if profile_name is not None:
            payload_item = self._get_loaded_payload(profile_name)
        else:
            speed_mm_s = gcmd.get_float("SPEED_MM_S", above=0.)
            payload_item, _ = self._load_runtime_payload(
                payload_path, speed_mm_s, direction)
        directions = payload_item.get("directions", {})
        direction_payload = directions.get(direction)
        if direction_payload is None:
            raise self.printer.command_error(
                "Loaded payload does not contain direction '%s'" % (direction,))
        return payload_item, direction, direction_payload

    def _resolve_optional_direction_payload(self, gcmd, speed_mm_s):
        direction = gcmd.get("DIRECTION", "forward").lower()
        profile_name = gcmd.get("PROFILE", None)
        payload_path = gcmd.get("PAYLOAD", None)
        if profile_name is None and payload_path is None:
            return None, direction, None
        if profile_name is not None and payload_path is not None:
            raise gcmd.error("Specify either PROFILE or PAYLOAD, not both")
        if profile_name is not None:
            payload_item = self._get_loaded_payload(profile_name)
        else:
            payload_item, _ = self._load_runtime_payload(
                payload_path, speed_mm_s, direction)
        directions = payload_item.get("directions", {})
        direction_payload = directions.get(direction)
        if direction_payload is None:
            raise self.printer.command_error(
                "Loaded payload does not contain direction '%s'" % (direction,))
        return payload_item, direction, direction_payload

    def _reverse_direction_name(self, direction_name):
        if direction_name == "forward":
            return "backward"
        if direction_name == "backward":
            return "forward"
        raise self.printer.command_error(
            "Unknown direction '%s'" % (direction_name,))

    def _build_baseline_direct_profile(self, phase_points):
        coil_a_q15 = []
        coil_b_q15 = []
        for phase_index in range(phase_points):
            electrical_phase = 2.0 * math.pi * phase_index / phase_points
            coil_a_q15.append(int(round(math.sin(electrical_phase) * 32767.0)))
            coil_b_q15.append(int(round(math.cos(electrical_phase) * 32767.0)))
        return {
            "profile_kind": "u1_direct_mode_coil_unit_baseline_v1",
            "phase_points": phase_points,
            "shift_scale_deg_default": 0.0,
            "coil_a_unit_q15": coil_a_q15,
            "coil_b_unit_q15": coil_b_q15,
        }

    def _parse_harmonics_arg(self, raw_value, arg_name):
        if raw_value is None:
            return None
        harmonics = []
        for token in raw_value.split(","):
            token = token.strip()
            if not token:
                continue
            harmonic = int(token)
            if harmonic <= 0:
                raise self.printer.command_error(
                    "%s must only contain positive integers" % (arg_name,))
            harmonics.append(harmonic)
        if not harmonics:
            raise self.printer.command_error(
                "%s must contain at least one harmonic" % (arg_name,))
        return tuple(sorted(set(harmonics)))

    def _harmonic_descriptor(self, values, harmonic):
        total = len(values)
        cosine = 0.0
        sine = 0.0
        for index, value in enumerate(values):
            angle = 2.0 * math.pi * harmonic * index / total
            cosine += value * math.cos(angle)
            sine += value * math.sin(angle)
        cosine /= total
        sine /= total
        magnitude = math.sqrt(cosine * cosine + sine * sine)
        phase_deg = math.degrees(math.atan2(-sine, cosine))
        return {
            "harmonic": harmonic,
            "magnitude": magnitude,
            "phase_deg": phase_deg,
        }

    def _reconstruct_harmonic_curve(self, values, harmonics):
        total = len(values)
        reconstructed = [0.0] * total
        descriptors = {
            harmonic: self._harmonic_descriptor(values, harmonic)
            for harmonic in harmonics
        }
        for harmonic, descriptor in descriptors.items():
            amplitude = descriptor["magnitude"] * 2.0
            phase_rad = math.radians(descriptor["phase_deg"])
            for index in range(total):
                angle = 2.0 * math.pi * harmonic * index / total
                reconstructed[index] += amplitude * math.cos(
                    angle + phase_rad)
        return reconstructed, descriptors

    def _get_filtered_phase_offset_q15(self, direction_payload, harmonics):
        cache = direction_payload.setdefault("_filtered_phase_offset_cache", {})
        cache_key = tuple(harmonics)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        reconstructed, _ = self._reconstruct_harmonic_curve(
            direction_payload["phase_offset_q15"], harmonics)
        quantized = [
            max(-32767, min(32767, int(round(value))))
            for value in reconstructed
        ]
        cache[cache_key] = quantized
        return quantized

    def _phase_payload_to_currents(self, phase_offset_q15, phase_points,
                                   phase_index, shift_scale_deg, coil_scale):
        base_phase = 2.0 * math.pi * (phase_index % phase_points) / phase_points
        normalized_shift = phase_offset_q15 / 32767.0 if phase_offset_q15 else 0.0
        corrected_phase = base_phase + math.radians(
            shift_scale_deg * normalized_shift)
        coil_a = int(round(coil_scale * math.sin(corrected_phase)))
        coil_b = int(round(coil_scale * math.cos(corrected_phase)))
        coil_a = max(-255, min(255, coil_a))
        coil_b = max(-255, min(255, coil_b))
        return coil_a, coil_b, normalized_shift

    def _q15_unit_to_coil(self, value_q15, coil_scale):
        return max(-255, min(255, int(round(coil_scale * value_q15 / 32767.0))))

    def _resolve_direct_coils(self, direction_payload, phase_index,
                              shift_scale_deg, coil_scale, harmonics=None):
        phase_points = int(direction_payload["phase_points"])
        phase_index = phase_index % phase_points
        if harmonics:
            filtered_offsets = self._get_filtered_phase_offset_q15(
                direction_payload, harmonics)
            phase_offset_q15 = filtered_offsets[phase_index]
            coil_a, coil_b, normalized_shift = self._phase_payload_to_currents(
                phase_offset_q15, phase_points, phase_index, shift_scale_deg,
                coil_scale)
            return {
                "phase_index": phase_index,
                "phase_points": phase_points,
                "representation": "harmonic_phase_offset_profile",
                "shift_scale_deg": shift_scale_deg,
                "shift_scale_deg_default": None,
                "normalized_shift": normalized_shift,
                "phase_offset_q15": phase_offset_q15,
                "coil_a": coil_a,
                "coil_b": coil_b,
                "coil_a_unit_q15": None,
                "coil_b_unit_q15": None,
                "harmonics": harmonics,
            }
        direct_profile = direction_payload.get("prototype_direct_profile")
        if direct_profile is not None:
            profile_shift = float(
                direct_profile.get("shift_scale_deg_default", 0.0))
            if abs(profile_shift - shift_scale_deg) <= (
                    DIRECT_PROFILE_SHIFT_TOLERANCE_DEG):
                coil_a_q15 = direct_profile["coil_a_unit_q15"][phase_index]
                coil_b_q15 = direct_profile["coil_b_unit_q15"][phase_index]
                return {
                    "phase_index": phase_index,
                    "phase_points": phase_points,
                    "representation": "prototype_direct_profile",
                    "shift_scale_deg": shift_scale_deg,
                    "shift_scale_deg_default": profile_shift,
                    "normalized_shift": (
                        direction_payload["phase_offset_q15"][phase_index] /
                        32767.0),
                    "phase_offset_q15": direction_payload["phase_offset_q15"][
                        phase_index],
                    "coil_a": self._q15_unit_to_coil(coil_a_q15, coil_scale),
                    "coil_b": self._q15_unit_to_coil(coil_b_q15, coil_scale),
                    "coil_a_unit_q15": coil_a_q15,
                    "coil_b_unit_q15": coil_b_q15,
                    "harmonics": None,
                }
        phase_offset_q15 = direction_payload["phase_offset_q15"][phase_index]
        coil_a, coil_b, normalized_shift = self._phase_payload_to_currents(
            phase_offset_q15, phase_points, phase_index, shift_scale_deg,
            coil_scale)
        return {
            "phase_index": phase_index,
            "phase_points": phase_points,
            "representation": "phase_offset_fallback",
            "shift_scale_deg": shift_scale_deg,
            "shift_scale_deg_default": None,
            "normalized_shift": normalized_shift,
            "phase_offset_q15": phase_offset_q15,
            "coil_a": coil_a,
            "coil_b": coil_b,
            "coil_a_unit_q15": None,
            "coil_b_unit_q15": None,
            "harmonics": None,
        }

    def _resolve_profile_variant(self, direction_payload, phase_index,
                                 coil_scale, variant):
        phase_points = int(direction_payload["phase_points"])
        phase_index = phase_index % phase_points
        if variant == "baseline":
            direct_profile = self._build_baseline_direct_profile(phase_points)
            coil_a_q15 = direct_profile["coil_a_unit_q15"][phase_index]
            coil_b_q15 = direct_profile["coil_b_unit_q15"][phase_index]
            return {
                "phase_index": phase_index,
                "phase_points": phase_points,
                "representation": "baseline_direct_profile",
                "phase_offset_q15": 0,
                "normalized_shift": 0.0,
                "coil_a": self._q15_unit_to_coil(coil_a_q15, coil_scale),
                "coil_b": self._q15_unit_to_coil(coil_b_q15, coil_scale),
                "coil_a_unit_q15": coil_a_q15,
                "coil_b_unit_q15": coil_b_q15,
            }
        return self._resolve_direct_coils(
            direction_payload, phase_index, 10.0, coil_scale)

    def _prepare_direct_profile_run(self, direction_payload, direction_name,
                                    distance, speed, phase_stride,
                                    electrical_cycle_mm):
        phase_points = int(direction_payload["phase_points"])
        phase_step_mm = electrical_cycle_mm / phase_points
        update_distance = phase_step_mm * phase_stride
        update_period = update_distance / speed
        step_count = max(1, int(round(distance / update_distance)))
        indices = []
        direction_sign = 1 if direction_name == "forward" else -1
        for step_index in range(step_count):
            indices.append(
                (step_index * phase_stride * direction_sign) % phase_points)
        return {
            "phase_points": phase_points,
            "phase_stride": phase_stride,
            "update_distance_mm": update_distance,
            "update_period_s": update_period,
            "step_count": step_count,
            "commanded_distance_mm": step_count * update_distance,
            "indices": indices,
        }

    def _run_direct_profile_path(self, driver, toolhead, direction_payload,
                                 direction_name, distance, speed, phase_stride,
                                 coil_scale, variant, electrical_cycle_mm,
                                 harmonics=None):
        run_plan = self._prepare_direct_profile_run(
            direction_payload, direction_name, distance, speed,
            phase_stride, electrical_cycle_mm)
        start_print_time = toolhead.get_last_move_time()
        representations = []
        for phase_index in run_plan["indices"]:
            if variant == "baseline":
                resolved = self._resolve_profile_variant(
                    direction_payload, phase_index, coil_scale, variant)
            else:
                resolved = self._resolve_direct_coils(
                    direction_payload, phase_index, 10.0, coil_scale,
                    harmonics=harmonics)
            print_time = toolhead.get_last_move_time()
            self._set_tmc_field(driver, "coil_a", resolved["coil_a"], print_time)
            self._set_tmc_field(driver, "coil_b", resolved["coil_b"], print_time)
            representations.append(resolved["representation"])
            toolhead.dwell(run_plan["update_period_s"])
        end_print_time = toolhead.get_last_move_time()
        run_plan.update({
            "start_print_time": start_print_time,
            "end_print_time": end_print_time,
            "representation": (
                sorted(set(representations))[0] if representations else "unknown"),
        })
        return run_plan

    def _run_scheduled_direct_profile_path(self, driver, toolhead,
                                           direction_payload, direction_name,
                                           distance, speed, phase_stride,
                                           coil_scale, variant,
                                           electrical_cycle_mm,
                                           harmonics=None):
        run_plan = self._prepare_direct_profile_run(
            direction_payload, direction_name, distance, speed,
            phase_stride, electrical_cycle_mm)
        start_print_time = toolhead.get_last_move_time()
        representations = []
        last_print_time = start_print_time
        for index, phase_index in enumerate(run_plan["indices"]):
            if variant == "baseline":
                resolved = self._resolve_profile_variant(
                    direction_payload, phase_index, coil_scale, variant)
            else:
                resolved = self._resolve_direct_coils(
                    direction_payload, phase_index, 10.0, coil_scale,
                    harmonics=harmonics)
            print_time = start_print_time + index * run_plan["update_period_s"]
            self._set_tmc_field(driver, "coil_a", resolved["coil_a"], print_time)
            self._set_tmc_field(driver, "coil_b", resolved["coil_b"], print_time)
            representations.append(resolved["representation"])
            last_print_time = print_time
        end_print_time = last_print_time
        toolhead.dwell(max(
            run_plan["update_period_s"],
            end_print_time - start_print_time + run_plan["update_period_s"]))
        run_plan.update({
            "start_print_time": start_print_time,
            "end_print_time": end_print_time,
            "representation": (
                sorted(set(representations))[0] if representations else "unknown"),
        })
        return run_plan

    def _with_direct_mode(self, stepper_name, callback):
        force_move, mcu_stepper = self._lookup_stepper(stepper_name)
        driver = self._lookup_tmc_driver(stepper_name)
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()
        was_enable = force_move._force_enable(mcu_stepper)
        try:
            print_time = toolhead.get_last_move_time()
            self._set_tmc_field(driver, "direct_mode", 1, print_time)
            callback(driver, toolhead)
        finally:
            print_time = toolhead.get_last_move_time()
            self._set_tmc_field(driver, "coil_a", 0, print_time)
            self._set_tmc_field(driver, "coil_b", 0, print_time)
            self._set_tmc_field(driver, "direct_mode", 0, print_time)
            force_move._restore_enable(mcu_stepper, was_enable)

    def _get_output_dir(self):
        if self.default_output_dir:
            return pathlib.Path(self.default_output_dir)
        vsd = self.printer.lookup_object("virtual_sdcard", None)
        if vsd is not None:
            return pathlib.Path(vsd.sdcard_dirname) / "motor_phase_data"
        return pathlib.Path("/userdata/gcodes/motor_phase_data")

    def _get_stepper_config(self, stepper_name):
        configfile = self.printer.lookup_object("configfile")
        settings = configfile.get_status(None)["settings"]
        stepper_cfg = settings.get(stepper_name, {})
        if not stepper_cfg:
            raise self.printer.command_error(
                "Unable to find config for stepper '%s'" % (stepper_name,))
        rotation_distance = float(stepper_cfg["rotation_distance"])
        full_steps = int(stepper_cfg.get("full_steps_per_rotation", 200))
        return rotation_distance, full_steps

    def _estimate_metrics(self, stepper_name, mcu_stepper, speed):
        rotation_distance, full_steps = self._get_stepper_config(stepper_name)
        electrical_cycle = rotation_distance / full_steps * 4.
        step_dist = mcu_stepper.get_step_dist()
        steps_per_cycle = electrical_cycle / step_dist
        electrical_hz = speed / electrical_cycle
        mscnt_units_per_step = 1024. / steps_per_cycle
        return electrical_cycle, electrical_hz, step_dist, steps_per_cycle, (
            mscnt_units_per_step)

    def _parse_speed_list(self, gcmd):
        raw_speeds = gcmd.get("SPEEDS", None)
        if raw_speeds is not None:
            speeds = []
            for raw_speed in raw_speeds.split(","):
                raw_speed = raw_speed.strip()
                if not raw_speed:
                    continue
                speed = float(raw_speed)
                if speed <= 0.:
                    raise gcmd.error("All SPEEDS values must be above 0")
                speeds.append(speed)
            if not speeds:
                raise gcmd.error("SPEEDS must contain at least one value")
            return speeds
        start = gcmd.get_float("START_SPEED", None, above=0.)
        stop = gcmd.get_float("STOP_SPEED", None, above=0.)
        step = gcmd.get_float("STEP_SPEED", None, above=0.)
        if None in (start, stop, step):
            raise gcmd.error(
                "Specify SPEEDS=... or START_SPEED/STOP_SPEED/STEP_SPEED")
        speeds = []
        current = start
        if start <= stop:
            while current <= stop + 1e-9:
                speeds.append(round(current, 6))
                current += step
        else:
            while current >= stop - 1e-9:
                speeds.append(round(current, 6))
                current -= step
        if not speeds:
            raise gcmd.error("Unable to build speed list")
        return speeds

    def _parse_sweep_directions(self, gcmd):
        raw_direction = gcmd.get("DIRECTION", "forward").lower()
        directions = VALID_SWEEP_DIRECTIONS.get(raw_direction)
        if directions is None:
            raise gcmd.error("Invalid DIRECTION '%s'" % (raw_direction,))
        return raw_direction, directions

    def _check_xy_homed(self):
        toolhead = self.printer.lookup_object("toolhead")
        curtime = self.printer.get_reactor().monotonic()
        homed_axes = toolhead.get_status(curtime)["homed_axes"]
        return "x" in homed_axes and "y" in homed_axes, homed_axes

    def _get_axis_limits(self):
        toolhead = self.printer.lookup_object("toolhead")
        curtime = self.printer.get_reactor().monotonic()
        status = toolhead.get_kinematics().get_status(curtime)
        return status["axis_minimum"], status["axis_maximum"]

    def _project_cartesian_delta(self, stepper_name, move_distance):
        half = 0.5 * move_distance
        if stepper_name == "stepper_x":
            return half, half
        if stepper_name == "stepper_y":
            return half, -half
        raise self.printer.command_error(
            "MOTOR_PHASE_SWEEP currently supports stepper_x and stepper_y only")

    def _validate_stage_position(self, stepper_name, center_x, center_y,
                                 distance, direction, margin):
        axis_min, axis_max = self._get_axis_limits()
        dx, dy = self._project_cartesian_delta(stepper_name, direction * distance)
        target_x = center_x + dx
        target_y = center_y + dy
        if not (axis_min.x + margin <= center_x <= axis_max.x - margin):
            raise self.printer.command_error("CENTER_X is outside safe bounds")
        if not (axis_min.y + margin <= center_y <= axis_max.y - margin):
            raise self.printer.command_error("CENTER_Y is outside safe bounds")
        if not (axis_min.x + margin <= target_x <= axis_max.x - margin):
            raise self.printer.command_error(
                "Sweep target exceeds X bounds; choose a different center or distance")
        if not (axis_min.y + margin <= target_y <= axis_max.y - margin):
            raise self.printer.command_error(
                "Sweep target exceeds Y bounds; choose a different center or distance")
    def _validate_stage_position_for_directions(
            self, stepper_name, center_x, center_y, distance, directions,
            margin):
        for raw_direction in directions:
            direction = VALID_STEP_DIRS[raw_direction]
            self._validate_stage_position(
                stepper_name, center_x, center_y, distance, direction, margin)

    def _stage_xy_position(self, center_x, center_y, travel_speed, safe_z):
        toolhead = self.printer.lookup_object("toolhead")
        curtime = self.printer.get_reactor().monotonic()
        status = toolhead.get_status(curtime)
        homed_axes = status["homed_axes"]
        pos = toolhead.get_position()
        if "z" in homed_axes and pos[2] < safe_z:
            toolhead.manual_move([None, None, safe_z, None], travel_speed)
            toolhead.wait_moves()
        toolhead.manual_move([center_x, center_y, None, None], travel_speed)
        toolhead.wait_moves()

    def _prepare_safe_centered_stepper_move(self, gcmd, stepper_name,
                                            distance, direction):
        travel_speed = gcmd.get_float("TRAVEL_SPEED", 150., above=0.)
        safe_z = gcmd.get_float("SAFE_Z", 10., minval=0.)
        margin = gcmd.get_float("MARGIN", 5., minval=0.)
        xy_homed, homed_axes = self._check_xy_homed()
        if not xy_homed:
            self.gcode.run_script_from_command("G28 X Y")
            xy_homed, homed_axes = self._check_xy_homed()
        if not xy_homed:
            raise gcmd.error("XY must be homed before this command")
        axis_min, axis_max = self._get_axis_limits()
        center_x = gcmd.get_float(
            "CENTER_X", 0.5 * (axis_min.x + axis_max.x))
        center_y = gcmd.get_float(
            "CENTER_Y", 0.5 * (axis_min.y + axis_max.y))
        self._validate_stage_position(
            stepper_name, center_x, center_y, distance, direction, margin)
        self._stage_xy_position(center_x, center_y, travel_speed, safe_z)
        return {
            "homed_axes": homed_axes,
            "center_x": center_x,
            "center_y": center_y,
            "travel_speed": travel_speed,
            "safe_z": safe_z,
            "margin": margin,
        }

    def _get_step_history(self, mcu_stepper, start_clock, end_clock):
        history = []
        query_end_clock = end_clock
        while True:
            data, count = mcu_stepper.dump_steps(
                128, start_clock, query_end_clock)
            if not count:
                break
            history.append((data, count))
            if count < len(data):
                break
            query_end_clock = data[count - 1].first_clock
        history.reverse()
        stable_history = []
        for batch, count in history:
            for index in range(count - 1, -1, -1):
                item = batch[index]
                stable_history.append({
                    "first_clock": int(item.first_clock),
                    "last_clock": int(item.last_clock),
                    "start_position": int(item.start_position),
                    "step_count": int(item.step_count),
                    "interval": int(item.interval),
                    "add": int(item.add),
                })
        return stable_history

    def _expand_step_history(self, mcu_stepper, step_history, cycle_steps):
        clock_to_print_time = mcu_stepper.get_mcu().clock_to_print_time
        expanded = []
        for segment_index, history_step in enumerate(step_history):
            signed_step_count = int(history_step["step_count"])
            step_sign = 1 if signed_step_count >= 0 else -1
            step_count = abs(signed_step_count)
            for step_index in range(step_count):
                tick_offset = (
                    history_step["interval"] * step_index
                    + history_step["add"] * step_index * (step_index - 1) // 2)
                step_clock = int(history_step["first_clock"] + tick_offset)
                mcu_position = int(
                    history_step["start_position"] + step_sign * (step_index + 1))
                cycle_step = mcu_position % cycle_steps
                phase_index = (cycle_step * 1024) // cycle_steps
                expanded.append({
                    "segment_index": segment_index,
                    "segment_first_clock": int(history_step["first_clock"]),
                    "segment_last_clock": int(history_step["last_clock"]),
                    "segment_interval": int(history_step["interval"]),
                    "segment_add": int(history_step["add"]),
                    "segment_step_count": signed_step_count,
                    "step_in_segment": step_index,
                    "step_clock": step_clock,
                    "step_time": clock_to_print_time(step_clock),
                    "mcu_position": mcu_position,
                    "commanded_position_mm": (
                        mcu_stepper.mcu_to_commanded_position(mcu_position)),
                    "phase_index": int(phase_index),
                })
        expanded.sort(key=lambda row: row["step_clock"])
        return expanded

    def _write_step_trace_csv(self, filename, expanded_steps):
        with filename.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "step_time", "step_clock", "mcu_position",
                "commanded_position_mm", "phase_index", "segment_index",
                "step_in_segment", "segment_first_clock", "segment_last_clock",
                "segment_interval", "segment_add", "segment_step_count",
            ])
            for row in expanded_steps:
                writer.writerow([
                    "%.9f" % (row["step_time"],),
                    row["step_clock"],
                    row["mcu_position"],
                    "%.9f" % (row["commanded_position_mm"],),
                    row["phase_index"],
                    row["segment_index"],
                    row["step_in_segment"],
                    row["segment_first_clock"],
                    row["segment_last_clock"],
                    row["segment_interval"],
                    row["segment_add"],
                    row["segment_step_count"],
                ])

    def _expand_runtime_flush_trace(self, flush_events, cycle_steps, step_dist):
        expanded = []
        for index, event in enumerate(flush_events):
            before_mcu = int(event["before_mcu_position"])
            after_mcu = int(event["after_mcu_position"])
            generated_steps = int(event["generated_steps"])
            start_phase = (before_mcu % cycle_steps) * 1024 // cycle_steps
            end_phase = (after_mcu % cycle_steps) * 1024 // cycle_steps
            expanded.append({
                "flush_index": index,
                "window_start_print_time": float(
                    event["window_start_print_time"]),
                "window_end_print_time": float(event["window_end_print_time"]),
                "window_duration_s": max(
                    0.0,
                    float(event["window_end_print_time"])
                    - float(event["window_start_print_time"])),
                "before_commanded_position_mm": float(
                    event["before_commanded_position_mm"]),
                "after_commanded_position_mm": float(
                    event["after_commanded_position_mm"]),
                "commanded_delta_mm": float(
                    event["after_commanded_position_mm"]
                    - event["before_commanded_position_mm"]),
                "before_mcu_position": before_mcu,
                "after_mcu_position": after_mcu,
                "generated_steps": generated_steps,
                "generated_distance_mm": generated_steps * step_dist,
                "start_phase_index": int(start_phase),
                "end_phase_index": int(end_phase),
                "phase_delta": int(end_phase - start_phase),
            })
        return expanded

    def _filter_runtime_flush_trace(self, flush_events, start_print_time,
                                    end_print_time):
        return [
            event for event in flush_events
            if (event["window_end_print_time"] >= start_print_time
                and event["window_start_print_time"] <= end_print_time)
        ]

    def _write_runtime_flush_trace_csv(self, filename, flush_events):
        with filename.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "flush_index", "window_start_print_time",
                "window_end_print_time", "window_duration_s",
                "before_commanded_position_mm", "after_commanded_position_mm",
                "commanded_delta_mm", "before_mcu_position",
                "after_mcu_position", "generated_steps",
                "generated_distance_mm", "start_phase_index",
                "end_phase_index", "phase_delta",
            ])
            for row in flush_events:
                writer.writerow([
                    row["flush_index"],
                    "%.9f" % (row["window_start_print_time"],),
                    "%.9f" % (row["window_end_print_time"],),
                    "%.9f" % (row["window_duration_s"],),
                    "%.9f" % (row["before_commanded_position_mm"],),
                    "%.9f" % (row["after_commanded_position_mm"],),
                    "%.9f" % (row["commanded_delta_mm"],),
                    row["before_mcu_position"],
                    row["after_mcu_position"],
                    row["generated_steps"],
                    "%.9f" % (row["generated_distance_mm"],),
                    row["start_phase_index"],
                    row["end_phase_index"],
                    row["phase_delta"],
                ])

    def _write_mcu_runtime_stats_csv(self, filename, stats):
        with filename.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "stepper_name", "queue_msgs", "load_next", "timer_events",
                "total_steps", "max_chunk", "queued_moves",
            ])
            writer.writerow([
                stats["stepper_name"],
                stats["queue_msgs"],
                stats["load_next"],
                stats["timer_events"],
                stats["total_steps"],
                stats["max_chunk"],
                stats["queued_moves"],
            ])

    def _write_exec_trace_csv(self, filename, mcu, exec_trace):
        with filename.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "sample_index", "step_number", "step_clock", "step_time",
                "delta_step_clock", "delta_step_time_s", "delta_steps",
                "trace_stride", "total_steps", "first_clock", "last_clock",
                "min_interval", "max_interval",
            ])
            prev_clock = prev_time = prev_step_number = None
            for sample in exec_trace["samples"]:
                step_clock = int(sample["step_clock"])
                step_number = int(sample["step_number"])
                step_time = mcu.clock_to_print_time(step_clock)
                writer.writerow([
                    int(sample["index"]),
                    step_number,
                    step_clock,
                    "%.9f" % (step_time,),
                    0 if prev_clock is None else (step_clock - prev_clock),
                    "0.000000000" if prev_time is None else (
                        "%.9f" % (step_time - prev_time,)),
                    0 if prev_step_number is None else (
                        step_number - prev_step_number),
                    int(exec_trace.get("stride", 0)),
                    int(exec_trace.get("total_steps", 0)),
                    int(exec_trace.get("first_clock", 0)),
                    int(exec_trace.get("last_clock", 0)),
                    int(exec_trace.get("min_interval", 0)),
                    int(exec_trace.get("max_interval", 0)),
                ])
                prev_clock = step_clock
                prev_time = step_time
                prev_step_number = step_number

    def _build_exec_correction_plan(self, expanded_steps, exec_trace,
                                    direction_payload, coil_scale,
                                    shift_scale_deg, harmonics=None):
        rows = []
        if exec_trace is None or not exec_trace.get("supported"):
            return rows
        if not expanded_steps:
            return rows
        for sample in exec_trace.get("samples", []):
            step_number = int(sample["step_number"])
            if step_number < 0 or step_number >= len(expanded_steps):
                continue
            trace_row = expanded_steps[step_number]
            phase_index = int(trace_row["phase_index"])
            profile = self._resolve_direct_coils(
                direction_payload, phase_index, shift_scale_deg, coil_scale,
                harmonics)
            baseline = self._resolve_profile_variant(
                direction_payload, phase_index, coil_scale, "baseline")
            rows.append({
                "sample_index": int(sample["index"]),
                "step_number": step_number,
                "step_clock": int(sample["step_clock"]),
                "step_time": float(trace_row["step_time"]),
                "mcu_position": int(trace_row["mcu_position"]),
                "commanded_position_mm": float(
                    trace_row["commanded_position_mm"]),
                "phase_index": phase_index,
                "profile_representation": profile["representation"],
                "profile_harmonics": (
                    ",".join(str(value) for value in profile["harmonics"])
                    if profile["harmonics"] else ""),
                "profile_phase_offset_q15": int(profile["phase_offset_q15"]),
                "profile_shift_norm": float(profile["normalized_shift"]),
                "profile_coil_a": int(profile["coil_a"]),
                "profile_coil_b": int(profile["coil_b"]),
                "baseline_coil_a": int(baseline["coil_a"]),
                "baseline_coil_b": int(baseline["coil_b"]),
                "delta_coil_a": int(profile["coil_a"] - baseline["coil_a"]),
                "delta_coil_b": int(profile["coil_b"] - baseline["coil_b"]),
            })
        return rows

    def _write_exec_correction_plan_csv(self, filename, rows):
        with filename.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "sample_index", "step_number", "step_clock", "step_time",
                "mcu_position", "commanded_position_mm", "phase_index",
                "profile_representation", "profile_harmonics",
                "profile_phase_offset_q15", "profile_shift_norm",
                "profile_coil_a", "profile_coil_b", "baseline_coil_a",
                "baseline_coil_b", "delta_coil_a", "delta_coil_b",
            ])
            for row in rows:
                writer.writerow([
                    row["sample_index"],
                    row["step_number"],
                    row["step_clock"],
                    "%.9f" % (row["step_time"],),
                    row["mcu_position"],
                    "%.9f" % (row["commanded_position_mm"],),
                    row["phase_index"],
                    row["profile_representation"],
                    row["profile_harmonics"],
                    row["profile_phase_offset_q15"],
                    "%.9f" % (row["profile_shift_norm"],),
                    row["profile_coil_a"],
                    row["profile_coil_b"],
                    row["baseline_coil_a"],
                    row["baseline_coil_b"],
                    row["delta_coil_a"],
                    row["delta_coil_b"],
                ])

    def _summarize_exec_correction_plan(self, rows):
        if not rows:
            return {
                "count": 0,
                "mean_abs_delta_coil": 0.0,
                "max_abs_delta_coil": 0,
                "mean_abs_shift_norm": 0.0,
            }
        abs_deltas = []
        abs_shifts = []
        for row in rows:
            abs_deltas.append(max(abs(row["delta_coil_a"]),
                                  abs(row["delta_coil_b"])))
            abs_shifts.append(abs(row["profile_shift_norm"]))
        return {
            "count": len(rows),
            "mean_abs_delta_coil": sum(abs_deltas) / len(abs_deltas),
            "max_abs_delta_coil": max(abs_deltas),
            "mean_abs_shift_norm": sum(abs_shifts) / len(abs_shifts),
        }

    def _summarize_runtime_flush_trace(self, flush_events):
        flush_count = len(flush_events)
        active_events = [
            event for event in flush_events if event["generated_steps"]]
        active_flush_count = len(active_events)
        total_generated_steps = sum(
            abs(event["generated_steps"]) for event in active_events)
        total_generated_distance_mm = sum(
            abs(event["generated_distance_mm"]) for event in active_events)
        mean_window_duration_s = (
            sum(event["window_duration_s"] for event in flush_events)
            / flush_count if flush_count else 0.0)
        mean_active_window_duration_s = (
            sum(event["window_duration_s"] for event in active_events)
            / active_flush_count if active_flush_count else 0.0)
        mean_steps_per_active_flush = (
            total_generated_steps / active_flush_count
            if active_flush_count else 0.0)
        mean_abs_phase_delta = (
            sum(abs(event["phase_delta"]) for event in active_events)
            / active_flush_count if active_flush_count else 0.0)
        return {
            "flush_count": flush_count,
            "active_flush_count": active_flush_count,
            "total_generated_steps": total_generated_steps,
            "total_generated_distance_mm": total_generated_distance_mm,
            "mean_window_duration_s": mean_window_duration_s,
            "mean_active_window_duration_s": mean_active_window_duration_s,
            "mean_steps_per_active_flush": mean_steps_per_active_flush,
            "mean_abs_phase_delta": mean_abs_phase_delta,
        }

    def _summarize_exec_trace(self, mcu, exec_trace):
        samples = exec_trace.get("samples", [])
        if len(samples) < 2:
            return {
                "count": len(samples),
                "supported": int(exec_trace.get("supported", 0)),
                "stride": int(exec_trace.get("stride", 0)),
                "mean_delta_steps": 0.,
                "mean_delta_time_s": 0.,
            }
        delta_steps = []
        delta_times = []
        prev = samples[0]
        prev_time = mcu.clock_to_print_time(int(prev["step_clock"]))
        for sample in samples[1:]:
            step_time = mcu.clock_to_print_time(int(sample["step_clock"]))
            delta_steps.append(
                int(sample["step_number"]) - int(prev["step_number"]))
            delta_times.append(step_time - prev_time)
            prev = sample
            prev_time = step_time
        return {
            "count": len(samples),
            "supported": int(exec_trace.get("supported", 0)),
            "stride": int(exec_trace.get("stride", 0)),
            "mean_delta_steps": sum(delta_steps) / len(delta_steps),
            "mean_delta_time_s": sum(delta_times) / len(delta_times),
        }

    cmd_MOTOR_PHASE_MEASURE_help = (
        "Capture raw accelerometer data during a controlled single-stepper move")
    def cmd_MOTOR_PHASE_MEASURE(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        distance = gcmd.get_float("DISTANCE", self.default_distance, above=0.)
        speed = gcmd.get_float("SPEED", self.default_speed, above=0.)
        accel = gcmd.get_float("ACCEL", self.default_accel, minval=0.)
        settle_time = gcmd.get_float("SETTLE_TIME", self.default_settle_time,
                                     minval=0.)
        write_csv = gcmd.get_int("WRITE_CSV", 1, minval=0, maxval=1)
        raw_direction = gcmd.get("DIRECTION", "forward").lower()
        direction = VALID_STEP_DIRS.get(raw_direction)
        if direction is None:
            raise gcmd.error("Invalid DIRECTION '%s'" % (raw_direction,))
        name = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not name.replace("-", "").replace("_", "").isalnum():
            raise gcmd.error("Invalid NAME parameter")

        force_move, mcu_stepper = self._lookup_stepper(stepper_name)
        accel_chip = self._lookup_accel_chip()
        toolhead = self.printer.lookup_object("toolhead")

        electrical_cycle, electrical_hz, step_dist, steps_per_cycle, (
            mscnt_units_per_step) = self._estimate_metrics(
                stepper_name, mcu_stepper, speed)
        move_distance = direction * distance
        was_enable = force_move._force_enable(mcu_stepper)
        aclient = accel_chip.start_internal_client()
        start_print_time = end_print_time = None
        returned_to_start = False
        try:
            toolhead.dwell(settle_time)
            start_print_time = toolhead.get_last_move_time()
            force_move.manual_move(mcu_stepper, move_distance, speed, accel)
            end_print_time = toolhead.get_last_move_time()
            toolhead.dwell(settle_time)
        finally:
            try:
                aclient.finish_measurements()
            finally:
                try:
                    if end_print_time is not None:
                        force_move.manual_move(
                            mcu_stepper, -move_distance, speed, accel)
                        toolhead.dwell(settle_time)
                        returned_to_start = True
                finally:
                    force_move._restore_enable(mcu_stepper, was_enable)

        samples = aclient.get_samples()
        sample_count = len(samples)
        if not sample_count:
            raise gcmd.error("No accelerometer samples captured")
        move_window = max(0., end_print_time - start_print_time)
        capture_duration = max(
            0., samples[-1].time - samples[0].time) if sample_count > 1 else 0.
        sample_rate = (
            (sample_count - 1) / capture_duration
            if capture_duration and sample_count > 1 else 0.)
        samples_per_cycle = (
            sample_rate / electrical_hz if electrical_hz > 0. else 0.)
        sample_density_over_move_window = (
            sample_count / move_window if move_window else 0.)

        filename = None
        if write_csv:
            output_dir = self._get_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = output_dir / (
                "motor-phase-%s-%s.csv" % (stepper_name, name))
            aclient.write_to_file(str(filename))

        gcmd.respond_info(
            "motor_phase_measure: stepper=%s direction=%s distance=%.3fmm "
            "speed=%.3fmm/s accel=%.3fmm/s^2 samples=%d "
            "electrical_cycle=%.6fmm electrical_hz=%.3f "
            "step_dist=%.6fmm steps_per_cycle=%.3f "
            "mscnt_units_per_step=%.3f sample_rate=%.3fHz "
            "samples_per_cycle=%.3f move_window_sample_density=%.3fHz "
            "capture_duration=%.6fs returned_to_start=%d "
            "window=%.6f..%.6f%s" % (
                stepper_name, raw_direction, distance, speed, accel,
                sample_count, electrical_cycle, electrical_hz,
                step_dist, steps_per_cycle, mscnt_units_per_step,
                sample_rate, samples_per_cycle,
                sample_density_over_move_window, capture_duration,
                returned_to_start,
                start_print_time, end_print_time,
                "" if filename is None else " file=%s" % (filename,)))

    cmd_MOTOR_PHASE_STEP_TRACE_help = (
        "Trace real stepcompress history for a controlled single-stepper move")
    def cmd_MOTOR_PHASE_STEP_TRACE(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        distance = gcmd.get_float("DISTANCE", self.default_distance, above=0.)
        speed = gcmd.get_float("SPEED", self.default_speed, above=0.)
        accel = gcmd.get_float("ACCEL", self.default_accel, minval=0.)
        settle_time = gcmd.get_float("SETTLE_TIME", self.default_settle_time,
                                     minval=0.)
        write_csv = gcmd.get_int("WRITE_CSV", 1, minval=0, maxval=1)
        write_runtime_trace = gcmd.get_int(
            "WRITE_RUNTIME_TRACE", 1, minval=0, maxval=1)
        write_exec_trace = gcmd.get_int(
            "WRITE_EXEC_TRACE", 1, minval=0, maxval=1)
        exec_trace_stride = gcmd.get_int(
            "EXEC_TRACE_STRIDE", 128, minval=1, maxval=65535)
        return_to_start = gcmd.get_int("RETURN_TO_START", 1, minval=0, maxval=1)
        raw_direction = gcmd.get("DIRECTION", "forward").lower()
        direction = VALID_STEP_DIRS.get(raw_direction)
        if direction is None:
            raise gcmd.error("Invalid DIRECTION '%s'" % (raw_direction,))
        name = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not name.replace("-", "").replace("_", "").isalnum():
            raise gcmd.error("Invalid NAME parameter")

        force_move, mcu_stepper = self._lookup_stepper(stepper_name)
        toolhead = self.printer.lookup_object("toolhead")
        electrical_cycle, electrical_hz, step_dist, steps_per_cycle, (
            mscnt_units_per_step) = self._estimate_metrics(
                stepper_name, mcu_stepper, speed)
        cycle_steps = max(1, int(round(steps_per_cycle)))
        move_distance = direction * distance
        self._prepare_safe_centered_stepper_move(
            gcmd, stepper_name, distance, direction)
        was_enable = force_move._force_enable(mcu_stepper)
        start_print_time = end_print_time = None
        returned_to_start = False
        runtime_flush_events = []
        mcu_runtime_stats = None
        exec_trace = None
        try:
            toolhead.dwell(settle_time)
            start_print_time = toolhead.get_last_move_time()
            mcu_stepper.reset_runtime_stats()
            if write_exec_trace:
                mcu_stepper.reset_execution_trace(exec_trace_stride)
            mcu_stepper.begin_generate_steps_trace(start_print_time)
            force_move.manual_move(mcu_stepper, move_distance, speed, accel)
            end_print_time = toolhead.get_last_move_time()
            toolhead.dwell(settle_time)
        finally:
            try:
                runtime_flush_events = mcu_stepper.end_generate_steps_trace()
                mcu_runtime_stats = mcu_stepper.query_runtime_stats()
                if write_exec_trace:
                    exec_trace = mcu_stepper.query_execution_trace()
                if end_print_time is not None and return_to_start:
                    force_move.manual_move(
                        mcu_stepper, -move_distance, speed, accel)
                    toolhead.dwell(settle_time)
                    returned_to_start = True
            finally:
                force_move._restore_enable(mcu_stepper, was_enable)

        mcu = mcu_stepper.get_mcu()
        start_clock = mcu.print_time_to_clock(start_print_time)
        end_clock = mcu.print_time_to_clock(end_print_time)
        step_history = self._get_step_history(mcu_stepper, start_clock, end_clock)
        if not step_history:
            raise gcmd.error("No step history captured in the requested window")
        expanded_steps = self._expand_step_history(
            mcu_stepper, step_history, cycle_steps)
        expanded_steps = [
            row for row in expanded_steps
            if start_clock <= row["step_clock"] <= end_clock]
        if not expanded_steps:
            raise gcmd.error("Unable to expand captured step history")
        runtime_flush_events = self._filter_runtime_flush_trace(
            runtime_flush_events, start_print_time, end_print_time)
        expanded_runtime_trace = self._expand_runtime_flush_trace(
            runtime_flush_events, cycle_steps, step_dist)
        runtime_summary = self._summarize_runtime_flush_trace(
            expanded_runtime_trace)
        exec_summary = self._summarize_exec_trace(
            mcu, exec_trace if exec_trace is not None else {"samples": []})

        filename = runtime_filename = mcu_filename = exec_filename = None
        if write_csv:
            output_dir = self._get_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = output_dir / (
                "motor-phase-trace-%s-%s.csv" % (stepper_name, name))
            self._write_step_trace_csv(filename, expanded_steps)
            if write_runtime_trace:
                runtime_filename = output_dir / (
                    "motor-phase-trace-%s-%s-runtime.csv" % (
                        stepper_name, name))
                self._write_runtime_flush_trace_csv(
                    runtime_filename, expanded_runtime_trace)
                mcu_filename = output_dir / (
                    "motor-phase-trace-%s-%s-mcu.csv" % (
                        stepper_name, name))
                self._write_mcu_runtime_stats_csv(
                    mcu_filename, mcu_runtime_stats)
            if (write_exec_trace and exec_trace is not None
                    and exec_trace["supported"]):
                exec_filename = output_dir / (
                    "motor-phase-trace-%s-%s-exec.csv" % (
                        stepper_name, name))
                self._write_exec_trace_csv(exec_filename, mcu, exec_trace)

        step_times = [row["step_time"] for row in expanded_steps]
        step_rate_hz = 0.0
        if len(step_times) > 1 and step_times[-1] > step_times[0]:
            step_rate_hz = ((len(step_times) - 1)
                            / (step_times[-1] - step_times[0]))
        gcmd.respond_info(
            "motor_phase_step_trace: stepper=%s direction=%s "
            "distance=%.3fmm speed=%.3fmm/s accel=%.3fmm/s^2 "
            "segments=%d steps=%d electrical_cycle=%.6fmm electrical_hz=%.3f "
            "step_dist=%.6fmm steps_per_cycle=%d mscnt_units_per_step=%.3f "
            "step_rate=%.3fHz runtime_flushes=%d active_runtime_flushes=%d "
            "runtime_steps_per_active_flush=%.3f runtime_phase_delta=%.3f "
            "exec_samples=%d exec_stride=%d exec_delta_steps=%.3f "
            "exec_delta_time_s=%.9f "
            "mcu_queue_msgs=%d mcu_load_next=%d mcu_timer_events=%d "
            "mcu_total_steps=%d mcu_max_chunk=%d "
            "returned_to_start=%d window=%.6f..%.6f "
            "step_window=%.6f..%.6f%s%s%s%s" % (
                stepper_name, raw_direction, distance, speed, accel,
                len(step_history), len(expanded_steps), electrical_cycle,
                electrical_hz, step_dist, cycle_steps, mscnt_units_per_step,
                step_rate_hz, runtime_summary["flush_count"],
                runtime_summary["active_flush_count"],
                runtime_summary["mean_steps_per_active_flush"],
                runtime_summary["mean_abs_phase_delta"],
                exec_summary["count"], exec_summary["stride"],
                exec_summary["mean_delta_steps"],
                exec_summary["mean_delta_time_s"],
                mcu_runtime_stats["queue_msgs"],
                mcu_runtime_stats["load_next"],
                mcu_runtime_stats["timer_events"],
                mcu_runtime_stats["total_steps"],
                mcu_runtime_stats["max_chunk"],
                returned_to_start, start_print_time,
                end_print_time, step_times[0], step_times[-1],
                "" if filename is None else " file=%s" % (filename,),
                "" if runtime_filename is None else " runtime_file=%s" % (
                    runtime_filename,),
                "" if exec_filename is None else " exec_file=%s" % (
                    exec_filename,),
                "" if mcu_filename is None else " mcu_file=%s" % (
                    mcu_filename,)))

    cmd_MOTOR_PHASE_CAPTURE_SYNC_help = (
        "Capture accelerometer data and real step history for the same move")
    def cmd_MOTOR_PHASE_CAPTURE_SYNC(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        distance = gcmd.get_float("DISTANCE", self.default_distance, above=0.)
        speed = gcmd.get_float("SPEED", self.default_speed, above=0.)
        accel = gcmd.get_float("ACCEL", self.default_accel, minval=0.)
        settle_time = gcmd.get_float("SETTLE_TIME", self.default_settle_time,
                                     minval=0.)
        write_csv = gcmd.get_int("WRITE_CSV", 1, minval=0, maxval=1)
        write_runtime_trace = gcmd.get_int(
            "WRITE_RUNTIME_TRACE", 1, minval=0, maxval=1)
        write_exec_trace = gcmd.get_int(
            "WRITE_EXEC_TRACE", 1, minval=0, maxval=1)
        write_correction_plan = gcmd.get_int(
            "WRITE_CORRECTION_PLAN", 1, minval=0, maxval=1)
        exec_trace_stride = gcmd.get_int(
            "EXEC_TRACE_STRIDE", 128, minval=1, maxval=65535)
        correction_coil_scale = gcmd.get_int(
            "CORRECTION_COIL_SCALE", 120, minval=1, maxval=255)
        correction_shift_scale_deg = gcmd.get_float(
            "CORRECTION_SHIFT_SCALE_DEG", 10.0)
        correction_plan_harmonics = self._parse_harmonics_arg(
            gcmd.get("CORRECTION_PLAN_HARMONICS", None),
            "CORRECTION_PLAN_HARMONICS")
        return_to_start = gcmd.get_int("RETURN_TO_START", 1, minval=0, maxval=1)
        raw_direction = gcmd.get("DIRECTION", "forward").lower()
        direction = VALID_STEP_DIRS.get(raw_direction)
        if direction is None:
            raise gcmd.error("Invalid DIRECTION '%s'" % (raw_direction,))
        name = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not name.replace("-", "").replace("_", "").isalnum():
            raise gcmd.error("Invalid NAME parameter")

        force_move, mcu_stepper = self._lookup_stepper(stepper_name)
        accel_chip = self._lookup_accel_chip()
        toolhead = self.printer.lookup_object("toolhead")
        _, _, direction_payload = self._resolve_optional_direction_payload(
            gcmd, speed)
        electrical_cycle, electrical_hz, step_dist, steps_per_cycle, (
            mscnt_units_per_step) = self._estimate_metrics(
                stepper_name, mcu_stepper, speed)
        cycle_steps = max(1, int(round(steps_per_cycle)))
        move_distance = direction * distance
        stage_info = self._prepare_safe_centered_stepper_move(
            gcmd, stepper_name, distance, direction)

        was_enable = force_move._force_enable(mcu_stepper)
        aclient = accel_chip.start_internal_client()
        start_print_time = end_print_time = None
        returned_to_start = False
        runtime_flush_events = []
        mcu_runtime_stats = None
        exec_trace = None
        try:
            toolhead.dwell(settle_time)
            start_print_time = toolhead.get_last_move_time()
            mcu_stepper.reset_runtime_stats()
            if write_exec_trace:
                mcu_stepper.reset_execution_trace(exec_trace_stride)
            mcu_stepper.begin_generate_steps_trace(start_print_time)
            force_move.manual_move(mcu_stepper, move_distance, speed, accel)
            end_print_time = toolhead.get_last_move_time()
            toolhead.dwell(settle_time)
        finally:
            try:
                aclient.finish_measurements()
            finally:
                try:
                    runtime_flush_events = mcu_stepper.end_generate_steps_trace()
                    mcu_runtime_stats = mcu_stepper.query_runtime_stats()
                    if write_exec_trace:
                        exec_trace = mcu_stepper.query_execution_trace()
                    if end_print_time is not None and return_to_start:
                        force_move.manual_move(
                            mcu_stepper, -move_distance, speed, accel)
                        toolhead.dwell(settle_time)
                        returned_to_start = True
                finally:
                    force_move._restore_enable(mcu_stepper, was_enable)

        samples = aclient.get_samples()
        sample_count = len(samples)
        if not sample_count:
            raise gcmd.error("No accelerometer samples captured")
        move_window = max(0., end_print_time - start_print_time)
        capture_duration = max(
            0., samples[-1].time - samples[0].time) if sample_count > 1 else 0.
        sample_rate = (
            (sample_count - 1) / capture_duration
            if capture_duration and sample_count > 1 else 0.)
        samples_per_cycle = (
            sample_rate / electrical_hz if electrical_hz > 0. else 0.)
        sample_density_over_move_window = (
            sample_count / move_window if move_window else 0.)

        mcu = mcu_stepper.get_mcu()
        start_clock = mcu.print_time_to_clock(start_print_time)
        end_clock = mcu.print_time_to_clock(end_print_time)
        step_history = self._get_step_history(mcu_stepper, start_clock, end_clock)
        if not step_history:
            raise gcmd.error("No step history captured in the requested window")
        expanded_steps = self._expand_step_history(
            mcu_stepper, step_history, cycle_steps)
        expanded_steps = [
            row for row in expanded_steps
            if start_clock <= row["step_clock"] <= end_clock]
        if not expanded_steps:
            raise gcmd.error("Unable to expand captured step history")
        runtime_flush_events = self._filter_runtime_flush_trace(
            runtime_flush_events, start_print_time, end_print_time)
        expanded_runtime_trace = self._expand_runtime_flush_trace(
            runtime_flush_events, cycle_steps, step_dist)
        runtime_summary = self._summarize_runtime_flush_trace(
            expanded_runtime_trace)
        exec_summary = self._summarize_exec_trace(
            mcu, exec_trace if exec_trace is not None else {"samples": []})
        step_times = [row["step_time"] for row in expanded_steps]
        step_rate_hz = 0.0
        if len(step_times) > 1 and step_times[-1] > step_times[0]:
            step_rate_hz = ((len(step_times) - 1)
                            / (step_times[-1] - step_times[0]))

        accel_filename = trace_filename = runtime_filename = mcu_filename = None
        exec_filename = correction_plan_filename = None
        correction_plan_rows = []
        correction_plan_summary = {
            "count": 0,
            "mean_abs_delta_coil": 0.0,
            "max_abs_delta_coil": 0,
            "mean_abs_shift_norm": 0.0,
        }
        if (write_correction_plan and direction_payload is not None
                and exec_trace is not None and exec_trace.get("supported")):
            correction_plan_rows = self._build_exec_correction_plan(
                expanded_steps, exec_trace, direction_payload,
                correction_coil_scale, correction_shift_scale_deg,
                correction_plan_harmonics)
            correction_plan_summary = self._summarize_exec_correction_plan(
                correction_plan_rows)
        if write_csv:
            output_dir = self._get_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            accel_filename = output_dir / (
                "motor-phase-sync-%s-%s-accel.csv" % (stepper_name, name))
            trace_filename = output_dir / (
                "motor-phase-sync-%s-%s-trace.csv" % (stepper_name, name))
            aclient.write_to_file(str(accel_filename))
            self._write_step_trace_csv(trace_filename, expanded_steps)
            if write_runtime_trace:
                runtime_filename = output_dir / (
                    "motor-phase-sync-%s-%s-runtime.csv" % (
                        stepper_name, name))
                self._write_runtime_flush_trace_csv(
                    runtime_filename, expanded_runtime_trace)
                mcu_filename = output_dir / (
                    "motor-phase-sync-%s-%s-mcu.csv" % (
                        stepper_name, name))
                self._write_mcu_runtime_stats_csv(
                    mcu_filename, mcu_runtime_stats)
            if (write_exec_trace and exec_trace is not None
                    and exec_trace["supported"]):
                exec_filename = output_dir / (
                    "motor-phase-sync-%s-%s-exec.csv" % (
                        stepper_name, name))
                self._write_exec_trace_csv(exec_filename, mcu, exec_trace)
            if correction_plan_rows:
                correction_plan_filename = output_dir / (
                    "motor-phase-sync-%s-%s-plan.csv" % (
                        stepper_name, name))
                self._write_exec_correction_plan_csv(
                    correction_plan_filename, correction_plan_rows)

        gcmd.respond_info(
            "motor_phase_capture_sync: stepper=%s direction=%s "
            "distance=%.3fmm speed=%.3fmm/s accel=%.3fmm/s^2 "
            "center=(%.3f,%.3f) samples=%d sample_rate=%.3fHz "
            "samples_per_cycle=%.3f move_window_sample_density=%.3fHz "
            "steps=%d segments=%d step_rate=%.3fHz "
            "runtime_flushes=%d active_runtime_flushes=%d "
            "runtime_steps_per_active_flush=%.3f runtime_phase_delta=%.3f "
            "exec_samples=%d exec_stride=%d exec_delta_steps=%.3f "
            "exec_delta_time_s=%.9f "
            "plan_samples=%d plan_mean_abs_delta_coil=%.3f "
            "plan_max_abs_delta_coil=%d plan_mean_abs_shift_norm=%.6f "
            "plan_harmonics=%s "
            "mcu_queue_msgs=%d mcu_load_next=%d mcu_timer_events=%d "
            "mcu_total_steps=%d mcu_max_chunk=%d "
            "step_dist=%.6fmm steps_per_cycle=%d mscnt_units_per_step=%.3f "
            "returned_to_start=%d accel_window=%.6f..%.6f "
            "step_window=%.6f..%.6f%s%s%s%s%s%s" % (
                stepper_name, raw_direction, distance, speed, accel,
                stage_info["center_x"], stage_info["center_y"],
                sample_count, sample_rate, samples_per_cycle,
                sample_density_over_move_window, len(expanded_steps),
                len(step_history), step_rate_hz,
                runtime_summary["flush_count"],
                runtime_summary["active_flush_count"],
                runtime_summary["mean_steps_per_active_flush"],
                runtime_summary["mean_abs_phase_delta"],
                exec_summary["count"], exec_summary["stride"],
                exec_summary["mean_delta_steps"],
                exec_summary["mean_delta_time_s"],
                correction_plan_summary["count"],
                correction_plan_summary["mean_abs_delta_coil"],
                correction_plan_summary["max_abs_delta_coil"],
                correction_plan_summary["mean_abs_shift_norm"],
                ("none" if correction_plan_harmonics is None else
                 ",".join(str(value) for value in correction_plan_harmonics)),
                mcu_runtime_stats["queue_msgs"],
                mcu_runtime_stats["load_next"],
                mcu_runtime_stats["timer_events"],
                mcu_runtime_stats["total_steps"],
                mcu_runtime_stats["max_chunk"],
                step_dist, cycle_steps,
                mscnt_units_per_step, returned_to_start,
                start_print_time, end_print_time,
                step_times[0], step_times[-1],
                "" if accel_filename is None else " accel_file=%s" % (
                    accel_filename,),
                "" if trace_filename is None else " trace_file=%s" % (
                    trace_filename,),
                "" if runtime_filename is None else " runtime_file=%s" % (
                    runtime_filename,),
                "" if exec_filename is None else " exec_file=%s" % (
                    exec_filename,),
                "" if correction_plan_filename is None else " plan_file=%s" % (
                    correction_plan_filename,),
                "" if mcu_filename is None else " mcu_file=%s" % (
                    mcu_filename,)))

    cmd_MOTOR_PHASE_SWEEP_help = (
        "Run a bounded multi-speed motor phase measurement sweep")
    def cmd_MOTOR_PHASE_SWEEP(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        distance = gcmd.get_float("DISTANCE", self.default_distance, above=0.)
        accel = gcmd.get_float("ACCEL", self.default_accel, minval=0.)
        settle_time = gcmd.get_float("SETTLE_TIME", self.default_settle_time,
                                     minval=0.)
        raw_direction, sweep_directions = self._parse_sweep_directions(gcmd)
        speeds = self._parse_speed_list(gcmd)
        travel_speed = gcmd.get_float("TRAVEL_SPEED", 150., above=0.)
        safe_z = gcmd.get_float("SAFE_Z", 10., minval=0.)
        margin = gcmd.get_float("MARGIN", 5., minval=0.)
        name_prefix = gcmd.get("NAME_PREFIX", time.strftime("%Y%m%d_%H%M%S"))
        if not name_prefix.replace("-", "").replace("_", "").isalnum():
            raise gcmd.error("Invalid NAME_PREFIX parameter")

        xy_homed, homed_axes = self._check_xy_homed()
        if not xy_homed:
            self.gcode.run_script_from_command("G28 X Y")
            xy_homed, homed_axes = self._check_xy_homed()
        if not xy_homed:
            raise gcmd.error("XY must be homed before MOTOR_PHASE_SWEEP")

        axis_min, axis_max = self._get_axis_limits()
        center_x = gcmd.get_float(
            "CENTER_X", 0.5 * (axis_min.x + axis_max.x))
        center_y = gcmd.get_float(
            "CENTER_Y", 0.5 * (axis_min.y + axis_max.y))
        self._validate_stage_position_for_directions(
            stepper_name, center_x, center_y, distance, sweep_directions,
            margin)
        self._stage_xy_position(center_x, center_y, travel_speed, safe_z)

        gcmd.respond_info(
            "motor_phase_sweep: stepper=%s speeds=%s direction=%s "
            "distance=%.3fmm center=(%.3f,%.3f) homed_axes=%s" % (
                stepper_name,
                ",".join("%.3f" % (speed,) for speed in speeds),
                raw_direction, distance, center_x, center_y, homed_axes))
        run_count = len(speeds) * len(sweep_directions)
        run_index = 0
        for sweep_direction in sweep_directions:
            for speed in speeds:
                run_index += 1
                measure_name = "%s_%02d_%s_%s" % (
                    name_prefix, run_index, sweep_direction,
                    str(speed).replace(".", "p"))
                gcmd.respond_info(
                    "motor_phase_sweep: run=%d/%d direction=%s speed=%.3f" % (
                        run_index, run_count, sweep_direction, speed))
                self.gcode.run_script_from_command(
                    "MOTOR_PHASE_MEASURE "
                    "STEPPER=%s SPEED=%.6f DISTANCE=%.6f ACCEL=%.6f "
                    "SETTLE_TIME=%.6f DIRECTION=%s NAME=%s" % (
                        stepper_name, speed, distance, accel,
                        settle_time, sweep_direction, measure_name))

    cmd_MOTOR_PHASE_DIRECT_SAMPLE_help = (
        "Apply one runtime-payload phase sample to a TMC2240 in direct mode")
    def cmd_MOTOR_PHASE_DIRECT_SAMPLE(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        _, direction, direction_payload = self._resolve_direction_payload(gcmd)
        phase_index = gcmd.get_int("PHASE_INDEX", minval=0)
        shift_scale_deg = gcmd.get_float("SHIFT_SCALE_DEG", 10., minval=0.)
        coil_scale = gcmd.get_int("COIL_SCALE", 180, minval=1, maxval=255)
        dwell = gcmd.get_float("DWELL", 0.2, minval=0.)
        resolved = self._resolve_direct_coils(
            direction_payload, phase_index, shift_scale_deg, coil_scale)
        phase_index = resolved["phase_index"]
        phase_points = resolved["phase_points"]
        phase_offset_q15 = resolved["phase_offset_q15"]
        normalized_shift = resolved["normalized_shift"]
        coil_a = resolved["coil_a"]
        coil_b = resolved["coil_b"]
        representation = resolved["representation"]

        def apply_sample(driver, toolhead):
            print_time = toolhead.get_last_move_time()
            self._set_tmc_field(driver, "coil_a", coil_a, print_time)
            self._set_tmc_field(driver, "coil_b", coil_b, print_time)
            toolhead.dwell(dwell)

        self._with_direct_mode(stepper_name, apply_sample)
        gcmd.respond_info(
            "motor_phase_direct_sample: stepper=%s direction=%s "
            "phase_index=%d phase_points=%d representation=%s "
            "shift_q15=%d shift_norm=%.6f shift_scale_deg=%.3f "
            "coil_scale=%d coil_a=%d coil_b=%d" % (
                stepper_name, direction, phase_index, phase_points,
                representation, phase_offset_q15, normalized_shift,
                shift_scale_deg,
                coil_scale, coil_a, coil_b))

    cmd_MOTOR_PHASE_DIRECT_SCAN_help = (
        "Apply a slow direct-mode scan from a runtime-payload prototype")
    def cmd_MOTOR_PHASE_DIRECT_SCAN(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        _, direction, direction_payload = self._resolve_direction_payload(gcmd)
        start = gcmd.get_int("START", 0, minval=0)
        count = gcmd.get_int("COUNT", 32, minval=1)
        stride = gcmd.get_int("STRIDE", 4, minval=1)
        shift_scale_deg = gcmd.get_float("SHIFT_SCALE_DEG", 10., minval=0.)
        coil_scale = gcmd.get_int("COIL_SCALE", 180, minval=1, maxval=255)
        dwell = gcmd.get_float("DWELL", 0.05, minval=0.)
        phase_points = int(direction_payload["phase_points"])
        indices = [
            (start + index * stride) % phase_points for index in range(count)
        ]
        representations = []

        def apply_scan(driver, toolhead):
            for phase_index in indices:
                resolved = self._resolve_direct_coils(
                    direction_payload, phase_index, shift_scale_deg,
                    coil_scale)
                coil_a = resolved["coil_a"]
                coil_b = resolved["coil_b"]
                representations.append(resolved["representation"])
                print_time = toolhead.get_last_move_time()
                self._set_tmc_field(driver, "coil_a", coil_a, print_time)
                self._set_tmc_field(driver, "coil_b", coil_b, print_time)
                toolhead.dwell(dwell)

        self._with_direct_mode(stepper_name, apply_scan)
        representation = (
            sorted(set(representations))[0] if representations
            else "unknown")
        gcmd.respond_info(
            "motor_phase_direct_scan: stepper=%s direction=%s start=%d "
            "count=%d stride=%d phase_points=%d representation=%s "
            "shift_scale_deg=%.3f coil_scale=%d dwell=%.3fs" % (
                stepper_name, direction, start, count, stride, phase_points,
                representation, shift_scale_deg, coil_scale, dwell))

    cmd_MOTOR_PHASE_DIRECT_MEASURE_help = (
        "Capture LIS2DW data during an open-loop direct-mode A/B move")
    def cmd_MOTOR_PHASE_DIRECT_MEASURE(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        profile_name = gcmd.get("PROFILE")
        variant = VALID_DIRECT_MEASURE_VARIANTS.get(
            gcmd.get("VARIANT", "profile").lower())
        if variant is None:
            raise gcmd.error("Invalid VARIANT parameter")
        payload_item = self._get_loaded_payload(profile_name)
        direction_name = gcmd.get("DIRECTION", "forward").lower()
        if direction_name not in ("forward", "backward"):
            raise gcmd.error("Invalid DIRECTION parameter")
        direction_payload = payload_item.get("directions", {}).get(direction_name)
        if direction_payload is None:
            raise gcmd.error(
                "Loaded profile '%s' has no direction '%s'" % (
                    profile_name, direction_name))
        return_direction = self._reverse_direction_name(direction_name)
        return_payload = payload_item.get("directions", {}).get(return_direction)
        if return_payload is None:
            raise gcmd.error(
                "Loaded profile '%s' has no direction '%s'" % (
                    profile_name, return_direction))

        distance = gcmd.get_float("DISTANCE", self.default_distance, above=0.)
        speed = gcmd.get_float("SPEED", float(payload_item["speed_mm_s"]), above=0.)
        phase_stride = gcmd.get_int("PHASE_STRIDE", 64, minval=1)
        coil_scale = gcmd.get_int("COIL_SCALE", 120, minval=1, maxval=255)
        settle_time = gcmd.get_float("SETTLE_TIME", self.default_settle_time,
                                     minval=0.)
        travel_speed = gcmd.get_float("TRAVEL_SPEED", 150., above=0.)
        safe_z = gcmd.get_float("SAFE_Z", 10., minval=0.)
        margin = gcmd.get_float("MARGIN", 5., minval=0.)
        write_csv = gcmd.get_int("WRITE_CSV", 1, minval=0, maxval=1)
        name = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not name.replace("-", "").replace("_", "").isalnum():
            raise gcmd.error("Invalid NAME parameter")

        _, mcu_stepper = self._lookup_stepper(stepper_name)
        electrical_cycle, electrical_hz, _, _, _ = self._estimate_metrics(
            stepper_name, mcu_stepper, speed)

        xy_homed, homed_axes = self._check_xy_homed()
        if not xy_homed:
            self.gcode.run_script_from_command("G28 X Y")
            xy_homed, homed_axes = self._check_xy_homed()
        if not xy_homed:
            raise gcmd.error("XY must be homed before MOTOR_PHASE_DIRECT_MEASURE")

        axis_min, axis_max = self._get_axis_limits()
        center_x = gcmd.get_float(
            "CENTER_X", 0.5 * (axis_min.x + axis_max.x))
        center_y = gcmd.get_float(
            "CENTER_Y", 0.5 * (axis_min.y + axis_max.y))
        self._validate_stage_position(
            stepper_name, center_x, center_y, distance,
            VALID_STEP_DIRS[direction_name], margin)
        self._stage_xy_position(center_x, center_y, travel_speed, safe_z)

        accel_chip = self._lookup_accel_chip()
        toolhead = self.printer.lookup_object("toolhead")
        aclient = accel_chip.start_internal_client()
        run_info = {}
        returned_to_start = False

        def apply_direct_measure(driver, toolhead):
            nonlocal run_info, returned_to_start
            toolhead.dwell(settle_time)
            run_info = self._run_direct_profile_path(
                driver, toolhead, direction_payload, direction_name,
                distance, speed, phase_stride, coil_scale, variant,
                electrical_cycle)
            toolhead.dwell(settle_time)
            self._run_direct_profile_path(
                driver, toolhead, return_payload, return_direction,
                run_info["commanded_distance_mm"], speed, phase_stride,
                coil_scale, variant, electrical_cycle)
            toolhead.dwell(settle_time)
            returned_to_start = True

        try:
            self._with_direct_mode(stepper_name, apply_direct_measure)
        finally:
            aclient.finish_measurements()

        samples = aclient.get_samples()
        sample_count = len(samples)
        if not sample_count:
            raise gcmd.error("No accelerometer samples captured")
        capture_duration = (
            max(0., samples[-1].time - samples[0].time)
            if sample_count > 1 else 0.)
        sample_rate = (
            (sample_count - 1) / capture_duration
            if capture_duration and sample_count > 1 else 0.)
        samples_per_cycle = (
            sample_rate / electrical_hz if electrical_hz > 0. else 0.)

        filename = None
        if write_csv:
            output_dir = self._get_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = output_dir / (
                "motor-phase-direct-%s-%s-%s-%s.csv" % (
                    stepper_name, profile_name, variant, name))
            aclient.write_to_file(str(filename))

        gcmd.respond_info(
            "motor_phase_direct_measure: stepper=%s profile=%s variant=%s "
            "direction=%s distance=%.3fmm speed=%.3fmm/s "
            "representation=%s phase_points=%d phase_stride=%d "
            "update_distance=%.6fmm update_period=%.6fs commanded_distance=%.6fmm "
            "samples=%d sample_rate=%.3fHz electrical_hz=%.3f "
            "samples_per_cycle=%.3f returned_to_start=%d homed_axes=%s%s" % (
                stepper_name, profile_name, variant, direction_name,
                distance, speed, run_info["representation"],
                run_info["phase_points"], run_info["phase_stride"],
                run_info["update_distance_mm"], run_info["update_period_s"],
                run_info["commanded_distance_mm"], sample_count, sample_rate,
                electrical_hz, samples_per_cycle, returned_to_start,
                homed_axes,
                "" if filename is None else " file=%s" % (filename,)))

    cmd_MOTOR_PHASE_DIRECT_SCHEDULED_MEASURE_help = (
        "Capture LIS2DW data during a scheduled direct-mode A/B move")
    def cmd_MOTOR_PHASE_DIRECT_SCHEDULED_MEASURE(self, gcmd):
        stepper_name = gcmd.get("STEPPER")
        profile_name = gcmd.get("PROFILE")
        variant = VALID_DIRECT_MEASURE_VARIANTS.get(
            gcmd.get("VARIANT", "profile").lower())
        if variant is None:
            raise gcmd.error("Invalid VARIANT parameter")
        direct_harmonics = self._parse_harmonics_arg(
            gcmd.get("HARMONICS", None), "HARMONICS")
        payload_item = self._get_loaded_payload(profile_name)
        direction_name = gcmd.get("DIRECTION", "forward").lower()
        if direction_name not in ("forward", "backward"):
            raise gcmd.error("Invalid DIRECTION parameter")
        direction_payload = payload_item.get("directions", {}).get(direction_name)
        if direction_payload is None:
            raise gcmd.error(
                "Loaded profile '%s' has no direction '%s'" % (
                    profile_name, direction_name))
        return_direction = self._reverse_direction_name(direction_name)
        return_payload = payload_item.get("directions", {}).get(return_direction)
        if return_payload is None:
            raise gcmd.error(
                "Loaded profile '%s' has no direction '%s'" % (
                    profile_name, return_direction))

        distance = gcmd.get_float("DISTANCE", self.default_distance, above=0.)
        speed = gcmd.get_float("SPEED", float(payload_item["speed_mm_s"]), above=0.)
        phase_stride = gcmd.get_int("PHASE_STRIDE", 16, minval=1)
        coil_scale = gcmd.get_int("COIL_SCALE", 120, minval=1, maxval=255)
        settle_time = gcmd.get_float("SETTLE_TIME", self.default_settle_time,
                                     minval=0.)
        travel_speed = gcmd.get_float("TRAVEL_SPEED", 150., above=0.)
        safe_z = gcmd.get_float("SAFE_Z", 10., minval=0.)
        margin = gcmd.get_float("MARGIN", 5., minval=0.)
        write_csv = gcmd.get_int("WRITE_CSV", 1, minval=0, maxval=1)
        name = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
        if not name.replace("-", "").replace("_", "").isalnum():
            raise gcmd.error("Invalid NAME parameter")

        _, mcu_stepper = self._lookup_stepper(stepper_name)
        electrical_cycle, electrical_hz, _, _, _ = self._estimate_metrics(
            stepper_name, mcu_stepper, speed)

        xy_homed, homed_axes = self._check_xy_homed()
        if not xy_homed:
            self.gcode.run_script_from_command("G28 X Y")
            xy_homed, homed_axes = self._check_xy_homed()
        if not xy_homed:
            raise gcmd.error(
                "XY must be homed before MOTOR_PHASE_DIRECT_SCHEDULED_MEASURE")

        axis_min, axis_max = self._get_axis_limits()
        center_x = gcmd.get_float(
            "CENTER_X", 0.5 * (axis_min.x + axis_max.x))
        center_y = gcmd.get_float(
            "CENTER_Y", 0.5 * (axis_min.y + axis_max.y))
        self._validate_stage_position(
            stepper_name, center_x, center_y, distance,
            VALID_STEP_DIRS[direction_name], margin)
        self._stage_xy_position(center_x, center_y, travel_speed, safe_z)

        accel_chip = self._lookup_accel_chip()
        toolhead = self.printer.lookup_object("toolhead")
        aclient = accel_chip.start_internal_client()
        run_info = {}
        returned_to_start = False

        def apply_direct_measure(driver, toolhead):
            nonlocal run_info, returned_to_start
            toolhead.dwell(settle_time)
            run_info = self._run_scheduled_direct_profile_path(
                driver, toolhead, direction_payload, direction_name,
                distance, speed, phase_stride, coil_scale, variant,
                electrical_cycle, harmonics=direct_harmonics)
            toolhead.dwell(settle_time)
            self._run_scheduled_direct_profile_path(
                driver, toolhead, return_payload, return_direction,
                run_info["commanded_distance_mm"], speed, phase_stride,
                coil_scale, variant, electrical_cycle,
                harmonics=direct_harmonics)
            toolhead.dwell(settle_time)
            returned_to_start = True

        try:
            self._with_direct_mode(stepper_name, apply_direct_measure)
        finally:
            aclient.finish_measurements()

        samples = aclient.get_samples()
        sample_count = len(samples)
        if not sample_count:
            raise gcmd.error("No accelerometer samples captured")
        capture_duration = (
            max(0., samples[-1].time - samples[0].time)
            if sample_count > 1 else 0.)
        sample_rate = (
            (sample_count - 1) / capture_duration
            if capture_duration and sample_count > 1 else 0.)
        samples_per_cycle = (
            sample_rate / electrical_hz if electrical_hz > 0. else 0.)

        filename = None
        if write_csv:
            output_dir = self._get_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = output_dir / (
                "motor-phase-direct-scheduled-%s-%s-%s-%s.csv" % (
                    stepper_name, profile_name, variant, name))
            aclient.write_to_file(str(filename))

        gcmd.respond_info(
            "motor_phase_direct_scheduled_measure: stepper=%s profile=%s "
            "variant=%s direction=%s distance=%.3fmm speed=%.3fmm/s "
            "representation=%s phase_points=%d phase_stride=%d "
            "update_distance=%.6fmm update_period=%.6fs "
            "commanded_distance=%.6fmm harmonics=%s samples=%d "
            "sample_rate=%.3fHz electrical_hz=%.3f samples_per_cycle=%.3f "
            "returned_to_start=%d homed_axes=%s%s" % (
                stepper_name, profile_name, variant, direction_name,
                distance, speed, run_info["representation"],
                run_info["phase_points"], run_info["phase_stride"],
                run_info["update_distance_mm"], run_info["update_period_s"],
                run_info["commanded_distance_mm"],
                ("none" if direct_harmonics is None else
                 ",".join(str(value) for value in direct_harmonics)),
                sample_count, sample_rate, electrical_hz, samples_per_cycle,
                returned_to_start, homed_axes,
                "" if filename is None else " file=%s" % (filename,)))

    cmd_MOTOR_PHASE_LOAD_PAYLOAD_help = (
        "Load a runtime-payload prototype into a named in-memory profile")
    def cmd_MOTOR_PHASE_LOAD_PAYLOAD(self, gcmd):
        profile_name = gcmd.get("PROFILE")
        self._validate_profile_name(profile_name, gcmd)
        payload_path = gcmd.get("PAYLOAD")
        speed_mm_s = gcmd.get_float("SPEED_MM_S", above=0.)
        payload_item, _ = self._load_runtime_payload(
            payload_path, speed_mm_s, "forward")
        self._store_loaded_payload(profile_name, payload_item)
        directions = sorted(payload_item.get("directions", {}).keys())
        gcmd.respond_info(
            "motor_phase_load_payload: profile=%s speed=%.3f "
            "selected_axis=%s directions=%s" % (
                profile_name, float(payload_item["speed_mm_s"]),
                payload_item.get("selected_axis", "unknown"),
                ",".join(directions)))

    cmd_MOTOR_PHASE_STORE_PAYLOAD_help = (
        "Import a runtime-payload prototype into managed persistent profile storage")
    def cmd_MOTOR_PHASE_STORE_PAYLOAD(self, gcmd):
        profile_name = gcmd.get("PROFILE")
        self._validate_profile_name(profile_name, gcmd)
        payload_path = gcmd.get("PAYLOAD")
        speed_mm_s = gcmd.get_float("SPEED_MM_S", above=0.)
        payload_item, _ = self._load_runtime_payload(
            payload_path, speed_mm_s, "forward")
        stored_path = self._write_profile_file(profile_name, payload_item)
        self._store_loaded_payload(profile_name, payload_item)
        directions = sorted(payload_item.get("directions", {}).keys())
        gcmd.respond_info(
            "motor_phase_store_payload: profile=%s path=%s speed=%.3f "
            "selected_axis=%s directions=%s" % (
                profile_name, stored_path, float(payload_item["speed_mm_s"]),
                payload_item.get("selected_axis", "unknown"),
                ",".join(directions)))

    cmd_MOTOR_PHASE_LOAD_PROFILE_help = (
        "Load a managed persistent motor-phase profile into memory")
    def cmd_MOTOR_PHASE_LOAD_PROFILE(self, gcmd):
        profile_name = gcmd.get("PROFILE")
        self._validate_profile_name(profile_name, gcmd)
        profile_path, payload_item = self._read_profile_file(profile_name)
        self._store_loaded_payload(profile_name, payload_item)
        directions = sorted(payload_item.get("directions", {}).keys())
        gcmd.respond_info(
            "motor_phase_load_profile: profile=%s path=%s speed=%.3f "
            "selected_axis=%s directions=%s" % (
                profile_name, profile_path, float(payload_item["speed_mm_s"]),
                payload_item.get("selected_axis", "unknown"),
                ",".join(directions)))

    cmd_MOTOR_PHASE_LIST_PROFILES_help = (
        "List managed persistent motor-phase profiles")
    def cmd_MOTOR_PHASE_LIST_PROFILES(self, gcmd):
        profile_paths = self._list_profile_paths()
        if not profile_paths:
            gcmd.respond_info(
                "motor_phase_list_profiles: profile_dir=%s count=0" % (
                    self._get_profile_dir(),))
            return
        parts = []
        for profile_path in profile_paths:
            profile_name = profile_path.stem
            loaded = 1 if profile_name in self.loaded_payloads else 0
            autoload = 1 if profile_name in self.autoload_profiles else 0
            try:
                _, payload_item = self._read_profile_file(profile_name)
                speed_mm_s = float(payload_item.get("speed_mm_s", 0.0))
                selected_axis = payload_item.get("selected_axis", "unknown")
            except Exception as e:
                logging.exception("Failed to inspect motor phase profile %s",
                                  profile_path)
                speed_mm_s = 0.0
                selected_axis = "error:%s" % (str(e),)
            parts.append(
                "%s(speed=%.3f,axis=%s,loaded=%d,autoload=%d)" % (
                    profile_name, speed_mm_s, selected_axis, loaded, autoload))
        gcmd.respond_info(
            "motor_phase_list_profiles: profile_dir=%s count=%d %s" % (
                self._get_profile_dir(), len(profile_paths), " ".join(parts)))

    cmd_MOTOR_PHASE_EXEC_RUN_help = (
        "Run baseline-only MCU phase executor on one stepper in direct mode. "
        "Motor position becomes unknown after this command; XY are re-homed "
        "automatically when done.")
    def cmd_MOTOR_PHASE_EXEC_RUN(self, gcmd):
        stepper_name = gcmd.get("STEPPER", self._exec_stepper)
        if stepper_name != self._exec_stepper:
            raise gcmd.error(
                "MOTOR_PHASE_EXEC_RUN only supports the configured "
                "exec_stepper '%s'" % (self._exec_stepper,))
        speed      = gcmd.get_float("SPEED", 30., above=0.)
        distance   = gcmd.get_float("DISTANCE", 40., above=0.)
        coil_scale = gcmd.get_int("COIL_SCALE", 100, minval=1, maxval=255)
        phase_stride = gcmd.get_int("PHASE_STRIDE", 16, minval=1, maxval=63)
        # Derive timer interval from MCU frequency and desired update cadence.
        # One normal step advances MSCNT by 4 on the U1. Running the executor at
        # every step floods the SPI/task path; stride over multiple steps keeps
        # the same nominal electrical velocity while reducing MCU SPI traffic.
        force_move, mcu_stepper = self._lookup_stepper(stepper_name)
        driver = self._lookup_tmc_driver(stepper_name)
        step_dist    = mcu_stepper.get_step_dist()
        steps_per_sec = speed / step_dist
        updates_per_sec = steps_per_sec / phase_stride
        if updates_per_sec <= 0.:
            raise gcmd.error("Invalid PHASE_STRIDE for executor update rate")
        mcu          = self._exec.get_mcu()
        mcu_freq     = mcu._mcu_freq
        interval     = max(1, int(mcu_freq / updates_per_sec))
        phase_advance = 4 * phase_stride
        duration     = distance / speed
        # Home and stage to XY centre
        travel_speed = gcmd.get_float("TRAVEL_SPEED", 150., above=0.)
        safe_z       = gcmd.get_float("SAFE_Z", 10., minval=0.)
        xy_homed, _ = self._check_xy_homed()
        if not xy_homed:
            self.gcode.run_script_from_command("G28 X Y")
        axis_min, axis_max = self._get_axis_limits()
        center_x = (axis_min.x + axis_max.x) / 2.
        center_y = (axis_min.y + axis_max.y) / 2.
        self._stage_xy_position(center_x, center_y, travel_speed, safe_z)
        toolhead = self.printer.lookup_object("toolhead")
        gcmd.respond_info(
            "motor_phase_exec_run: stepper=%s speed=%.1f mm/s "
            "distance=%.1f mm coil_scale=%d phase_stride=%d "
            "interval=%d ticks duration=%.3f s"
            % (stepper_name, speed, distance, coil_scale, phase_stride,
               interval, duration))
        toolhead.wait_moves()
        was_enable = force_move._force_enable(mcu_stepper)
        original_driver_state = None
        current_phase = 0
        try:
            original_driver_state, current_phase = self._prepare_executor_driver(
                driver, toolhead, coil_scale)
            self._exec.start(interval, current_phase, coil_scale, phase_advance)
            toolhead.dwell(duration)
            toolhead.wait_moves()
            self._exec.stop()
        finally:
            if original_driver_state is not None:
                self._restore_executor_driver(
                    driver, toolhead, original_driver_state)
            force_move._restore_enable(mcu_stepper, was_enable)
        # After direct-mode motion the stepper position is unknown; re-home.
        gcmd.respond_info(
            "motor_phase_exec_run: complete start_phase=%d — re-homing XY "
            "(position unknown after direct-mode run)" % (current_phase,))
        self.gcode.run_script_from_command("G28 X Y")

    cmd_MOTOR_PHASE_SHOW_PAYLOAD_help = (
        "Show metadata for a loaded runtime-payload profile")
    def cmd_MOTOR_PHASE_SHOW_PAYLOAD(self, gcmd):
        profile_name = gcmd.get("PROFILE")
        payload_item = self._get_loaded_payload(profile_name)
        directions = payload_item.get("directions", {})
        parts = []
        for direction_name in sorted(directions):
            direction_payload = directions[direction_name]
            direct_profile = direction_payload.get("prototype_direct_profile")
            parts.append(
                "%s(points=%d,harmonics=%d,%s)" % (
                    direction_name,
                    int(direction_payload["phase_points"]),
                    int(direction_payload["harmonic_count"]),
                    "direct_profile=1" if direct_profile is not None
                    else "direct_profile=0"))
        gcmd.respond_info(
            "motor_phase_show_payload: profile=%s speed=%.3f "
            "selected_axis=%s %s" % (
                profile_name, float(payload_item["speed_mm_s"]),
                payload_item.get("selected_axis", "unknown"),
                " ".join(parts)))


class MotorPhaseExec:
    """MCU-side phase executor helper.

    Sets up the MCU SPI device and executor OID at config time, then exposes
    start() / stop() methods for use during a MOTOR_PHASE_EXEC_RUN command.
    """
    def __init__(self, mcu_spi):
        self._mcu_spi = mcu_spi
        mcu = mcu_spi.get_mcu()
        self._oid = mcu.create_oid()
        self._start_cmd = None
        self._stop_cmd = None
        mcu.register_config_callback(self._build_config)

    def _build_config(self):
        mcu = self._mcu_spi.get_mcu()
        mcu.add_config_cmd(
            "config_motor_phase_exec oid=%d spi_oid=%d"
            % (self._oid, self._mcu_spi.get_oid()))
        cq = self._mcu_spi.get_command_queue()
        self._start_cmd = mcu.lookup_command(
            "motor_phase_exec_start oid=%c interval=%u phase_index=%u"
            " coil_scale=%c phase_advance=%c",
            cq=cq)
        self._stop_cmd = mcu.lookup_command(
            "motor_phase_exec_stop oid=%c", cq=cq)

    def get_mcu(self):
        return self._mcu_spi.get_mcu()

    def start(self, interval, phase_index, coil_scale, phase_advance):
        self._start_cmd.send(
            [self._oid, interval, phase_index, coil_scale, phase_advance])

    def stop(self):
        self._stop_cmd.send([self._oid])


def load_config(config):
    return MotorPhaseCalibration(config)
