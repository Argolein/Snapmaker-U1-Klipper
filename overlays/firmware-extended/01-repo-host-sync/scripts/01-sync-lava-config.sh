#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -euo pipefail

SOURCE_DIR="$ROOT_DIR/lava"
TARGET_DIR="$ROOTFS_DIR/home/lava/origin_printer_data/config"
MCU_BUNDLE_DIR="$ROOTFS_DIR/home/lava/firmware_MCU"
LOCAL_MAIN_MCU_BIN="$ROOT_DIR/out_at32f403a/at32f403a.bin"
LOCAL_MAIN_MCU_VERSION="19700101000000-localbuild"
DEFAULT_HEAD_MCU_VERSION="20260323110253-51d366c286"
CONFIG_FILES=(
  printer.cfg
  fluidd.cfg
  xyz_offset_calibration.cfg
)
# Host files are staged under /usr/share/snapmaker-klipper/ in the squashfs.
# /home/lava/klipper is on the userdata partition and is not updated by the
# squashfs overlay at image-build time.
# S48-sync-klipper-host-files copies them to /home/lava/klipper/ on every
# boot so firmware updates always win over the userdata copy.
HOST_FILE_MAPPINGS=(
  "klippy/toolhead.py:usr/share/snapmaker-klipper/klippy/toolhead.py"
  "klippy/stepper.py:usr/share/snapmaker-klipper/klippy/stepper.py"
  "klippy/extras/tmc2240.py:usr/share/snapmaker-klipper/klippy/extras/tmc2240.py"
  "klippy/extras/motor_phase_calibration.py:usr/share/snapmaker-klipper/klippy/extras/motor_phase_calibration.py"
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

rewrite_main_mcu_bundle() {
  local version_file="$MCU_BUNDLE_DIR/VERSION"
  local version_main_file="$MCU_BUNDLE_DIR/VERSION_MAIN"
  local version_head_file="$MCU_BUNDLE_DIR/VERSION_HEAD"
  local head_bin="$MCU_BUNDLE_DIR/at32f415.bin"
  local host_mcu_bin="$MCU_BUNDLE_DIR/klippy_mcu"
  local md5_file="$MCU_BUNDLE_DIR/md5sum.txt"
  local head_mcu_version="$DEFAULT_HEAD_MCU_VERSION"

  if [[ ! -f "$LOCAL_MAIN_MCU_BIN" ]]; then
    echo ">> No local out_at32f403a/at32f403a.bin build found, keeping stock firmware_MCU bundle"
    return
  fi

  if [[ -f "$version_file" ]]; then
    head_mcu_version="$(head -n 1 "$version_file")"
  fi

  echo ">> Replacing firmware_MCU main MCU bundle with local build"

  install_with_mode "$LOCAL_MAIN_MCU_BIN" "$MCU_BUNDLE_DIR/at32f403a.bin" 600
  printf '%s\n' "$head_mcu_version" > "$version_file"
  chmod 600 "$version_file"
  printf '%s\n' "$LOCAL_MAIN_MCU_VERSION" > "$version_main_file"
  chmod 600 "$version_main_file"
  printf '%s\n' "$head_mcu_version" > "$version_head_file"
  chmod 600 "$version_head_file"

  : > "$md5_file"
  chmod 600 "$md5_file"
  (
    cd "$MCU_BUNDLE_DIR"
    md5sum VERSION >> "$md5_file"
    md5sum VERSION_MAIN >> "$md5_file"
    md5sum VERSION_HEAD >> "$md5_file"
    md5sum at32f403a.bin >> "$md5_file"
    if [[ -f "$head_bin" ]]; then
      md5sum at32f415.bin >> "$md5_file"
    fi
    if [[ -f "$host_mcu_bin" ]]; then
      md5sum klippy_mcu >> "$md5_file"
    fi
  )
}

rewrite_upgrade_bundle() {
  local upgrade_main_mcu_bin="$BUILD_DIR/at32f403a.bin"
  local upgrade_desc_file="$BUILD_DIR/MCU_DESC"

  if [[ ! -f "$LOCAL_MAIN_MCU_BIN" ]]; then
    return
  fi

  echo ">> Replacing top-level upgrade bundle main MCU payload with local build"
  install_with_mode "$LOCAL_MAIN_MCU_BIN" "$upgrade_main_mcu_bin" 600
  printf '%s\n' "$LOCAL_MAIN_MCU_VERSION" > "$upgrade_desc_file"
  chmod 600 "$upgrade_desc_file"
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

rewrite_main_mcu_bundle
rewrite_upgrade_bundle
