"""Undo/redo over Live's own history."""

from typing import Any

from ..registry import REGISTRY, ParamSchema, ParamType


@REGISTRY.register(
    "undo",
    params=[
        ParamSchema("steps", ParamType.INT, required=False, default=1, min_value=1, max_value=20),
        ParamSchema(
            "redo", ParamType.BOOL, required=False, default=False, description="Redo instead"
        ),
    ],
    category="history",
    destructive=True,
    undo_step=False,
    description=(
        "Undo (or redo) the latest steps in Live's history, exactly like Ctrl+Z. "
        "Each write tool call is one step — except that Live records a rename "
        "(track, clip, scene, locator) as a step of its own, so a call that "
        "also renamed needs one more. Live has ONE history shared with the "
        "user: if they edited after your change, this undoes their edit instead "
        "— check with them first if they may have."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "undone": {"type": "integer"},
            "redone": {"type": "integer"},
            "can_undo": {"type": "boolean"},
            "can_redo": {"type": "boolean"},
        },
    },
)
def undo(ctx, steps: int = 1, redo: bool = False) -> dict[str, Any]:
    song = ctx.song
    done = 0
    for _ in range(steps):
        if redo:
            if not song.can_redo:
                break
            song.redo()
        else:
            if not song.can_undo:
                break
            song.undo()
        done += 1
    return {
        "redone" if redo else "undone": done,
        "can_undo": song.can_undo,
        "can_redo": song.can_redo,
    }
