#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

FIRMWARE_FILES=(
  rtl_nic/rtl8153a-4.fw
  rtl_nic/rtl8153b-2.fw
  rtl_nic/rtl8153c-1.fw
  rtl_nic/rtl8156a-2.fw
  rtl_nic/rtl8156b-2.fw
)

HOST_FIRMWARE_DIR=/usr/lib/firmware
ROOTFS_FIRMWARE_DIR="$ROOTFS_DIR/lib/firmware"

for fw_file in "${FIRMWARE_FILES[@]}"; do
  dest="$ROOTFS_FIRMWARE_DIR/$fw_file"
  src="$HOST_FIRMWARE_DIR/$fw_file"

  if [[ -e "$dest" ]]; then
    echo "Error: '$fw_file' already exists in firmware rootfs - refusing to overwrite."
    exit 1
  fi

  if [[ ! -f "$src" ]]; then
    echo "Error: '$fw_file' not found on host system at '$src'."
    exit 1
  fi

  echo "[+] Pulling firmware: $fw_file"
  install -D -m 644 "$src" "$dest"
done
