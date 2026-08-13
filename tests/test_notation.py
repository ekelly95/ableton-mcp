"""Notation parser/serializer — pure module, no Live involved."""

from mcp_server.notation import parse_notation, serialize_notation


def _index(notes):
    return {(n["pitch"], n["start_time"]): n for n in notes}


def test_basic_triad_with_state():
    notes, warnings = parse_notation("v100 n/4 C3 E3 G3 1|1")
    assert warnings == []
    assert [n["pitch"] for n in notes] == [60, 64, 67]
    assert all(n["start_time"] == 0.0 for n in notes)
    assert all(n["duration"] == 1.0 for n in notes)
    assert all(n["velocity"] == 100.0 for n in notes)


def test_state_persists_across_positions():
    notes, _ = parse_notation("v80 n/8 C3 1|2 D3 2|1")
    by = _index(notes)
    assert by[(60, 1.0)]["velocity"] == 80.0
    assert by[(62, 4.0)]["duration"] == 0.5


def test_velocity_range_becomes_deviation():
    notes, _ = parse_notation("v90-110 C1 1|1")
    assert notes[0]["velocity"] == 90.0
    assert notes[0]["velocity_deviation"] == 20.0


def test_probability_and_omitted_defaults():
    notes, _ = parse_notation("p0.5 C3 1|1 p1 D3 1|2")
    assert notes[0]["probability"] == 0.5
    assert "probability" not in notes[1]
    assert "velocity_deviation" not in notes[1]


def test_durations_dotted_triplet_bar():
    notes, _ = parse_notation("n/4d C3 1|1 n/8t D3 1|2 1bar E3 2|1")
    by = _index(notes)
    assert by[(60, 0.0)]["duration"] == 1.5
    assert abs(by[(62, 1.0)]["duration"] - 1.0 / 3.0) < 1e-6
    assert by[(64, 4.0)]["duration"] == 4.0


def test_repeat_operator_hats():
    notes, warnings = parse_notation("v100 n/16 F#1 1|1x16@n/16")
    assert warnings == []
    assert len(notes) == 16
    starts = [n["start_time"] for n in notes]
    assert starts == [i * 0.25 for i in range(16)]


def test_repeat_default_step_is_current_duration():
    notes, _ = parse_notation("n/4 C1 1|1x4")
    assert [n["start_time"] for n in notes] == [0.0, 1.0, 2.0, 3.0]


def test_bar_copy_and_range_tiling():
    notes, warnings = parse_notation("v100 n/16 C1 1|1 D1 1|3 @2=1 @3-6=1-2")
    assert warnings == []
    # bar 2 copies bar 1; bars 3-6 tile bars 1-2 (1,2,1,2)
    assert len(notes) == 2 * 6
    bar3 = [n for n in notes if 8.0 <= n["start_time"] < 12.0]
    assert {n["pitch"] for n in bar3} == {36, 38}


def test_copy_previous_bar_shorthand():
    notes, _ = parse_notation("C1 1|1 @2=")
    assert [n["start_time"] for n in notes] == [0.0, 4.0]


def test_v0_deletes_from_copy():
    notes, _ = parse_notation("v100 C1 1|1 D1 1|2 @2=1 v0 D1 2|2")
    pitches_bar2 = [n["pitch"] for n in notes if n["start_time"] >= 4.0]
    assert pitches_bar2 == [36]


def test_tuplet_offset_position():
    notes, _ = parse_notation("C3 1|1+n/12")
    assert abs(notes[0]["start_time"] - 1.0 / 3.0) < 1e-6


def test_meter_awareness_6_8():
    notes, _ = parse_notation("C3 2|1", sig_numerator=6, sig_denominator=8)
    assert notes[0]["start_time"] == 3.0  # 6/8 bar = 3 Ableton beats


def test_warn_and_skip_never_raises():
    notes, warnings = parse_notation("wibble C3 1|1 Z9 2|9 v300 D3 2|1")
    assert len(notes) == 2  # C3 lands; D3 lands with clamped velocity
    assert any("wibble" in w for w in warnings)
    assert any("clamped" in w for w in warnings)


def test_zero_denominator_duration_warns_not_crashes():
    # n/0 used to raise ZeroDivisionError out of the tool, breaking the
    # warn-and-skip contract documented in the module docstring.
    notes, warnings = parse_notation("n/0 C3 1|1 D3 1|2")
    assert len(notes) == 2  # both notes land with the default duration
    assert all(n["duration"] == 1.0 for n in notes)
    assert any("n/0" in w for w in warnings)


def test_zero_denominator_offset_skips_emission():
    # A broken tuplet offset makes the position unknowable — the whole
    # placement is skipped rather than emitted at the unadjusted time.
    notes, warnings = parse_notation("C3 1|1+n/0")
    assert notes == []
    assert any("zero-denominator" in w for w in warnings)


def test_zero_denominator_bar_adjust_warns():
    notes, warnings = parse_notation("1bar+n/0 C3 1|1")
    assert len(notes) == 1 and notes[0]["duration"] == 1.0
    assert any("1bar+n/0" in w for w in warnings)


def test_dangling_pitches_warn():
    notes, warnings = parse_notation("C3 E3")
    assert notes == []
    assert any("dangling" in w for w in warnings)


def test_time_without_pitches_warns():
    _, warnings = parse_notation("1|1")
    assert any("no pitches" in w for w in warnings)


def test_serialize_empty():
    assert serialize_notation([]) == ""


def test_round_trip_simple():
    original, _ = parse_notation("v100 n/8 C3 E3 G3 1|1 v80 D3 1|3 p0.5 F3 2|1")
    text = serialize_notation(original)
    reparsed, warnings = parse_notation(text)
    assert warnings == []
    assert reparsed == original


def test_round_trip_drum_pattern_with_copies():
    src = "v100 n/16 C1 1|1 C1 1|3.5 D1 1|3 F#1 1|1x8@n/8 @2-4=1"
    original, _ = parse_notation(src)
    text = serialize_notation(original)
    reparsed, warnings = parse_notation(text)
    assert warnings == []
    assert reparsed == original


def test_serializer_bar_diffing_compresses():
    # 8 identical bars must collapse into copy ops, not 8 spelled-out bars.
    original, _ = parse_notation("v100 n/16 C1 1|1x4@n/4 @2-8=1")
    text = serialize_notation(original)
    assert "@" in text
    assert len(text) < 120
    reparsed, _ = parse_notation(text)
    assert reparsed == original


def test_serializer_much_smaller_than_json():
    import json

    original, _ = parse_notation("v100 n/16 C1 1|1x2@n/2 D1 1|3 F#1 1|1x16@n/16 @2-8=1")
    as_json = json.dumps(original, separators=(",", ":"))
    text = serialize_notation(original)
    assert len(text) < len(as_json) / 10
