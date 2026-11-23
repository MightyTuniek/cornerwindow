# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

CornerWindow is a GTK 3 (PyGObject + pycairo) desktop panel for Linux: a quarter-circle "trigger" in a screen corner that spring-animates open into a card panel. Python ≥ 3.11, no PyPI dependencies (PyGObject/pycairo come from the distro). User-facing docs are in `README.md`; the module API and code map are in `docs/DEVELOPING.md`. Read that file before adding a module or backend. To see which features (panel cards) exist right now, look at `FEATURELIST.md`, and keep it updated when adding, removing or renaming a card.

## Commands

```bash
python3 -m pytest                                   # all tests (pyproject sets testpaths and pythonpath=src)
python3 -m pytest tests/test_core.py::test_calc     # a single test
PYTHONPATH=src python3 -m cornerwindow -v           # run from source with verbose logging
PYTHONPATH=src python3 -m cornerwindow doctor       # probe which backends work on this machine
CORNERWINDOW_FPS=1 PYTHONPATH=src python3 -m cornerwindow   # print per-animation frame stats
CORNERWINDOW_DEMO=1 PYTHONPATH=src python3 -m cornerwindow  # made-up data, own D-Bus name and throwaway home
scripts/screenshots.sh                              # regenerate every README picture, headless (Xvfb + demo mode)
scripts/screenshots.sh panel glass --out /tmp/shots # only some, somewhere else (to look at a change)
```

## Screenshots

Any visible change must come with fresh README pictures: run `scripts/screenshots.sh` and commit `docs/screenshots/*.png`. It needs no display or running desktop (it brings its own Xvfb and session bus) and never touches a running CornerWindow. If GTK is missing: `apt install xvfb dbus gir1.2-gtk-3.0 gir1.2-wnck-3.0 python3-gi-cairo`; PyGObject may exist for only one Python version, and the script picks the one that works (use that same `python3.X` for `pytest` too). Scenes live in `scripts/screenshots.py`; fake data for cards in `src/cornerwindow/demo.py`. To look at any state yourself, the scenes have the app object (`app.island`, `app.host.instances[...]`) and `cornerwindow.ui.snapshot.save_island(app, path)`. The script warns when the black and glass pictures differ in size (a theme switch must never move anything).

The app is single-instance over D-Bus (`io.github.cornerwindow`, `Gio.ApplicationFlags.HANDLES_COMMAND_LINE`). If an installed copy is already running (autostart via `install.sh`), launching again only forwards the command line to it. Quit it first when testing source changes: `gdbus call --session --dest io.github.cornerwindow --object-path /io/github/cornerwindow --method org.gtk.Actions.Activate quit [] {}`. There is no linter configured.

## Architecture

- **Entry/wiring**: `__main__.py` handles `doctor`/`version` without GTK, picks `GDK_BACKEND`, then hands off to `app.py` (`CornerWindowApp`). `_build()` wires config → `EventBus`, `State`, `ActivityManager`, `ActionRegistry`, `Services` → `Island` window → `ModuleHost`, then `_layout()` fills the grid from `[modules]`. CLI verbs (`set`, `get`, `action`, `settings`, ...) go through `_dispatch()` in the running instance.
- **Live reload**: every config edit (settings window or an external editor, watched via Gio file monitors) goes through the debounced `schedule_reload()` → `_reload()`. That reloads config, calls `services.reconfigure()` and `host.reconfigure()`, builds a *new grid*, and calls `build()` on every module again. Modules must therefore tolerate `configure()`/`build()` being called repeatedly, and keep long-lived state (timers, clipboard history) outside the widgets.
- **Config layering** (`config.py`): `config/default.toml` → the matching `config/machines/*.toml` (by `[match]` hostname/monitor model) → `~/.config/cornerwindow/config.toml`. Tables merge, lists replace. `Config` keeps each layer so `origin()` can report where a value came from (drives the "this PC"/"changed" badges). `src/cornerwindow/data` is a **symlink to `config/`**, so edit `config/` directly.
- **Adding a config option**: put it with a comment in `config/default.toml` and describe it in `ui/settings/schema.py` (label, editor kind, range). Unlisted keys still show up through `infer_field`, but with a generic editor. `config_edit.py`/`UserConfigWriter` edit the user file in place, preserving comments, and only write values that differ from the lower layers.
- **Services** (`services.py`): lazily created `cached_property` singletons (`display`, `audio`, `media`, ...), so disabled modules cost nothing. To add one, add a property here plus a backend under `platform/`, and update `reconfigure()` if it reads config.
- **Platform layer** (`platform/`): all OS access lives here, behind small interfaces with probing (`available()`). Display has several backends tried in the order of config `display.backend` (native `ddc_i2c`, `ddcutil`, backlight, gamma). Window placement is `x11.py` (EWMH/Wnck) or `layershell.py` (wlr-layer-shell). Features hide themselves when probing fails rather than erroring.
- **Threading**: never block the GTK main thread. Use `core/tasks.run_async(fn, then)` for one-off work, and `LatestWins` for slider→hardware writes (coalesces to the newest value per key). Use `on_main` to get back to the UI thread.
- **Display limits**: `display.limits` is a hard ceiling that every write path must respect (slider, preset, scroll, command, CLI). It is enforced in `DisplayService` and covered by `test_display_limit_clamps_every_write`, so don't bypass the service when writing brightness/contrast.
- **Modules** (`modules/`): one file per card, registered with `@module("id")` from `modules/base.py` (which documents the API). User modules load from `~/.config/cornerwindow/modules/`. Quick toggles are `@toggle` subclasses in `modules/toggles.py`. Modules with `area="tool"` have no card, only commands/actions (`[modules] tools`); prefer a tool or a command over a new card, and make cards hide themselves when they have nothing to show (keeps the panel uncluttered).
- **Rendering/animation** (`ui/`): `island.py` owns the window, morph, input shape and gestures. `shape.py` computes the quarter-circle→panel outline. `content.py` renders cards offscreen and blends them during the morph. `core/anim.py` holds springs (`response`/`damping`) driven by the frame clock. Performance invariants to preserve: the window is sized once and never resized or moved while animating (outside the outline is transparent and click-through); nothing is laid out mid-animation; nothing ticks while collapsed (0% idle CPU). Per-module timers via `ctx.every()` run only while the panel is open.

## Tests

`tests/` covers pure logic only (config layering and TOML editing, springs/outline geometry, DDC packet encoding, wpctl/ddcutil parsers, calculator, timer parsing, module loading, and in `test_features.py` the newer cards' and tools' parsers); there are no GUI tests, so check visible changes with `scripts/screenshots.sh --out /tmp/shots`. `test_shipped_configs_parse` parses the defaults and every machine file present, so keep `config/machines/*.toml` valid when editing it. Machine files are gitignored and stay local.
