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


# --- install() end to end, against a fake User Library (2.20) ---------------

import pytest  # noqa: E402

from mcp_server import install as installer  # noqa: E402


class FakeLive:
    def __init__(self, reachable=True):
        self.reachable = reachable
        self.sent = []

    def send(self, command, **params):
        from mcp_server.client import AbletonConnectionError

        if not self.reachable:
            raise AbletonConnectionError("Live is not running")
        self.sent.append(command)
        return {"command_count": 51}


@pytest.fixture()
def user_library(tmp_path, monkeypatch):
    home = tmp_path / "home"
    library = home / ("Music" if sys.platform == "darwin" else "Documents") / "Ableton"
    (library / "User Library").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return library / "User Library" / "Remote Scripts" / "AbletonMCP"


def test_first_install_copies_and_explains_the_preferences_step(user_library):
    result = installer.install(client=FakeLive())
    assert result["installed_to"] == str(user_library)
    assert (user_library / "__init__.py").exists()
    assert (user_library / "commands" / "clips.py").exists()
    assert not list(user_library.rglob("__pycache__"))
    assert "Control Surface" in result["next_steps"]


def test_reinstall_with_nothing_changed(user_library):
    installer.install(client=FakeLive())
    assert installer.install(client=FakeLive())["status"] == "Already up to date."


def test_handler_change_hot_reloads(user_library):
    installer.install(client=FakeLive())
    (user_library / "commands" / "clips.py").write_text("# stale copy\n")
    live = FakeLive()
    result = installer.install(client=live)
    assert live.sent == ["reload_commands"]
    assert result["status"].startswith("Reloaded into the running Live")
    assert (user_library / "commands" / "clips.py").read_text() != "# stale copy\n"


def test_core_change_asks_for_a_restart(user_library):
    installer.install(client=FakeLive())
    (user_library / "socket_server.py").write_text("# stale copy\n")
    live = FakeLive()
    result = installer.install(client=live)
    assert live.sent == []
    assert "Restart Ableton Live" in result["status"]
    assert "socket_server.py" in result["status"]


def test_live_not_running_is_fine(user_library):
    installer.install(client=FakeLive())
    (user_library / "commands" / "clips.py").write_text("# stale copy\n")
    result = installer.install(client=FakeLive(reachable=False))
    assert "loads the new version when it starts" in result["status"]


def test_no_live_installed_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "nobody")
    monkeypatch.setattr(installer, "PROGRAMDATA_ABLETON", tmp_path / "none")
    monkeypatch.setattr(installer, "MACOS_APPLICATIONS", tmp_path / "none")
    with pytest.raises(installer.InstallError, match="Has Live been installed"):
        installer.install(client=FakeLive())
