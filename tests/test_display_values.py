"""Setting parameters from the text Live displays (2.14): '-6 dB', '+5 st',
'800 Hz', '30L', 'Lowpass'. Mock parameters render displays like Live's,
including non-linear ones (log frequency, dB with -inf)."""

import math

import pytest

from control_surface.registry import LiveAPIError, ValidationError
from control_surface.utils.display_values import parse_display
from tests.helpers import run_command
from tests.mock_live import MockDevice, MockParameter


def _hz(v):
    hz = 20 * 1000**v  # 20 Hz .. 20 kHz on a log scale over native 0-1
    return f"{hz / 1000:.2f} kHz" if hz >= 1000 else f"{hz:.0f} Hz"


def _db(v):
    return "-inf dB" if v <= 0 else f"{-70 + 76 * v:.1f} dB"


def _pan(v):
    amount = round(abs(v) * 50)
    return "C" if amount == 0 else f"{amount}{'L' if v < 0 else 'R'}"


def _st(v):
    n = int(round(v))
    return "0 st" if n == 0 else f"{n:+d} st"


@pytest.fixture()
def device(song):
    dev = MockDevice("Test Synth", "TestSynth")
    dev.parameters += [
        MockParameter("Transpose", value=0.0, min=-48, max=48, display=_st),
        MockParameter("Filter Freq", value=1.0, min=0.0, max=1.0, display=_hz),
        MockParameter("Volume", value=0.5, min=0.0, max=1.0, display=_db),
        MockParameter("Pan", value=0.0, min=-1.0, max=1.0, display=_pan),
        MockParameter(
            "Filter Type",
            value=0,
            min=0,
            max=2,
            is_quantized=True,
            value_items=["Low-pass", "High-pass", "Band-pass"],
        ),
    ]
    song.tracks[0].devices.append(dev)
    return dev


def _set(registry, ctx, **values):
    result = run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[{"parameter": k.replace("_", " "), "value": v} for k, v in values.items()],
    )
    return {c["name"]: c["display_value"] for c in result["changed"]}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("-6 dB", (-6.0, "db")),
        ("+5 st", (5.0, "st")),
        ("1.2 kHz", (1200.0, "hz")),
        ("800Hz", (800.0, "hz")),
        ("250 ms", (250.0, "ms")),
        ("0.5 s", (500.0, "ms")),
        ("30L", (-30.0, "pan")),
        ("C", (0.0, "pan")),
        ("-inf dB", (-math.inf, "db")),
        ("19", (19.0, "")),
        ("loud", None),
        ("5 parsecs", None),
    ],
)
def test_parse_display(text, expected):
    assert parse_display(text) == expected


def test_semitones(registry, ctx, device):
    assert _set(registry, ctx, Transpose="+19 st") == {"Transpose": "+19 st"}
    assert _set(registry, ctx, Transpose="-7 st") == {"Transpose": "-7 st"}


def test_bare_number_uses_the_displayed_unit(registry, ctx, device):
    assert _set(registry, ctx, Transpose="12") == {"Transpose": "+12 st"}


def test_log_frequency_with_unit_conversion(registry, ctx, device):
    assert _set(registry, ctx, Filter_Freq="800 Hz") == {"Filter Freq": "800 Hz"}
    assert _set(registry, ctx, Filter_Freq="1.2 kHz") == {"Filter Freq": "1.20 kHz"}
    assert _set(registry, ctx, Filter_Freq="1200 Hz") == {"Filter Freq": "1.20 kHz"}


def test_decibels_including_minus_infinity(registry, ctx, device):
    assert _set(registry, ctx, Volume="-6 dB") == {"Volume": "-6.0 dB"}
    assert _set(registry, ctx, Volume="-inf dB") == {"Volume": "-inf dB"}


def test_pan(registry, ctx, device):
    assert _set(registry, ctx, Pan="30L") == {"Pan": "30L"}
    assert _set(registry, ctx, Pan="C") == {"Pan": "C"}


def test_choice_label(registry, ctx, device):
    # Live writes "High-pass"; spelling it without the hyphen still matches.
    assert _set(registry, ctx, Filter_Type="highpass") == {"Filter Type": "High-pass"}
    assert _set(registry, ctx, Filter_Type="Band Pass") == {"Filter Type": "Band-pass"}


def test_unknown_choice_lists_the_choices(registry, ctx, device):
    with pytest.raises(LiveAPIError, match="Choices: \\['Low-pass', 'High-pass', 'Band-pass'\\]"):
        _set(registry, ctx, Filter_Type="Notch")


def test_out_of_range_names_the_range(registry, ctx, device):
    with pytest.raises(ValidationError, match="range is '-48 st' to '\\+48 st'"):
        _set(registry, ctx, Transpose="+60 st")


def test_wrong_unit_names_the_shown_units(registry, ctx, device):
    with pytest.raises(ValidationError, match="give the value in those units"):
        _set(registry, ctx, Filter_Freq="-6 dB")


def test_numbers_still_mean_normalized(registry, ctx, device):
    assert _set(registry, ctx, Transpose=0.5) == {"Transpose": "0 st"}
