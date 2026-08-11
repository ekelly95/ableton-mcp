"""Pitch-name <-> MIDI conversion, Ableton convention.

ABLETON'S convention: C3 = MIDI 60 (what Live's piano roll displays).
Most other software calls MIDI 60 "C4" — tool descriptions must state the
convention loudly, and note output always carries pitch_name so what Claude
says matches what the user sees in Live.

midi = (octave + 2) * 12 + semitone  →  C-2 = 0, C3 = 60, G8 = 127.
"""

import re

from ..errors import ValidationError

# Natural-note semitones; accidentals shift from here (B#3 == C4, Cb3 == B2).
NOTE_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_PITCH_RE = re.compile(r"^([A-Ga-g])([#b]{0,2})(-?\d+)$")


def pitch_to_midi(value, param: str = "pitch") -> int:
    """Accept a MIDI number (int) or a pitch name like 'C3', 'F#4', 'Bb-1'."""
    if isinstance(value, bool):
        raise ValidationError("Pitch cannot be a boolean", param=param)
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        raise ValidationError(
            f"Pitch must be a MIDI number or a name like 'C3', got {type(value).__name__}",
            param=param,
        )

    text = value.strip()
    if text.lower().startswith("h"):
        raise ValidationError(
            f"Unknown note '{text}' — did you mean B? (German H is B here)", param=param
        )
    match = _PITCH_RE.match(text)
    if match is None:
        raise ValidationError(
            f"Cannot parse pitch '{value}'. Use a MIDI number or a name like "
            f"'C3', 'F#4', 'Bb2' (Ableton convention: C3 = 60)",
            param=param,
        )

    letter, accidentals, octave_text = match.groups()
    semitone = NOTE_SEMITONES[letter.upper()]
    for accidental in accidentals:
        semitone += 1 if accidental == "#" else -1

    midi = (int(octave_text) + 2) * 12 + semitone
    if not 0 <= midi <= 127:
        raise ValidationError(
            f"Pitch '{value}' is MIDI {midi}, outside 0-127 (C-2 to G8)", param=param
        )
    return midi


def midi_to_pitch_name(midi: int) -> str:
    """MIDI number to sharp-spelled name, Ableton convention (60 -> 'C3')."""
    return f"{SHARP_NAMES[midi % 12]}{midi // 12 - 2}"


def root_name_to_pitch_class(value, param: str = "scale_root"):
    """Accept 0-11 or a bare note name ('D', 'F#', 'Bb') for scale roots."""
    if isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= value <= 11:
            raise ValidationError(f"Root must be 0-11 or a note name, got {value}", param=param)
        return value
    # Reuse the full parser with a dummy octave, then reduce to pitch class.
    return pitch_to_midi(f"{value}0", param=param) % 12
