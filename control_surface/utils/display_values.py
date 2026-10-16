"""Set a parameter from the text Live displays for it: '-6 dB', '+5 st',
'800 Hz', '1.2 kHz', '250 ms', '50 %', '30L' / 'C' for pan, '-inf dB'.

Live has no "set from string" call, but DeviceParameter.str_for_value(v)
renders any native value as Live would display it. Displays are monotonic in
the native value for continuous parameters, so a bisection over [min, max]
finds the native value whose display matches — whatever the underlying
scaling (linear semitones, log frequency, dB curves).
"""

import math
import re
from typing import Any

from ..errors import ValidationError
from ..registry import LiveAPIError

# unit -> (base unit, factor to base)
_UNITS = {
    "": ("", 1.0),
    "hz": ("hz", 1.0),
    "khz": ("hz", 1000.0),
    "ms": ("ms", 1.0),
    "s": ("ms", 1000.0),
    "db": ("db", 1.0),
    "st": ("st", 1.0),
    "ct": ("ct", 1.0),
    "%": ("%", 1.0),
    "°": ("°", 1.0),
}
_NUMBER = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+|inf))\s*([a-z%°]*)\s*$", re.IGNORECASE)
_PAN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([LR])\s*$", re.IGNORECASE)
_SEARCH_STEPS = 48


def parse_display(text: str) -> tuple[float, str] | None:
    """(number in base units, base unit), or None if the text isn't a number
    with a known unit. Pan reads '30L' as -30, 'C' as 0."""
    stripped = text.strip()
    if stripped.upper() == "C":
        return 0.0, "pan"
    pan = _PAN.match(stripped)
    if pan:
        amount = float(pan.group(1))
        return (-amount if pan.group(2).upper() == "L" else amount), "pan"
    match = _NUMBER.match(stripped)
    if match is None:
        return None
    number, unit = match.group(1).lower(), match.group(2).lower()
    if unit not in _UNITS:
        return None
    base, factor = _UNITS[unit]
    if number.lstrip("+-") == "inf":
        return (-math.inf if number.startswith("-") else math.inf), base
    return float(number) * factor, base


def _shown(param: Any, native: float) -> tuple[float, str]:
    text = param.str_for_value(native)
    parsed = parse_display(text)
    if parsed is None:
        raise LiveAPIError(
            f"'{param.name}' displays values like '{text}', which can't be matched "
            f"from text — give a 0-1 value instead"
        )
    return parsed


def native_for_display(param: Any, text: str) -> float:
    """The native value whose display is closest to `text`."""
    target = parse_display(text)
    if target is None:
        raise ValidationError(
            f"Can't read {text!r} as a value for '{param.name}': use a number with "
            f"the unit Live shows (e.g. '-6 dB', '+5 st', '800 Hz'), or 0-1"
        )
    goal, unit = target
    low, high = _shown(param, param.min), _shown(param, param.max)
    shown_unit = low[1]
    if unit == "":  # a bare number means "in whatever unit Live shows"
        unit = shown_unit
    if unit != shown_unit:
        raise ValidationError(
            f"'{param.name}' is shown as {param.str_for_value(param.min)!r} to "
            f"{param.str_for_value(param.max)!r} — give the value in those units"
        )
    bottom, top = sorted((low[0], high[0]))
    if not bottom <= goal <= top:
        raise ValidationError(
            f"{text!r} is outside '{param.name}': its range is "
            f"{param.str_for_value(param.min)!r} to {param.str_for_value(param.max)!r}"
        )
    if goal == low[0]:
        return param.min
    if goal == high[0]:
        return param.max

    increasing = high[0] >= low[0]
    a, b = param.min, param.max
    for _ in range(_SEARCH_STEPS):
        mid = (a + b) / 2
        if (_shown(param, mid)[0] < goal) == increasing:
            a = mid
        else:
            b = mid
    candidates = [a, b]
    if param.is_quantized:
        candidates = [
            float(v) for v in {math.floor(a), math.ceil(b)} if param.min <= v <= param.max
        ] or [round(a)]
    return min(candidates, key=lambda v: abs(_shown(param, v)[0] - goal))


def native_for_choice(param: Any, label: str) -> float | None:
    """A quantized parameter's native value for one of its value_items
    labels (case-insensitive), or None if it has no such label."""
    items = list(getattr(param, "value_items", []) or [])
    lowered = [item.lower() for item in items]
    if label.strip().lower() in lowered:
        return param.min + lowered.index(label.strip().lower())
    return None
