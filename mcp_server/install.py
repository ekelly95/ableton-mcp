"""Install (or update) the AbletonMCP control surface into Ableton Live.

Copies the control_surface package that ships inside this Python package —
so it works from a wheel, `uvx`, or an MCP Bundle, not only a repo checkout —
to a Remote Scripts location as "AbletonMCP". Probes, in order:
  1. User Library Remote Scripts (survives Live upgrades, no admin rights).
     Windows: including the OneDrive-redirected Documents variant common on
     Windows 11. macOS: ~/Music/Ableton/User Library.
  2. Each installed Live's own MIDI Remote Scripts folder — under ProgramData
     on Windows, inside the app bundle on macOS.

With reload=True and Live running, handler changes (commands/, utils/) are
hot-reloaded (2.16); anything else needs a Live restart, and the result says
so. Entry points: the install_control_surface tool, `ableton-mcp-install`,
and scripts/install_control_surface.py.
"""

import filecmp
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import control_surface

SOURCE = Path(control_surface.__file__).resolve().parent
TARGET_NAME = "AbletonMCP"
PROGRAMDATA_ABLETON = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Ableton"
MACOS_APPLICATIONS = Path("/Applications")
# Modules the running bridge can swap in place (control_surface/reloader.py).
RELOADABLE_DIRS = ("commands", "utils")


class InstallError(RuntimeError):
    pass


def candidate_user_libraries(platform: str = sys.platform) -> list[Path]:
    home = Path.home()
    if platform == "darwin":
        return [home / "Music" / "Ableton" / "User Library"]
    return [
        home / "Documents" / "Ableton" / "User Library",
        home / "OneDrive" / "Documents" / "Ableton" / "User Library",
    ]


def candidate_programdata_script_dirs() -> list[Path]:
    if not PROGRAMDATA_ABLETON.exists():
        return []
    dirs = []
    for install in sorted(PROGRAMDATA_ABLETON.iterdir()):
        scripts = install / "Resources" / "MIDI Remote Scripts"
        if scripts.is_dir():
            dirs.append(scripts)
    return dirs


def candidate_macos_app_script_dirs(applications: Path = MACOS_APPLICATIONS) -> list[Path]:
    # macOS installs are self-contained app bundles named per version
    # ("Ableton Live 12 Suite.app") — glob rather than hard-code editions.
    if not applications.exists():
        return []
    dirs = []
    for bundle in sorted(applications.glob("Ableton Live*.app")):
        scripts = bundle / "Contents" / "App-Resources" / "MIDI Remote Scripts"
        if scripts.is_dir():
            dirs.append(scripts)
    return dirs


def pick_target() -> Path:
    probed: list[str] = []
    for library in candidate_user_libraries():
        probed.append(str(library))
        if library.is_dir():
            target_root = library / "Remote Scripts"
            target_root.mkdir(exist_ok=True)
            return target_root
    if sys.platform == "darwin":
        app_dirs = candidate_macos_app_script_dirs()
        if app_dirs:
            return app_dirs[-1]  # newest install wins (sorted by bundle name)
        probed.append(str(MACOS_APPLICATIONS / "Ableton Live*.app" / "Contents" / "App-Resources"))
    else:
        programdata = candidate_programdata_script_dirs()
        if programdata:
            return programdata[-1]  # newest install wins (sorted)
        probed.append(str(PROGRAMDATA_ABLETON / "*" / "Resources" / "MIDI Remote Scripts"))
    raise InstallError(
        "Could not find Ableton Live's User Library or Remote Scripts folder "
        f"(looked in: {'; '.join(probed)}). Has Live been installed and run at least once?"
    )


def changed_files(target: Path) -> list[str]:
    """Source .py files that differ from (or are missing in) the install."""
    changed = []
    for src in SOURCE.rglob("*.py"):
        if "__pycache__" in src.parts:
            continue
        rel = src.relative_to(SOURCE)
        dst = target / rel
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            changed.append(rel.as_posix())
    return changed


def load_version() -> str:
    # config.py imports nothing beyond the stdlib, so load it by path.
    spec = importlib.util.spec_from_file_location("ableton_mcp_config", SOURCE / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VERSION


def _copy(target: Path) -> None:
    # Copy-over instead of delete-and-recreate: __pycache__ dirs are often
    # locked by OneDrive sync or Ableton's indexer even after Live quits.
    if target.exists():
        for stale in target.rglob("*.py"):
            if not (SOURCE / stale.relative_to(target)).exists():
                try:
                    stale.unlink()
                except PermissionError as e:
                    raise InstallError(f"Locked (quit Live and retry): {stale}") from e
    try:
        shutil.copytree(
            SOURCE,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            dirs_exist_ok=True,
        )
    except PermissionError as e:
        raise InstallError(f"A file is locked — is Live still running? ({e})") from e


def _hot_reload(changed: list[str], client: Any) -> tuple[bool, str]:
    """(no restart needed, what happened)."""
    from .client import AbletonClient, AbletonConnectionError, CommandError

    needs_restart = [c for c in changed if c.split("/")[0] not in RELOADABLE_DIRS]
    if needs_restart:
        return False, f"These can't be hot-reloaded: {', '.join(needs_restart)}"
    own = client is None
    client = client or AbletonClient.from_env(timeout=30.0)
    try:
        result = client.send("reload_commands")
    except AbletonConnectionError:
        return True, "Live isn't running; it loads the new version when it starts."
    except CommandError as e:
        return False, f"Reload failed and Live kept the previous version: {e}"
    finally:
        if own:
            client.close()
    return True, f"Reloaded into the running Live ({result['command_count']} commands)."


def install(reload: bool = True, client: Any = None) -> dict[str, Any]:
    target = pick_target() / TARGET_NAME
    first_install = not target.exists()
    changed = changed_files(target)
    if changed:
        _copy(target)
    result: dict[str, Any] = {
        "installed_to": str(target),
        "version": load_version(),
        "changed_files": len(changed),
    }
    if first_install:
        result["next_steps"] = (
            "Restart Ableton Live, then Options > Preferences > Link, Tempo & MIDI: "
            f"pick '{TARGET_NAME}' in a free Control Surface slot (Input/Output: None)."
        )
    elif not changed:
        result["status"] = "Already up to date."
    elif reload:
        ok, message = _hot_reload(changed, client)
        result["status"] = message if ok else f"Restart Ableton Live to finish. {message}"
    else:
        result["status"] = "Restart Ableton Live to load the new version."
    return result


def main() -> None:
    """`ableton-mcp-install [--no-reload]`."""
    try:
        result = install(reload="--no-reload" not in sys.argv[1:])
    except InstallError as e:
        print(e)
        sys.exit(1)
    print(f"AbletonMCP v{result['version']} → {result['installed_to']}")
    for key in ("status", "next_steps"):
        if key in result:
            print(result[key])
