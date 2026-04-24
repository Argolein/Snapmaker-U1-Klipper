#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -euo pipefail

SOURCE_DIR="$ROOT_DIR/lava"
TARGET_DIR="$ROOTFS_DIR/home/lava/origin_printer_data/config"
CONFIG_FILES=(
  printer.cfg
  fluidd.cfg
  xyz_offset_calibration.cfg
)
# Stage host files outside /home/lava. The active /home/lava/klipper tree can
# survive firmware updates, so S48-sync-klipper-host-files copies these staged
# files into place before Klipper starts.
HOST_FILE_MAPPINGS=(
  "klippy/toolhead.py:usr/share/snapmaker-klipper/klippy/toolhead.py"
  "klippy/kinematics/extruder.py:usr/share/snapmaker-klipper/klippy/kinematics/extruder.py"
  "klippy/extras/stealth_mode.py:usr/share/snapmaker-klipper/klippy/extras/stealth_mode.py"
)

install_preserve_target() {
  local source_file="$1"
  local target_file="$2"
  local default_mode="${3:-600}"

  if [[ -f "$target_file" ]]; then
    install -m "$(stat -c '%a' "$target_file")" \
      -o "$(stat -c '%u' "$target_file")" \
      -g "$(stat -c '%g' "$target_file")" \
      "$source_file" "$target_file"
  else
    install -D -m "$default_mode" "$source_file" "$target_file"
  fi
}

install_with_mode() {
  local source_file="$1"
  local target_file="$2"
  local mode="$3"

  if [[ -f "$target_file" ]]; then
    install -m "$mode" \
      -o "$(stat -c '%u' "$target_file")" \
      -g "$(stat -c '%g' "$target_file")" \
      "$source_file" "$target_file"
  else
    install -D -m "$mode" "$source_file" "$target_file"
  fi
}

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo ">> Repo lava config directory not present, skipping sync"
  exit 0
fi

echo ">> Syncing repo-owned lava config into origin_printer_data"

for config_name in "${CONFIG_FILES[@]}"; do
  source_file="$SOURCE_DIR/$config_name"
  target_file="$TARGET_DIR/$config_name"

  if [[ ! -f "$source_file" ]]; then
    echo "   - skipping missing $config_name"
    continue
  fi

  install_preserve_target "$source_file" "$target_file" 600

  echo "   - synced $config_name"
done

echo ">> Staging repo-owned Klipper host files into /usr/share/snapmaker-klipper"
echo "   (runtime sync to /home/lava/klipper is handled by S48-sync-klipper-host-files)"

for mapping in "${HOST_FILE_MAPPINGS[@]}"; do
  source_rel="${mapping%%:*}"
  target_rel="${mapping#*:}"
  source_file="$ROOT_DIR/$source_rel"
  target_file="$ROOTFS_DIR/$target_rel"

  if [[ ! -f "$source_file" ]]; then
    echo "   - skipping missing $source_rel"
    continue
  fi

  install_with_mode "$source_file" "$target_file" 644
  echo "   - synced $source_rel"
done
