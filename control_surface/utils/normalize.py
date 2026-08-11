"""Device-parameter normalization.

All device parameters cross the wire as 0.0–1.0 regardless of their real
range: it prevents out-of-range errors and lets the model reason in halves
and quarters. Note for mixer volume specifically: normalized 0.85 ≈ 0 dB in
Live's fader law (documented in tool schemas).
"""

from typing import Any, Optional


def normalize_parameter(param: Any, value: Optional[float] = None) -> float:
    """Convert a parameter's value to 0.0-1.0 within its min/max range."""
    raw = param.value if value is None else value
    span = param.max - param.min
    if span == 0:
        return 0.0
    return (raw - param.min) / span


def denormalize_parameter(param: Any, normalized: float) -> float:
    """Convert 0.0-1.0 back to the parameter's native range, clamped."""
    clamped = max(0.0, min(1.0, normalized))
    return param.min + clamped * (param.max - param.min)
