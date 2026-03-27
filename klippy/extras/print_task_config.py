# print task config info
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, os, copy, string
from . import filament_feed

LOGICAL_EXTRUDER_NUM = 32
PHYSICAL_EXTRUDER_NUM = 4

ENTANGLE_SENSITIVITY_LOW    = 'low'
ENTANGLE_SENSITIVITY_MEDIUM = 'medium'
ENTANGLE_SENSITIVITY_HIGH   = 'high'

PRINT_TASK_CONFIG_FILE = "print_task.json"

DEFAULT_PRINT_TASK_CONFIG = {
    'filament_vendor': ['NONE'] * PHYSICAL_EXTRUDER_NUM,
    'filament_type': ['NONE'] * PHYSICAL_EXTRUDER_NUM,
    'filament_sub_type': ['NONE'] * PHYSICAL_EXTRUDER_NUM,
    'filament_color': [0xFFFFFFFF] * PHYSICAL_EXTRUDER_NUM,
    'filament_color_rgba': ['FFFFFFFF'] * PHYSICAL_EXTRUDER_NUM,
    'filament_official': [False] * PHYSICAL_EXTRUDER_NUM,
    'filament_sku': [0] * PHYSICAL_EXTRUDER_NUM,
    'filament_edit': [True] * PHYSICAL_EXTRUDER_NUM,
    'filament_exist': [False] * PHYSICAL_EXTRUDER_NUM,
    'filament_soft': [False] * PHYSICAL_EXTRUDER_NUM,
    'extruder_map_table': [i for i in range(PHYSICAL_EXTRUDER_NUM)] + [0] * (LOGICAL_EXTRUDER_NUM - PHYSICAL_EXTRUDER_NUM),
    'extruders_used' : [False] * PHYSICAL_EXTRUDER_NUM,
    'extruders_replenished': [i for i in range(PHYSICAL_EXTRUDER_NUM)],
    'time_lapse_camera': False,
    'auto_bed_leveling': False,
    'flow_calibrate': False,
    'flow_calib_extruders': [True] * PHYSICAL_EXTRUDER_NUM,
    'shaper_calibrate': False,
    'auto_replenish_filament': True,
    'filament_entangle_detect': False,
    'filament_entangle_sen': ENTANGLE_SENSITIVITY_MEDIUM,
    'reprint_info': {
        'auto_bed_leveling': False,
        'flow_calibrate': False,
        'flow_calib_extruders': [True] * PHYSICAL_EXTRUDER_NUM,
        'time_lapse_camera': False,
        'extruder_map_table': [i for i in range(PHYSICAL_EXTRUDER_NUM)] + [0] * (LOGICAL_EXTRUDER_NUM - PHYSICAL_EXTRUDER_NUM),
        'extruders_used' : [False] * PHYSICAL_EXTRUDER_NUM,
    }
}

class PrintTaskConfig:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        self.filament_dt_obj = None
        self.filament_param_obj = None
        self.filament_feed_objects = None
        self.filament_info_backup = {
            'filament_vendor': ['NONE'] * PHYSICAL_EXTRUDER_NUM,
            'filament_type': ['NONE'] * PHYSICAL_EXTRUDER_NUM,
            'filament_sub_type': ['NONE'] * PHYSICAL_EXTRUDER_NUM,
            'filament_color': [0xFFFFFFFF] * PHYSICAL_EXTRUDER_NUM,
            'filament_color_rgba': ['FFFFFFFF'] * PHYSICAL_EXTRUDER_NUM
        }
        self.perform_auto_replenish = False

        config_dir = self.printer.get_snapmaker_config_dir()
        config_name = PRINT_TASK_CONFIG_FILE
        self.config_path = os.path.join(config_dir, config_name)
        self.print_task_config = self.printer.load_snapmaker_config_file(self.config_path, DEFAULT_PRINT_TASK_CONFIG)
        self.reset_print_info()

        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            "SET_PRINT_EXTRUDER_MAP", self.cmd_SET_PRINT_EXTRUDER_MAP)
        self.gcode.register_command(
            "GET_PRINT_EXTRUDER_MAP", self.cmd_GET_PRINT_EXTRUDER_MAP)
        self.gcode.register_command(
            "SET_PRINT_FILAMENT_CONFIG", self.cmd_SET_PRINT_FILAMENT_CONFIG)
        self.gcode.register_command(
            "GET_PRINT_TASK_CONFIG", self.cmd_GET_PRINT_TASK_CONFIG)
        self.gcode.register_command(
            "SAVE_CURRENT_PRINT_TASK_CONFIG", self.cmd_SAVE_CURRENT_PRINT_TASK_CONFIG)
        self.gcode.register_command(
            "RESET_PRINT_TASK_CONFIG", self.cmd_RESET_PRINT_TASK_CONFIG)
        self.gcode.register_command(
            "LOAD_PRINT_TASK_CONFIG", self.cmd_LOAD_PRINT_TASK_CONFIG)
        self.gcode.register_command(
            "SET_TIME_LAPSE_CAMERA", self.cmd_SET_TIME_LAPSE_CAMERA)
        self.gcode.register_command(
            "SET_PRINT_AUTO_BED_LEVELING", self.cmd_SET_PRINT_AUTO_BED_LEVELING)
        self.gcode.register_command(
            "SET_PRINT_PREFERENCES", self.cmd_SET_PRINT_PREFERENCES)
        self.gcode.register_command(
            "SET_PRINT_USED_EXTRUDERS", self.cmd_SET_PRINT_USED_EXTRUDERS)
        self.gcode.register_command(
            "SET_REPRINT_INFO", self.cmd_SET_REPRINT_INFO)
        self.gcode.register_command(
            "INNER_CHECK_AND_RELOAD_FILAMENT_INFO", self.cmd_INNER_CHECK_AND_RELOAD_FILAMENT_INFO)
        self.gcode.register_command(
            "INNER_AUTO_REPLENISH_FILAMENT", self.cmd_INNER_AUTO_REPLENISH_FILAMENT)

        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("print_task_config/set_print_preferences",
                                   self._handle_set_print_preferences)
        self.printer.register_event_handler("klippy:ready", self._ready)

    def _handle_set_print_preferences(self, web_request):
        try:
            logging.info("[print_task_config] wb, set_print_preferences")
            auto_replenish_filament = web_request.get_int('auto_replenish_filament', None)
            filament_entangle_detect = web_request.get_int('filament_entangle_detect', None)
            filament_entangle_sen = web_request.get_str('filament_entangle_sen', None)

            if auto_replenish_filament is not None:
                self.print_task_config['auto_replenish_filament'] = bool(auto_replenish_filament)

            if filament_entangle_detect is not None:
                self.print_task_config['filament_entangle_detect'] = bool(filament_entangle_detect)
                self.printer.send_event("print_task_config:set_entangle_detect", self.print_task_config['filament_entangle_detect'])

            if filament_entangle_sen is not None:
                if filament_entangle_sen not in [ENTANGLE_SENSITIVITY_LOW, ENTANGLE_SENSITIVITY_MEDIUM, ENTANGLE_SENSITIVITY_HIGH]:
                    raise ValueError(f"filament_entangle_sen error: {filament_entangle_sen}")
                self.print_task_config['filament_entangle_sen'] = filament_entangle_sen
                self.printer.send_event("print_task_config:set_entangle_detect", self.print_task_config['filament_entangle_detect'])

            if not self.printer.update_snapmaker_config_file(self.config_path,
                        self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
                logging.error("[print_task_config] save print_task_config failed\r\n")

            web_request.send({'state': 'success'})
        except Exception as e:
            logging.error("[print_task_config] set_print_preferences: %s", str(e))
            web_request.send({'state': 'error', 'message': str(e)})

    def _ready(self):
        self.filament_feed_objects = self.printer.lookup_objects('filament_feed')
        self.filament_param_obj = self.printer.lookup_object('filament_parameters', None)
        self.filament_dt_obj = self.printer.lookup_object("filament_detect", None)
        if self.filament_dt_obj is not None:
            self.filament_dt_obj.register_cb_2_update_filament_info(self._rfid_filament_info_update_cb)

        # Compatible with old versions
        need_save = False
        for i in range(PHYSICAL_EXTRUDER_NUM):
            if type(self.print_task_config['filament_color'][i]) is str:
                self.print_task_config['filament_color'] = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG['filament_color'])
                self.print_task_config['filament_color_rgba'] = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG['filament_color_rgba'])
                need_save = True

        if need_save:
            if not self.printer.update_snapmaker_config_file(self.config_path,
                    self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
                logging.error("[print_task_config] save print_task_config failed\r\n")

        self.filament_info_backup['filament_vendor'] = copy.deepcopy(self.print_task_config['filament_vendor'])
        self.filament_info_backup['filament_type'] = copy.deepcopy(self.print_task_config['filament_type'])
        self.filament_info_backup['filament_sub_type'] = copy.deepcopy(self.print_task_config['filament_sub_type'])
        self.filament_info_backup['filament_color'] = copy.deepcopy(self.print_task_config['filament_color'])
        self.filament_info_backup['filament_color_rgba'] = copy.deepcopy(self.print_task_config['filament_color_rgba'])

    def _reset_print_task_config(self):
        self.print_task_config = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG)
        self.filament_info_backup['filament_vendor'] = copy.deepcopy(self.print_task_config['filament_vendor'])
        self.filament_info_backup['filament_type'] = copy.deepcopy(self.print_task_config['filament_type'])
        self.filament_info_backup['filament_sub_type'] = copy.deepcopy(self.print_task_config['filament_sub_type'])
        self.filament_info_backup['filament_color'] = copy.deepcopy(self.print_task_config['filament_color'])
        self.filament_info_backup['filament_color_rgba'] = copy.deepcopy(self.print_task_config['filament_color_rgba'])
        if not self.printer.update_snapmaker_config_file(self.config_path,
                self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
            logging.error("[print_task_config] save print_task_config failed\r\n")

    def get_print_task_config(self):
        return copy.deepcopy(self.print_task_config)

    def backup_filament_info(self, extruder_index):
        if extruder_index < 0 or extruder_index >= PHYSICAL_EXTRUDER_NUM:
            logging.error("[print_task_config] backup_filament_info: extruder_index error")
            return
        if self.print_task_config['filament_vendor'][extruder_index] != "" and self.print_task_config['filament_vendor'][extruder_index] != "NONE":
            self.filament_info_backup['filament_vendor'][extruder_index] = self.print_task_config['filament_vendor'][extruder_index]
            self.filament_info_backup['filament_type'][extruder_index] = self.print_task_config['filament_type'][extruder_index]
            self.filament_info_backup['filament_sub_type'][extruder_index] = self.print_task_config['filament_sub_type'][extruder_index]
            self.filament_info_backup['filament_color'][extruder_index] = self.print_task_config['filament_color'][extruder_index]
            self.filament_info_backup['filament_color_rgba'][extruder_index] = self.print_task_config['filament_color_rgba'][extruder_index]

    def _rfid_filament_info_update_cb(self, channel, info, is_clear=False):
        if channel < 0 or channel >= PHYSICAL_EXTRUDER_NUM:
            logging.error("[print_task_config] rfid channel[%d] is out of range[0, %d]",
                          channel, PHYSICAL_EXTRUDER_NUM -1)
            return

        if is_clear == False and info['OFFICIAL'] == False and \
                self.print_task_config['filament_vendor'][channel] != 'NONE':
            return

        if is_clear == False and self.print_task_config['filament_sku'][channel] == info['SKU'] and \
                self.print_task_config['filament_official'][channel] == info['OFFICIAL'] and \
                info['OFFICIAL'] == True:
            return

        filament_color_rgba = f"{info['RGB_1']:06X}" + f"{info['ALPHA']:02X}"

        self.print_task_config['filament_vendor'][channel] = info['VENDOR']
        self.print_task_config['filament_type'][channel] = info['MAIN_TYPE']
        self.print_task_config['filament_sub_type'][channel] = info['SUB_TYPE']
        self.print_task_config['filament_color'][channel] = info['ARGB_COLOR']
        self.print_task_config['filament_color_rgba'][channel] = filament_color_rgba
        self.print_task_config['filament_official'][channel] = info['OFFICIAL']
        self.print_task_config['filament_sku'][channel] = info['SKU']
        if self.filament_param_obj is not None:
            self.print_task_config['filament_soft'][channel] = \
                self.filament_param_obj.get_is_soft(info['VENDOR'], info['MAIN_TYPE'], info['SUB_TYPE'])
        else:
            self.print_task_config['filament_soft'][channel] = False
        if info['OFFICIAL'] == True:
            logging.info(f"[print_task_config] rfid info: {info['VENDOR']} {info['MAIN_TYPE']} {info['SUB_TYPE']} {filament_color_rgba}")

        # do not use run_script_from_command api
        self.gcode.run_script(f"FLOW_RESET_K EXTRUDER={channel}\r\n")

        if not self.printer.update_snapmaker_config_file(self.config_path,
                self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
            logging.error("[print_task_config] save print_task_config failed\r\n")

    def get_extruder_map_table(self):
        return self.print_task_config['extruder_map_table']

    def get_extruder_map_index(self, index):
        if index + 1 > len(self.print_task_config['extruder_map_table']):
            raise ValueError("[print_task_config] index out of range[0,%d]" % (LOGICAL_EXTRUDER_NUM - 1))
        else:
            return self.print_task_config['extruder_map_table'][index]

    def reset_print_info(self):
        try:
            logging.info("[print_task_config] reset print info")
            self.print_task_config['extruder_map_table'] = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG['extruder_map_table'])
            self.print_task_config['extruders_used'] = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG['extruders_used'])
            self.print_task_config['extruders_replenished'] = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG['extruders_replenished'])
            self.print_task_config['flow_calibrate'] = False
            self.print_task_config['flow_calib_extruders'] = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG['flow_calib_extruders'])
            self.print_task_config['auto_bed_leveling'] = False
            self.print_task_config['time_lapse_camera'] = False
            # Compatible with old firmware versions
            if 'reprint_info' not in self.print_task_config or \
                    'auto_bed_leveling' not in self.print_task_config['reprint_info'] or \
                    'flow_calibrate' not in self.print_task_config['reprint_info'] or \
                    'flow_calib_extruders' not in self.print_task_config['reprint_info'] or \
                    'time_lapse_camera' not in self.print_task_config['reprint_info'] or \
                    'extruder_map_table' not in self.print_task_config['reprint_info'] or \
                    'extruders_used' not in self.print_task_config['reprint_info'] or \
                    len(self.print_task_config['reprint_info']['flow_calib_extruders']) != PHYSICAL_EXTRUDER_NUM or \
                    len(self.print_task_config['reprint_info']['extruder_map_table']) != LOGICAL_EXTRUDER_NUM or \
                    len(self.print_task_config['reprint_info']['extruders_used']) != PHYSICAL_EXTRUDER_NUM:
                self.print_task_config['auto_replenish_filament'] = DEFAULT_PRINT_TASK_CONFIG['auto_replenish_filament']
                if self.print_task_config['filament_entangle_sen'] == ENTANGLE_SENSITIVITY_LOW:
                    self.print_task_config['filament_entangle_sen'] = ENTANGLE_SENSITIVITY_MEDIUM
                self.print_task_config['reprint_info'] = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG['reprint_info'])
        except Exception as e:
            logging.error("[print_task_config] reset print info failed: %s", str(e))
            self.print_task_config = copy.deepcopy(DEFAULT_PRINT_TASK_CONFIG)
        finally:
            if not self.printer.update_snapmaker_config_file(self.config_path,
                    self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
                logging.error("[print_task_config] save print_task_config failed\r\n")

    def set_reprint_info(self):
        logging.info("[print_task_config] set reprint info")
        self.print_task_config['extruder_map_table'] = copy.deepcopy(self.print_task_config['reprint_info']['extruder_map_table'])
        self.print_task_config['extruders_used'] = copy.deepcopy(self.print_task_config['reprint_info']['extruders_used'])
        self.print_task_config['time_lapse_camera'] = copy.deepcopy(self.print_task_config['reprint_info']['time_lapse_camera'])
        self.print_task_config['flow_calibrate'] = copy.deepcopy(self.print_task_config['reprint_info']['flow_calibrate'])
        self.print_task_config['flow_calib_extruders'] = copy.deepcopy(self.print_task_config['reprint_info']['flow_calib_extruders'])
        self.print_task_config['auto_bed_leveling'] = copy.deepcopy(self.print_task_config['reprint_info']['auto_bed_leveling'])
        if not self.printer.update_snapmaker_config_file(self.config_path,
                self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
            logging.error("[print_task_config] save print_task_config failed\r\n")

    def update_filament_edit_flag(self):
        for ch in range(PHYSICAL_EXTRUDER_NUM):
            allowd_edit = False
            if self.print_task_config['filament_exist'][ch]:
                if self.print_task_config['filament_official'][ch] == True:
                    allowd_edit = False
                else:
                    allowd_edit = True

            self.print_task_config['filament_edit'][ch] = allowd_edit

    def update_filament_exist_flag(self):
        filament_feed_infos = {}
        if self.filament_feed_objects is not None:
            for obj_name, obj in self.filament_feed_objects:
                status = obj.get_status(0)
                filament_feed_infos.update(status)

        for ch in range(PHYSICAL_EXTRUDER_NUM):
            sensor_obj = self.printer.lookup_object(f'filament_motion_sensor e{ch}_filament', None)
            e_obj = filament_feed_infos.get(f'extruder{ch}', None)
            is_exist = True
            if sensor_obj != None and sensor_obj.get_status(0)['enabled']:
                if sensor_obj.get_status(0)['filament_detected']:
                    is_exist = True
                else:
                    if e_obj != None and e_obj['module_exist'] and not e_obj['disable_auto']:
                        if e_obj['filament_detected']:
                            is_exist = True
                        else:
                            is_exist = False
                    else:
                        is_exist = False
            else:
                is_exist = True

            self.print_task_config['filament_exist'][ch] = is_exist

    def get_status(self, eventtime=None):
        ##### It is not allowed to adjust the order of the following codes. ####
        self.update_filament_exist_flag()
        self.update_filament_edit_flag()
        ########################################################################
        print_task_config = copy.deepcopy(self.print_task_config)
        return print_task_config

    def cmd_SET_PRINT_EXTRUDER_MAP(self, gcmd):
        config_extruder = gcmd.get_int("CONFIG_EXTRUDER", None)
        map_extruder = gcmd.get_int("MAP_EXTRUDER", None)
        logging.info("[print_task_config] SET_PRINT_EXTRUDER_MAP %s",
                        gcmd.get_raw_command_parameters())

        machine_state_manager = self.printer.lookup_object('machine_state_manager', None)
        if machine_state_manager is not None:
            machine_sta = machine_state_manager.get_status()
            if str(machine_sta["main_state"]) == "PRINTING":
                raise gcmd.error("[print_task_config] not allowed to set extruder map during printing!")

        if config_extruder is None or map_extruder is None:
            raise gcmd.error("[print_task_config] extruder map, incomplete parameters")

        if (config_extruder < 0 or config_extruder >= LOGICAL_EXTRUDER_NUM) or \
                (map_extruder < 0 or map_extruder >= PHYSICAL_EXTRUDER_NUM):
            raise gcmd.error("[print_task_config] extruder map, invalid extruder index!!!")

        self.print_task_config['extruder_map_table'][config_extruder] = map_extruder
        self.print_task_config['reprint_info']['extruder_map_table'][config_extruder] = map_extruder

        ###### No need, because saving will be triggered in other necessary commands.
        # if not self.printer.update_snapmaker_config_file(self.config_path,
        #         self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
        #     logging.error("[print_task_config] save print_task_config failed\r\n")

    def cmd_GET_PRINT_EXTRUDER_MAP(self, gcmd):
        map_info = ""
        for n in range(len(self.print_task_config['extruder_map_table'])):
            map_info += "T{} -> T{}\n".format(n, self.print_task_config['extruder_map_table'][n])
        self.gcode.respond_info(map_info)

    def cmd_GET_PRINT_TASK_CONFIG(self, gcmd):
        self.gcode.respond_info(str(self.print_task_config))

    def cmd_SAVE_CURRENT_PRINT_TASK_CONFIG(self, gcmd):
        if self.printer.update_snapmaker_config_file(self.config_path, self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
            self.gcode.respond_info("print task config saved successfully!!!")
        else:
            raise gcmd.error("Error: print task config save failure!!!")

    def cmd_SET_PRINT_FILAMENT_CONFIG(self, gcmd):
        config_extruder = gcmd.get_int('CONFIG_EXTRUDER')
        filament_vendor = gcmd.get('VENDOR', None)
        filament_type = gcmd.get('FILAMENT_TYPE', None)
        filament_sub_type = gcmd.get('FILAMENT_SUBTYPE', None)
        filament_color = gcmd.get_int('FILAMENT_COLOR', None)
        filament_color_rgba = gcmd.get('FILAMENT_COLOR_RGBA', None)
        logging.info("[print_task_config] PRINT_FILAMENT_CONFIG %s",
                        gcmd.get_raw_command_parameters())

        if filament_color is None and filament_color_rgba is None and filament_type is None:
            raise gcmd.error("[print_task_config] filament_config, incomplete parameters")

        if filament_color is not None and filament_color_rgba is not None:
            raise gcmd.error("[print_task_config] filament_config, cannot set both filament_color and filament_color_rgba")

        if filament_type is not None:
            if filament_vendor is None or filament_sub_type is None:
                raise gcmd.error("[print_task_config] filament_config, incomplete parameters")

        if config_extruder < 0 or config_extruder >= PHYSICAL_EXTRUDER_NUM:
            raise gcmd.error("[print_task_config] extruder{} is out of range[0, {}]".format(config_extruder, PHYSICAL_EXTRUDER_NUM -1))

        if self.print_task_config['filament_official'][config_extruder]:
            raise gcmd.error("[print_task_config] filament_config, official filament, not configurable!")

        if filament_type is not None:
            self.print_task_config['filament_vendor'][config_extruder] = filament_vendor
            self.print_task_config['filament_type'][config_extruder] = filament_type
            self.print_task_config['filament_sub_type'][config_extruder] = filament_sub_type
            if self.filament_param_obj is not None:
                self.print_task_config['filament_soft'][config_extruder] = \
                    self.filament_param_obj.get_is_soft(filament_vendor, filament_type, filament_sub_type)
            else:
                self.print_task_config['filament_soft'][config_extruder] = False

        if filament_color_rgba is not None:
            if len(filament_color_rgba) == 6:
                filament_color_rgba = filament_color_rgba + 'FF'

            if len(filament_color_rgba) != 8:
                raise gcmd.error("[print_task_config] Invalid filament rgba color, e.i.#11223344")

            for i in range(len(filament_color_rgba)):
                if not filament_color_rgba[i] in string.hexdigits:
                    raise gcmd.error("[print_task_config] Invalid filament rgba color, e.i.#11223344")

            self.print_task_config['filament_color_rgba'][config_extruder] = filament_color_rgba
            red =   int(filament_color_rgba[0:2], 16)
            green = int(filament_color_rgba[2:4], 16)
            blue =  int(filament_color_rgba[4:6], 16)
            alpha = int(filament_color_rgba[6:8], 16)
            self.print_task_config['filament_color'][config_extruder] = (alpha << 24) | (red << 16) | (green << 8) | blue

        if filament_color is not None:
            self.print_task_config['filament_color'][config_extruder] = filament_color & 0xFFFFFFFF
            alpha = (filament_color & 0xFF000000) >> 24
            red =   (filament_color & 0x00FF0000) >> 16
            green = (filament_color & 0x0000FF00) >> 8
            blue =  (filament_color & 0x000000FF) >> 0
            self.print_task_config['filament_color_rgba'][config_extruder] = \
                f'{red:02X}' + f'{green:02X}' + f'{blue:02X}' + f'{alpha:02X}'

        self.gcode.run_script_from_command(f"FLOW_RESET_K EXTRUDER={config_extruder}\r\n")

        self.print_task_config['filament_official'][config_extruder] = False
        self.print_task_config['filament_sku'][config_extruder] = 0

        if not self.printer.update_snapmaker_config_file(self.config_path,
                self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
            logging.error("[print_task_config] save print_task_config failed\r\n")

    def cmd_SET_TIME_LAPSE_CAMERA(self, gcmd):
        enable = gcmd.get_int('ENABLE', minval=0, maxval=1)
        need_save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        self.print_task_config['time_lapse_camera'] = bool(enable)
        if need_save:
            load_config = self.printer.load_snapmaker_config_file(self.config_path, DEFAULT_PRINT_TASK_CONFIG)
            load_config['time_lapse_camera'] = bool(enable)
            if not self.printer.update_snapmaker_config_file(self.config_path, load_config, DEFAULT_PRINT_TASK_CONFIG):
                raise gcmd.error("time_lapse_camera config save failed")

    def cmd_SET_PRINT_AUTO_BED_LEVELING(self, gcmd):
        enable = gcmd.get_int('ENABLE', minval=0, maxval=1)
        need_save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        self.print_task_config['auto_bed_leveling'] = bool(enable)
        if need_save:
            load_config = self.printer.load_snapmaker_config_file(self.config_path, DEFAULT_PRINT_TASK_CONFIG)
            load_config['auto_bed_leveling'] = bool(enable)
            if not self.printer.update_snapmaker_config_file(self.config_path, load_config, DEFAULT_PRINT_TASK_CONFIG):
                raise gcmd.error("print auto_bed_leveling config save failed")

    def cmd_SET_PRINT_PREFERENCES(self, gcmd):
        bed_level = gcmd.get_int('BED_LEVEL', None, minval=0, maxval=1)
        flow_calibrate = gcmd.get_int('FLOW_CALIBRATE', None, minval=0, maxval=1)
        flow_calibrate_extruders = gcmd.get('FLOW_CALIBRATE_EXTRUDERS', None)
        shaper_calibrate = gcmd.get_int('SHAPER_CALIBRATE', None, minval=0, maxval=1)
        time_lapse_camera  = gcmd.get_int('TIME_LAPSE_CAMERA', None, minval=0, maxval=1)
        auto_replenish_filament  = gcmd.get_int('AUTO_REPLENISH_FILAMENT', None, minval=0, maxval=1)
        filament_entangle_detect = gcmd.get_int('FILAMENT_ENTANGLE_DETECT', None, minval=0, maxval=1)
        filament_entangle_sen = gcmd.get('FILAMENT_ENTANGLE_SEN', None)
        logging.info("[print_task_config] SET_PRINT_PREFERENCES %s", gcmd.get_raw_command_parameters())

        machine_state_manager = self.printer.lookup_object('machine_state_manager', None)
        is_printing = False
        if machine_state_manager is not None:
            machine_sta = machine_state_manager.get_status()
            if str(machine_sta["main_state"]) == "PRINTING":
                is_printing = True

        if bed_level is not None:
            if is_printing:
                raise gcmd.error("[print_task_config] not allow to set bed_level during printing!")
            self.print_task_config['auto_bed_leveling'] = bool(bed_level)
            self.print_task_config['reprint_info']['auto_bed_leveling'] = bool(bed_level)

        if flow_calibrate is not None:
            if is_printing:
                raise gcmd.error("[print_task_config] not allow to set flow_calibrate during printing!")
            self.print_task_config['flow_calibrate'] = bool(flow_calibrate)
            self.print_task_config['reprint_info']['flow_calibrate'] = bool(flow_calibrate)

        if flow_calibrate_extruders is not None:
            if is_printing:
                raise gcmd.error("[print_task_config] not allow to set flow_calibrate_extruders during printing!")

            calib_extruders = [int(value) for value in flow_calibrate_extruders.split(',')]
            for i in range(PHYSICAL_EXTRUDER_NUM):
                if i in calib_extruders:
                    self.print_task_config['flow_calib_extruders'][i] = True
                    self.print_task_config['reprint_info']['flow_calib_extruders'][i] = True
                else:
                    self.print_task_config['flow_calib_extruders'][i] = False
                    self.print_task_config['reprint_info']['flow_calib_extruders'][i] = False

        if shaper_calibrate is not None:
            self.print_task_config['shaper_calibrate'] = bool(shaper_calibrate)

        if time_lapse_camera is not None:
            if is_printing:
                raise gcmd.error("[print_task_config] not allow to set time_lapse_camera during printing!")
            self.print_task_config['time_lapse_camera'] = bool(time_lapse_camera)
            self.print_task_config['reprint_info']['time_lapse_camera'] = bool(time_lapse_camera)

        if auto_replenish_filament is not None:
            self.print_task_config['auto_replenish_filament'] = bool(auto_replenish_filament)

        if filament_entangle_detect is not None:
            self.print_task_config['filament_entangle_detect'] = bool(filament_entangle_detect)
            self.printer.send_event("print_task_config:set_entangle_detect", self.print_task_config['filament_entangle_detect'])

        if filament_entangle_sen is not None:
            if filament_entangle_sen not in [ENTANGLE_SENSITIVITY_HIGH, ENTANGLE_SENSITIVITY_MEDIUM, ENTANGLE_SENSITIVITY_LOW]:
                raise gcmd.error(f"[print_task_config] filament_entangle_sen error: {filament_entangle_sen}")
            self.print_task_config['filament_entangle_sen'] = filament_entangle_sen
            self.printer.send_event("print_task_config:set_entangle_detect", self.print_task_config['filament_entangle_detect'])

        if not self.printer.update_snapmaker_config_file(self.config_path,
                    self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
            logging.error("[print_task_config] save print_task_config failed\r\n")

    def cmd_SET_PRINT_USED_EXTRUDERS(self, gcmd):
        extruders_str = gcmd.get('EXTRUDERS', None)
        self.print_task_config['extruders_used'] = [False] * PHYSICAL_EXTRUDER_NUM
        self.print_task_config['reprint_info']['extruders_used'] = [False] * PHYSICAL_EXTRUDER_NUM
        logging.info("[print_task_config] SET_PRINT_USED_EXTRUDERS %s", gcmd.get_raw_command_parameters())

        machine_state_manager = self.printer.lookup_object('machine_state_manager', None)
        if machine_state_manager is not None:
            machine_sta = machine_state_manager.get_status()
            if str(machine_sta["main_state"]) == "PRINTING":
                raise gcmd.error("[print_task_config] not allow to set used_extruders during printing!")

        if extruders_str is not None:
            used_extruders = [int(value) for value in extruders_str.split(',')]
            for i in range(PHYSICAL_EXTRUDER_NUM):
                if i in used_extruders:
                    self.print_task_config['extruders_used'][i] = True
                    self.print_task_config['reprint_info']['extruders_used'][i] = True
                else:
                    self.print_task_config['extruders_used'][i] = False
                    self.print_task_config['reprint_info']['extruders_used'][i] = False

        if not self.printer.update_snapmaker_config_file(self.config_path,
                self.print_task_config, DEFAULT_PRINT_TASK_CONFIG):
            logging.error("[print_task_config] save print_task_config failed\r\n")

    def cmd_RESET_PRINT_TASK_CONFIG(self, gcmd):
        if not self._reset_print_task_config():
            raise gcmd.error("[print_task_config] reset print_task_config failed!")

    def cmd_LOAD_PRINT_TASK_CONFIG(self, gcmd):
        self.print_task_config = self.printer.load_snapmaker_config_file(self.config_path, DEFAULT_PRINT_TASK_CONFIG)

    def cmd_SET_REPRINT_INFO(self, gcmd):
        self.set_reprint_info()

    def cmd_INNER_CHECK_AND_RELOAD_FILAMENT_INFO(self, gcmd):
        extruder_index = gcmd.get_int('EXTRUDER')
        is_runout = gcmd.get_int('IS_RUNOUT')

        if extruder_index < 0 or extruder_index >= PHYSICAL_EXTRUDER_NUM:
            raise gcmd.error("[print_task_config] INNER_CHECK_AND_RELOAD_FILAMENT_INFO extruder_index error")

        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()

        logging.info(f"[print_task_config] INNER_CHECK_AND_RELOAD_FILAMENT_INFO extruder_index: {extruder_index}")

        if self.print_task_config['filament_type'][extruder_index] != "" and self.print_task_config['filament_type'][extruder_index] != "NONE":
            return

        if is_runout and self.filament_info_backup:
            try:
                if self.filament_info_backup['filament_type'][extruder_index] != "" and self.filament_info_backup['filament_type'][extruder_index] != "NONE":
                    self.gcode.run_script_from_command("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=%d VENDOR=%s FILAMENT_TYPE=%s FILAMENT_SUBTYPE=%s FILAMENT_COLOR_RGBA=%s" %
                                                        (extruder_index,
                                                        self.filament_info_backup['filament_vendor'][extruder_index],
                                                        self.filament_info_backup['filament_type'][extruder_index],
                                                        self.filament_info_backup['filament_sub_type'][extruder_index],
                                                        self.filament_info_backup['filament_color_rgba'][extruder_index]))

            except Exception as e:
                logging.error("[print_task_config] INNER_CHECK_AND_RELOAD_FILAMENT_INFO error: %s", str(e))

        if self.print_task_config['filament_type'][extruder_index] == "" or self.print_task_config['filament_type'][extruder_index] == "NONE":
            raise gcmd.error(
                    message = f"e{extruder_index} not edit filament",
                    action = 'pause',
                    id = 523,
                    index = extruder_index,
                    code = 39,
                    oneshot = 0,
                    level = 2)

    def cmd_INNER_AUTO_REPLENISH_FILAMENT(self, gcmd):
        self.perform_auto_replenish = False
        if self.print_task_config['auto_replenish_filament'] == False:
            logging.info("[print_task_config] auto_replenish_filament is disabled.")
            return
        else:
            logging.info("[print_task_config] try to auto replenish filament...")

        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()

        current_extruder = gcmd.get_int('EXTRUDER')
        if current_extruder < 0 or current_extruder >= PHYSICAL_EXTRUDER_NUM:
            logging.error(f"[print_task_config] extruder_index input error: {current_extruder}")
            return

        if current_extruder != toolhead.get_extruder().extruder_index:
            logging.error("[print_task_config] current extruder is %d, but input extruder is %d",
                          toolhead.get_extruder().extruder_index, current_extruder)
            return

        if self.filament_info_backup is None or \
                self.filament_info_backup['filament_type'][current_extruder] == "" or \
                self.filament_info_backup['filament_type'][current_extruder] == "NONE":
            logging.error("[print_task_config] filament_info_backup is none.\r\n")
            return

        print_stats = self.printer.lookup_object("print_stats", None)
        if print_stats is None or print_stats.state != 'paused':
            logging.error(f"[print_task_config] print_stats error: {print_stats.state}\r\n")
            return

        macro = self.printer.lookup_object('gcode_macro INNER_RESUME', None)
        if macro is None:
            logging.error("[print_task_config] INNER_RESUME macro is none.\r\n")
            return

        replenish_extruder = None
        replenish_extruder_name = None
        current_extruder_name = toolhead.get_extruder().name
        current_extruder_temp = macro.variables.get('last_extruder_temp', 0)

        filament_feed_infos = {}
        for obj_name, obj in self.filament_feed_objects:
            status = obj.get_status(0)
            filament_feed_infos.update(status)

        e_obj = filament_feed_infos.get(f'extruder{current_extruder}', None)
        runout_sensor = self.printer.lookup_object(f"filament_motion_sensor e{current_extruder}_filament", None)
        if e_obj is not None and runout_sensor is not None and \
                e_obj['module_exist'] == True and \
                e_obj['disable_auto'] == False and \
                e_obj['filament_detected'] == True and \
                runout_sensor.get_status(0)['enabled'] == True:
            replenish_extruder = current_extruder
        else:
            for i in range(PHYSICAL_EXTRUDER_NUM):
                if i == current_extruder:
                    continue

                runout_sensor = self.printer.lookup_object(f"filament_motion_sensor e{i}_filament", None)
                e_obj = filament_feed_infos.get(f'extruder{i}', None)
                if e_obj is None or runout_sensor is None:
                    continue

                if e_obj['channel_state'] == filament_feed.FEED_STA_LOAD_FINISH or \
                        (e_obj['module_exist'] == True and e_obj['disable_auto'] == False and e_obj['filament_detected'] == True and runout_sensor.get_status(0)['enabled'] == True):
                    if self.print_task_config['filament_vendor'][i] != 'NONE' and \
                            self.print_task_config['filament_vendor'][i] == self.filament_info_backup['filament_vendor'][current_extruder] and \
                            self.print_task_config['filament_type'][i] == self.filament_info_backup['filament_type'][current_extruder] and \
                            self.print_task_config['filament_sub_type'][i] == self.filament_info_backup['filament_sub_type'][current_extruder] and \
                            self.print_task_config['filament_color_rgba'][i][0:6] == self.filament_info_backup['filament_color_rgba'][current_extruder][0:6]:

                        replenish_extruder = i
                        break

        if replenish_extruder == None:
            runout_sensors = self.printer.lookup_objects('filament_motion_sensor')
            runout_sensor_infos = {}
            for obj_name, obj in runout_sensors:
                status = obj.get_status(0)
                runout_sensor_infos[obj_name] = status
            logging.info("[print_task_config] =========== cannot auto replenish filament ====================== ")
            logging.info(f"feeder info: {str(filament_feed_infos)}")
            logging.info(f"runout sensor info: {str(runout_sensor_infos)}")
            logging.info(f"filament info: {str(self.print_task_config['filament_vendor'])}, {str(self.print_task_config['filament_type'])}, {str(self.print_task_config['filament_sub_type'])}, {str(self.print_task_config['filament_color_rgba'])}")
            logging.info("[print_task_config] ================================================================= ")
            return
        else:
            logging.info(f"[print_task_config] auto replenish filament: T{current_extruder} -> T{replenish_extruder}")

        if replenish_extruder == 0:
            replenish_extruder_name = 'extruder'
        else:
            replenish_extruder_name = f'extruder{replenish_extruder}'

        toolhead.wait_moves()
        if current_extruder != replenish_extruder:
            virtual_sdcard = self.printer.lookup_object('virtual_sdcard', None)
            if virtual_sdcard is not None:
                temp_dir = {
                    replenish_extruder_name: current_extruder_temp,
                    current_extruder_name: 0
                }
                virtual_sdcard.record_pl_print_temperature_env(temp_dir, ignore_pl_condition = True)
                virtual_sdcard.force_refresh_move_env_extruder(replenish_extruder_name)
            self.gcode.run_script_from_command(f"SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=extruder{current_extruder}_temp VALUE=0\n")
            self.gcode.run_script_from_command(f"M104 S0 T{current_extruder} A0\n")
            for i in range(LOGICAL_EXTRUDER_NUM):
                if self.print_task_config['extruder_map_table'][i] == current_extruder:
                    self.print_task_config['extruder_map_table'][i] = replenish_extruder
            self.print_task_config['extruders_used'][current_extruder] = False
            self.print_task_config['extruders_used'][replenish_extruder] = True
            self.print_task_config['flow_calib_extruders'][replenish_extruder] = True
            self.print_task_config['extruders_replenished'][current_extruder] = replenish_extruder
            self.print_task_config['reprint_info']['extruder_map_table'] = copy.deepcopy(self.print_task_config['extruder_map_table'])
            self.print_task_config['reprint_info']['flow_calib_extruders'] = copy.deepcopy(self.print_task_config['flow_calib_extruders'])
            self.print_task_config['reprint_info']['extruders_used'] = copy.deepcopy(self.print_task_config['extruders_used'])
            self.printer.update_snapmaker_config_file(self.config_path, self.print_task_config, DEFAULT_PRINT_TASK_CONFIG)

        self.perform_auto_replenish = True
        self.gcode.run_script_from_command(f"RESUME REPLENISH=1 REPLENISH_EXTRUDER={replenish_extruder}\n")

def load_config(config):
    return PrintTaskConfig(config)
