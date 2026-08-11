"""Install (or reinstall) the AbletonMCP control surface into Ableton Live.

Copies the control_surface package to a Remote Scripts location as "AbletonMCP".
Probes, in order of preference:
  1. User Library Remote Scripts (survives Live upgrades, no admin rights),
     including the OneDrive-redirected Documents variant common on Windows 11.
  2. Each installed Live's own MIDI Remote Scripts folder under ProgramData.

Run:  python scripts/install_control_surface.py
"""

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "control_surface"
TARGET_NAME = "AbletonMCP"


def candidate_user_libraries() -> list[Path]:
    home = Path.home()
    return [
        home / "Documents" / "Ableton" / "User Library",
        home / "OneDrive" / "Documents" / "Ableton" / "User Library",
    ]


def candidate_programdata_script_dirs() -> list[Path]:
    ableton_root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Ableton"
    if not ableton_root.exists():
        return []
    dirs = []
    for install in sorted(ableton_root.iterdir()):
        scripts = install / "Resources" / "MIDI Remote Scripts"
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

    programdata = candidate_programdata_script_dirs()
    if programdata:
        return programdata[-1]  # newest install wins (sorted)

    print("Could not find an install location. Probed:")
    for p in probed:
        print(f"  - {p}")
    print(
        f"  - {Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'Ableton'}/*/Resources/MIDI Remote Scripts"
    )
    print("Has Ableton Live been installed and run at least once?")
    sys.exit(1)


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

    version = "unknown"
    for line in (SOURCE / "config.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("VERSION"):
            version = line.split('"')[1]
            break

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
