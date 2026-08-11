"""Track commands: reads, creation, duplication, deletion, batch setter."""

import pytest

from control_surface.registry import LiveAPIError, ValidationError
from tests.conftest import run_command


def test_get_tracks_shape(registry, ctx, song):
    result = run_command(registry, ctx, "get_tracks")
    assert result["track_count"] == 2
    assert len(result["return_tracks"]) == 2
    assert result["master_track"]["type"] == "master"
    first = result["tracks"][0]
    assert first["is_midi"] is True
    assert 0.0 <= first["volume"] <= 1.0
    assert len(first["sends"]) == 2
    assert "devices" not in first


def test_get_tracks_with_flags(registry, ctx, song):
    result = run_command(registry, ctx, "get_tracks", include_devices=True, include_clips=True)
    first = result["tracks"][0]
    assert first["devices"] == []
    assert len(first["clip_slots"]) == 4


def test_create_midi_track(registry, ctx, song):
    result = run_command(registry, ctx, "create_track", type="midi")
    assert result["track_index"] == 2
    assert len(song.tracks) == 3


def test_create_track_at_index(registry, ctx, song):
    result = run_command(registry, ctx, "create_track", type="audio", index=0)
    assert result["track_index"] == 0
    assert song.tracks[0].has_audio_input


def test_create_return_track_ignores_index(registry, ctx, song):
    result = run_command(registry, ctx, "create_track", type="return", index=0)
    assert result["track_type"] == "return"
    assert result["track_index"] == 2
    assert len(song.return_tracks) == 3


def test_duplicate_track(registry, ctx, song):
    result = run_command(registry, ctx, "duplicate_track", track_index=0)
    assert result["track_index"] == 1
    assert len(song.tracks) == 3


def test_delete_track(registry, ctx, song):
    result = run_command(registry, ctx, "delete_track", track_index=0)
    assert result["track_count"] == 1
    assert len(song.tracks) == 1


def test_delete_track_out_of_range(registry, ctx, song):
    with pytest.raises(LiveAPIError, match="out of range"):
        run_command(registry, ctx, "delete_track", track_index=99)


def test_set_track_batch(registry, ctx, song):
    result = run_command(
        registry,
        ctx,
        "set_track",
        track_index=0,
        name="Drums",
        color_index=10,
        volume=0.85,
        pan=0.5,
        arm=True,
        mute=False,
        solo=True,
        sends=[{"index": 0, "value": 0.5}, {"index": 1, "value": 0.25}],
    )
    track = song.tracks[0]
    assert track.name == "Drums"
    assert track.arm is True
    assert track.solo is True
    assert abs(track.mixer_device.volume.value - 0.85) < 1e-9
    assert abs(track.mixer_device.panning.value - 0.0) < 1e-9  # 0.5 normalized = center
    assert abs(track.mixer_device.sends[0].value - 0.5) < 1e-9
    assert result["name"] == "Drums"


def test_set_track_master(registry, ctx, song):
    result = run_command(registry, ctx, "set_track", track_type="master", volume=0.7)
    assert abs(song.master_track.mixer_device.volume.value - 0.7) < 1e-9
    assert result["type"] == "master"


def test_set_track_master_cannot_mute(registry, ctx, song):
    with pytest.raises(LiveAPIError, match="cannot be muted"):
        run_command(registry, ctx, "set_track", track_type="master", mute=True)


def test_set_track_return_by_index(registry, ctx, song):
    run_command(registry, ctx, "set_track", track_type="return", track_index=1, name="Delay Bus")
    assert song.return_tracks[1].name == "Delay Bus"


def test_set_track_requires_index_for_track(registry, ctx, song):
    with pytest.raises(LiveAPIError, match="track_index is required"):
        run_command(registry, ctx, "set_track", name="X")


def test_set_track_send_out_of_range(registry, ctx, song):
    with pytest.raises(LiveAPIError, match="Send index"):
        run_command(registry, ctx, "set_track", track_index=0, sends=[{"index": 9, "value": 0.5}])


def test_master_batch_rejection_leaves_volume_unchanged(registry, ctx, song):
    # Validate-then-write: the mute rejection must fire BEFORE the volume
    # write (the old code renamed/re-volumed master, then raised).
    original = song.master_track.mixer_device.volume.value
    with pytest.raises(LiveAPIError, match="cannot be muted"):
        run_command(registry, ctx, "set_track", track_type="master", volume=0.5, mute=True)
    assert song.master_track.mixer_device.volume.value == original


def test_bad_send_index_leaves_earlier_fields_unwritten(registry, ctx, song):
    original_name = song.tracks[0].name
    original_send = song.tracks[0].mixer_device.sends[0].value
    with pytest.raises(LiveAPIError, match="Send index"):
        run_command(
            registry,
            ctx,
            "set_track",
            track_index=0,
            name="New Name",
            sends=[{"index": 0, "value": 0.7}, {"index": 9, "value": 0.5}],
        )
    assert song.tracks[0].name == original_name
    assert song.tracks[0].mixer_device.sends[0].value == original_send


def test_create_track_rejects_bad_type(registry, ctx, song):
    with pytest.raises(ValidationError):
        run_command(registry, ctx, "create_track", type="warp")
