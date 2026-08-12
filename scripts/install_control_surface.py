"""Install (or reinstall) the AbletonMCP control surface into Ableton Live.

Copies the control_surface package to a Remote Scripts location as "AbletonMCP".
Probes, in order of preference:
  1. User Library Remote Scripts (survives Live upgrades, no admin rights).
     Windows: including the OneDrive-redirected Documents variant common on
     Windows 11. macOS: ~/Music/Ableton/User Library.
  2. Each installed Live's own MIDI Remote Scripts folder — under ProgramData
     on Windows, inside the app bundle on macOS.

Run:  python scripts/install_control_surface.py
"""

import importlib.util
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "control_surface"
TARGET_NAME = "AbletonMCP"
PROGRAMDATA_ABLETON = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Ableton"
MACOS_APPLICATIONS = Path("/Applications")


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

    print("Could not find an install location. Probed:")
    for p in probed:
        print(f"  - {p}")
    print("Has Ableton Live been installed and run at least once?")
    sys.exit(1)


def load_version() -> str:
    # config.py imports nothing beyond the stdlib (test_import_purity.py keeps
    # it that way), so load it by path — importing the package __init__ from
    # here would be heavier and could leave __pycache__ noise in the source.
    spec = importlib.util.spec_from_file_location("ableton_mcp_config", SOURCE / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VERSION


def main() -> None:
    if not SOURCE.is_dir():
        print(f"Source not found: {SOURCE}")
        sys.exit(1)

    target_root = pick_target()
    target = target_root / TARGET_NAME

    # Copy-over instead of delete-and-recreate: __pycache__ dirs are often
    # locked by OneDrive sync or Ableton's indexer even after Live quits.
    # Stale .pyc files are harmless — Python recompiles on source mtime change.
    if target.exists():
        stale = [p for p in target.rglob("*.py") if not (SOURCE / p.relative_to(target)).exists()]
        for p in stale:
            try:
                p.unlink()
            except PermissionError:
                print(f"Locked (quit Live and retry): {p}")
                sys.exit(2)

    try:
        shutil.copytree(
            SOURCE,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            dirs_exist_ok=True,
        )
    except PermissionError as e:
        print("A source file is locked — is Ableton Live still running?")
        print(f"  {e}")
        sys.exit(2)

    version = load_version()

    file_count = sum(1 for _ in target.rglob("*.py"))
    print(f"Installed {TARGET_NAME} v{version} ({file_count} files) to:")
    print(f"  {target}")
    print()
    print("Next steps:")
    print("  1. Restart Ableton Live (fully quit it first).")
    print("  2. Options > Preferences > Link, Tempo & MIDI.")
    print(f"  3. In a Control Surface dropdown, pick '{TARGET_NAME}'.")
    print("     Leave Input/Output set to None.")
    print(f"  4. Live's status bar should show '{TARGET_NAME} v{version} ready'.")


if __name__ == "__main__":
    main()
