"""Compact note notation: text -> note dicts, and note dicts -> text.

Server-side only. Live never sees notation: the MCP server expands a
`notation` string into the same note dicts `add_notes` has always taken, and
can render `get_notes` results back into notation. This keeps the parser
iterable without control-surface reinstalls, and keeps the wire protocol dumb.

The dialect (original implementation; same family of ideas as other compact
music notations, deliberately simplified):

    v100 n/16 p0.9 C1 1|1x16@n/16 @2-8=1

- `vN` velocity, `vN-M` velocity range (min + velocity_deviation span),
  `v0` turns emission into DELETION of matching pitch+time notes.
- `nK/X` duration as a fraction of a whole note (n/4 = quarter = 1 beat),
  suffix `d` = dotted (x1.5), `t` = triplet (x2/3); `Nbar` forms allowed,
  with optional `+n/X` / `-n/X` adjustment.
- `pF` probability 0..1.
- All three are STATEFUL: they persist until changed. Defaults: v100 n/4 p1.
- Pitch names (Ableton convention, C3=60) or MIDI ints; consecutive pitches
  buffer into a chord.
- `B|b` emits the buffered pitches at bar B beat b (both 1-indexed; a beat is
  an Ableton quarter-note beat — deliberately simpler than meter-relative
  beats, and identical in 4/4). Optional `+n/X`/`-n/X` offsets for tuplet
  positions; optional repeat `xN@step` emits the buffer N times step apart
  (step defaults to the current duration).
- `@D=S` copies bar S into bar D; `@D=` copies bar D-1; ranges tile with
  modulo wrap (`@3-10=1-2`); `@clear` empties the copy source buffer.
- Errors warn-and-skip: bad tokens never abort the phrase, they come back in
  the warnings list so the model can self-correct.
"""

from __future__ import annotations

import re
from typing import Any

from control_surface.utils.pitch import midi_to_pitch_name, pitch_to_midi

_TIME_EPSILON = 0.001  # matching tolerance for v0 deletion and chord grouping

_DEFAULT_VELOCITY = 100.0
_DEFAULT_DURATION = 1.0  # n/4
_DEFAULT_PROBABILITY = 1.0

_VELOCITY_RE = re.compile(r"^v(\d+)(?:-(\d+))?$")
_PROB_RE = re.compile(r"^p(\d*\.?\d+)$")
_DURATION_RE = re.compile(r"^n(\d+(?:\.\d+)?)?/(\d+)([dt])?$")
_BAR_DURATION_RE = re.compile(r"^(\d+)bar(?:([+-])n(\d+(?:\.\d+)?)?/(\d+)([dt])?)?$")
_TIME_RE = re.compile(
    r"^(\d+)\|(\d+(?:\.\d+)?)"  # bar|beat
    r"((?:[+-]n(?:\d+(?:\.\d+)?)?/\d+[dt]?)*)"  # tuplet offsets
    r"(?:x(\d+)(?:@(.+))?)?$"  # repeat count and step
)
_TIME_OFFSET_RE = re.compile(r"([+-])n(\d+(?:\.\d+)?)?/(\d+)([dt]?)")
_COPY_RE = re.compile(r"^@(\d+)(?:-(\d+))?=(?:(\d+)(?:-(\d+))?)?$")
_PITCH_NAME_RE = re.compile(r"^[A-Ga-g][#b]{0,2}-?\d+$")
_INT_RE = re.compile(r"^\d+$")


def _fraction_beats(numerator: str | None, denominator: str, suffix: str | None) -> float:
    beats = 4.0 * float(numerator or 1) / float(denominator)
    if suffix == "d":
        beats *= 1.5
    elif suffix == "t":
        beats *= 2.0 / 3.0
    return beats


def _parse_duration_token(token: str, beats_per_bar: float) -> float | None:
    match = _DURATION_RE.match(token)
    if match:
        return _fraction_beats(match.group(1), match.group(2), match.group(3))
    match = _BAR_DURATION_RE.match(token)
    if match:
        beats = float(match.group(1)) * beats_per_bar
        if match.group(2):
            adjust = _fraction_beats(match.group(3), match.group(4), match.group(5))
            beats += adjust if match.group(2) == "+" else -adjust
        return beats
    return None


class _Parser:
    def __init__(self, sig_numerator: int, sig_denominator: int):
        self.beats_per_bar = sig_numerator * 4.0 / sig_denominator
        self.velocity = _DEFAULT_VELOCITY
        self.velocity_deviation = 0.0
        self.duration = _DEFAULT_DURATION
        self.probability = _DEFAULT_PROBABILITY
        self.pitch_buffer: list[int] = []
        self.notes: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.copy_cleared_before: int = 0  # notes below this index are @clear-protected

    def parse(self, text: str) -> tuple[list[dict[str, Any]], list[str]]:
        for token in text.split():
            self._consume(token)
        if self.pitch_buffer:
            self.warnings.append(
                f"{len(self.pitch_buffer)} dangling pitch(es) at end of phrase were never "
                "placed with a bar|beat position and were dropped"
            )
        self.notes.sort(key=lambda n: (n["start_time"], n["pitch"]))
        return self.notes, self.warnings

    def _consume(self, token: str) -> None:
        if token == "@clear":
            self.copy_cleared_before = len(self.notes)
            return
        match = _VELOCITY_RE.match(token)
        if match:
            self._set_velocity(token, match)
            return
        match = _PROB_RE.match(token)
        if match:
            value = float(match.group(1))
            if not 0.0 <= value <= 1.0:
                self.warnings.append(f"probability '{token}' clamped to 0..1")
                value = min(max(value, 0.0), 1.0)
            self.probability = value
            return
        duration = _parse_duration_token(token, self.beats_per_bar)
        if duration is not None:
            if duration <= 0:
                self.warnings.append(f"duration '{token}' is not positive; skipped")
                return
            self.duration = duration
            return
        match = _COPY_RE.match(token)
        if match:
            self._copy_bars(token, match)
            return
        match = _TIME_RE.match(token)
        if match:
            self._emit_at(token, match)
            return
        if _PITCH_NAME_RE.match(token) or _INT_RE.match(token):
            try:
                midi = pitch_to_midi(int(token) if _INT_RE.match(token) else token)
            except Exception as exc:  # ValidationError carries the message
                self.warnings.append(str(exc))
                return
            if not 0 <= midi <= 127:
                self.warnings.append(f"pitch '{token}' outside 0-127; skipped")
                return
            self.pitch_buffer.append(midi)
            return
        self.warnings.append(f"unrecognized token '{token}' skipped")

    def _set_velocity(self, token: str, match: re.Match) -> None:
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) else None
        if high is not None:
            if low == 0 or high == 0:
                self.warnings.append(f"'{token}': 0 is reserved for deletion, range skipped")
                return
            low, high = min(low, high), max(low, high)
            self.velocity = float(min(low, 127))
            self.velocity_deviation = float(min(high, 127) - min(low, 127))
        else:
            if low > 127:
                self.warnings.append(f"velocity '{token}' clamped to 127")
                low = 127
            self.velocity = float(low)
            self.velocity_deviation = 0.0

    def _emit_at(self, token: str, match: re.Match) -> None:
        if not self.pitch_buffer:
            self.warnings.append(f"time '{token}' has no pitches before it; skipped")
            return
        bar = int(match.group(1))
        beat = float(match.group(2))
        start = (bar - 1) * self.beats_per_bar + (beat - 1.0)
        for sign, num, den, suffix in _TIME_OFFSET_RE.findall(match.group(3) or ""):
            offset = _fraction_beats(num or None, den, suffix or None)
            start += offset if sign == "+" else -offset
        count = int(match.group(4)) if match.group(4) else 1
        step = self.duration
        if match.group(5):
            parsed = _parse_duration_token(match.group(5), self.beats_per_bar)
            if parsed is None:
                self.warnings.append(
                    f"repeat step '@{match.group(5)}' unrecognized; using current duration"
                )
            else:
                step = parsed
        if start < 0:
            self.warnings.append(f"time '{token}' is before 1|1; skipped")
            self.pitch_buffer = []
            return
        for i in range(count):
            self._emit_chord(start + i * step)
        self.pitch_buffer = []

    def _emit_chord(self, start: float) -> None:
        for pitch in self.pitch_buffer:
            if self.velocity == 0:
                self._delete_matching(pitch, start)
                continue
            note: dict[str, Any] = {
                "pitch": pitch,
                "start_time": round(start, 6),
                "duration": round(self.duration, 6),
                "velocity": self.velocity,
            }
            if self.velocity_deviation:
                note["velocity_deviation"] = self.velocity_deviation
            if self.probability != _DEFAULT_PROBABILITY:
                note["probability"] = self.probability
            self.notes.append(note)

    def _delete_matching(self, pitch: int, start: float) -> None:
        before = len(self.notes)
        self.notes = [
            n
            for n in self.notes
            if not (n["pitch"] == pitch and abs(n["start_time"] - start) <= _TIME_EPSILON)
        ]
        if len(self.notes) == before:
            self.warnings.append(
                f"v0 {midi_to_pitch_name(pitch)} at beat {start:g}: nothing to delete"
            )

    def _copy_bars(self, token: str, match: re.Match) -> None:
        dst_first = int(match.group(1))
        dst_last = int(match.group(2)) if match.group(2) else dst_first
        src_first = int(match.group(3)) if match.group(3) else dst_first - 1
        src_last = int(match.group(4)) if match.group(4) else src_first
        if dst_last < dst_first or src_last < src_first or src_first < 1:
            self.warnings.append(f"copy '{token}' has an invalid range; skipped")
            return
        bpb = self.beats_per_bar
        source: list[dict[str, Any]] = [
            n
            for n in self.notes[self.copy_cleared_before :]
            if (src_first - 1) * bpb <= n["start_time"] < src_last * bpb
        ]
        if not source:
            self.warnings.append(f"copy '{token}': source bars are empty; skipped")
            return
        src_count = src_last - src_first + 1
        copies = []
        for dst in range(dst_first, dst_last + 1):
            src = src_first + (dst - dst_first) % src_count
            shift = (dst - src) * bpb
            for n in source:
                if (src - 1) * bpb <= n["start_time"] < src * bpb:
                    copy = dict(n)
                    copy["start_time"] = round(n["start_time"] + shift, 6)
                    copies.append(copy)
        self.notes.extend(copies)


def parse_notation(
    text: str, sig_numerator: int = 4, sig_denominator: int = 4
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a notation phrase into add_notes-shaped note dicts + warnings."""
    return _Parser(sig_numerator, sig_denominator).parse(text)


# --- Serialization (notes -> notation) --------------------------------------

# Exact-match duration table, longest first so formatting is deterministic.
_DURATION_NAMES = [
    (6.0, "n/1d"),
    (4.0, "n/1"),
    (3.0, "n/2d"),
    (8.0 / 3.0, "n/1t"),
    (2.0, "n/2"),
    (1.5, "n/4d"),
    (4.0 / 3.0, "n/2t"),
    (1.0, "n/4"),
    (0.75, "n/8d"),
    (2.0 / 3.0, "n/4t"),
    (0.5, "n/8"),
    (0.375, "n/16d"),
    (1.0 / 3.0, "n/8t"),
    (0.25, "n/16"),
    (1.0 / 6.0, "n/16t"),
    (0.125, "n/32"),
    (1.0 / 12.0, "n/32t"),
]


def _format_duration(beats: float) -> str:
    for value, name in _DURATION_NAMES:
        if abs(beats - value) <= 1e-4:
            return name
    return f"n{round(beats, 4):g}/4"


def _format_number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_velocity(velocity: float, deviation: float) -> str:
    low = int(round(velocity))
    if deviation:
        return f"v{low}-{low + int(round(deviation))}"
    return f"v{low}"


def _note_key(note: dict[str, Any], bar_start: float) -> tuple:
    """Bar-relative identity used for chord grouping and bar-diffing."""
    return (
        round(note["start_time"] - bar_start, 4),
        note["pitch"],
        round(note.get("duration", _DEFAULT_DURATION), 4),
        round(note.get("velocity", _DEFAULT_VELOCITY), 1),
        round(note.get("velocity_deviation", 0.0), 1),
        round(note.get("probability", _DEFAULT_PROBABILITY), 3),
    )


def serialize_notation(
    notes: list[dict[str, Any]], sig_numerator: int = 4, sig_denominator: int = 4
) -> str:
    """Render notes as notation: state-delta prefixes, chords, bar-copy diffing.

    Bar-diffing is the read-back edge: bars whose content exactly repeats an
    earlier bar collapse to @D=S copy ops instead of being spelled out again.
    """
    if not notes:
        return ""
    bpb = sig_numerator * 4.0 / sig_denominator

    bars: dict[int, list[dict[str, Any]]] = {}
    for note in sorted(notes, key=lambda n: (n["start_time"], n["pitch"])):
        bars.setdefault(int(note["start_time"] // bpb) + 1, []).append(note)

    fingerprints: dict[tuple, int] = {}
    copy_of: dict[int, int] = {}
    for bar_index in sorted(bars):
        print_ = tuple(_note_key(n, (bar_index - 1) * bpb) for n in bars[bar_index])
        if print_ in fingerprints:
            copy_of[bar_index] = fingerprints[print_]
        else:
            fingerprints[print_] = bar_index

    tokens: list[str] = []
    # velocity/duration start as None so the first group is always explicit
    # (round-trip determinism); probability starts at the parser default so a
    # phrase that never leaves p1 never mentions it.
    state = {"velocity": None, "deviation": None, "duration": None, "probability": 1.0}
    pending_copies: list[tuple[int, int]] = []

    def flush_copies() -> None:
        # Collapse consecutive destinations sharing one source into @D-D2=S.
        i = 0
        while i < len(pending_copies):
            dst, src = pending_copies[i]
            j = i
            while j + 1 < len(pending_copies) and pending_copies[j + 1] == (
                pending_copies[j][0] + 1,
                src,
            ):
                j += 1
            tokens.append(f"@{dst}={src}" if i == j else f"@{dst}-{pending_copies[j][0]}={src}")
            i = j + 1
        pending_copies.clear()

    for bar_index in sorted(bars):
        if bar_index in copy_of:
            pending_copies.append((bar_index, copy_of[bar_index]))
            continue
        flush_copies()
        bar_notes = bars[bar_index]
        groups: dict[tuple, list[int]] = {}
        order: list[tuple] = []
        for note in bar_notes:
            key = _note_key(note, 0.0)[:1] + _note_key(note, 0.0)[2:]
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(note["pitch"])
        for key in order:
            rel_start, duration, velocity, deviation, probability = key
            if state["velocity"] != velocity or state["deviation"] != deviation:
                tokens.append(_format_velocity(velocity, deviation))
                state["velocity"], state["deviation"] = velocity, deviation
            if state["duration"] != duration:
                tokens.append(_format_duration(duration))
                state["duration"] = duration
            if state["probability"] != probability:
                tokens.append(f"p{_format_number(probability)}")
                state["probability"] = probability
            tokens.extend(midi_to_pitch_name(p) for p in groups[key])
            bar = int(rel_start // bpb) + 1
            beat = rel_start - (bar - 1) * bpb + 1.0
            tokens.append(f"{bar}|{_format_number(beat)}")
    flush_copies()
    return " ".join(tokens)
