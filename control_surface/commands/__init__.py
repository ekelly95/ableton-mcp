"""Command handlers, grouped by domain.

Importing this package registers every command with the global REGISTRY.
"""

from ..registry import REGISTRY
from . import browser, clips, devices, meta, tracks, transport

__all__ = ["REGISTRY", "browser", "clips", "devices", "meta", "tracks", "transport"]
