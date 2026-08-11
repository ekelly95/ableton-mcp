"""Browser commands against the mock tree."""

import pytest

from control_surface.registry import LiveAPIError
from tests.conftest import run_command


def test_browse_roots(registry, ctx):
    result = run_command(registry, ctx, "browse")
    names = [i["name"] for i in result["items"]]
    assert "instruments" in names
    assert "audio_effects" in names


def test_browse_level(registry, ctx):
    result = run_command(registry, ctx, "browse", path=["instruments"])
    names = [i["name"] for i in result["items"]]
    assert "Drift" in names
    drift = next(i for i in result["items"] if i["name"] == "Drift")
    assert drift["is_loadable"] is True


def test_browse_nested_case_insensitive(registry, ctx):
    result = run_command(registry, ctx, "browse", path=["Instruments", "drum rack"])
    assert result["items"][0]["name"] == "Kit-Core 909"


def test_browse_missing_lists_entries(registry, ctx):
    with pytest.raises(LiveAPIError, match="First entries"):
        run_command(registry, ctx, "browse", path=["instruments", "Nonexistent"])


def test_browse_unknown_root(registry, ctx):
    with pytest.raises(LiveAPIError, match="Unknown browser root"):
        run_command(registry, ctx, "browse", path=["vsts"])


def test_load_item_onto_track(registry, ctx, song):
    result = run_command(
        registry, ctx, "load_item", path=["instruments", "Drift"], track_index=1
    )
    assert result["loaded"] == "Drift"
    assert result["onto_track"] == song.tracks[1].name
    assert [d.name for d in song.tracks[1].devices] == ["Drift"]
    assert song.view.selected_track is song.tracks[1]


def test_load_item_folder_rejected(registry, ctx):
    with pytest.raises(LiveAPIError, match="not loadable"):
        run_command(registry, ctx, "load_item", path=["instruments", "Drum Rack"], track_index=0)
