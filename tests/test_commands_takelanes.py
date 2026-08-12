"""Take lanes: creation, addressing lane clips, and arrangement summaries.

LOM basis (Live 12.3.5 docs): Track.create_take_lane() appends, no delete
exists, TakeLane has name/arrangement_clips/create_midi_clip. VERIFY at the
2.7 checkpoint: main-lane exclusion, the 8-lane cap, silent no-op of
track-scoped clip APIs on lane clips.
"""

import pytest

from control_surface.registry import LiveAPIError, ValidationError
from tests.helpers import run_command


@pytest.fixture()
def with_lane(registry, ctx, song):
    run_command(registry, ctx, "create_take_lane", track_index=0, name="Take A")
    return song.tracks[0].take_lanes[0]


def test_create_take_lane_appends_and_names(registry, ctx, song):
    result = run_command(registry, ctx, "create_take_lane", track_index=0, name="Sparse")
    assert result == {"take_lane_index": 0, "name": "Sparse", "take_lane_count": 1}
    second = run_command(registry, ctx, "create_take_lane", track_index=0)
    assert second["take_lane_index"] == 1
    assert second["take_lane_count"] == 2
    assert song.tracks[0].take_lanes[0].name == "Sparse"


def test_create_take_lane_capped(registry, ctx):
    for _ in range(8):
        run_command(registry, ctx, "create_take_lane", track_index=0)
    with pytest.raises(LiveAPIError, match="cannot be deleted"):
        run_command(registry, ctx, "create_take_lane", track_index=0)


def test_arrangement_omits_lanes_when_none(registry, ctx):
    result = run_command(registry, ctx, "get_arrangement", track_index=0)
    assert "take_lanes" not in result["tracks"][0]


def test_arrangement_lists_lanes_with_clips(registry, ctx, with_lane):
    run_command(
        registry,
        ctx,
        "create_arrangement_clip",
        track_index=0,
        start_time=4.0,
        length_beats=2.0,
        take_lane_index=0,
    )
    result = run_command(registry, ctx, "get_arrangement", track_index=0)
    lanes = result["tracks"][0]["take_lanes"]
    assert lanes[0]["take_lane_index"] == 0
    assert lanes[0]["name"] == "Take A"
    (clip,) = lanes[0]["clips"]
    assert clip["start_time"] == 4.0
    assert clip["is_midi_clip"] is True
    # The lane clip must NOT appear in the track's main-lane clip list.
    assert result["tracks"][0]["arrangement_clips"] == []


def test_create_arrangement_clip_in_lane_reports_lane(registry, ctx, with_lane):
    result = run_command(
        registry,
        ctx,
        "create_arrangement_clip",
        track_index=0,
        start_time=0.0,
        length_beats=4.0,
        take_lane_index=0,
    )
    assert result["created"]["take_lane_index"] == 0
    assert result["created"]["arrangement_clip_index"] == 0
    assert len(with_lane.arrangement_clips) == 1


def test_create_arrangement_clip_unknown_lane(registry, ctx):
    with pytest.raises(LiveAPIError, match="take lanes"):
        run_command(
            registry,
            ctx,
            "create_arrangement_clip",
            track_index=0,
            start_time=0.0,
            length_beats=4.0,
            take_lane_index=0,
        )


def test_notes_round_trip_in_lane_clip(registry, ctx, with_lane):
    run_command(
        registry,
        ctx,
        "create_arrangement_clip",
        track_index=0,
        start_time=0.0,
        length_beats=4.0,
        take_lane_index=0,
    )
    run_command(
        registry,
        ctx,
        "add_notes",
        track_index=0,
        arrangement_clip_index=0,
        take_lane_index=0,
        notes=[
            {
                "pitch": 60,
                "start_time": 0.0,
                "duration": 1.0,
                "velocity": 100.0,
                "mute": False,
                "probability": 1.0,
                "velocity_deviation": 0.0,
                "release_velocity": 64.0,
            }
        ],
    )
    read = run_command(
        registry, ctx, "get_notes", track_index=0, arrangement_clip_index=0, take_lane_index=0
    )
    assert read["note_count"] == 1
    note_id = read["notes"][0]["note_id"]

    run_command(
        registry,
        ctx,
        "update_notes",
        track_index=0,
        arrangement_clip_index=0,
        take_lane_index=0,
        modifications=[{"note_id": note_id, "velocity": 80}],
    )
    read = run_command(
        registry, ctx, "get_notes", track_index=0, arrangement_clip_index=0, take_lane_index=0
    )
    assert read["notes"][0]["velocity"] == 80.0

    run_command(
        registry,
        ctx,
        "remove_notes",
        track_index=0,
        arrangement_clip_index=0,
        take_lane_index=0,
        note_ids=[note_id],
    )
    read = run_command(
        registry, ctx, "get_notes", track_index=0, arrangement_clip_index=0, take_lane_index=0
    )
    assert read["note_count"] == 0


def test_set_clip_renames_lane_clip(registry, ctx, with_lane):
    run_command(
        registry,
        ctx,
        "create_arrangement_clip",
        track_index=0,
        start_time=0.0,
        length_beats=4.0,
        take_lane_index=0,
    )
    run_command(
        registry,
        ctx,
        "set_clip",
        track_index=0,
        arrangement_clip_index=0,
        take_lane_index=0,
        name="Take A — sparse",
    )
    assert with_lane.arrangement_clips[0].name == "Take A — sparse"


def test_take_lane_index_requires_arrangement_clip_index(registry, ctx, with_lane):
    with pytest.raises(ValidationError, match="arrangement_clip_index"):
        run_command(registry, ctx, "get_notes", track_index=0, slot_index=0, take_lane_index=0)


def test_lane_clip_indices_are_per_lane(registry, ctx, with_lane):
    # A main-lane clip at index 0 must not shadow lane clip addressing.
    run_command(
        registry, ctx, "create_arrangement_clip", track_index=0, start_time=0.0, length_beats=4.0
    )
    run_command(
        registry,
        ctx,
        "create_arrangement_clip",
        track_index=0,
        start_time=8.0,
        length_beats=4.0,
        take_lane_index=0,
    )
    read = run_command(
        registry, ctx, "get_notes", track_index=0, arrangement_clip_index=0, take_lane_index=0
    )
    assert read["clip_length"] == 4.0
    # Lane index out of the lane's range errors even though the track has clips.
    with pytest.raises(LiveAPIError, match="take lane has 1 clips"):
        run_command(
            registry, ctx, "get_notes", track_index=0, arrangement_clip_index=1, take_lane_index=0
        )
