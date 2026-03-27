# device Scanning Processing

import subprocess, re, configparser

SOC_MCU_STRING = "Geschwister Schneider CAN adapter"
EXTRUDER_MCU_STRING = "usb-Klipper"
CFG_MCU_SECTION_NAME = ["mcu", "mcu E0", "mcu E1", "mcu E2", "mcu E3"]
SCAN_RESULT_MAP_INDEX = {'scan_mcu_number': 0, 'mcu': 1, 'mcu E0': 2, 'mcu E1': 3, 'mcu E2': 4, 'mcu E3': 5}
OPTION_NAME = 'serial'

def run_shell_command(command):
    try:
        output = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return [output.returncode, output.stdout.strip(), output.stderr.strip()]
    except subprocess.CalledProcessError as e:
        return [e.returncode, getattr(e, 'stdout', ''), getattr(e, 'stderr', '')]
    except Exception as e:
        return [-1, '', str(e)]

def scan_mcu_device():
    mcu_info_list = [0, None, None, None, None, None]
    def get_usb_devices():
        return run_shell_command("lsusb")
    def get_serial_devices():
        return run_shell_command("ls /dev/serial/by-id/")
    def contains_instruction(main_string, instruction):
        return instruction in main_string
    def update_mcu_list(device, index):
        if contains_instruction(device, EXTRUDER_MCU_STRING) and (f"/dev/serial/by-id/{device}") not in mcu_info_list:
            mcu_info_list[index + 2] = f"/dev/serial/by-id/{device}"
            mcu_info_list[0] += 1
            return True
        return False

    # soc mcu scan
    # TODO: power down the soc mcu and all extruder mcu's
    pass
    # TODO: boot pin configuration, soc mcu normal, mcu0 boot, mcu1 boot, mcu2 boot, mcu3 boot
    pass
    # TODO: power on soc mcu
    pass
    if get_usb_devices()[0] == 0:
        devices = re.split(r'\r?\n', get_usb_devices()[1])
        for device in devices:
            if contains_instruction(device, SOC_MCU_STRING):
                mcu_info_list[1] = "/dev/ttyS6"
                mcu_info_list[0] += 1
                break

    for index in range(4):
        # TODO: power down the soc mcu and all extruder mcu's
        pass
        # TODO: boot pin configuration, soc mcu, mcu0, mcu1, mcu2, mcu3
        pass
        # TODO: power on the specified mcu
        pass
        if get_serial_devices()[0] == 0:
            devices = re.split(r'\r?\n', get_serial_devices()[1])
            for device in devices:
                if update_mcu_list(device, index):
                    break
    # TODO: power on all mcu
    print(mcu_info_list)
    return mcu_info_list

# lists info_list and section_name_list must correspond to each other
def update_printer_cfg(cfg_file_path, info_list, section_name_list, option):
    config = configparser.ConfigParser()
    config.read(cfg_file_path)
    sections_to_add = []
    option_to_add = []
    sections_to_update = []

    for index, mcu_section in enumerate(section_name_list):
        if not config.has_section(mcu_section):
            # print(f"no have section {mcu_section}")
            config.add_section(mcu_section)
            config.set(mcu_section, option, f"{info_list[index]}")
            sections_to_add.append([mcu_section, option, info_list[index]])
        else:
            if not config.has_option(mcu_section, option):
                # print(f"no have option {mcu_section}")
                config.set(mcu_section, option, f"{info_list[index]}")
                option_to_add.append([mcu_section, option, info_list[index]])
            elif config.get(mcu_section, option) != info_list[index] and (config.get(mcu_section, option) is not None and info_list[index] is not None):
                # print(f"update option {mcu_section}")
                config.set(mcu_section, option, f"{info_list[index]}")
                sections_to_update.append([mcu_section, option, info_list[index]])

    if len(sections_to_update) or len(sections_to_add) or len(option_to_add):
        with open(cfg_file_path, 'r') as file:
            lines = file.readlines()

        updated_lines = []
        section_name = None
        is_first_section = True

        for line in lines:
            if line.startswith(';') or line.startswith('#') or line.strip() == '':
                updated_lines.append(line)
                continue

            if line.startswith('['):
                section_name = line.strip('[]\n')
                if is_first_section:
                    wrap = ""
                    for index in range(len(sections_to_add)):
                        updated_lines.append(f"{wrap}[{sections_to_add[index][0]}]\n")
                        updated_lines.append(f"{sections_to_add[index][1]}: {sections_to_add[index][2]}\n")
                        wrap = '\n'
                        if index + 1 == len(sections_to_add):
                            updated_lines.append("\n")
                    is_first_section = False

                option_to_add_fg = False
                for index in range(len(option_to_add)):
                    if section_name == option_to_add[index][0]:
                        updated_lines.append(line)
                        updated_lines.append(f"{option_to_add[index][1]}: {option_to_add[index][2]}\n")
                        option_to_add_fg = True
                        break
                if option_to_add_fg:
                    continue
            elif line.startswith(option + '=') or line.startswith(option + ':'):
                for index in range(len(sections_to_update)):
                    if section_name == sections_to_update[index][0]:
                        line = f"{sections_to_update[index][1]}: {sections_to_update[index][2]}\n"
                        break
            updated_lines.append(line)

        with open(cfg_file_path, 'w') as file:
            file.writelines(updated_lines)

if __name__ == '__main__':
    dev_list = scan_mcu_device()
    # update_printer_cfg("test.cfg", dev_list[1:], CFG_MCU_SECTION_NAME, OPTION_NAME)