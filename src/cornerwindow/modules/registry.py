"""Finds modules (built-in and user-provided) and wires them into the island."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from ..core import log
from .base import Command, Module, ModuleContext, registered

_log = log.get("modules")


def load_builtin(module_id: str) -> None:
    if module_id in registered():
        return
    try:
        importlib.import_module(f"cornerwindow.modules.{module_id}")
    except ModuleNotFoundError as exc:
        if exc.name != f"cornerwindow.modules.{module_id}":
            raise
        _log.debug("No built-in module %r", module_id)


def load_user_modules(directories: list[Path]) -> list[str]:
    """Import every *.py in the given directories; they register via @module."""
    loaded = []
    for directory in directories:
        directory = Path(directory).expanduser()
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            name = f"cornerwindow_user_{path.stem}"
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                continue
            try:
                spec.loader.exec_module(importlib.util.module_from_spec(spec))
                loaded.append(path.stem)
                _log.info("Loaded user module file %s", path)
            except Exception:
                _log.exception("User module %s failed to load", path)
    return loaded


class ModuleHost:
    """Instantiates modules listed in config and drives their lifecycle."""

    def __init__(self, make_context) -> None:
        self._make_context = make_context
        self.instances: dict[str, Module] = {}

    def get(self, module_id: str) -> Module | None:
        if module_id in self.instances:
            return self.instances[module_id]
        load_builtin(module_id)
        cls = registered().get(module_id)
        if cls is None:
            _log.warning("Unknown module %r (check [modules] in your config)", module_id)
            return None
        ctx: ModuleContext = self._make_context(module_id)
        try:
            instance = cls(ctx)
            instance.setup()
            instance.configure()
        except Exception:
            _log.exception("Module %s failed during setup", module_id)
            return None
        self.instances[module_id] = instance
        return instance

    def build(self, module_id: str):
        instance = self.get(module_id)
        if instance is None:
            return None
        try:
            if not instance.available():
                _log.info("Module %s not available here, hiding it", module_id)
                return None
            return instance.build()
        except Exception:
            _log.exception("Module %s failed to build", module_id)
            return None

    def reconfigure(self, config) -> None:
        """Settings changed: give every module its new config section. Widgets are rebuilt
        afterwards via build(); module instances (and their state, e.g. a running timer) survive."""
        for module_id, instance in self.instances.items():
            ctx = instance.ctx
            ctx._stop_timers()
            ctx._timers.clear()  # build() registers them again
            ctx.config = config
            ctx.section = config.section(module_id)
            instance.config = ctx.section
            try:
                instance.configure()
            except Exception:
                _log.exception("%s.configure failed", module_id)

    def known_ids(self) -> list[str]:
        """Every module id that could be placed in a column (built-in files + loaded)."""
        from pathlib import Path

        builtin = {p.stem for p in Path(__file__).parent.glob("*.py")
                   if p.stem not in ("__init__", "base", "registry")}
        return sorted(builtin | set(registered()))

    def opened(self) -> None:
        for instance in self.instances.values():
            try:
                instance.on_open()
            except Exception:
                _log.exception("%s.on_open failed", instance.id)
            instance.ctx._start_timers()

    def closed(self) -> None:
        for instance in self.instances.values():
            instance.ctx._stop_timers()
            try:
                instance.on_close()
            except Exception:
                _log.exception("%s.on_close failed", instance.id)

    def commands(self) -> list[Command]:
        out: list[Command] = []
        for instance in self.instances.values():
            try:
                out.extend(instance.commands())
            except Exception:
                _log.exception("%s.commands failed", instance.id)
        return out
