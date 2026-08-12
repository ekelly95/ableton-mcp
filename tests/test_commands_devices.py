"""Device commands against the mock."""

import pytest

from control_surface.registry import LiveAPIError
from tests.helpers import run_command
from tests.mock_live import MockDevice


@pytest.fixture()
def with_device(song):
    song.tracks[0].devices.append(MockDevice(name="Drift", class_name="InstrumentVector"))
    return song.tracks[0].devices[0]


def test_get_devices_list(registry, ctx, with_device):
    result = run_command(registry, ctx, "get_devices", track_index=0)
    assert result["devices"][0]["name"] == "Drift"
    assert result["devices"][0]["parameter_count"] == 3


def test_get_device_parameters(registry, ctx, with_device):
    result = run_command(registry, ctx, "get_devices", track_index=0, device_index=0)
    params = result["device"]["parameters"]
    assert params[0]["name"] == "Device On"
    assert all(0.0 <= p["value"] <= 1.0 for p in params)
    assert all("display_value" in p for p in params)


def test_set_parameters_by_name_and_index(registry, ctx, with_device):
    result = run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[
            {"parameter": "macro 1", "value": 0.9},
            {"parameter": 2, "value": 0.1},
        ],
    )
    assert abs(with_device.parameters[1].value - 0.9) < 1e-9
    assert abs(with_device.parameters[2].value - 0.1) < 1e-9
    assert len(result["changed"]) == 2


def test_set_parameters_unknown_name_lists_available(registry, ctx, with_device):
    with pytest.raises(LiveAPIError, match="Available"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            parameters=[{"parameter": "Filter Cutoff", "value": 0.5}],
        )


def test_bypass_device_drives_device_on_parameter(registry, ctx, with_device):
    # is_active is get/observe-only in Live's API; `enabled` must go through
    # the Device On parameter (the mock derives is_active from it).
    result = run_command(
        registry, ctx, "set_device_parameters", track_index=0, device_index=0, enabled=False
    )
    assert with_device.parameters[0].value == 0.0
    assert with_device.is_active is False
    assert result["is_active"] is False
    assert result["enabled_requested"] is False
    assert result["device_on"] == {"name": "Device On", "value": 0.0}

    result = run_command(
        registry, ctx, "set_device_parameters", track_index=0, device_index=0, enabled=True
    )
    assert with_device.parameters[0].value == 1.0
    assert with_device.is_active is True


def test_mock_is_active_is_read_only(with_device):
    # Contract with reality: assigning device.is_active raises, exactly as the
    # LOM documents (get/observe only). The old code wrote it directly.
    with pytest.raises(AttributeError):
        with_device.is_active = False


def test_enabled_without_device_on_parameter_errors_before_writes(registry, ctx, with_device):
    with_device.parameters = [p for p in with_device.parameters if p.name != "Device On"]
    original = with_device.parameters[0].value  # Macro 1
    with pytest.raises(LiveAPIError, match="Device On"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            parameters=[{"parameter": "Macro 1", "value": 0.9}],
            enabled=False,
        )
    # The Device On lookup happens BEFORE the batch applies: nothing changed.
    assert with_device.parameters[0].value == original


def test_invalid_later_selector_leaves_earlier_parameters_unwritten(registry, ctx, with_device):
    # Validate-then-write: a bad second entry must not leave the first applied.
    original = with_device.parameters[1].value
    with pytest.raises(LiveAPIError, match="Available"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            parameters=[
                {"parameter": "Macro 1", "value": 0.9},
                {"parameter": "No Such Knob", "value": 0.5},
            ],
        )
    assert with_device.parameters[1].value == original


def test_delete_device(registry, ctx, song, with_device):
    result = run_command(registry, ctx, "delete_device", track_index=0, device_index=0)
    assert result == {"deleted": "Drift", "device_count": 0}
    assert song.tracks[0].devices == []


def test_parameter_metadata_exposed(registry, ctx, with_device):
    result = run_command(registry, ctx, "get_devices", track_index=0, device_index=0)
    params = {p["name"]: p for p in result["device"]["parameters"]}
    device_on = params["Device On"]
    assert device_on["is_quantized"] is True
    assert device_on["value_items"] == ["Off", "On"]
    # Absent = default: is_enabled true, automation_state "none". The constant
    # tail at defaults was ~40% of a full device dump.
    assert "is_enabled" not in device_on
    assert "automation_state" not in device_on
    # Continuous parameters carry no value_items (LOM: quantized only).
    assert "value_items" not in params["Macro 1"]


def test_disabled_parameter_rejected_before_any_write(registry, ctx, with_device):
    # LOM is_enabled=false: a macro/live.remote~ owns the parameter — the
    # batch must refuse it up front rather than write into a refusal.
    with_device.parameters[2].is_enabled = False
    original = with_device.parameters[1].value
    with pytest.raises(LiveAPIError, match="not currently editable"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            parameters=[
                {"parameter": "Macro 1", "value": 0.9},
                {"parameter": "Macro 2", "value": 0.4},
            ],
        )
    assert with_device.parameters[1].value == original


def test_re_enable_automation_restores_overridden_state(registry, ctx, with_device):
    macro = with_device.parameters[1]
    macro.automation_state = 1  # automation active

    run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[{"parameter": "Macro 1", "value": 0.9}],
    )
    assert macro.automation_state == 2  # a plain write overrides automation

    run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[{"parameter": "Macro 1", "value": 0.3}],
        re_enable_automation=True,
    )
    assert macro.automation_state == 1  # restored for the params this batch wrote


def test_insert_device_native(registry, ctx, song, with_device):
    result = run_command(registry, ctx, "insert_device", track_index=0, device_name="Reverb")
    assert result["inserted"]["name"] == "Reverb"
    assert result["inserted"]["index"] == 1  # appended after Drift
    assert result["device_count"] == 2

    # At an explicit chain position:
    result = run_command(
        registry, ctx, "insert_device", track_index=0, device_name="EQ Eight", device_index=0
    )
    assert result["inserted"]["index"] == 0
    assert [d.name for d in song.tracks[0].devices] == ["EQ Eight", "Drift", "Reverb"]


def test_insert_device_unknown_name_suggests_browser(registry, ctx, song):
    with pytest.raises(LiveAPIError, match="browse"):
        run_command(registry, ctx, "insert_device", track_index=0, device_name="Sylenth1")
    assert song.tracks[0].devices == []


def test_device_out_of_range(registry, ctx):
    with pytest.raises(LiveAPIError, match="out of range"):
        run_command(registry, ctx, "get_devices", track_index=0, device_index=5)


# --- Class-level property tables (2.7) ---------------------------------------


@pytest.fixture()
def with_simpler(song):
    from tests.mock_live import MockSimplerDevice

    device = MockSimplerDevice()
    song.tracks[0].devices.append(device)
    return device


@pytest.fixture()
def with_eq8(song):
    from tests.mock_live import MockEq8Device

    device = MockEq8Device()
    song.tracks[0].devices.append(device)
    return device


@pytest.fixture()
def with_drift(song):
    from tests.mock_live import MockDriftDevice

    device = MockDriftDevice()
    song.tracks[0].devices.append(device)
    return device


def test_unknown_class_has_no_class_properties(registry, ctx, with_device):
    result = run_command(registry, ctx, "get_devices", track_index=0, device_index=0)
    assert "class_properties" not in result["device"]
    assert "class_methods" not in result["device"]


def test_simpler_class_properties_serialized(registry, ctx, with_simpler):
    result = run_command(registry, ctx, "get_devices", track_index=0, device_index=0)
    props = result["device"]["class_properties"]
    assert props["playback_mode"] == {
        "value": "Classic",
        "items": ["Classic", "One-Shot", "Slicing"],
    }
    assert props["retrigger"] is True
    assert props["voices"] == 8
    assert props["multi_sample_mode"] is False
    # Gated methods: can_warp_half is False on the mock, so warp_half is absent.
    methods = result["device"]["class_methods"]
    assert "reverse" in methods and "warp_as" in methods
    assert "warp_half" not in methods


def test_set_class_property_by_label_and_bool(registry, ctx, with_simpler):
    result = run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[
            {"parameter": "playback_mode", "value": "One-Shot"},
            {"parameter": "retrigger", "value": False},
            {"parameter": "voices", "value": 4},
        ],
    )
    assert with_simpler.playback_mode == 1
    assert with_simpler.retrigger is False
    assert with_simpler.voices == 4
    changed = {c["name"]: c["value"] for c in result["changed"]}
    assert changed == {"playback_mode": "One-Shot", "retrigger": False, "voices": 4}


def test_set_class_property_bad_label_lists_choices(registry, ctx, with_simpler):
    with pytest.raises(LiveAPIError, match="Classic"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            parameters=[{"parameter": "playback_mode", "value": "Granular"}],
        )
    assert with_simpler.playback_mode == 0  # validate-then-write held


def test_class_property_mixed_with_regular_parameter(registry, ctx, with_simpler):
    run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[
            {"parameter": "Macro 1", "value": 0.7},
            {"parameter": "slicing_playback_mode", "value": 2},
        ],
    )
    assert abs(with_simpler.parameters[1].value - 0.7) < 1e-9
    assert with_simpler.slicing_playback_mode == 2


def test_regular_parameter_range_still_enforced(registry, ctx, with_device):
    # The schema no longer type-locks `value` (class props take labels/bools),
    # so the 0-1 range check for regular parameters lives in the handler now.
    with pytest.raises(LiveAPIError, match="0-1"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            parameters=[{"parameter": "Macro 1", "value": 1.5}],
        )


def test_invoke_methods_with_gates_and_results(registry, ctx, with_simpler):
    result = run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        invoke=[
            {"method": "reverse"},
            {"method": "warp_as", "beats": 4},
            {"method": "guess_playback_length"},
        ],
    )
    assert ("reverse",) in with_simpler.method_calls
    assert ("warp_as", 4) in with_simpler.method_calls
    invoked = {i["method"]: i for i in result["invoked"]}
    assert invoked["guess_playback_length"]["result"] == 4.0
    assert "result" not in invoked["reverse"]


def test_invoke_refused_when_gate_false(registry, ctx, with_simpler):
    # can_warp_half is False on the mock — the gate must refuse BEFORE writes.
    with pytest.raises(LiveAPIError, match="can_warp_half"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            parameters=[{"parameter": "retrigger", "value": False}],
            invoke=[{"method": "warp_half"}],
        )
    assert with_simpler.retrigger is True  # validate-then-write held


def test_invoke_missing_required_arg(registry, ctx, with_simpler):
    with pytest.raises(LiveAPIError, match="beats"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            invoke=[{"method": "warp_as"}],
        )


def test_invoke_unknown_method_names_choices(registry, ctx, with_simpler):
    with pytest.raises(LiveAPIError, match="reverse"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            invoke=[{"method": "granulate"}],
        )


def test_eq8_global_mode_labels(registry, ctx, with_eq8):
    result = run_command(registry, ctx, "get_devices", track_index=0, device_index=0)
    props = result["device"]["class_properties"]
    assert props["global_mode"]["value"] == "Stereo"
    run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[{"parameter": "global_mode", "value": "M/S"}],
    )
    assert with_eq8.global_mode == 2


def test_drift_indexed_properties_read_runtime_lists(registry, ctx, with_drift):
    result = run_command(registry, ctx, "get_devices", track_index=0, device_index=0)
    props = result["device"]["class_properties"]
    # Labels come from the paired *_list on the DEVICE, never from our code.
    assert props["voice_mode"]["value"] == "Poly"
    assert props["voice_mode"]["items"] == with_drift.voice_mode_list
    assert props["pitch_bend_range"] == 2

    run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[
            {"parameter": "voice_mode", "value": "mono"},  # case-insensitive label
            {"parameter": "mod_matrix_lfo_source", "value": 3},  # index form
        ],
    )
    assert with_drift.voice_mode_index == 1
    assert with_drift.mod_matrix_lfo_source_index == 3


def test_inserted_native_devices_get_class_mocks(registry, ctx, song):
    run_command(registry, ctx, "insert_device", track_index=0, device_name="Simpler")
    result = run_command(registry, ctx, "get_devices", track_index=0, device_index=0)
    assert result["device"]["class_name"] == "OriginalSimpler"
    assert "class_properties" in result["device"]
