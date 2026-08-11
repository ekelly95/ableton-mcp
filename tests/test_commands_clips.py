"""Clip, scene, and note commands — including the modern note-ID API."""

import pytest

from control_surface.registry import LiveAPIError
from tests.conftest import run_command

MELODY = [
    {"pitch": 60, "start_time": 0.0, "duration": 0.5},
    {"pitch": 64, "start_time": 0.5, "duration": 0.5, "velocity": 90},
    {"pitch": 67, "start_time": 1.0, "duration": 1.0, "probability": 0.75},
]


@pytest.fixture()
def with_clip(registry, ctx, song):
    run_command(registry, ctx, "create_clip", track_index=0, slot_index=0, length_beats=4.0)
    return song.tracks[0].clip_slots[0].clip


class TestClips:
    def test_create_clip(self, registry, ctx, song):
        result = run_command(
            registry, ctx, "create_clip", track_index=0, slot_index=1, length_beats=8.0
        )
        assert result["length"] == 8.0
        assert song.tracks[0].clip_slots[1].has_clip

    def test_create_clip_occupied_slot(self, registry, ctx, song, with_clip):
        with pytest.raises(LiveAPIError, match="already has a clip"):
            run_command(
                registry, ctx, "create_clip", track_index=0, slot_index=0, length_beats=4.0
            )

    def test_create_clip_needs_midi_track(self, registry, ctx, song):
        song.tracks[1].has_midi_input = False
        song.tracks[1].has_audio_input = True
        with pytest.raises(LiveAPIError, match="not a MIDI track"):
            run_command(
                registry, ctx, "create_clip", track_index=1, slot_index=0, length_beats=4.0
            )

    def test_set_clip(self, registry, ctx, song, with_clip):
        result = run_command(
            registry,
            ctx,
            "set_clip",
            track_index=0,
            slot_index=0,
            name="Verse",
            looping=True,
            loop_start=0.0,
            loop_end=2.0,
        )
        assert with_clip.name == "Verse"
        assert with_clip.loop_end == 2.0
        assert result["name"] == "Verse"

    def test_duplicate_clip(self, registry, ctx, song, with_clip):
        with_clip.name = "Original"
        result = run_command(registry, ctx, "duplicate_clip", track_index=0, slot_index=0)
        assert result["new_slot"] == 1
        copy = song.tracks[0].clip_slots[1].clip
        assert copy.name == "Original"

    def test_duplicate_clip_next_occupied(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "create_clip", track_index=0, slot_index=1, length_beats=4.0)
        with pytest.raises(LiveAPIError, match="occupied"):
            run_command(registry, ctx, "duplicate_clip", track_index=0, slot_index=0)

    def test_delete_clip(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "delete_clip", track_index=0, slot_index=0)
        assert not song.tracks[0].clip_slots[0].has_clip

    def test_launch_and_stop(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "launch_clip", track_index=0, slot_index=0)
        assert with_clip.is_playing
        run_command(registry, ctx, "stop_clips", track_index=0)
        assert not with_clip.is_playing

    def test_stop_all_clips(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "launch_clip", track_index=0, slot_index=0)
        result = run_command(registry, ctx, "stop_clips")
        assert result["stopped"] == "all"
        assert not with_clip.is_playing

    def test_get_clips(self, registry, ctx, song, with_clip):
        result = run_command(registry, ctx, "get_clips")
        assert len(result["tracks"]) == 2
        assert result["tracks"][0]["clip_slots"][0]["has_clip"] is True
        assert result["tracks"][0]["clip_slots"][1]["has_clip"] is False
        assert len(result["scenes"]) == 4


class TestScenes:
    def test_launch_scene(self, registry, ctx, song):
        run_command(registry, ctx, "launch_scene", scene_index=2)
        assert song.scenes[2].is_triggered

    def test_create_scene_appends(self, registry, ctx, song):
        result = run_command(registry, ctx, "create_scene")
        assert result["scene_count"] == 5
        assert len(song.tracks[0].clip_slots) == 5

    def test_create_scene_at_index(self, registry, ctx, song):
        result = run_command(registry, ctx, "create_scene", index=0)
        assert result["scene_index"] == 0

    def test_delete_scene(self, registry, ctx, song):
        run_command(registry, ctx, "delete_scene", scene_index=0)
        assert len(song.scenes) == 3
        assert len(song.tracks[0].clip_slots) == 3


class TestNotes:
    def test_add_and_get_notes(self, registry, ctx, song, with_clip):
        result = run_command(
            registry, ctx, "add_notes", track_index=0, slot_index=0, notes=MELODY
        )
        assert result == {"added": 3, "note_count": 3}

        read = run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)
        assert read["note_count"] == 3
        assert read["truncated"] is False
        first = read["notes"][0]
        assert first["pitch"] == 60
        assert first["velocity"] == 100.0
        assert first["probability"] == 1.0
        assert isinstance(first["note_id"], int)
        third = read["notes"][2]
        assert third["probability"] == 0.75

    def test_note_ids_stable_across_reads(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "add_notes", track_index=0, slot_index=0, notes=MELODY)
        ids_a = [n["note_id"] for n in run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]]
        ids_b = [n["note_id"] for n in run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]]
        assert ids_a == ids_b

    def test_get_notes_region(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "add_notes", track_index=0, slot_index=0, notes=MELODY)
        read = run_command(
            registry,
            ctx,
            "get_notes",
            track_index=0,
            slot_index=0,
            from_pitch=64,
            pitch_span=1,
        )
        assert read["note_count"] == 1
        assert read["notes"][0]["pitch"] == 64

    def test_update_notes_by_id(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "add_notes", track_index=0, slot_index=0, notes=MELODY)
        notes = run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]
        target = notes[1]

        result = run_command(
            registry,
            ctx,
            "update_notes",
            track_index=0,
            slot_index=0,
            modifications=[{"note_id": target["note_id"], "velocity": 45, "pitch": 65}],
        )
        assert result["updated"] == 1

        reread = run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]
        edited = next(n for n in reread if n["note_id"] == target["note_id"])
        assert edited["velocity"] == 45.0
        assert edited["pitch"] == 65
        untouched = next(n for n in reread if n["note_id"] == notes[0]["note_id"])
        assert untouched["pitch"] == 60

    def test_update_notes_unknown_id(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "add_notes", track_index=0, slot_index=0, notes=MELODY)
        with pytest.raises(LiveAPIError, match="Unknown note_ids"):
            run_command(
                registry,
                ctx,
                "update_notes",
                track_index=0,
                slot_index=0,
                modifications=[{"note_id": 999999, "velocity": 45}],
            )

    def test_remove_notes_by_id(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "add_notes", track_index=0, slot_index=0, notes=MELODY)
        notes = run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]
        result = run_command(
            registry,
            ctx,
            "remove_notes",
            track_index=0,
            slot_index=0,
            note_ids=[notes[0]["note_id"]],
        )
        assert result["note_count"] == 2

    def test_remove_notes_by_region(self, registry, ctx, song, with_clip):
        run_command(registry, ctx, "add_notes", track_index=0, slot_index=0, notes=MELODY)
        result = run_command(
            registry,
            ctx,
            "remove_notes",
            track_index=0,
            slot_index=0,
            from_time=0.5,
            time_span=10.0,
        )
        assert result["note_count"] == 1

    def test_remove_notes_requires_selector(self, registry, ctx, song, with_clip):
        with pytest.raises(LiveAPIError, match="Provide note_ids or a region"):
            run_command(registry, ctx, "remove_notes", track_index=0, slot_index=0)

    def test_notes_on_audio_clip_rejected(self, registry, ctx, song, with_clip):
        with_clip.is_midi_clip = False
        with pytest.raises(LiveAPIError, match="not a MIDI clip"):
            run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)


class TestSessionOverview:
    def test_overview_shape(self, registry, ctx, song, with_clip):
        result = run_command(registry, ctx, "get_session_overview")
        assert result["transport"]["tempo"] == 120.0
        assert len(result["tracks"]) == 2
        assert result["tracks"][0]["clip_slots"][0]["has_clip"] is True
        assert len(result["scenes"]) == 4
        assert result["master_track"]["type"] == "master"
        # Notes deliberately excluded from the overview payload
        assert "notes" not in str(result["tracks"][0]["clip_slots"][0])
