"""Registry: validation, coercion, schema generation, drift hash."""

import pytest

from control_surface.registry import (
    CommandRegistry,
    ParamSchema,
    ParamType,
    ValidationError,
)


def make_registry_with_command(**register_kwargs):
    registry = CommandRegistry()
    params = register_kwargs.pop("params", [])

    @registry.register("test_cmd", params=params, **register_kwargs)
    def handler(ctx, **kwargs):
        return kwargs

    return registry


class TestParamValidation:
    def test_int_coercion(self):
        schema = ParamSchema("x", ParamType.INT)
        assert schema.validate(5) == 5
        assert schema.validate(5.7) == 5
        assert schema.validate("12") == 12

    def test_int_rejects_bool(self):
        schema = ParamSchema("x", ParamType.INT)
        with pytest.raises(ValidationError):
            schema.validate(True)

    def test_float_range(self):
        schema = ParamSchema("bpm", ParamType.FLOAT, min_value=20, max_value=999)
        assert schema.validate(128.0) == 128.0
        with pytest.raises(ValidationError) as exc:
            schema.validate(10)
        assert exc.value.param == "bpm"
        with pytest.raises(ValidationError):
            schema.validate(1000)

    def test_bool_coercion(self):
        schema = ParamSchema("flag", ParamType.BOOL)
        assert schema.validate(True) is True
        assert schema.validate("true") is True
        assert schema.validate("0") is False
        assert schema.validate(1) is True
        with pytest.raises(ValidationError):
            schema.validate([1])

    def test_string_rejects_non_string(self):
        schema = ParamSchema("name", ParamType.STRING)
        assert schema.validate("hi") == "hi"
        with pytest.raises(ValidationError):
            schema.validate(42)

    def test_enum(self):
        schema = ParamSchema("action", ParamType.STRING, enum_values=["play", "stop"])
        assert schema.validate("play") == "play"
        with pytest.raises(ValidationError):
            schema.validate("dance")

    def test_required_missing(self):
        schema = ParamSchema("x", ParamType.INT, required=True)
        with pytest.raises(ValidationError):
            schema.validate(None)

    def test_optional_default(self):
        schema = ParamSchema("x", ParamType.INT, required=False, default=-1)
        assert schema.validate(None) == -1

    def test_lists(self):
        assert ParamSchema("a", ParamType.INT_LIST).validate([1, "2", 3.0]) == [1, 2, 3]
        assert ParamSchema("b", ParamType.STRING_LIST).validate(["x"]) == ["x"]
        with pytest.raises(ValidationError):
            ParamSchema("c", ParamType.FLOAT_LIST).validate("not a list")

    def test_object_list(self):
        schema = ParamSchema("mods", ParamType.OBJECT_LIST)
        assert schema.validate([{"a": 1}]) == [{"a": 1}]
        with pytest.raises(ValidationError):
            schema.validate([{"a": 1}, "nope"])


class TestNoteValidation:
    def test_note_defaults(self):
        schema = ParamSchema("notes", ParamType.NOTE)
        note = schema.validate({"pitch": 60, "start_time": 0.0, "duration": 1.0})
        assert note["velocity"] == 100.0
        assert note["mute"] is False
        assert note["probability"] == 1.0
        assert note["velocity_deviation"] == 0.0
        assert note["release_velocity"] == 64.0

    def test_note_modern_fields(self):
        schema = ParamSchema("notes", ParamType.NOTE)
        note = schema.validate(
            {
                "pitch": 60,
                "start_time": 0.5,
                "duration": 0.25,
                "probability": 0.5,
                "velocity_deviation": 20,
            }
        )
        assert note["probability"] == 0.5
        assert note["velocity_deviation"] == 20.0

    @pytest.mark.parametrize(
        "bad",
        [
            {"pitch": 128, "start_time": 0, "duration": 1},
            {"pitch": 60, "start_time": -1, "duration": 1},
            {"pitch": 60, "start_time": 0, "duration": 0},
            {"pitch": 60, "start_time": 0, "duration": 1, "velocity": 200},
            {"pitch": 60, "start_time": 0, "duration": 1, "probability": 1.5},
            {"pitch": 60, "start_time": 0, "duration": 1, "velocity_deviation": 200},
            {"pitch": 60, "start_time": 0},
        ],
    )
    def test_note_rejects(self, bad):
        schema = ParamSchema("notes", ParamType.NOTE)
        with pytest.raises(ValidationError):
            schema.validate(bad)

    def test_note_list(self):
        schema = ParamSchema("notes", ParamType.NOTE_LIST)
        notes = schema.validate(
            [
                {"pitch": 60, "start_time": 0, "duration": 1},
                {"pitch": 64, "start_time": 1, "duration": 1},
            ]
        )
        assert len(notes) == 2


class TestCommandSchema:
    def test_validate_params_filters_unknown(self):
        registry = make_registry_with_command(
            params=[ParamSchema("x", ParamType.INT, required=False, default=0)]
        )
        validated = registry.get("test_cmd").validate_params({"x": 1, "bogus": 2})
        assert validated == {"x": 1}

    def test_to_mcp_tool_shape(self):
        registry = make_registry_with_command(
            params=[
                ParamSchema("x", ParamType.INT, min_value=0, description="an int"),
                ParamSchema("y", ParamType.STRING, required=False, default="hi"),
            ],
            description="A test command",
            read_only=True,
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )
        tool = registry.get("test_cmd").to_mcp_tool()
        assert tool["name"] == "test_cmd"
        assert tool["description"] == "A test command"
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False
        assert tool["inputSchema"]["properties"]["x"]["minimum"] == 0
        assert tool["inputSchema"]["required"] == ["x"]
        assert tool["inputSchema"]["properties"]["y"]["default"] == "hi"
        assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False}
        assert tool["outputSchema"]["properties"]["ok"]["type"] == "boolean"


class TestRegistry:
    def test_duplicate_registration_raises(self):
        registry = CommandRegistry()

        @registry.register("dup")
        def a(ctx):
            return {}

        with pytest.raises(ValueError):

            @registry.register("dup")
            def b(ctx):
                return {}

    def test_categories(self):
        registry = CommandRegistry()

        @registry.register("one", category="alpha")
        def one(ctx):
            return {}

        @registry.register("two", category="alpha")
        def two(ctx):
            return {}

        assert registry.list_by_category("alpha") == ["one", "two"]
        assert "alpha" in registry.get_categories()

    def test_schema_hash_stable_and_sensitive(self):
        r1 = make_registry_with_command(description="same")
        r2 = make_registry_with_command(description="same")
        r3 = make_registry_with_command(description="different")
        assert r1.schema_hash() == r2.schema_hash()
        assert r1.schema_hash() != r3.schema_hash()

    def test_timeout_from_config(self):
        registry = CommandRegistry()

        @registry.register("load_item")
        def load_item(ctx):
            return {}

        assert registry.get("load_item").timeout == 120.0
