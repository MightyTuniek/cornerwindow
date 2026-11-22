#!/usr/bin/env bash
# CornerWindow installer: dependencies, monitor (I²C) access, launcher, autostart, hotkey.
#
#   ./install.sh               everything (asks for sudo once, for packages + the I²C rule)
#   ./install.sh --no-sudo     skip everything that needs root
#   ./install.sh --no-hotkey   don't register the global shortcut
#   ./install.sh --uninstall   same as ./uninstall.sh (see it for --purge)
#
# Safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/cornerwindow"
AUTOSTART="$HOME/.config/autostart/cornerwindow.desktop"
UDEV_RULE="/etc/udev/rules.d/60-cornerwindow-i2c.rules"
MODULES_LOAD="/etc/modules-load.d/cornerwindow-i2c.conf"
HOTKEY="${CORNERWINDOW_HOTKEY:-<Super>backslash}"
KB_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/cornerwindow/"
TOGGLE_CMD="gdbus call --session --dest io.github.cornerwindow --object-path /io/github/cornerwindow --method org.gtk.Actions.Activate toggle [] {}"

USE_SUDO=1; USE_HOTKEY=1; UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --no-sudo) USE_SUDO=0 ;;
    --no-hotkey) USE_HOTKEY=0 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option $arg"; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m›\033[0m %s\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m•\033[0m %s\n' "$*"; }

has_gnome_keys() {
  gsettings list-schemas 2>/dev/null | grep -qx org.gnome.settings-daemon.plugins.media-keys
}

# ── uninstall ────────────────────────────────────────────────────────────────
if [ "$UNINSTALL" = 1 ]; then
  args=()
  [ "$USE_SUDO" = 0 ] && args+=(--no-sudo)
  exec "$REPO/uninstall.sh" "${args[@]}"
fi

# ── 1. dependencies ──────────────────────────────────────────────────────────
say "Checking dependencies"
missing_py() {
  python3 - <<'PY'
import sys
missing = []
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk  # noqa
except Exception:
    missing.append("gtk")
try:
    import cairo  # noqa
    from gi.repository import Gdk
    Gdk.cairo_set_source_pixbuf  # noqa
except Exception:
    missing.append("cairo")
try:
    gi.require_version("Wnck", "3.0")
    from gi.repository import Wnck  # noqa
except Exception:
    missing.append("wnck")
print(" ".join(missing))
PY
}
MISSING="$(missing_py)"
command -v ddcutil >/dev/null || MISSING="$MISSING ddcutil"
if [ "${XDG_SESSION_TYPE:-}" = wayland ] && ! echo "${XDG_CURRENT_DESKTOP:-}" | grep -qi gnome; then
  python3 -c 'import gi; gi.require_version("GtkLayerShell","0.1")' 2>/dev/null || MISSING="$MISSING layershell"
fi
MISSING="$(echo "$MISSING" | xargs)"

if [ -z "$MISSING" ]; then
  ok "all dependencies present"
elif [ "$USE_SUDO" = 0 ]; then
  warn "missing: $MISSING (skipped, --no-sudo)"
else
  PKGS=()
  if command -v apt-get >/dev/null; then
    declare -A MAP=([gtk]="python3-gi gir1.2-gtk-3.0" [cairo]="python3-gi-cairo python3-cairo"
                    [wnck]="gir1.2-wnck-3.0" [ddcutil]="ddcutil" [layershell]="gir1.2-gtklayershell-0.1")
    for m in $MISSING; do PKGS+=(${MAP[$m]}); done
    say "Installing: ${PKGS[*]}"; sudo apt-get install -y "${PKGS[@]}"
  elif command -v dnf >/dev/null; then
    declare -A MAP=([gtk]="python3-gobject gtk3" [cairo]="python3-cairo" [wnck]="libwnck3"
                    [ddcutil]="ddcutil" [layershell]="gtk-layer-shell")
    for m in $MISSING; do PKGS+=(${MAP[$m]}); done
    say "Installing: ${PKGS[*]}"; sudo dnf install -y "${PKGS[@]}"
  elif command -v pacman >/dev/null; then
    declare -A MAP=([gtk]="python-gobject gtk3" [cairo]="python-cairo" [wnck]="libwnck3"
                    [ddcutil]="ddcutil" [layershell]="gtk-layer-shell")
    for m in $MISSING; do PKGS+=(${MAP[$m]}); done
    say "Installing: ${PKGS[*]}"; sudo pacman -S --needed --noconfirm "${PKGS[@]}"
  elif command -v zypper >/dev/null; then
    declare -A MAP=([gtk]="python3-gobject typelib-1_0-Gtk-3_0" [cairo]="python3-cairo python3-gobject-cairo"
                    [wnck]="typelib-1_0-Wnck-3_0" [ddcutil]="ddcutil" [layershell]="typelib-1_0-GtkLayerShell-0_1")
    for m in $MISSING; do PKGS+=(${MAP[$m]}); done
    say "Installing: ${PKGS[*]}"; sudo zypper install -y "${PKGS[@]}"
  else
    warn "unknown package manager; please install: $MISSING"
  fi
fi

# ── 2. monitor access over I²C (DDC/CI) ──────────────────────────────────────
say "Monitor brightness/contrast access (DDC/CI over I²C)"
if [ "$USE_SUDO" = 1 ]; then
  echo "i2c-dev" | sudo tee "$MODULES_LOAD" >/dev/null
  sudo modprobe i2c-dev || true
  # uaccess: logind grants the person at the seat access, no group or re-login needed.
  # GROUP/MODE: fallback for systems without logind.
  sudo tee "$UDEV_RULE" >/dev/null <<'RULE'
# CornerWindow: let the logged-in user talk DDC/CI to monitors (brightness, contrast)
KERNEL=="i2c-[0-9]*", SUBSYSTEM=="i2c-dev", TAG+="uaccess", GROUP="i2c", MODE="0660"
RULE
  getent group i2c >/dev/null || sudo groupadd --system i2c
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=i2c-dev --action=add
  sleep 1
  ok "udev rule installed ($UDEV_RULE)"
else
  warn "skipped (--no-sudo): brightness/contrast need access to /dev/i2c-*"
fi

# ── 3. Liquid Glass kernel (optional: the glass theme hides itself without it) ──
say "Building the Liquid Glass pixel kernel"
if PYTHONPATH="$REPO/src" python3 -c 'from cornerwindow import native; native.build(); print(native.library_path())' >/dev/null 2>&1; then
  ok "built into ~/.cache/cornerwindow"
else
  warn "no C compiler (gcc/clang): the Liquid Glass theme stays unavailable"
fi

# ── 3b. launcher ────────────────────────────────────────────────────────────
say "Installing launcher"
mkdir -p "$(dirname "$BIN")"
cat > "$BIN" <<EOF
#!/bin/sh
# CornerWindow launcher (runs from the source tree: $REPO)
PYTHONPATH="$REPO/src\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m cornerwindow "\$@"
EOF
chmod +x "$BIN"
ok "$BIN"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "~/.local/bin is not on your PATH" ;; esac

# ── 4. autostart ────────────────────────────────────────────────────────────
say "Starting with your session"
mkdir -p "$(dirname "$AUTOSTART")"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=CornerWindow
Comment=Hidden quick panel in your screen corner
Exec=$BIN run
Icon=preferences-desktop-display
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
EOF
ok "$AUTOSTART"

# ── 5. hotkey ───────────────────────────────────────────────────────────────
if [ "$USE_HOTKEY" = 1 ] && has_gnome_keys; then
  say "Global shortcut $HOTKEY"
  if gsettings list-recursively 2>/dev/null | grep -v "custom-keybindings/cornerwindow" | grep -qF "'$HOTKEY'"; then
    warn "$HOTKEY is already used elsewhere; set CORNERWINDOW_HOTKEY='<Super>something' and re-run"
  else
    python3 - "$KB_PATH" <<'PY'
import ast, subprocess, sys
path = sys.argv[1]
key = "org.gnome.settings-daemon.plugins.media-keys"
raw = subprocess.check_output(["gsettings", "get", key, "custom-keybindings"], text=True).replace("@as ", "")
current = ast.literal_eval(raw)
if path not in current:
    current.append(path)
    subprocess.run(["gsettings", "set", key, "custom-keybindings", str(current)], check=True)
PY
    SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB_PATH"
    gsettings set "$SCHEMA" name "CornerWindow"
    gsettings set "$SCHEMA" command "$TOGGLE_CMD"
    gsettings set "$SCHEMA" binding "$HOTKEY"
    ok "$HOTKEY toggles the island"
  fi
elif [ "$USE_HOTKEY" = 1 ]; then
  warn "not GNOME: bind this command to a key in your desktop's settings:"
  echo "      $TOGGLE_CMD"
fi

# ── 6. start + check ────────────────────────────────────────────────────────
say "Starting CornerWindow"
if gdbus call --session --dest io.github.cornerwindow --object-path /io/github/cornerwindow \
     --method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1; then sleep 1; fi
nohup "$BIN" run >/dev/null 2>&1 &
sleep 1
"$BIN" doctor || true
