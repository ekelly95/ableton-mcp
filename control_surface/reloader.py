"""Reload command handlers from disk without restarting Live.

Live imports a Remote Script once, at startup, so every handler change used to
mean a Live restart. reload_command_modules() drops the command and helper
modules from sys.modules and imports the commands package again: a fresh
import in Python's normal dependency order, so no module keeps a stale
reference to another, and every command re-registers into the (cleared)
REGISTRY. If the new code fails to import, the previous modules and commands
are put back and the error is raised.

Not reloaded, because live objects depend on their identity: the socket
server, the main-thread queue and marshal, the registry itself, the error
classes (the server matches exceptions by class), config (VERSION) and the
package __init__. Changes there still need a Live restart.
"""

import importlib
import sys
from typing import Any

from .registry import REGISTRY

_BASE = __package__  # "AbletonMCP" inside Live, "control_surface" in tests
_RELOADED_PACKAGES = (f"{_BASE}.commands", f"{_BASE}.utils")


def _reloadable() -> list[str]:
    return sorted(
        name
        for name in sys.modules
        if any(name == pkg or name.startswith(pkg + ".") for pkg in _RELOADED_PACKAGES)
    )


def reload_command_modules() -> dict[str, Any]:
    names = _reloadable()
    saved_modules = {name: sys.modules[name] for name in names}
    saved_commands = dict(REGISTRY._commands)
    saved_categories = {k: list(v) for k, v in REGISTRY._categories.items()}

    for name in names:
        del sys.modules[name]
    REGISTRY.clear()
    try:
        importlib.import_module(f"{_BASE}.commands")
    except Exception:
        for name in _reloadable():
            del sys.modules[name]
        sys.modules.update(saved_modules)
        REGISTRY.clear()
        REGISTRY._commands.update(saved_commands)
        REGISTRY._categories.update(saved_categories)
        raise
    return {
        "reloaded": len(_reloadable()),
        "command_count": len(REGISTRY),
        "schema_hash": REGISTRY.schema_hash(),
    }
