#!/usr/bin/env bash
# CornerWindow uninstaller: removes what install.sh set up.
#
#   ./uninstall.sh             remove launcher, autostart, hotkey, udev rule and build cache
#   ./uninstall.sh --purge     also delete your settings and data (asks first)
#   ./uninstall.sh --yes       with --purge: don't ask
#   ./uninstall.sh --no-sudo   leave the udev rule and i2c-dev autoload alone
#
# To update instead, pull the repo and re-run ./install.sh (it is safe to re-run).
# Distro packages (GTK, ddcutil, ...) are left installed: other programs may use them.
set -euo pipefail

BIN="$HOME/.local/bin/cornerwindow"
AUTOSTART="$HOME/.config/autostart/cornerwindow.desktop"
UDEV_RULE="/etc/udev/rules.d/60-cornerwindow-i2c.rules"
MODULES_LOAD="/etc/modules-load.d/cornerwindow-i2c.conf"
KB_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/cornerwindow/"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/cornerwindow"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/cornerwindow"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/cornerwindow"

USE_SUDO=1; PURGE=0; ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --no-sudo) USE_SUDO=0 ;;
    --purge) PURGE=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option $arg"; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m›\033[0m %s\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m•\033[0m %s\n' "$*"; }

say "Stopping CornerWindow"
gdbus call --session --dest io.github.cornerwindow --object-path /io/github/cornerwindow \
  --method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1 || true
pkill -f 'python3 -m cornerwindow' >/dev/null 2>&1 || true
ok "stopped"

# [experimental] hide_taskbar: quitting restores the taskbar, but not after a crash or a kill
if [ -f "$DATA_DIR/taskbar.json" ]; then
  say "Restoring the taskbar"
  if "$BIN" taskbar show >/dev/null 2>&1 || \
     PYTHONPATH="$(cd "$(dirname "$0")" && pwd)/src" python3 -m cornerwindow taskbar show >/dev/null 2>&1; then
    ok "taskbar back"
  else
    warn "could not restore it; re-enable its auto-hide settings or extension by hand"
  fi
fi

say "Removing launcher and autostart"
if [ -x "$BIN" ]; then
  "$BIN" agent setup --remove --yes >/dev/null 2>&1 && ok "coding agents' hooks removed" || true
fi
rm -f "$BIN" "$AUTOSTART"
ok "$BIN, $AUTOSTART"

say "Removing the global shortcut"
if command -v gsettings >/dev/null && \
   gsettings list-schemas 2>/dev/null | grep -qx org.gnome.settings-daemon.plugins.media-keys; then
  python3 - "$KB_PATH" <<'PY'
import ast, subprocess, sys
path = sys.argv[1]
key = "org.gnome.settings-daemon.plugins.media-keys"
raw = subprocess.check_output(["gsettings", "get", key, "custom-keybindings"], text=True).replace("@as ", "")
current = ast.literal_eval(raw)
if path in current:
    current.remove(path)
    subprocess.run(["gsettings", "set", key, "custom-keybindings", str(current)], check=True)
schema = f"{key}.custom-keybinding:{path}"
for k in ("name", "command", "binding"):
    subprocess.run(["gsettings", "reset", schema, k], check=False, stderr=subprocess.DEVNULL)
PY
  ok "hotkey removed"
else
  warn "no GNOME shortcut to remove (if you bound one by hand, remove it in your desktop's settings)"
fi

say "Removing the build cache"
rm -rf "$CACHE_DIR" && ok "$CACHE_DIR"

say "Removing monitor (I²C) access rule"
if [ -f "$UDEV_RULE" ] || [ -f "$MODULES_LOAD" ]; then
  if [ "$USE_SUDO" = 1 ]; then
    sudo rm -f "$UDEV_RULE" "$MODULES_LOAD"
    sudo udevadm control --reload-rules
    ok "udev rule and i2c-dev autoload removed"
  else
    warn "skipped (--no-sudo): $UDEV_RULE, $MODULES_LOAD remain"
  fi
else
  ok "nothing to remove"
fi

if [ "$PURGE" = 1 ]; then
  say "Deleting settings and data"
  echo "  $CONFIG_DIR"
  echo "  $DATA_DIR"
  reply=n
  if [ "$ASSUME_YES" = 1 ]; then reply=y
  else read -r -p "  Delete these permanently? [y/N] " reply || reply=n; fi
  case "$reply" in
    y|Y|yes) rm -rf "$CONFIG_DIR" "$DATA_DIR"; ok "deleted" ;;
    *) warn "kept" ;;
  esac
else
  echo "Your settings in $CONFIG_DIR and data in $DATA_DIR were kept (use --purge to delete them)."
fi

echo "CornerWindow is removed. The source folder was not touched; delete it yourself if you no longer want it."
