"""Read-only escape hatch (2.19): inspect any object in Live's API by path.

For everything the dedicated tools don't cover. Paths are Live's canonical
object paths, as Max for Live writes them — "live_set tracks 0 devices 1
chains 2" — so documentation and M4L knowledge carry over. Only PROPERTIES
are read; methods are never called, so nothing can change. Writes stay with
the dedicated, validated tools.
"""

from typing import Any

from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType

_ROOTS = ("live_set", "live_app")
_MAX_PROPERTIES = 120
_MAX_LIST_VALUES = 64
_MAX_STRING = 400

# Not read: unverified suspects in the access violation that crashed Live
# 12.4.6 during a capture probe (the same probe also created, re-routed and
# deleted a track in one task, so these may be innocent). Revisit with care.
_SKIPPED = {"available_input_routing_channels", "available_output_routing_channels"}


def _property_names(obj: Any) -> list[str]:
    names = {
        name
        for name in dir(type(obj))
        if not name.startswith("_") and isinstance(getattr(type(obj), name, None), property)
    }
    try:  # plain-Python objects (tests' mocks) keep attributes per instance
        names |= {name for name in vars(obj) if not name.startswith("_")}
    except TypeError:
        pass
    return sorted(names - _SKIPPED)


def _is_live_object(value: Any) -> bool:
    return not isinstance(value, (str, bytes, int, float, bool, type(None))) and not callable(value)


def _describe(value: Any) -> Any:
    """A JSON-safe view of one property value; child objects become markers."""
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else value[:_MAX_STRING] + "…"
    if isinstance(value, (list, tuple)) or type(value).__name__.endswith("Vector"):
        items = list(value)
        if all(isinstance(v, (bool, int, float, str)) or v is None for v in items):
            return items[:_MAX_LIST_VALUES] + (["…"] if len(items) > _MAX_LIST_VALUES else [])
        return {"list": len(items), "of": type(items[0]).__name__ if items else None}
    if hasattr(value, "display_name"):  # routing types/channels: show the name
        return {"object": type(value).__name__, "display_name": str(value.display_name)}
    return {"object": type(value).__name__}


def _resolve(ctx, path: str) -> tuple[Any, str]:
    tokens = path.split()
    if not tokens or tokens[0] not in _ROOTS:
        raise LiveAPIError(f"Paths start with {' or '.join(_ROOTS)}, e.g. 'live_set tracks 0'")
    obj = ctx.song if tokens[0] == "live_set" else ctx.app
    walked = [tokens[0]]
    i = 1
    while i < len(tokens):
        name = tokens[i]
        if name.startswith("_") or name in _SKIPPED:
            raise LiveAPIError(f"'{name}' can't be inspected")
        try:
            child = getattr(obj, name)
        except AttributeError:
            raise LiveAPIError(
                f"'{' '.join(walked)}' has no '{name}'. It has: {_property_names(obj)[:40]}"
            ) from None
        if callable(child) and not _is_list(child):
            raise LiveAPIError(f"'{name}' is a method; inspect only reads properties")
        walked.append(name)
        i += 1
        if _is_list(child):
            if i < len(tokens) and tokens[i].lstrip("-").isdigit():
                items = list(child)
                index = int(tokens[i])
                if not 0 <= index < len(items):
                    raise LiveAPIError(
                        f"'{' '.join(walked)}' has {len(items)} items; {index} is out of range"
                    )
                child = items[index]
                walked.append(tokens[i])
                i += 1
            elif i < len(tokens):
                raise LiveAPIError(f"'{' '.join(walked)}' is a list: give an index next")
        obj = child
    return obj, " ".join(walked)


def _is_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) or type(value).__name__.endswith("Vector")


@REGISTRY.register(
    "inspect",
    params=[
        ParamSchema(
            "path",
            ParamType.STRING,
            description="Live object path, e.g. 'live_set tracks 0 devices 1 chains 2'",
        ),
        ParamSchema(
            "properties",
            ParamType.STRING_LIST,
            required=False,
            description="Read only these properties (default: all readable ones)",
        ),
    ],
    category="meta",
    read_only=True,
    description=(
        "Read-only escape hatch: show any object in Live's API by its canonical "
        "path (as in Max for Live) — its properties and what it contains — for "
        "anything the dedicated tools don't cover. Walk down by following the "
        "listed children. Never changes anything; use the dedicated tools to write."
    ),
)
def inspect(ctx, path: str, properties: list[str] | None = None) -> dict[str, Any]:
    obj, canonical = _resolve(ctx, path)
    if _is_list(obj):
        items = list(obj)
        return {
            "path": canonical,
            "list": len(items),
            "items": [_describe(v) for v in items[:_MAX_LIST_VALUES]],
        }
    names = properties if properties else _property_names(obj)
    values: dict[str, Any] = {}
    children: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name in names[:_MAX_PROPERTIES]:
        if name.startswith("_") or name in _SKIPPED:
            continue
        try:
            value = getattr(obj, name)
        except Exception as e:  # e.g. value_items on a continuous parameter
            errors[name] = str(e)[:120]
            continue
        if callable(value) and not _is_list(value):
            continue
        described = _describe(value)
        if isinstance(described, dict) and ("list" in described or "object" in described):
            children[name] = described
        else:
            values[name] = described
    result: dict[str, Any] = {"path": canonical, "type": type(obj).__name__, "properties": values}
    if children:
        result["children"] = children
    if errors:
        result["unreadable"] = errors
    if not properties and len(names) > _MAX_PROPERTIES:
        result["truncated"] = True
    return result
