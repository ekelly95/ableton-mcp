"""Generated tool schemas must be machine-independent.

The MCP server (repo checkout) and the deployed control surface (copy inside
Live) each generate the schema hash from their own location — any absolute
path baked into a description would make the drift check cry wolf on every
machine or install location.
"""

import json
import re

from control_surface.commands import REGISTRY

MACHINE_PATH = re.compile(r"[A-Za-z]:\\|/home/|/Users/")


def test_no_machine_paths_in_tool_schemas():
    blob = json.dumps(REGISTRY.generate_mcp_tools())
    match = MACHINE_PATH.search(blob)
    assert match is None, (
        f"machine-specific path leaked into tool schemas near: ...{blob[max(0, match.start() - 60) : match.end() + 60]}..."
    )
