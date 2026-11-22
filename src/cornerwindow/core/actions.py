"""Named actions, so config can bind gestures to features without knowing modules.

    ctx.actions.register("display.brightness.step", step_fn)    # module side
    [trigger] scroll = "display.brightness.step"                 # config side

Scroll-bound actions are called with `delta` (+1 / -1, or fractional for smooth
scrolling); press-bound actions are called without arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import log

_log = log.get("actions")


@dataclass
class Action:
    name: str
    fn: Callable[..., Any]
    description: str = ""


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, name: str, fn: Callable[..., Any], description: str = "") -> None:
        self._actions[name] = Action(name, fn, description)

    def has(self, name: str | None) -> bool:
        return bool(name) and name in self._actions

    def run(self, name: str | None, **kwargs: Any) -> Any:
        if not name or name == "none":
            return None
        action = self._actions.get(name)
        if action is None:
            _log.warning("Unknown action %r (known: %s)", name, ", ".join(sorted(self._actions)))
            return None
        try:
            return action.fn(**kwargs)
        except Exception:
            _log.exception("Action %s failed", name)
            return None

    def names(self) -> list[str]:
        return sorted(self._actions)
