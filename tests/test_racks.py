"""Racks (2.13): Drum Rack pads and rack chains, reached through rack_index +
chain on the device tools, and set_chain for a pad's name/mute/solo/mix."""

import pytest

from control_surface.registry import LiveAPIError, ValidationError
from tests.helpers import run_command
from tests.mock_live import MockChain, MockDevice, MockRackDevice, MockSimplerDevice


@pytest.fixture()
def drum_rack(song):
    rack = MockRackDevice(name="Drum Rack", drum=True)
    rack.fill_pad(36, "Kick", [MockSimplerDevice()])
    rack.fill_pad(38, "Snare", [MockSimplerDevice(), MockDevice("Saturator", "Saturator")])
    song.tracks[0].devices.append(rack)
    return rack


@pytest.fixture()
def instrument_rack(song):
    rack = MockRackDevice(name="Keys Rack")
    rack.chains.append(MockChain("Piano"))
    song.tracks[1].devices.append(rack)
    return rack


def test_device_list_shows_filled_pads_only(registry, ctx, drum_rack):
    devices = run_command(registry, ctx, "get_devices", track_index=0)["devices"]
    pads = devices[0]["pads"]
    assert pads == [
        {"pad": "C1", "note": 36, "name": "Kick", "devices": ["Simpler"]},
        {"pad": "D1", "note": 38, "name": "Snare", "devices": ["Simpler", "Saturator"]},
    ]


def test_other_racks_list_chains(registry, ctx, instrument_rack):
    devices = run_command(registry, ctx, "get_devices", track_index=1)["devices"]
    assert devices[0]["chains"] == [{"index": 0, "name": "Piano", "devices": []}]


def test_reach_a_pad_by_name_or_number(registry, ctx, drum_rack):
    by_name = run_command(registry, ctx, "get_devices", track_index=0, rack_index=0, chain="D1")[
        "devices"
    ]
    by_number = run_command(registry, ctx, "get_devices", track_index=0, rack_index=0, chain=38)[
        "devices"
    ]
    assert (
        [d["name"] for d in by_name]
        == [d["name"] for d in by_number]
        == [
            "Simpler",
            "Saturator",
        ]
    )


def test_set_parameters_on_a_pads_device(registry, ctx, drum_rack):
    run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        rack_index=0,
        chain="C1",
        device_index=0,
        parameters=[{"parameter": "Macro 1", "value": 0.9}],
    )
    simpler = drum_rack.drum_pads[36].chains[0].devices[0]
    assert next(p for p in simpler.parameters if p.name == "Macro 1").value == pytest.approx(0.9)


def test_insert_and_delete_inside_a_pad(registry, ctx, drum_rack):
    run_command(
        registry,
        ctx,
        "insert_device",
        track_index=0,
        rack_index=0,
        chain="C1",
        device_name="Compressor",
    )
    kick = drum_rack.drum_pads[36].chains[0]
    assert [d.name for d in kick.devices] == ["Simpler", "Compressor"]
    run_command(
        registry, ctx, "delete_device", track_index=0, rack_index=0, chain="C1", device_index=1
    )
    assert [d.name for d in kick.devices] == ["Simpler"]


def test_set_chain_mixes_a_pad(registry, ctx, drum_rack):
    result = run_command(
        registry,
        ctx,
        "set_chain",
        track_index=0,
        rack_index=0,
        chain="D1",
        name="Snare Tight",
        mute=True,
        volume=0.5,
    )
    assert result["chain"]["name"] == "Snare Tight"
    assert result["chain"]["mute"] is True
    assert result["volume"] == pytest.approx(0.5)
    assert result["applied"] == ["name", "mute", "volume"]


def test_empty_pad_is_a_clear_error(registry, ctx, drum_rack):
    with pytest.raises(LiveAPIError, match="Pad E1 .* is empty"):
        run_command(registry, ctx, "get_devices", track_index=0, rack_index=0, chain="E1")


def test_chain_on_a_plain_device_is_refused(registry, ctx, song):
    song.tracks[0].devices.append(MockDevice("Reverb", "Reverb"))
    with pytest.raises(LiveAPIError, match="not a rack"):
        run_command(registry, ctx, "get_devices", track_index=0, rack_index=0, chain=0)


def test_rack_index_and_chain_go_together(registry, ctx, drum_rack):
    with pytest.raises(ValidationError, match="go together"):
        run_command(registry, ctx, "get_devices", track_index=0, rack_index=0)


def test_non_drum_rack_needs_an_index(registry, ctx, instrument_rack):
    with pytest.raises(ValidationError, match="not a Drum Rack"):
        run_command(registry, ctx, "get_devices", track_index=1, rack_index=0, chain="C1")


def test_nested_rack_inside_a_pad(registry, ctx, song):
    # Live's Core Library kits: pad C1 holds an Instrument Rack whose chain
    # holds the Simpler (confirmed on Live 12.4.6).
    inner = MockRackDevice(name="Bassdrum")
    inner.chains.append(MockChain("Kick 505", [MockSimplerDevice("Kick 505")]))
    kit = MockRackDevice(name="505 Core Kit", drum=True)
    kit.fill_pad(36, "Bassdrum", [inner])
    song.tracks[0].devices.append(kit)

    devices = run_command(
        registry, ctx, "get_devices", track_index=0, rack_index=[0, 0], chain=["C1", 0]
    )["devices"]
    assert [d["name"] for d in devices] == ["Kick 505"]
    run_command(
        registry,
        ctx,
        "set_chain",
        track_index=0,
        rack_index=[0, 0],
        chain=["C1", 0],
        name="Kick Layer",
    )
    assert inner.chains[0].name == "Kick Layer"


def test_nested_paths_must_match_in_length(registry, ctx, drum_rack):
    with pytest.raises(ValidationError, match="same length"):
        run_command(registry, ctx, "get_devices", track_index=0, rack_index=[0, 0], chain=["C1"])
