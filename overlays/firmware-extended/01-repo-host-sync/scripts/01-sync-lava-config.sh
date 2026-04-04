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

  if [[ -f "$target_file" ]]; then
    install -m "$(stat -c '%a' "$target_file")" \
      -o "$(stat -c '%u' "$target_file")" \
      -g "$(stat -c '%g' "$target_file")" \
      "$source_file" "$target_file"
  else
    install -m 600 "$source_file" "$target_file"
  fi

  echo "   - synced $config_name"
done
