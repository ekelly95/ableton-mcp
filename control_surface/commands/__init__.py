"""Command handlers, grouped by domain.

Importing this package registers every command with the global REGISTRY.
Command modules are populated in build phase P2+; until then this package
exists so the skeleton (ping-only) control surface loads in Live.
"""

from ..registry import REGISTRY

__all__ = ["REGISTRY"]
