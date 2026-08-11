"""Command handlers, grouped by domain.

Importing this package registers every command with the global REGISTRY.
"""

from ..registry import REGISTRY
from . import arrangement, browser, clips, devices, meta, tracks, transport

__all__ = [
    "REGISTRY",
    "arrangement",
    "browser",
    "clips",
    "devices",
    "meta",
    "tracks",
    "transport",
]
