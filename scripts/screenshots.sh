#!/usr/bin/env bash
# Regenerate the README screenshots: scripts/screenshots.sh [panel|glass|commandbar|settings|trigger|agents ...] [--out DIR]
#
# Runs the real app in demo mode on a virtual 3840x2160 screen (2x scale) with its own session
# bus, so it works headless (CI, a cloud container) and never touches a CornerWindow you have
# running. Needs: xvfb-run (xvfb), dbus-run-session (dbus), GTK 3 + PyGObject + pycairo, Wnck.
#   Debian/Ubuntu: sudo apt install xvfb dbus gir1.2-gtk-3.0 gir1.2-wnck-3.0 python3-gi-cairo
set -euo pipefail
cd "$(dirname "$0")/.."

# The first Python that can import GTK 3 (PyGObject is often built for one version only).
python=""
for candidate in python3 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk; import cairo" 2>/dev/null; then
    python="$candidate"
    break
  fi
done
if [ -z "$python" ]; then
  echo "No Python here can import GTK 3. Install: gir1.2-gtk-3.0 gir1.2-wnck-3.0 python3-gi-cairo" >&2
  exit 1
fi
for tool in xvfb-run dbus-run-session; do
  command -v "$tool" >/dev/null || { echo "Missing $tool (apt install xvfb dbus)" >&2; exit 1; }
done

exec xvfb-run -a -s "-screen 0 3840x2160x24" \
  dbus-run-session -- "$python" scripts/screenshots.py "$@" 2> >(grep -v -E "fd limit|AT-SPI|No compositor|^$" >&2)
