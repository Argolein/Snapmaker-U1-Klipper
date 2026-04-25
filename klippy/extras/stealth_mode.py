# Runtime stealth mode for Snapmaker U1
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class PrinterStealthMode:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.velocity_limit = config.getfloat('velocity', 120., above=0.)
        self.accel_limit = config.getfloat('accel', 2500., above=0.)
        self.enabled = False
        self.toolhead = None
        self.driver_restore = {}
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("SET_STEALTH_MODE", self.cmd_SET_STEALTH_MODE,
                               desc=self.cmd_SET_STEALTH_MODE_help)

    def _handle_connect(self):
        self.toolhead = self.printer.lookup_object("toolhead")
        self.toolhead.register_stealth_mode(self)
        self._register_xy_drivers()

    def _register_xy_drivers(self):
        for name in ("tmc2240 stepper_x", "tmc2240 stepper_y"):
            driver = self.printer.lookup_object(name, None)
            if driver is None:
                raise self.printer.config_error(
                    "[stealth_mode] requires %s" % (name,))
            reg_name = driver.fields.lookup_register("en_pwm_mode", None)
            if reg_name is None:
                raise self.printer.config_error(
                    "%s does not support en_pwm_mode" % (name,))
            self.driver_restore[name] = {
                'driver': driver,
                'reg_name': reg_name,
                'normal_value': driver.fields.get_field("en_pwm_mode"),
            }

    def is_enabled(self):
        return self.enabled

    def get_velocity_limit(self):
        return self.velocity_limit

    def get_accel_limit(self):
        return self.accel_limit

    def get_status(self, eventtime=None):
        return {
            'enabled': self.enabled,
            'mode': 'stealth' if self.enabled else 'normal',
            'velocity_limit': self.velocity_limit,
            'accel_limit': self.accel_limit,
            'driver_stealthchop': self.enabled,
        }

    def _apply_pressure_advance_profiles(self):
        for extruder in self.printer.lookup_object('extruder_list', []):
            if extruder.extruder_stepper is None:
                continue
            extruder.extruder_stepper.apply_stealth_mode(self.enabled)

    def _set_driver_field(self, driver, reg_name, field_name, value):
        reg_value = driver.fields.set_field(field_name, value)
        print_time = self.toolhead.get_last_move_time()
        driver.mcu_tmc.set_register(reg_name, reg_value, print_time)

    def _apply_driver_mode(self, enabled):
        for restore in self.driver_restore.values():
            driver = restore['driver']
            reg_name = restore['reg_name']
            if enabled:
                restore['normal_value'] = driver.fields.get_field(
                    "en_pwm_mode")
                self._set_driver_field(driver, reg_name, "en_pwm_mode", 1)
            else:
                self._set_driver_field(driver, reg_name, "en_pwm_mode",
                                       restore['normal_value'])

    def set_mode(self, enabled):
        enabled = bool(enabled)
        if self.toolhead is None:
            raise self.printer.command_error(
                "Stealth mode is not ready yet")
        if self.enabled == enabled:
            return
        self.toolhead.wait_moves()
        if enabled:
            self.enabled = True
            self.toolhead.refresh_stealth_limits()
            self._apply_driver_mode(True)
            self._apply_pressure_advance_profiles()
        else:
            self._apply_driver_mode(False)
            self.enabled = False
            self.toolhead.refresh_stealth_limits()
            self._apply_pressure_advance_profiles()

    cmd_SET_STEALTH_MODE_help = (
        "Set or report the runtime motion stealth mode")
    def cmd_SET_STEALTH_MODE(self, gcmd):
        mode = gcmd.get('MODE', None)
        if mode is None:
            gcmd.respond_info("stealth_mode: %s"
                              % ("STEALTH" if self.enabled else "NORMAL"),
                              log=False)
            return
        mode = mode.strip().upper()
        if mode not in ("STEALTH", "NORMAL"):
            raise gcmd.error("MODE must be STEALTH or NORMAL")
        self.set_mode(mode == "STEALTH")
        gcmd.respond_info("stealth_mode: %s" % (mode,), log=False)


def load_config(config):
    return PrinterStealthMode(config)
