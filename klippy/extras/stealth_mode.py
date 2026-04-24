# Prusa-style runtime stealth mode for Snapmaker U1
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class PrinterStealthMode:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.velocity_limit = config.getfloat('velocity', 160., above=0.)
        self.accel_limit = config.getfloat('accel', 2500., above=0.)
        self.enabled = False
        self.toolhead = None
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("SET_STEALTH_MODE", self.cmd_SET_STEALTH_MODE,
                               desc=self.cmd_SET_STEALTH_MODE_help)

    def _handle_connect(self):
        self.toolhead = self.printer.lookup_object("toolhead")
        self.toolhead.register_stealth_mode(self)

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
        }

    def _apply_pressure_advance_profiles(self):
        for extruder in self.printer.lookup_object('extruder_list', []):
            if extruder.extruder_stepper is None:
                continue
            extruder.extruder_stepper.apply_stealth_mode(self.enabled)

    def set_mode(self, enabled):
        enabled = bool(enabled)
        if self.toolhead is None:
            raise self.printer.command_error(
                "Stealth mode is not ready yet")
        if self.enabled == enabled:
            return
        self.toolhead.wait_moves()
        self.enabled = enabled
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
