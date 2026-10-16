"""The MCP Bundle builder: a schema-valid manifest and a complete, clean bundle."""

import json
import sys
import zipfile
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

from build_mcpb import SCHEMA, build  # noqa: E402

from mcp_server.server import registry_tools  # noqa: E402


def test_bundle_is_complete_and_valid(tmp_path):
    bundle = build(tmp_path)
    with zipfile.ZipFile(bundle) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    jsonschema.validate(manifest, json.loads(SCHEMA.read_text()))
    assert manifest["server"]["type"] == "uv"
    assert {"server.py", "pyproject.toml", "uv.lock", "AGENTS.md"} <= names
    assert "control_surface/commands/clips.py" in names
    assert "mcp_server/install.py" in names
    assert "m4l/AbletonMCP Tap.amxd" in names
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in names if n.startswith(("tests/", ".venv/", "scripts/"))]
    assert [t["name"] for t in manifest["tools"]] == [t.name for t in registry_tools()]
