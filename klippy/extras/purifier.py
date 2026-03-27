import logging, json, copy, os
from . import fan
from . import pulse_counter

FAN_STATE_TURN_ON                                   = 0
FAN_STATE_TURN_OFF                                  = 1
FAN_STATE_TURNING_OFF                               = 2

FAN_DELAY_TIME_MAX                                  = 600
FAN_DELAY_TIME_MIN                                  = 1
DEFAULT_FAN_DELAY_TIME                              = 180

SAVE_PURIFIER_INFO_TIME                             = 360

DEFAULT_POWER_DT_SAMPLE_TIME                        = 0.08
DEFAULT_POWER_DT_SAMPLE_COUNT                       = 4
DEFAULT_POWER_DT_REPORT_TIME                        = 0.350
DEFAULT_POWER_DT_THRESHOLD                          = 0.88

PURIFIER_CONFIG_FILE                                = "purifier_config.json"
DEFAULT_PURIFIER_CONFIG_STRUCT = {
    'work_time': 0,
    'delay_time': DEFAULT_FAN_DELAY_TIME
}

class PurifierFanTachometer:
    def __init__(self, printer, pin, ppr, sample_time, poll_time):
        self._frequence = pulse_counter.FrequencyCounter(printer, pin, sample_time, poll_time)
        self._ppr = ppr

    def get_status(self, eventtime=None):
        rpm = None
        if self._frequence is not None:
            rpm = self._frequence.get_frequency()  * 30. / self._ppr
        return {'rpm': rpm}

class Purifier:
    def __init__(self, config):
        self.printer = config.get_printer()
        ppins = self.printer.lookup_object('pins')
        self.reactor = self.printer.get_reactor()

        config_dir = self.printer.get_snapmaker_config_dir()
        config_name = PURIFIER_CONFIG_FILE
        self._config_path = os.path.join(config_dir, config_name)
        self._config = self.printer.load_snapmaker_config_file(self._config_path, DEFAULT_PURIFIER_CONFIG_STRUCT)

        # read config
        tach_ppr = config.getint('tachometer_ppr', 2)
        tach_poll_time = config.getfloat('tachometer_poll_interval', 0.001)
        extra_fan_tach_pin = config.get('extra_fan_tach_pin')

        # main fan
        self._fan = fan.Fan(config, default_shutdown_speed=0.)
        # extra fan
        sample_time = 1.
        self._extra_fan_tach = PurifierFanTachometer(self.printer, extra_fan_tach_pin,
                                    tach_ppr, sample_time, tach_poll_time)

        # power detect
        power_det_pin = config.get('power_det_pin')
        self._power_det_threshold = config.getfloat('power_det_threshold', DEFAULT_POWER_DT_THRESHOLD)
        self._power_det_pin = ppins.setup_pin('adc', power_det_pin)
        self._power_det_pin.setup_adc_sample(DEFAULT_POWER_DT_SAMPLE_TIME, DEFAULT_POWER_DT_SAMPLE_COUNT)
        self._power_det_pin.setup_adc_callback(DEFAULT_POWER_DT_REPORT_TIME, self._adc_callback)
        self._power_detected = False
        self._power_det_value = 1

        self._fan_state = FAN_STATE_TURN_OFF
        self._work_time = self._config['work_time']
        self._delay_time = self._config['delay_time']
        self._work_time_last = 0

        # timer
        self._delay_turnoff_timer = self.reactor.register_timer(
                self._delay_turnoff_handle)
        self._save_config_timer = self.reactor.register_timer(
                self._save_config_handle)

        # gcode
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command('SET_PURIFIER', self.cmd_SET_PURIFIER)
        gcode.register_command('GET_PURIFIER', self.cmd_GET_PURIFIER)

        wh = self.printer.lookup_object('webhooks')
        wh.register_endpoint("control/purifier", self._handle_control_purifier)

        self.printer.register_event_handler("klippy:ready", self._ready)

    def _ready(self):
         self.reactor.update_timer(self._save_config_timer, self.reactor.NOW)

    def _adc_callback(self, read_time, read_value):
        self._power_det_value = read_value
        if (self._power_det_value < self._power_det_threshold):
            self._power_detected = True
        else:
            if self._power_detected:
                self.fan_turn_off(0)
            self._power_detected = False

    def fan_turn_on(self, speed):
        if not self._power_detected:
            logging.error("Purifier not exist!")
            return

        if speed > 100:
            speed = 100
        if speed < 0:
            speed = 0

        if self._fan_state == FAN_STATE_TURN_ON:
            self._fan.set_speed_from_command(speed / 100.0)
            return
        elif self._fan_state == FAN_STATE_TURNING_OFF:
            self.reactor.update_timer(self._delay_turnoff_timer,
                    self.reactor.NEVER)
            self._fan.set_speed_from_command(speed / 100.0)
        else:
            self._fan.set_speed_from_command(speed / 100.0)
            self._work_time_last = self.reactor.monotonic()

        self._fan_state = FAN_STATE_TURN_ON

    def fan_turn_off(self, delay_time):
        if delay_time < FAN_DELAY_TIME_MIN:
            self._fan.set_speed_from_command(0)
            if self._fan_state != FAN_STATE_TURN_OFF:
                self.reactor.update_timer(self._delay_turnoff_timer, self.reactor.NEVER)
                work_time_tmp = self.reactor.monotonic()
                if work_time_tmp > self._work_time_last:
                    self._work_time += work_time_tmp - self._work_time_last

                load_config = self.printer.load_snapmaker_config_file(self._config_path, DEFAULT_PURIFIER_CONFIG_STRUCT)
                load_config['work_time'] = self._work_time
                ret = self.printer.update_snapmaker_config_file(self._config_path, load_config, DEFAULT_PURIFIER_CONFIG_STRUCT)
                if not ret:
                    logging.error("save purifier failed!")

                self._fan_state = FAN_STATE_TURN_OFF
        else:
            self._fan_state = FAN_STATE_TURNING_OFF
            self.reactor.update_timer(self._delay_turnoff_timer, self.reactor.monotonic() + delay_time)

    def _delay_turnoff_handle(self, eventtime):
        self._fan.set_speed_from_command(0)

        if self._fan_state != FAN_STATE_TURN_OFF:
            work_time_tmp = self.reactor.monotonic()
            if work_time_tmp > self._work_time_last:
                self._work_time += work_time_tmp - self._work_time_last

                load_config = self.printer.load_snapmaker_config_file(self._config_path, DEFAULT_PURIFIER_CONFIG_STRUCT)
                load_config['work_time'] = self._work_time
                ret = self.printer.update_snapmaker_config_file(self._config_path, load_config, DEFAULT_PURIFIER_CONFIG_STRUCT)
                if not ret:
                    logging.error("save purifier failed!")

            self._fan_state = FAN_STATE_TURN_OFF
        return self.reactor.NEVER

    def get_fan_speed(self):
        return self._fan.last_fan_value

    def _save_config_handle(self, eventtime):
        if self._fan_state != FAN_STATE_TURN_OFF:
            work_time_tmp = self.reactor.monotonic()
            if work_time_tmp > self._work_time_last:
                self._work_time += work_time_tmp - self._work_time_last
                self._work_time_last = work_time_tmp

                load_config = self.printer.load_snapmaker_config_file(self._config_path, DEFAULT_PURIFIER_CONFIG_STRUCT)
                load_config['work_time'] = self._work_time
                ret = self.printer.update_snapmaker_config_file(self._config_path, load_config, DEFAULT_PURIFIER_CONFIG_STRUCT)
                if not ret:
                    logging.error("save purifier failed!")

        return self.reactor.monotonic() + SAVE_PURIFIER_INFO_TIME

    def get_status(self, eventtime):
        fan_status = self._fan.get_status(eventtime)
        extra_fan_status = self._extra_fan_tach.get_status(eventtime)

        return {
            'power_detected': self._power_detected,
            'power_det_value': self._power_det_value * 3.3,
            'work_time': int(self._work_time),
            'fan_state': self._fan_state,
            'fan_speed': fan_status['speed'],
            'fan_rpm': fan_status['rpm'],
            'extra_fan_speed': fan_status['speed'],
            'extra_fan_rpm': extra_fan_status['rpm'],
            'delay_time': self._delay_time
        }

    def cmd_SET_PURIFIER(self, gcmd):
        fan_speed = gcmd.get_int('FAN_SPEED', None, minval= 0, maxval=100)
        delay_time = gcmd.get_int('DELAY_TIME', None, minval=0,  maxval=FAN_DELAY_TIME_MAX)
        work_time = gcmd.get_int('WORK_TIME', None, minval= 0)
        save = 0

        if work_time is not None:
            self._work_time = work_time
            save = 1

        if delay_time is not None:
            self._delay_time = delay_time
            save = 1

        if fan_speed is not None:
            if fan_speed > 0:
                self.fan_turn_on(fan_speed)
            else:
                self.fan_turn_off(self._delay_time)

        if save:
            load_config = self.printer.load_snapmaker_config_file(self._config_path, DEFAULT_PURIFIER_CONFIG_STRUCT)
            load_config['work_time'] = self._work_time
            load_config['delay_time'] = self._delay_time
            ret = self.printer.update_snapmaker_config_file(self._config_path, load_config, DEFAULT_PURIFIER_CONFIG_STRUCT)
            if not ret:
                gcmd.respond_info("save purifier failed!")

    def cmd_GET_PURIFIER(self, gcmd):
        eventtime = self.reactor.monotonic()
        fan_status = self._fan.get_status(eventtime)
        extra_fan_status = self._extra_fan_tach.get_status(eventtime)
        msg = ("power_detected = %d\r\n"
               "power_det_value = %f\r\n"
               "work_time = %d\r\n"
               "fan_state = %d\r\n"
               "fan_speed = %f\r\n"
               "fan_rpm = %d\r\n"
               "extra_fan_speed = %f\r\n"
               "extra_fan_rpm = %d\r\n"
               "delay_time = %d\r\n"
               % (self._power_detected,
                  self._power_det_value * 3.3,
                  int(self._work_time),
                  self._fan_state,
                  fan_status['speed'],
                  fan_status['rpm'],
                  fan_status['speed'],
                  extra_fan_status['rpm'],
                  self._delay_time))
        gcmd.respond_info(msg, log=False)

    def _handle_control_purifier(self, web_request):
        try:
            fan_speed = web_request.get_int('fan_speed', None)
            delay_time = web_request.get_int('delay_time', None)
            work_time = web_request.get_int('work_time', None)
            save = 0

            if delay_time is not None:
                if delay_time > FAN_DELAY_TIME_MAX:
                    delay_time = FAN_DELAY_TIME_MAX
                if delay_time < 0:
                    delay_time = 0
                self._delay_time = delay_time
                save = 1

            if work_time is not None:
                if work_time < 0:
                    work_time = 0
                self._work_time = work_time
                save = 1

            if fan_speed is not None:
                if fan_speed > 0:
                    self.fan_turn_on(fan_speed)
                else:
                    self.fan_turn_off(self._delay_time)

            if save:
                load_config = self.printer.load_snapmaker_config_file(self._config_path, DEFAULT_PURIFIER_CONFIG_STRUCT)
                load_config['work_time'] = self._work_time
                load_config['delay_time'] = self._delay_time
                ret = self.printer.update_snapmaker_config_file(self._config_path, load_config, DEFAULT_PURIFIER_CONFIG_STRUCT)
                if not ret:
                    logging.info("save purifier failed!")

            web_request.send({'state': 'success'})
        except Exception as e:
            logging.error(f'failed to set purifier: {str(e)}')
            web_request.send({'state': 'error', 'message': str(e)})

def load_config(config):
    return Purifier(config)

