"""Pitch-name parsing, Ableton convention (C3 = 60)."""

import pytest

from control_surface.errors import ValidationError
from control_surface.utils.pitch import (
    midi_to_pitch_name,
    pitch_to_midi,
    root_name_to_pitch_class,
)


@pytest.mark.parametrize(
    "name,midi",
    [
        ("C-2", 0),
        ("C3", 60),
        ("c3", 60),
        ("F#4", 78),
        ("Bb2", 58),
        ("bb2", 58),
        ("B3", 71),
        ("b3", 71),
        ("G8", 127),
        ("Bb-1", 22),
        ("B#3", 72),  # == C4
        ("Cb3", 59),  # == B2
        ("A0", 33),
    ],
)
def test_names_to_midi(name, midi):
    assert pitch_to_midi(name) == midi


def test_ints_pass_through():
    assert pitch_to_midi(60) == 60
    assert pitch_to_midi(60.0) == 60


@pytest.mark.parametrize("bad", ["H3", "C", "3", "C##b2x", "Dz4", "", "C99"])
def test_rejects_garbage(bad):
    with pytest.raises(ValidationError):
        pitch_to_midi(bad)


def test_h_gets_a_hint():
    with pytest.raises(ValidationError, match="did you mean B"):
        pitch_to_midi("H3")


def test_out_of_range():
    with pytest.raises(ValidationError, match="outside 0-127"):
        pitch_to_midi("A8")  # 129
    with pytest.raises(ValidationError):
        pitch_to_midi("B-3")


def test_bool_rejected():
    with pytest.raises(ValidationError):
        pitch_to_midi(True)


def test_round_trip_all_midi():
    for midi in range(128):
        assert pitch_to_midi(midi_to_pitch_name(midi)) == midi


def test_midi_to_name_convention():
    assert midi_to_pitch_name(60) == "C3"
    assert midi_to_pitch_name(0) == "C-2"
    assert midi_to_pitch_name(127) == "G8"
    assert midi_to_pitch_name(61) == "C#3"


def test_root_names():
    assert root_name_to_pitch_class(0) == 0
    assert root_name_to_pitch_class("C") == 0
    assert root_name_to_pitch_class("F#") == 6
    assert root_name_to_pitch_class("Bb") == 10
    with pytest.raises(ValidationError):
        root_name_to_pitch_class(12)
    with pytest.raises(ValidationError):
        root_name_to_pitch_class("H")
