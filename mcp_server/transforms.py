"""Note transforms: a small expression language over note lists.

Pure server-side processing — notes in, notes out, no Live API involvement —
so the language can grow without control-surface reinstalls. Original
implementation; the idea (selector + assignment + function library) is shared
with other music tools, the code and grammar are this project's own.

Statements are separated by ';' or newlines. Each statement:

    [selectors ':'] action

Selectors (all optional, AND-composed):
    C3          single pitch          C1-C3      pitch range (inclusive)
    2|1         exact onset           1|1-3|4    time range (inclusive)
    1|1-<3|1    exclusive end         3|*        whole bar   3|*-5|*  bar range
    where(note.velocity > 80 && note.duration < n/4)

Actions:
    velocity = 100        assignment; params: velocity pitch timing duration
    pitch += 12           probability deviation; operators = += -= *= /=
    v90  v90-110  v+10  v-10  v0(delete)  p0.8  p+0.1  n/8      shorthands
    ratchet(4) ratchet(n/16)  repeat(n/8) repeat(1bar,3)  merge() merge(n/16)

Expression values: numbers, durations (n/8, 1bar = beats), note.pitch,
note.velocity, note.start, note.duration, note.probability, note.deviation,
note.index, note.count, clip.duration, and functions:
    sin/cos/tri/saw/square(period[,phase])   waveforms over note.start, -1..1
    ramp(a,b)  swing([amount])  quant(grid[,strength])  legato([tolerance])
    snap(C,Eb,G,...)  clamp(x,lo,hi)  rand()  choose(a,b,...)
    round/floor/ceil/abs/min/max/pow
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable
from typing import Any

from control_surface.config import MAX_NOTES_PER_READ
from control_surface.registry import NOTE_FIELD_DEFAULTS
from control_surface.utils.pitch import pitch_to_midi, root_name_to_pitch_class

from .notation import _VELOCITY_RE as _SHORTHAND_V_RE
from .notation import _parse_duration_token, _velocity_range_fields

_PITCH_SEL_RE = re.compile(r"^([A-Ga-g][#b]{0,2}-?\d+)(?:-([A-Ga-g][#b]{0,2}-?\d+))?$")
_TIME_SEL_RE = re.compile(r"^(\d+)\|(\*|\d+(?:\.\d+)?)(?:-(<)?(\d+)\|(\*|\d+(?:\.\d+)?))?$")

# The two note fields the compact emission omits when default-valued — the
# transform path must rehydrate them with Live's own defaults, single-sourced
# from the registry's NOTE_FIELD_DEFAULTS.
TRANSFORM_FIELD_DEFAULTS = {
    k: NOTE_FIELD_DEFAULTS[k] for k in ("probability", "velocity_deviation")
}
_SHORTHAND_V_DELTA_RE = re.compile(r"^v([+-]\d+(?:\.\d+)?)$")
_SHORTHAND_P_RE = re.compile(r"^p(\d*\.\d+|\d+)$")
_SHORTHAND_P_DELTA_RE = re.compile(r"^p([+-]\d*\.?\d+)$")

_PARAMS = {
    "velocity": "velocity",
    "pitch": "pitch",
    "timing": "start_time",
    "duration": "duration",
    "probability": "probability",
    "deviation": "velocity_deviation",
}
_NOTE_VARS = {
    "pitch": ("pitch", None),
    "velocity": ("velocity", 100.0),
    "start": ("start_time", None),
    "duration": ("duration", None),
    "probability": ("probability", 1.0),
    "deviation": ("velocity_deviation", 0.0),
}

# Value clamps applied after every assignment so a wild expression can't
# produce notes Live would reject.
_CLAMPS = {
    "velocity": (1.0, 127.0),
    "pitch": (0.0, 127.0),
    "start_time": (0.0, None),
    "duration": (0.001, None),
    "probability": (0.0, 1.0),
    "velocity_deviation": (-127.0, 127.0),
}


class TransformError(ValueError):
    pass


def _clamp_field(field: str, value: float) -> float:
    lo, hi = _CLAMPS[field]
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


# --- expression parsing ------------------------------------------------------


class _Tokens:
    _TOKEN_RE = re.compile(
        r"\s*(?:(>=|<=|==|!=|&&|\|\||[-+*/%(),!<>])"
        r"|(\d+bar(?:[+-]n[\d.]*/\d+[dt]?)?|n[\d.]*/\d+[dt]?)"
        r"|(\d+\.\d+|\d+)"
        r"|(note\.\w+|clip\.\w+)"
        r"|([A-Za-z_][A-Za-z_#b0-9]*))"
    )

    def __init__(self, text: str, beats_per_bar: float):
        self.items: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            match = self._TOKEN_RE.match(text, pos)
            if match is None:
                if text[pos:].strip():
                    raise TransformError(f"unparseable expression near '{text[pos:].strip()}'")
                break
            op, dur, num, var, word = match.groups()
            if op:
                self.items.append(("op", op))
            elif dur:
                beats = _parse_duration_token(dur, beats_per_bar)
                if beats is None:
                    raise TransformError(f"bad duration '{dur}'")
                self.items.append(("num", str(beats)))
            elif num:
                self.items.append(("num", num))
            elif var:
                self.items.append(("var", var))
            elif word:
                self.items.append(("word", word))
            pos = match.end()
        self.index = 0

    def peek(self) -> tuple[str, str] | None:
        return self.items[self.index] if self.index < len(self.items) else None

    def next(self) -> tuple[str, str]:
        item = self.peek()
        if item is None:
            raise TransformError("unexpected end of expression")
        self.index += 1
        return item

    def accept(self, value: str) -> bool:
        item = self.peek()
        if item and item[0] == "op" and item[1] == value:
            self.index += 1
            return True
        return False

    def expect(self, value: str) -> None:
        if not self.accept(value):
            raise TransformError(f"expected '{value}'")


class _Env:
    """Per-note evaluation context shared by expressions and functions."""

    def __init__(self, rng: random.Random, beats_per_bar: float, clip_duration: float):
        self.rng = rng
        self.beats_per_bar = beats_per_bar
        self.clip_duration = clip_duration
        self.note: dict[str, Any] = {}
        self.selection: list[dict[str, Any]] = []
        self.index = 0
        self.count = 1


def _phase(env: _Env, args: list[float]) -> float:
    period = args[0] if args else env.beats_per_bar
    offset = args[1] if len(args) > 1 else 0.0
    if period <= 0:
        raise TransformError("waveform period must be positive")
    return ((env.note.get("start_time", 0.0) / period) + offset) % 1.0


_WAVEFORMS: dict[str, Callable[[float], float]] = {
    "sin": lambda ph: math.sin(2 * math.pi * ph),
    "cos": lambda ph: math.cos(2 * math.pi * ph),
    "tri": lambda ph: 1 - 4 * abs(ph - 0.5) if ph <= 1 else 0,
    "saw": lambda ph: 2 * ph - 1,
    "square": lambda ph: 1.0 if ph < 0.5 else -1.0,
}


def _call_function(name: str, args: list[float], env: _Env, raw_args: list[str]) -> float:
    if name in _WAVEFORMS:
        return _WAVEFORMS[name](_phase(env, args))
    if name == "ramp":
        if len(args) != 2:
            raise TransformError("ramp(start, end) takes two arguments")
        span = env.clip_duration or 1.0
        t = min(env.note.get("start_time", 0.0) / span, 1.0)
        return args[0] + (args[1] - args[0]) * t
    if name == "rand":
        return env.rng.random()
    if name == "swing":
        # New start for the current note: off-beat eighths (x.5) move to
        # x + amount. 0.5 = straight, 0.55 subtle, 0.67 = triplet feel.
        amount = args[0] if args else 0.55
        start = env.note.get("start_time", 0.0)
        within_beat = start % 1.0
        if abs(within_beat - 0.5) < 0.05:
            return math.floor(start) + amount
        return start
    if name == "quant":
        if not args:
            raise TransformError("quant(grid[, strength]) needs a grid")
        grid = args[0]
        if grid <= 0:
            raise TransformError("quant grid must be positive")
        strength = args[1] if len(args) > 1 else 1.0
        start = env.note.get("start_time", 0.0)
        nearest = round(start / grid) * grid
        return start + (nearest - start) * strength
    if name == "legato":
        # New duration: extend to the next onset in the selection (+tolerance).
        tolerance = args[0] if args else 0.0
        start = env.note.get("start_time", 0.0)
        onsets = [n["start_time"] for n in env.selection if n["start_time"] > start + 1e-9]
        if not onsets:
            return float(env.note.get("duration", 1.0))
        return min(onsets) - start + tolerance
    if name == "choose":
        if not args:
            raise TransformError("choose() needs at least one argument")
        return env.rng.choice(args)
    if name == "clamp":
        if len(args) != 3:
            raise TransformError("clamp(x, lo, hi) takes three arguments")
        return min(max(args[0], args[1]), args[2])
    if name == "snap":
        classes = sorted({root_name_to_pitch_class(a) for a in raw_args})
        if not classes:
            raise TransformError("snap(C, Eb, ...) needs pitch classes")
        pitch = env.note.get("pitch", 60)
        best = min(
            (
                p
                for octave in (-12, 0, 12)
                for c in classes
                if 0 <= (p := (pitch // 12) * 12 + c + octave) <= 127
            ),
            key=lambda p: (abs(p - pitch), p),
        )
        return float(best)
    simple = {
        "round": lambda a: float(round(a[0])),
        "floor": lambda a: float(math.floor(a[0])),
        "ceil": lambda a: float(math.ceil(a[0])),
        "abs": lambda a: abs(a[0]),
        "min": lambda a: min(a),
        "max": lambda a: max(a),
        "pow": lambda a: math.pow(a[0], a[1]),
    }
    if name in simple:
        if not args:
            raise TransformError(f"{name}() needs arguments")
        return simple[name](args)
    raise TransformError(f"unknown function '{name}'")


def _parse_primary(tokens: _Tokens, env: _Env) -> float:
    kind, value = tokens.next()
    if kind == "num":
        return float(value)
    if kind == "op" and value == "(":
        result = _parse_expr(tokens, env)
        tokens.expect(")")
        return result
    if kind == "op" and value == "-":
        return -_parse_primary(tokens, env)
    if kind == "var":
        scope, field = value.split(".", 1)
        if scope == "note":
            if field == "index":
                return float(env.index)
            if field == "count":
                return float(env.count)
            if field not in _NOTE_VARS:
                raise TransformError(f"unknown variable '{value}'")
            attr, default = _NOTE_VARS[field]
            result = env.note.get(attr, default)
            if result is None:
                raise TransformError(f"note is missing '{attr}'")
            return float(result)
        if scope == "clip" and field == "duration":
            return float(env.clip_duration)
        raise TransformError(f"unknown variable '{value}'")
    if kind == "word":
        if tokens.accept("("):
            args: list[float] = []
            raw_args: list[str] = []
            if not tokens.accept(")"):
                while True:
                    item = tokens.peek()
                    if value == "snap" and item and item[0] == "word":
                        raw_args.append(tokens.next()[1])
                        args.append(0.0)
                    else:
                        args.append(_parse_expr(tokens, env))
                    if not tokens.accept(","):
                        break
                tokens.expect(")")
            return _call_function(value, args, env, raw_args)
        # A bare pitch name is a number (C3 == 60).
        try:
            return float(pitch_to_midi(value))
        except Exception:
            raise TransformError(f"unknown name '{value}'") from None
    raise TransformError(f"unexpected token '{value}'")


def _parse_term(tokens: _Tokens, env: _Env) -> float:
    result = _parse_primary(tokens, env)
    while True:
        if tokens.accept("*"):
            result *= _parse_primary(tokens, env)
        elif tokens.accept("/"):
            result /= _parse_primary(tokens, env)
        elif tokens.accept("%"):
            result %= _parse_primary(tokens, env)
        else:
            return result


def _parse_expr(tokens: _Tokens, env: _Env) -> float:
    result = _parse_term(tokens, env)
    while True:
        if tokens.accept("+"):
            result += _parse_term(tokens, env)
        elif tokens.accept("-"):
            result -= _parse_term(tokens, env)
        else:
            return result


def _parse_comparison(tokens: _Tokens, env: _Env) -> bool:
    left = _parse_expr(tokens, env)
    item = tokens.peek()
    if item and item[0] == "op" and item[1] in (">", "<", ">=", "<=", "==", "!="):
        tokens.next()
        right = _parse_expr(tokens, env)
        return {
            ">": left > right,
            "<": left < right,
            ">=": left >= right,
            "<=": left <= right,
            "==": abs(left - right) < 1e-9,
            "!=": abs(left - right) >= 1e-9,
        }[item[1]]
    return bool(left)


def _parse_bool(tokens: _Tokens, env: _Env) -> bool:
    if tokens.accept("!"):
        return not _parse_bool(tokens, env)
    if tokens.accept("("):
        result = _parse_or(tokens, env)
        tokens.expect(")")
        return result
    return _parse_comparison(tokens, env)


def _parse_and(tokens: _Tokens, env: _Env) -> bool:
    result = _parse_bool(tokens, env)
    while tokens.accept("&&"):
        result = _parse_bool(tokens, env) and result
    return result


def _parse_or(tokens: _Tokens, env: _Env) -> bool:
    result = _parse_and(tokens, env)
    while tokens.accept("||"):
        result = _parse_and(tokens, env) or result
    return result


def _evaluate_expr(text: str, env: _Env) -> float:
    tokens = _Tokens(text, env.beats_per_bar)
    result = _parse_expr(tokens, env)
    if tokens.peek() is not None:
        raise TransformError(f"trailing input in expression '{text}'")
    return result


def _evaluate_where(text: str, env: _Env) -> bool:
    tokens = _Tokens(text, env.beats_per_bar)
    result = _parse_or(tokens, env)
    if tokens.peek() is not None:
        raise TransformError(f"trailing input in where() '{text}'")
    return result


# --- statement parsing -------------------------------------------------------


class _Selector:
    def __init__(self):
        self.pitch_range: tuple[int, int] | None = None
        self.time_range: tuple[float, float, bool] | None = None  # start, end, end_exclusive
        self.where: str | None = None


def _split_statements(text: str) -> list[str]:
    statements, depth, current = [], 0, []
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char in ";\n" and depth == 0:
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


def _split_selector_action(statement: str) -> tuple[str | None, str]:
    depth = 0
    for i, char in enumerate(statement):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == ":" and depth == 0:
            return statement[:i].strip(), statement[i + 1 :].strip()
    return None, statement.strip()


def _parse_selector(text: str, beats_per_bar: float) -> _Selector:
    selector = _Selector()
    rest = text
    while rest:
        if rest.startswith("where"):
            open_paren = rest.index("(")
            depth, i = 0, open_paren
            for i in range(open_paren, len(rest)):
                if rest[i] == "(":
                    depth += 1
                elif rest[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
            if depth != 0:
                raise TransformError("unbalanced parentheses in where()")
            selector.where = rest[open_paren + 1 : i]
            rest = rest[i + 1 :].strip()
            continue
        token, _, rest = rest.partition(" ")
        rest = rest.strip()
        match = _TIME_SEL_RE.match(token)
        if match:
            b1, beat1, exclusive, b2, beat2 = match.groups()
            start_bar = int(b1)
            start = (start_bar - 1) * beats_per_bar + (0.0 if beat1 == "*" else float(beat1) - 1)
            if b2 is None:
                if beat1 == "*":
                    end, end_exclusive = start_bar * beats_per_bar, True
                else:
                    end, end_exclusive = start, False  # exact onset
            else:
                end_bar = int(b2)
                if beat2 == "*":
                    end, end_exclusive = end_bar * beats_per_bar, True
                else:
                    end = (end_bar - 1) * beats_per_bar + float(beat2) - 1
                    end_exclusive = bool(exclusive)
            selector.time_range = (start, end, end_exclusive)
            continue
        match = _PITCH_SEL_RE.match(token)
        if match:
            low = pitch_to_midi(match.group(1))
            high = pitch_to_midi(match.group(2)) if match.group(2) else low
            selector.pitch_range = (min(low, high), max(low, high))
            continue
        raise TransformError(f"unrecognized selector '{token}'")
    return selector


def _matches(note: dict[str, Any], selector: _Selector, env: _Env) -> bool:
    if selector.pitch_range is not None:
        low, high = selector.pitch_range
        if not low <= note["pitch"] <= high:
            return False
    if selector.time_range is not None:
        start, end, end_exclusive = selector.time_range
        t = note["start_time"]
        if t < start - 1e-9:
            return False
        if end_exclusive:
            if t >= end - 1e-9:
                return False
        elif t > end + 1e-9:
            return False
    if selector.where is not None:
        env.note = note
        if not _evaluate_where(selector.where, env):
            return False
    return True


_NOTE_OP_RE = re.compile(r"^(ratchet|repeat|merge)\((.*)\)$")
_ASSIGN_RE = re.compile(
    r"^(velocity|pitch|timing|duration|probability|deviation)\s*(\+=|-=|\*=|/=|=)\s*(.+)$"
)


def _apply_assignment(field: str, op: str, value: float, note: dict[str, Any]) -> None:
    current = float(note.get(field, TRANSFORM_FIELD_DEFAULTS.get(field, 0.0)))
    if op == "=":
        result = value
    elif op == "+=":
        result = current + value
    elif op == "-=":
        result = current - value
    elif op == "*=":
        result = current * value
    else:
        if value == 0:
            raise TransformError("division by zero")
        result = current / value
    result = _clamp_field(field, result)
    if field == "pitch":
        result = int(round(result))
    note[field] = result


def _grid_ratchet(note: dict[str, Any], grid: float) -> list[dict[str, Any]]:
    start, end = note["start_time"], note["start_time"] + note["duration"]
    if grid <= 0:
        raise TransformError(f"ratchet grid must be positive, got {grid}")
    # Bound before walking: the loop below steps by `grid`, so a very fine grid
    # over a long note is a very long loop.
    _check_generated("ratchet", (end - start) / grid + 1)
    cuts = []
    first = math.floor(start / grid) * grid + grid
    cut = first
    while cut < end - 1e-9:
        cuts.append(cut)
        cut += grid
    if not cuts:
        return [note]
    pieces = []
    edges = [start] + cuts + [end]
    for a, b in zip(edges, edges[1:], strict=False):
        piece = {k: v for k, v in note.items() if k != "note_id"}
        piece["start_time"], piece["duration"] = a, b - a
        pieces.append(piece)
    return pieces


def _op_number(name: str, text: str) -> float:
    # Duration-looking args that fail duration parsing (n/0) land here too —
    # surface them as the statement warning the dialect promises, not as a
    # naked ValueError that escapes the statement loop as a tool error.
    try:
        value = float(text)
    except ValueError:
        raise TransformError(f"bad argument '{text}' for {name}(...)") from None
    if not math.isfinite(value):
        # float('inf') parses fine and then blows up as OverflowError in int(),
        # which is not a TransformError and so escapes the statement loop.
        raise TransformError(f"bad argument '{text}' for {name}(...)")
    return value


def _op_int(name: str, text: str) -> int:
    return int(_op_number(name, text))


def _check_generated(name: str, total: float) -> None:
    """Refuse note ops that would generate more notes than a clip can hold.

    ratchet(N) and repeat(step, count) multiply the matched set by a count
    taken straight from the transform string, so one absurd number — a
    hallucinated digit is enough — asks this process for hundreds of millions
    of dicts and takes the machine down with it. Anything above the read limit
    is useless anyway: the clip could never be read back afterwards.
    """
    if total > MAX_NOTES_PER_READ:
        raise TransformError(
            f"{name}(...) would generate about {int(total)} notes, over the "
            f"{MAX_NOTES_PER_READ}-note limit — a clip that large cannot be read back. "
            f"Use a smaller count, or a narrower selector."
        )


def _apply_note_op(
    name: str, arg_text: str, matched: list[dict[str, Any]], env: _Env
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (replacement notes for the matched set, removed originals)."""
    args = [a.strip() for a in arg_text.split(",") if a.strip()]

    if name == "ratchet":
        if not args:
            raise TransformError("ratchet(N) or ratchet(grid) needs an argument")
        grid = _parse_duration_token(args[0], env.beats_per_bar)
        replaced: list[dict[str, Any]] = []
        if grid is None:
            _check_generated("ratchet", len(matched) * max(_op_int("ratchet", args[0]), 1))
        for note in matched:
            if grid is not None:
                replaced.extend(_grid_ratchet(note, grid))
                _check_generated("ratchet", len(replaced))
            else:
                pieces = max(_op_int("ratchet", args[0]), 1)
                width = note["duration"] / pieces
                for i in range(pieces):
                    piece = {k: v for k, v in note.items() if k != "note_id"}
                    piece["start_time"] = note["start_time"] + i * width
                    piece["duration"] = width
                    replaced.append(piece)
        return replaced, matched

    if name == "repeat":
        if not args:
            raise TransformError("repeat(step[, count]) needs a step")
        step = _parse_duration_token(args[0], env.beats_per_bar)
        if step is None:
            step = _op_number("repeat", args[0])
        count = _op_int("repeat", args[1]) if len(args) > 1 else 1
        _check_generated("repeat", len(matched) * (max(count, 0) + 1))
        added = []
        for note in matched:
            for i in range(1, count + 1):
                echo = {k: v for k, v in note.items() if k != "note_id"}
                echo["start_time"] = note["start_time"] + i * step
                added.append(echo)
        return matched + added, []

    if name == "merge":
        gap = _parse_duration_token(args[0], env.beats_per_bar) if args else None
        if gap is None and args:
            gap = _op_number("merge", args[0])
        by_pitch: dict[int, list[dict[str, Any]]] = {}
        for note in sorted(matched, key=lambda n: n["start_time"]):
            by_pitch.setdefault(note["pitch"], []).append(note)
        replaced = []
        removed = []
        for runs in by_pitch.values():
            run = [runs[0]]
            for note in runs[1:]:
                previous = run[-1]
                touching = (
                    gap is None
                    or note["start_time"] - (previous["start_time"] + previous["duration"])
                    <= gap + 1e-9
                )
                if touching:
                    run.append(note)
                else:
                    replaced.append(_merge_run(run))
                    removed.extend(run if len(run) > 1 else [])
                    run = [note]
            replaced.append(_merge_run(run))
            removed.extend(run if len(run) > 1 else [])
        return replaced, removed

    raise TransformError(f"unknown note op '{name}'")


def _merge_run(run: list[dict[str, Any]]) -> dict[str, Any]:
    if len(run) == 1:
        return run[0]
    # Dynamics inherit from the EARLIEST note in the run.
    merged = {k: v for k, v in run[0].items() if k != "note_id"}
    end = max(n["start_time"] + n["duration"] for n in run)
    merged["duration"] = end - merged["start_time"]
    return merged


# --- entry point -------------------------------------------------------------


def apply_transforms(
    notes: list[dict[str, Any]],
    transform_text: str,
    sig_numerator: int = 4,
    sig_denominator: int = 4,
    clip_duration: float | None = None,
    seed: int | None = None,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Apply transform statements to note dicts.

    Returns (notes, total matched count, warnings). Statements that fail to
    parse warn and skip — same policy as the notation parser. Mutated notes
    keep their note_id; notes created by ratchet/repeat/merge have none.
    """
    beats_per_bar = sig_numerator * 4.0 / sig_denominator
    env = _Env(random.Random(seed), beats_per_bar, clip_duration or 0.0)
    if clip_duration is None and notes:
        env.clip_duration = max(n["start_time"] + n["duration"] for n in notes)

    working = [dict(n) for n in notes]
    warnings: list[str] = []
    total_matched = 0

    for statement in _split_statements(transform_text):
        try:
            selector_text, action = _split_selector_action(statement)
            selector = (
                _parse_selector(selector_text, beats_per_bar) if selector_text else _Selector()
            )
            matched = [n for n in working if _matches(n, selector, env)]
            total_matched += len(matched)
            if not matched:
                warnings.append(f"'{statement}': no notes matched")
                continue

            note_op = _NOTE_OP_RE.match(action)
            if note_op:
                replaced, removed = _apply_note_op(note_op.group(1), note_op.group(2), matched, env)
                removed_ids = {id(n) for n in removed}
                matched_ids = {id(n) for n in matched}
                if note_op.group(1) == "ratchet":
                    working = [n for n in working if id(n) not in matched_ids] + replaced
                else:
                    working = [n for n in working if id(n) not in removed_ids]
                    for n in replaced:
                        if id(n) not in {id(w) for w in working}:
                            working.append(n)
                continue

            field, op, expression = _action_to_assignment(action)
            if field == "__delete__":
                matched_ids = {id(n) for n in matched}
                working = [n for n in working if id(n) not in matched_ids]
                continue
            if field == "__range__":
                range_match = _SHORTHAND_V_RE.match(expression)
                velocity, deviation = _velocity_range_fields(
                    int(range_match.group(1)), int(range_match.group(2))
                )
                for note in matched:
                    note["velocity"] = velocity
                    note["velocity_deviation"] = deviation
                continue
            env.count = len(matched)
            env.selection = sorted(matched, key=lambda n: n["start_time"])
            for i, note in enumerate(env.selection):
                env.index = i
                env.note = note
                _apply_assignment(field, op, _evaluate_expr(expression, env), note)
        except TransformError as exc:
            warnings.append(f"'{statement}': {exc}")

    working.sort(key=lambda n: (n["start_time"], n["pitch"]))
    return working, total_matched, warnings


def _action_to_assignment(action: str) -> tuple[str, str, str]:
    """Normalize an action (assignment or shorthand) to (field, op, expr)."""
    match = _ASSIGN_RE.match(action)
    if match:
        return _PARAMS[match.group(1)], match.group(2), match.group(3)
    if action == "v0":
        return "__delete__", "=", "0"
    match = _SHORTHAND_V_RE.match(action)
    if match:
        if match.group(2):
            # v80-100: base velocity + deviation span — expressed as two
            # assignments would need two statements; do velocity here and let
            # deviation ride along via a compound trick is NOT worth it, so
            # the range shorthand sets velocity to the low bound and deviation
            # to the span in one go.
            return "__range__", "=", action
        return "velocity", "=", match.group(1)
    match = _SHORTHAND_V_DELTA_RE.match(action)
    if match:
        # Sign becomes the operator: the expression grammar has no unary "+",
        # so "v+10" passed through as the expression "+10" would fail to parse.
        delta = match.group(1)
        return "velocity", "+=" if delta[0] == "+" else "-=", delta[1:]
    match = _SHORTHAND_P_RE.match(action)
    if match:
        return "probability", "=", match.group(1)
    match = _SHORTHAND_P_DELTA_RE.match(action)
    if match:
        delta = match.group(1)
        return "probability", "+=" if delta[0] == "+" else "-=", delta[1:]
    if _DURATION_SHORTHAND_RE.match(action):
        return "duration", "=", action
    raise TransformError(f"unrecognized action '{action}'")


_DURATION_SHORTHAND_RE = re.compile(r"^(?:n[\d.]*/\d+[dt]?|\d+bar(?:[+-]n[\d.]*/\d+[dt]?)?)$")
