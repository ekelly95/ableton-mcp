"""Command handlers, grouped by domain.

Importing this package registers every command with the global REGISTRY.
"""

from ..registry import REGISTRY
from . import (
    arrangement,
    browser,
    capture,
    clips,
    devices,
    envelopes,
    history,
    meta,
    tracks,
    transport,
)

__all__ = [
    "REGISTRY",
    "arrangement",
    "browser",
    "capture",
    "clips",
    "devices",
    "envelopes",
    "history",
    "meta",
    "tracks",
    "transport",
]
