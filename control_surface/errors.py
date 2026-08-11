"""Error taxonomy shared across the control surface.

Lives in its own module so leaf utilities (utils/pitch.py) can raise typed
errors without importing the registry (which imports them back).
"""

from collections.abc import Callable
from typing import Any


class ValidationError(Exception):
    """A parameter failed validation. Carries the parameter name to the wire."""

    def __init__(self, message: str, param: str | None = None, value: Any = None):
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


class PartialApplyError(LiveAPIError):
    """A batch write failed mid-apply; `applied` names the fields that already landed.

    Batch setters cannot be atomic (Live has no rollback), so when a write
    fails partway the caller must learn what changed. The message is the
    primary channel — the MCP server surfaces only str(e) to the model — and
    the structured `applied` list additionally reaches the wire for journals
    and tests.
    """

    def __init__(self, failed_field: str, cause: str, applied: list[str]):
        applied_text = ", ".join(applied) if applied else "nothing"
        super().__init__(
            f"{failed_field} failed: {cause}. Already applied: {applied_text}. "
            "The target is partially updated — re-read it before retrying."
        )
        self.failed_field = failed_field
        self.applied = list(applied)


def batch_writer(applied: list[str]) -> Callable[..., None]:
    """Bind an applied-list for a batch setter; returns write(field, setter).

    Each successful write appends `field` to `applied`; a failure raises
    PartialApplyError naming the failed field and everything that landed
    before it. `label`, when given, is the error's field name while `field`
    stays the applied-list entry (e.g. label="parameter 'Cutoff'" vs
    field="Cutoff"). Catches Exception, not a narrower type: whatever Live
    throws, the caller must still learn what already changed.
    """

    def write(field: str, setter: Callable[[], Any], label: str | None = None) -> None:
        try:
            setter()
        except Exception as e:
            raise PartialApplyError(label or field, str(e), applied) from e
        applied.append(field)

    return write
