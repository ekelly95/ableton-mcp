"""Error taxonomy shared across the control surface.

Lives in its own module so leaf utilities (utils/pitch.py) can raise typed
errors without importing the registry (which imports them back).
"""

from typing import Any, Optional


class ValidationError(Exception):
    """A parameter failed validation. Carries the parameter name to the wire."""

    def __init__(self, message: str, param: Optional[str] = None, value: Any = None):
        super().__init__(message)
        self.message = message
        self.param = param
        self.value = value

    def __str__(self) -> str:
        if self.param:
            return f"Validation error for '{self.param}': {self.message}"
        return f"Validation error: {self.message}"


class LiveAPIError(Exception):
    """Live refused or couldn't perform the operation (bad index, occupied slot...)."""
