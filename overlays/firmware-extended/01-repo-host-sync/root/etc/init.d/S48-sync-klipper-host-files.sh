#!/bin/sh
# Sync repo-owned Klipper host files from squashfs staging area to the
# userdata klipper directory. Runs before Klipper starts (S48 < S50).
# Always overwrites so that a firmware update wins over the userdata copy.
#
# Also ensures printer.cfg contains the extended klipper include glob so
# that config files dropped into extended/klipper/ by other init scripts
# (e.g. S49extended-config) are picked up by Klipper.

STAGING_DIR="/usr/share/snapmaker-klipper"
KLIPPER_DIR="/home/lava/klipper"
PRINTER_CFG="/home/lava/printer_data/config/printer.cfg"
INCLUDE_LINE="[include extended/klipper/*.cfg]"

MANAGED_FILES="
klippy/toolhead.py
klippy/stepper.py
klippy/extras/tmc2240.py
klippy/extras/motor_phase_calibration.py
"

sync_files() {
    for rel in $MANAGED_FILES; do
        src="$STAGING_DIR/$rel"
        dst="$KLIPPER_DIR/$rel"
        [ -f "$src" ] || continue
        install -D -m 644 -o lava -g lava "$src" "$dst"
        echo "synced $rel"
    done
}

ensure_klipper_include() {
    [ -f "$PRINTER_CFG" ] || return
    grep -qF "$INCLUDE_LINE" "$PRINTER_CFG" && return
    printf '\n### extended klipper includes (managed by firmware)\n%s\n' \
        "$INCLUDE_LINE" >> "$PRINTER_CFG"
    echo "added extended/klipper include to printer.cfg"
}

case "$1" in
    start|restart|reload)
        sync_files
        ensure_klipper_include
        ;;
    stop)
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|reload}"
        exit 1
        ;;
esac

exit 0
