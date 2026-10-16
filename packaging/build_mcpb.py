"""Build dist/ableton-mcp-<version>.mcpb — a one-click MCP Bundle for Claude
Desktop (and other MCPB hosts).

Uses the bundle format's `uv` server type (manifest 0.4): the bundle carries
pyproject.toml, uv.lock and the source, and the host installs the locked
dependencies with uv — numpy's compiled wheels included, on every platform,
no Python install needed. The manifest's tool list is generated from the
server itself, and the manifest is validated against the vendored official
schema before anything is written.

After installing the bundle, ask the model to run install_control_surface to
put the Live side in place.

Run:  uv run python packaging/build_mcpb.py
"""

import json
import sys
import tomllib
import zipfile
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCHEMA = ROOT / "packaging" / "mcpb-manifest-v0.4.schema.json"
INCLUDE_DIRS = ("mcp_server", "control_surface", "m4l")
INCLUDE_FILES = ("pyproject.toml", "uv.lock", "README.md", "LICENSE", "AGENTS.md")
ENTRY_POINT = "server.py"
ENTRY_SOURCE = '''"""MCP Bundle entry point: the host runs `uv run --directory <bundle> server.py`."""

from mcp_server import run

run()
'''


def manifest(version: str) -> dict:
    from mcp_server.server import registry_tools

    return {
        "manifest_version": "0.4",
        "name": "ableton-mcp",
        "display_name": "Ableton Live",
        "version": version,
        "description": (
            "Control Ableton Live: write and edit MIDI, mix, automate, build "
            "arrangements, load sounds and samples, and measure the mix."
        ),
        "long_description": (
            "Works with Ableton Live 12 through a control surface that runs inside "
            "Live. After installing, ask Claude to run install_control_surface, "
            "restart Live, and pick AbletonMCP under Preferences > Link, Tempo & "
            "MIDI > Control Surface."
        ),
        "author": {"name": "ekelly95"},
        "repository": {"type": "git", "url": "https://github.com/ekelly95/ableton-mcp"},
        "license": "MIT",
        "keywords": ["ableton", "live", "music", "midi", "daw"],
        "server": {
            "type": "uv",
            "entry_point": ENTRY_POINT,
            "mcp_config": {
                "command": "uv",
                "args": ["run", "--directory", "${__dirname}", ENTRY_POINT],
            },
        },
        "tools": [
            {"name": t.name, "description": (t.description or "").split(". ")[0][:200]}
            for t in registry_tools()
        ],
        "compatibility": {
            # Live runs on Windows and macOS; the server needs to sit beside it.
            "platforms": ["darwin", "win32"],
            "runtimes": {"python": ">=3.11"},
        },
    }


def _files(directory: Path):
    for path in sorted(directory.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield path


def build(out_dir: Path | None = None) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    data = manifest(version)
    jsonschema.validate(data, json.loads(SCHEMA.read_text()))

    out = (out_dir or ROOT / "dist") / f"ableton-mcp-{version}.mcpb"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(data, indent=2) + "\n")
        bundle.writestr(ENTRY_POINT, ENTRY_SOURCE)
        for name in INCLUDE_FILES:
            if (ROOT / name).exists():
                bundle.write(ROOT / name, name)
        for directory in INCLUDE_DIRS:
            for path in _files(ROOT / directory):
                bundle.write(path, path.relative_to(ROOT).as_posix())
    return out


if __name__ == "__main__":
    path = build()
    print(f"{path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
