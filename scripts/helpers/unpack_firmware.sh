#!/usr/bin/env bash

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <upgrade.bin> <output_dir>"
  exit 1
fi

set -eo pipefail

IN="$(realpath "$1")"
OUT_DIR="$(realpath -m "$2")"
ROOT_DIR="$(realpath "$(dirname "$0")/../..")"

if [[ ! -f "$IN" ]]; then
  echo "Error: Input file $IN does not exist."
  exit 1
fi

if [[ -d "$OUT_DIR" ]]; then
  echo "Error: Output directory $OUT_DIR already exists."
  exit 1
fi

mkdir -p "$OUT_DIR"
cd "$OUT_DIR/"

echo ">> Extracting input firmware $IN..."
"$ROOT_DIR/tools/upfile/upfile" unpack "$IN"

echo ">> Unpacking update.img..."
"$ROOT_DIR/tools/rk2918_tools/img_unpack" update.img rk-loader.img rk-rom.img

echo ">> Unpacking rk-rom.img to rk-unpacked/..."
"$ROOT_DIR/tools/rk2918_tools/afptool" -unpack rk-rom.img rk-unpacked

echo ">> Done. Unpacked files are in $OUT_DIR/"
