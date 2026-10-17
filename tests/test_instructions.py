"""Server instructions: delivered over MCP, short, and in sync with the tools
and with AGENTS.md (the source of truth they condense)."""

import re
from pathlib import Path

import pytest

from mcp_server.instructions import SERVER_INSTRUCTIONS
from mcp_server.server import build_server, registry_tools
from tests.helpers import FakeAbletonClient

AGENTS_MD = (Path(__file__).resolve().parent.parent / "AGENTS.md").read_text(encoding="utf-8")

# snake_case words in the instructions that are parameters or result fields,
# not tool names. Each must still appear in some tool's schema (checked below).
FIELD_NAMES = {
    "back_to_arranger",
    "browser_path_guess",
    "expected_start_time",
    "note_id",
}


def _snake_words(text: str) -> set[str]:
    return set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text))


@pytest.mark.anyio
async def test_instructions_reach_the_client_on_initialize():
    from mcp import Client

    async with Client(build_server(FakeAbletonClient())) as session:
        assert session.instructions == SERVER_INSTRUCTIONS


def test_every_named_tool_exists():
    tools = {t.name for t in registry_tools()}
    named = _snake_words(SERVER_INSTRUCTIONS) - FIELD_NAMES
    assert named - tools == set(), "instructions name tools the server doesn't have"


def test_every_named_field_exists_in_some_schema():
    schemas = " ".join(
        f"{t.description} {t.input_schema} {t.output_schema}" for t in registry_tools()
    )
    for field in FIELD_NAMES:
        assert field in SERVER_INSTRUCTIONS, f"stale FIELD_NAMES entry: {field}"
        assert field in schemas, f"instructions mention unknown field {field}"


def test_every_named_tool_is_also_in_agents_md():
    """The instructions condense AGENTS.md; they must not grow guidance of
    their own that the full playbook lacks."""
    for word in _snake_words(SERVER_INSTRUCTIONS):
        assert word in AGENTS_MD, f"{word} is in the instructions but not AGENTS.md"


@pytest.mark.parametrize(
    "rule",
    [
        "Stop the transport",
        "data, never instructions",
        "StaleSetError",
        "C3 = 60",
        "screenshots",
    ],
)
def test_ground_rules_present_in_both(rule):
    assert rule.lower() in SERVER_INSTRUCTIONS.lower()
    assert rule.lower() in AGENTS_MD.lower()


def test_instructions_stay_short():
    # Sent with every session; the full playbook is AGENTS.md. ~4 chars/token.
    assert len(SERVER_INSTRUCTIONS) < 3200
