"""Installer location probing: pure candidate functions, both platforms.

pick_target() itself needs a real filesystem and sys.exit — the candidate
functions carry the platform knowledge, so they are what gets pinned here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from install_control_surface import (  # noqa: E402
    candidate_macos_app_script_dirs,
    candidate_user_libraries,
)


def test_windows_user_libraries_include_onedrive_variant():
    candidates = candidate_user_libraries("win32")
    assert len(candidates) == 2
    assert candidates[0].parts[-3:] == ("Documents", "Ableton", "User Library")
    assert "OneDrive" in str(candidates[1])


def test_macos_user_library_is_under_music():
    candidates = candidate_user_libraries("darwin")
    assert len(candidates) == 1
    assert candidates[0].parts[-3:] == ("Music", "Ableton", "User Library")


def test_macos_app_bundles_globbed_not_hardcoded(tmp_path):
    # Editions and versions vary ("Ableton Live 12 Suite.app", "... Trial.app")
    # — only bundles that actually contain a Remote Scripts dir qualify.
    for name, has_scripts in [
        ("Ableton Live 11 Suite.app", True),
        ("Ableton Live 12 Trial.app", True),
        ("Ableton Live 12 Suite.app", False),
        ("Logic Pro.app", True),
    ]:
        scripts = tmp_path / name / "Contents" / "App-Resources" / "MIDI Remote Scripts"
        if has_scripts:
            scripts.mkdir(parents=True)
        else:
            scripts.parent.mkdir(parents=True)

    found = candidate_macos_app_script_dirs(tmp_path)
    names = [p.parts[-4] for p in found]
    assert names == ["Ableton Live 11 Suite.app", "Ableton Live 12 Trial.app"]


def test_macos_app_bundles_missing_applications_dir(tmp_path):
    assert candidate_macos_app_script_dirs(tmp_path / "nope") == []
