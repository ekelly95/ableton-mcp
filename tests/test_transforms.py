"""Transforms engine — pure module, no Live involved."""

from mcp_server.notation import parse_notation
from mcp_server.transforms import apply_transforms


def _pattern(text: str, **kwargs):
    notes, warnings = parse_notation(text, **kwargs)
    assert warnings == []
    return notes


def test_plain_assignment_all_notes():
    notes = _pattern("v100 C3 1|1 D3 1|2 E3 1|3")
    result, matched, warnings = apply_transforms(notes, "velocity = 80")
    assert warnings == []
    assert matched == 3
    assert all(n["velocity"] == 80.0 for n in result)


def test_pitch_selector_range():
    notes = _pattern("C3 1|1 E3 1|2 C5 1|3")
    result, _, _ = apply_transforms(notes, "C3-E3: velocity = 30")
    by_pitch = {n["pitch"]: n for n in result}
    assert by_pitch[60]["velocity"] == 30.0
    assert by_pitch[64]["velocity"] == 30.0
    assert by_pitch[84]["velocity"] == 100.0


def test_time_selector_bar_wildcard():
    notes = _pattern("C3 1|1 C3 2|1 C3 3|1")
    result, _, _ = apply_transforms(notes, "2|*: velocity = 10")
    velocities = [n["velocity"] for n in sorted(result, key=lambda n: n["start_time"])]
    assert velocities == [100.0, 10.0, 100.0]


def test_time_range_exclusive_end():
    notes = _pattern("C3 1|1 C3 2|1 C3 3|1")
    result, _, _ = apply_transforms(notes, "1|1-<3|1: velocity = 10")
    velocities = [n["velocity"] for n in sorted(result, key=lambda n: n["start_time"])]
    assert velocities == [10.0, 10.0, 100.0]


def test_where_predicate():
    notes = _pattern("v100 C3 1|1 v40 D3 1|2")
    result, _, _ = apply_transforms(notes, "where(note.velocity > 60): pitch += 12")
    by_start = sorted(result, key=lambda n: n["start_time"])
    assert by_start[0]["pitch"] == 72
    assert by_start[1]["pitch"] == 62


def test_compound_operators_and_clamping():
    notes = _pattern("v100 C3 1|1")
    result, _, _ = apply_transforms(notes, "velocity *= 2")
    assert result[0]["velocity"] == 127.0  # clamped


def test_duration_values_in_expressions():
    notes = _pattern("n/4 C3 1|1")
    result, _, _ = apply_transforms(notes, "duration = n/8")
    assert result[0]["duration"] == 0.5


def test_shorthands():
    notes = _pattern("v100 C3 1|1 D3 1|2")
    result, _, warnings = apply_transforms(notes, "v90-110; p0.8")
    assert warnings == []
    assert all(n["velocity"] == 90.0 and n["velocity_deviation"] == 20.0 for n in result)
    assert all(n["probability"] == 0.8 for n in result)


def test_delta_shorthands_accept_plus_and_minus():
    # v+10 / p+0.1 are advertised; the '+' forms used to die in the
    # expression parser (no unary plus) while the '-' forms worked.
    notes = _pattern("v100 p0.8 C3 1|1")
    for action, field, expected in [
        ("v+10", "velocity", 110.0),
        ("v-10", "velocity", 90.0),
        ("p+0.1", "probability", 0.9),
        ("p-0.1", "probability", 0.7),
    ]:
        result, matched, warnings = apply_transforms([dict(n) for n in notes], action)
        assert warnings == [], action
        assert matched == 1
        assert abs(result[0][field] - expected) < 1e-9, action


def test_delta_shorthand_still_clamps():
    notes = _pattern("v120 C3 1|1")
    result, _, warnings = apply_transforms(notes, "v+10")
    assert warnings == []
    assert result[0]["velocity"] == 127.0


def test_zero_denominator_duration_expression_warns():
    notes = _pattern("C3 1|1")
    for statement in ("duration = n/0", "duration = 1bar+n/0"):
        result, _, warnings = apply_transforms([dict(n) for n in notes], statement)
        assert len(warnings) == 1 and "bad duration" in warnings[0], statement
        assert result[0]["duration"] == 1.0  # unchanged


def test_zero_denominator_note_op_args_warn():
    # ratchet/repeat/merge fall back from duration parsing to plain float();
    # n/0 used to escape that fallback as a raw ValueError tool error.
    notes = _pattern("C3 1|1")
    for statement in (
        "C3: ratchet(n/0)",
        "C3: repeat(n/0)",
        "C3: merge(n/0)",
        "C3: repeat(n/8, n/0)",
    ):
        result, _, warnings = apply_transforms([dict(n) for n in notes], statement)
        assert len(warnings) == 1 and "bad argument" in warnings[0], statement
        assert len(result) == 1 and result[0]["duration"] == 1.0  # untouched


def test_v0_deletes_selection():
    notes = _pattern("C3 1|1 D3 1|2")
    result, _, _ = apply_transforms(notes, "D3: v0")
    assert [n["pitch"] for n in result] == [60]


def test_waveform_velocity_shape():
    notes = _pattern("v100 n/4 C3 1|1x8@n/4", sig_numerator=4, sig_denominator=4)
    result, _, warnings = apply_transforms(notes, "velocity = 90 + 30 * tri(2bar)")
    assert warnings == []
    velocities = [n["velocity"] for n in sorted(result, key=lambda n: n["start_time"])]
    assert velocities[0] == 60.0  # trough at phase 0
    assert velocities[4] == 120.0  # peak mid-period
    assert min(velocities) >= 1.0 and max(velocities) <= 127.0


def test_ramp_over_clip():
    notes = _pattern("C3 1|1 C3 2|1 C3 3|1 C3 4|1")
    result, _, _ = apply_transforms(notes, "velocity = ramp(20, 120)", clip_duration=16.0)
    velocities = [n["velocity"] for n in sorted(result, key=lambda n: n["start_time"])]
    assert velocities[0] == 20.0
    assert velocities[-1] == 95.0  # start 12.0 of 16.0 -> 20 + 100*0.75


def test_swing_moves_offbeat_eighths():
    notes = _pattern("n/8 C3 1|1x8@n/8")
    result, _, warnings = apply_transforms(notes, "timing = swing(0.6)")
    assert warnings == []
    starts = sorted(n["start_time"] for n in result)
    assert starts == [0.0, 0.6, 1.0, 1.6, 2.0, 2.6, 3.0, 3.6]


def test_quant_full_strength():
    notes = _pattern("C3 1|1.1 C3 1|2.9")
    result, _, _ = apply_transforms(notes, "timing = quant(1)")
    assert sorted(n["start_time"] for n in result) == [0.0, 2.0]


def test_legato_extends_to_next_onset():
    notes = _pattern("n/16 C3 1|1 D3 1|2 E3 1|3")
    result, _, _ = apply_transforms(notes, "duration = legato()")
    by_start = sorted(result, key=lambda n: n["start_time"])
    assert by_start[0]["duration"] == 1.0
    assert by_start[1]["duration"] == 1.0
    assert by_start[2]["duration"] == 0.25  # last note keeps its duration


def test_snap_to_pitch_classes():
    notes = _pattern("C#3 1|1 F#3 1|2")
    result, _, warnings = apply_transforms(notes, "pitch = snap(C, D, E, G, A)")
    assert warnings == []
    assert sorted(n["pitch"] for n in result) == [60, 67]  # C#3->C3, F#3->G3


def test_rand_and_choose_deterministic_with_seed():
    notes = _pattern("C3 1|1x8@n/4")
    a, _, _ = apply_transforms(notes, "velocity = 60 + 40 * rand()", seed=7)
    b, _, _ = apply_transforms(notes, "velocity = 60 + 40 * rand()", seed=7)
    assert [n["velocity"] for n in a] == [n["velocity"] for n in b]
    c, _, _ = apply_transforms(notes, "velocity = choose(20, 70, 120)", seed=3)
    assert all(n["velocity"] in (20.0, 70.0, 120.0) for n in c)


def test_ratchet_count():
    notes = _pattern("n/4 C3 1|1")
    result, _, _ = apply_transforms(notes, "ratchet(4)")
    assert len(result) == 4
    assert all(abs(n["duration"] - 0.25) < 1e-9 for n in result)
    assert [n["start_time"] for n in result] == [0.0, 0.25, 0.5, 0.75]


def test_ratchet_grid_cuts_on_lines():
    notes = _pattern("n/2 C3 1|1.5")  # spans 0.5..2.5
    result, _, _ = apply_transforms(notes, "ratchet(n/4)")
    starts = [round(n["start_time"], 3) for n in result]
    assert starts == [0.5, 1.0, 2.0]


def test_repeat_echoes():
    notes = _pattern("C3 1|1")
    result, _, _ = apply_transforms(notes, "repeat(n/8, 2)")
    assert sorted(n["start_time"] for n in result) == [0.0, 0.5, 1.0]


def test_merge_touching_runs():
    notes = _pattern("n/4 C3 1|1 C3 1|2 C3 1|4")
    result, _, _ = apply_transforms(notes, "merge(0)")
    starts = sorted(n["start_time"] for n in result)
    assert starts == [0.0, 3.0]
    merged = min(result, key=lambda n: n["start_time"])
    assert merged["duration"] == 2.0


def test_note_ids_preserved_on_mutation_dropped_on_creation():
    notes = [
        {"note_id": 11, "pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100.0},
        {"note_id": 22, "pitch": 62, "start_time": 1.0, "duration": 1.0, "velocity": 100.0},
    ]
    result, _, _ = apply_transforms(notes, "velocity = 50")
    assert {n["note_id"] for n in result} == {11, 22}
    ratcheted, _, _ = apply_transforms(notes, "C3: ratchet(2)")
    pieces = [n for n in ratcheted if n["pitch"] == 60]
    assert len(pieces) == 2 and all("note_id" not in n for n in pieces)
    untouched = next(n for n in ratcheted if n["pitch"] == 62)
    assert untouched["note_id"] == 22


def test_statement_errors_warn_and_skip():
    notes = _pattern("C3 1|1")
    result, _, warnings = apply_transforms(notes, "wibble = 3; velocity = 55")
    assert result[0]["velocity"] == 55.0
    assert any("wibble" in w for w in warnings)


def test_no_match_warns():
    notes = _pattern("C3 1|1")
    _, _, warnings = apply_transforms(notes, "C7: velocity = 1")
    assert any("no notes matched" in w for w in warnings)
