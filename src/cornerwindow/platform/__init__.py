"""Everything that touches the operating system lives here, behind small interfaces.

Each service picks the best working backend at startup via `probe()`, so the
same code runs on GNOME/KDE/wlroots, X11/Wayland, desktops and laptops.
"""
