"""Monitor brightness/contrast and night light.

Backends, tried in order unless config forces one:
  ddc        native DDC/CI over /dev/i2c-N (external monitors, fast, pure Python)
  ddcutil    the ddcutil CLI (external monitors, slower, very compatible)
  backlight  /sys/class/backlight via logind (laptop panels, no root needed)
  gamma      xrandr software brightness (last resort, X11 only)
"""
