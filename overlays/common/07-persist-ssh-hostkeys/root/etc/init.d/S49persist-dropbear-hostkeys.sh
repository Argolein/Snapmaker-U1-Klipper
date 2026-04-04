#!/bin/sh

set -eu

PERSISTENT_DIR="/oem/dropbear"
LINK_PATH="/etc/dropbear"
LEGACY_TARGET="/var/run/dropbear"

case "$1" in
  start)
    mkdir -p "$PERSISTENT_DIR"
    chmod 700 "$PERSISTENT_DIR"

    if [ -L "$LINK_PATH" ] && [ "$(readlink "$LINK_PATH")" = "$LEGACY_TARGET" ]; then
      rm -f "$LINK_PATH"
      ln -s "$PERSISTENT_DIR" "$LINK_PATH"
    elif [ -d "$LINK_PATH" ] && [ ! -L "$LINK_PATH" ]; then
      find "$LINK_PATH" -maxdepth 1 -type f -name 'dropbear_*_host_key' -exec cp -f {} "$PERSISTENT_DIR"/ \;
      rm -rf "$LINK_PATH"
      ln -s "$PERSISTENT_DIR" "$LINK_PATH"
    elif [ ! -e "$LINK_PATH" ]; then
      ln -s "$PERSISTENT_DIR" "$LINK_PATH"
    fi
    ;;
  stop|restart|reload)
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|reload}"
    exit 1
    ;;
esac

exit 0
