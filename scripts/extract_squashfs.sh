#!/usr/bin/env bash

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <upgrade.bin> <output-dir>"
  exit 1
fi

set -eo pipefail

IN="$(realpath "$1")"
OUT_DIR="$(realpath -m "$2")"
ROOT_DIR="$(realpath "$(dirname "$0")/..")"

rm -rf "$OUT_DIR"

echo ">> Unpacking firmware..."
"$ROOT_DIR/scripts/helpers/unpack_firmware.sh" "$IN" "$OUT_DIR"

echo ">> Extracting squashfs from rootfs.img..."
unsquashfs -d "$OUT_DIR/rootfs" "$OUT_DIR/rk-unpacked/rootfs.img"

echo ">> Done. Extracted rootfs is in $OUT_DIR/rootfs/."
