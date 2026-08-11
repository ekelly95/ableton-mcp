"""Clip automation envelope commands against the mock."""

import pytest

from control_surface.registry import LiveAPIError, ValidationError
from tests.conftest import run_command
from tests.mock_live import MockDevice

RAMP = [
    {"time": 0.0, "value": 1.0},
    {"time": 1.0, "value": 0.75},
    {"time": 2.0, "value": 0.5},
    {"time": 3.0, "value": 0.1},
]


@pytest.fixture()
def with_clip_and_device(registry, ctx, song):
    song.tracks[0].devices.append(MockDevice(name="Drift"))
    run_command(registry, ctx, "create_clip", track_index=0, slot_index=0, length_beats=4.0)
    return song.tracks[0].clip_slots[0].clip


class TestSetAndGet:
    def test_device_parameter_round_trip(self, registry, ctx, song, with_clip_and_device):
        result = run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
            points=RAMP,
        )
        assert result == {"parameter": "Macro 1", "points_written": 4, "clip_length": 4.0}

        read = run_command(
            registry,
            ctx,
            "get_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
            samples=5,
        )
        assert read["exists"] is True
        values = [p["value"] for p in read["points"]]
        assert values == [1.0, 0.75, 0.5, 0.1, 0.1]
        times = [p["time"] for p in read["points"]]
        assert times == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_mixer_volume_envelope(self, registry, ctx, song, with_clip_and_device):
        run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            slot_index=0,
            mixer_parameter="volume",
            points=[{"time": 0.0, "value": 0.85}, {"time": 2.0, "value": 0.0}],
        )
        read = run_command(
            registry,
            ctx,
            "get_clip_envelope",
            track_index=0,
            slot_index=0,
            mixer_parameter="volume",
            samples=3,
        )
        assert read["exists"] is True
        assert [p["value"] for p in read["points"]] == [0.85, 0.0, 0.0]

    def test_pan_envelope_denormalizes(self, registry, ctx, song, with_clip_and_device):
        # pan native range is -1..1; normalized 0.5 = center = native 0.0
        run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            slot_index=0,
            mixer_parameter="pan",
            points=[{"time": 0.0, "value": 0.5}],
        )
        read = run_command(
            registry,
            ctx,
            "get_clip_envelope",
            track_index=0,
            slot_index=0,
            mixer_parameter="pan",
            samples=2,
        )
        assert read["points"][0]["native_value"] == 0.0
        assert read["points"][0]["value"] == 0.5

    def test_absent_envelope_reports_not_exists(self, registry, ctx, song, with_clip_and_device):
        read = run_command(
            registry,
            ctx,
            "get_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 2",
        )
        assert read == {"exists": False, "parameter": "Macro 2", "points": []}

    def test_arrangement_clip_addressing(self, registry, ctx, song, with_clip_and_device):
        run_command(
            registry,
            ctx,
            "place_clip_in_arrangement",
            track_index=0,
            slot_index=0,
            destination_time=0.0,
        )
        run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            arrangement_clip_index=0,
            device_index=0,
            parameter=1,
            points=[{"time": 0.0, "value": 0.9}],
        )
        read = run_command(
            registry,
            ctx,
            "get_clip_envelope",
            track_index=0,
            arrangement_clip_index=0,
            device_index=0,
            parameter=1,
            samples=2,
        )
        assert read["exists"] is True
        # session copy untouched
        session_read = run_command(
            registry,
            ctx,
            "get_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter=1,
        )
        assert session_read["exists"] is False


class TestValidation:
    def test_target_xor_enforced(self, registry, ctx, song, with_clip_and_device):
        with pytest.raises(ValidationError, match="exactly one target"):
            run_command(
                registry,
                ctx,
                "set_clip_envelope",
                track_index=0,
                slot_index=0,
                points=RAMP,
            )
        with pytest.raises(ValidationError, match="exactly one target"):
            run_command(
                registry,
                ctx,
                "set_clip_envelope",
                track_index=0,
                slot_index=0,
                device_index=0,
                parameter="Macro 1",
                mixer_parameter="volume",
                points=RAMP,
            )

    def test_device_target_needs_both_halves(self, registry, ctx, song, with_clip_and_device):
        with pytest.raises(ValidationError, match="BOTH device_index and parameter"):
            run_command(
                registry,
                ctx,
                "set_clip_envelope",
                track_index=0,
                slot_index=0,
                device_index=0,
                points=RAMP,
            )

    def test_unknown_parameter_lists_available(self, registry, ctx, song, with_clip_and_device):
        with pytest.raises(LiveAPIError, match="Available"):
            run_command(
                registry,
                ctx,
                "set_clip_envelope",
                track_index=0,
                slot_index=0,
                device_index=0,
                parameter="Cutoff Wobble",
                points=RAMP,
            )

    def test_empty_points_rejected(self, registry, ctx, song, with_clip_and_device):
        with pytest.raises(ValidationError, match="at least one"):
            run_command(
                registry,
                ctx,
                "set_clip_envelope",
                track_index=0,
                slot_index=0,
                device_index=0,
                parameter="Macro 1",
                points=[],
            )


class TestClear:
    def test_clear_one_parameter(self, registry, ctx, song, with_clip_and_device):
        run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
            points=RAMP,
        )
        result = run_command(
            registry,
            ctx,
            "clear_clip_envelopes",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
        )
        assert result == {"cleared": "Macro 1"}
        read = run_command(
            registry,
            ctx,
            "get_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
        )
        assert read["exists"] is False

    def test_clear_all(self, registry, ctx, song, with_clip_and_device):
        run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
            points=RAMP,
        )
        run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            slot_index=0,
            mixer_parameter="volume",
            points=RAMP,
        )
        result = run_command(registry, ctx, "clear_clip_envelopes", track_index=0, slot_index=0)
        assert result == {"cleared": "all"}
        assert not with_clip_and_device.has_envelopes

    def test_clear_is_destructive_annotated(self, registry):
        assert registry.get("clear_clip_envelopes").destructive is True
        assert registry.get("get_clip_envelope").read_only is True


class TestOverwrite:
    def test_clear_first_replaces(self, registry, ctx, song, with_clip_and_device):
        run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
            points=RAMP,
        )
        run_command(
            registry,
            ctx,
            "set_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
            points=[{"time": 0.0, "value": 0.2}],
        )
        read = run_command(
            registry,
            ctx,
            "get_clip_envelope",
            track_index=0,
            slot_index=0,
            device_index=0,
            parameter="Macro 1",
            samples=3,
        )
        assert [p["value"] for p in read["points"]] == [0.2, 0.2, 0.2]
