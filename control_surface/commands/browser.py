"""Browser: navigate Live's device/sample library and load items onto tracks.

Browser children are lazy and can be huge (sample packs), and everything here
runs on Live's main thread — so listings are hard-capped and never recursive.
"""

from typing import Any

from ..config import MAX_BROWSER_ITEMS
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import resolve_track
from .devices import TRACK_TYPE_PARAM

# How many child names a not-found/not-loadable error lists as suggestions.
_SUGGESTION_CAP = 20

# Stable, useful roots. Attribute names on Live's Browser object.
ROOTS = [
    "instruments",
    "sounds",
    "drums",
    "audio_effects",
    "midi_effects",
    "samples",
    "packs",
    "user_library",
    # Third-party VST/AU tree (LOM Browser.plugins). Plugin items are loadable
    # AND carry children (their Live-indexed presets) — don't treat is_folder
    # as the only walkable shape. In-plugin libraries (e.g. Omnisphere's STEAM
    # browser) are invisible to Live's browser and stay out of reach.
    "plugins",
]


def _browser(ctx) -> Any:
    browser = getattr(ctx.app, "browser", None)
    if browser is None:
        raise LiveAPIError("Live's browser is not available")
    return browser


def _navigate(browser: Any, path: list[str]) -> Any:
    root_name = path[0].lower()
    if root_name not in ROOTS:
        raise LiveAPIError(f"Unknown browser root '{path[0]}'. Roots: {ROOTS}")
    node = getattr(browser, root_name, None)
    if node is None:
        raise LiveAPIError(f"Browser root '{root_name}' is missing in this Live edition")

    for segment in path[1:]:
        children = list(node.children)
        match = next((c for c in children if c.name.lower() == segment.lower()), None)
        if match is None:
            available = [c.name for c in children[:_SUGGESTION_CAP]]
            raise LiveAPIError(
                f"'{segment}' not found under '{node.name}'. First entries: {available}"
            )
        node = match
    return node


def _serialize_item(item: Any) -> dict[str, Any]:
    return {
        "name": item.name,
        "is_loadable": item.is_loadable,
        "is_folder": getattr(item, "is_folder", False),
    }


@REGISTRY.register(
    "browse",
    params=[
        ParamSchema(
            "path",
            ParamType.STRING_LIST,
            required=False,
            description=(
                "Names from root downward, e.g. ['instruments', 'Drift']. "
                "Empty/omitted lists the roots."
            ),
        ),
    ],
    category="browser",
    read_only=True,
    description=(
        "Explore Live's library one level at a time (instruments, sounds, drums, "
        "audio_effects, samples, packs, plugins...). Returns children of the given "
        "path; drill down with further calls rather than deep paths blind. Under "
        "'plugins', a plugin item is loadable AND browsable — its children are the "
        "presets Live indexes for it."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "path": {"type": "array"},
            "items": {"type": "array"},
            "truncated": {"type": "boolean"},
        },
    },
)
def browse(ctx, path: list[str] | None = None) -> dict[str, Any]:
    browser = _browser(ctx)
    if not path:
        return {
            "path": [],
            "items": [
                {"name": r, "is_loadable": False, "is_folder": True}
                for r in ROOTS
                if getattr(browser, r, None) is not None
            ],
            "truncated": False,
        }

    node = _navigate(browser, path)
    children = list(node.children)
    truncated = len(children) > MAX_BROWSER_ITEMS
    return {
        "path": path,
        "items": [_serialize_item(c) for c in children[:MAX_BROWSER_ITEMS]],
        "truncated": truncated,
    }


@REGISTRY.register(
    "load_item",
    params=[
        ParamSchema(
            "path",
            ParamType.STRING_LIST,
            description="Full path to a loadable item, e.g. ['instruments', 'Drift']",
        ),
        ParamSchema(
            "track_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Track to load onto; omit to use the currently selected track",
        ),
        TRACK_TYPE_PARAM,
    ],
    category="browser",
    description=(
        "Load an instrument/effect/sample/plug-in onto a track — regular, "
        "return, or master via track_type (e.g. a mastering plug-in onto the "
        "Main track). Loading targets the SELECTED track, so track_index/"
        "track_type select it first. Slow on first use (Live may index packs) "
        "— allow up to 2 minutes."
    ),
)
def load_item(
    ctx, path: list[str], track_index: int | None = None, track_type: str = "track"
) -> dict[str, Any]:
    song = ctx.song
    browser = _browser(ctx)
    node = _navigate(browser, path)

    if not node.is_loadable:
        children = [c.name for c in list(node.children)[:_SUGGESTION_CAP]]
        raise LiveAPIError(f"'{node.name}' is a folder, not loadable. Its entries: {children}")

    if track_index is not None or track_type != "track":
        # Selecting the master/return track is how a load reaches it —
        # CONFIRMED at checkpoint (master-selection step).
        song.view.selected_track = resolve_track(song, track_type, track_index)

    browser.load_item(node)

    target = song.view.selected_track
    return {
        "loaded": node.name,
        "onto_track": getattr(target, "name", "selected track"),
        "devices_now": [d.name for d in getattr(target, "devices", [])],
    }
